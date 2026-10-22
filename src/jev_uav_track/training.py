"""Small, dependency-light rollout outcome calibration utilities.

This is an offline baseline, not a policy-gradient trainer. It fits a ridge
regressor to observed state transitions so the candidate rollout can be
calibrated before a learned dynamics/world model is introduced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from .data.hdf5_adapter import HDF5Episode
from .models import MissionConstraints, TrackingState


FEATURE_NAMES = (
    "target_range_m",
    "target_vx_mps",
    "target_vy_mps",
    "target_vz_mps",
    "uav_vx_mps",
    "uav_vy_mps",
    "uav_vz_mps",
    "visibility_probability",
    "occlusion_risk",
    "image_center_error",
    "uav_speed_mps",
    "command_vx_mps",
    "command_vy_mps",
    "command_vz_mps",
    "command_yaw_rate_dps",
)

OUTPUT_NAMES = (
    "visibility_probability",
    "image_center_error",
    "range_error_m",
    "covariance_trace",
)


def _numpy() -> Any:
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised without optional extras
        raise ImportError(
            "Rollout training requires NumPy; install with pip install -e \".[data]\""
        ) from exc
    return np


def _norm(values: Sequence[float]) -> float:
    return sum(float(value) ** 2 for value in values) ** 0.5


def feature_vector(
    state: TrackingState,
    command_mps: Sequence[float],
    yaw_rate_dps: float = 0.0,
) -> tuple[float, ...]:
    """Build the fixed state/action feature vector used by the calibrator."""

    target_velocity = state.target.velocity_mps
    uav_velocity = state.uav.velocity_mps
    return (
        float(state.target.range_m),
        float(target_velocity[0]),
        float(target_velocity[1]),
        float(target_velocity[2]),
        float(uav_velocity[0]),
        float(uav_velocity[1]),
        float(uav_velocity[2]),
        float(state.target.visibility_probability),
        float(state.target.occlusion_risk),
        float(state.target.image_center_error),
        _norm(uav_velocity),
        float(command_mps[0]),
        float(command_mps[1]),
        float(command_mps[2]),
        float(yaw_rate_dps),
    )


@dataclass(frozen=True)
class RolloutTrainingConfig:
    horizon_frames: int = 5
    stride: int = 2
    max_samples: int = 512
    desired_range_m: float = 20.0
    ridge_alpha: float = 1.0
    train_fraction: float = 0.7


@dataclass
class RolloutTrainingDataset:
    features: Any
    labels: Any
    feature_names: tuple[str, ...] = FEATURE_NAMES
    output_names: tuple[str, ...] = OUTPUT_NAMES

    @property
    def sample_count(self) -> int:
        return int(self.features.shape[0])

    def to_summary(self) -> Dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "feature_count": int(self.features.shape[1]),
            "output_count": int(self.labels.shape[1]),
            "feature_names": list(self.feature_names),
            "output_names": list(self.output_names),
        }


def build_training_dataset(
    episode: HDF5Episode,
    config: RolloutTrainingConfig | None = None,
) -> RolloutTrainingDataset:
    """Create chronological state/action -> future-outcome samples.

    The observed camera velocity is used as the action feature. This is a
    calibration baseline: it learns the relationship present in the recorded
    behavior and should not be mistaken for counterfactual dynamics coverage.
    """

    np = _numpy()
    cfg = config or RolloutTrainingConfig()
    if cfg.horizon_frames <= 0 or cfg.stride <= 0 or cfg.max_samples <= 0:
        raise ValueError("horizon_frames, stride, and max_samples must be positive")
    if not 0.0 < cfg.train_fraction < 1.0:
        raise ValueError("train_fraction must be in (0, 1)")

    last_start = episode.frame_count - cfg.horizon_frames - 1
    indices = list(range(0, max(0, last_start + 1), cfg.stride))
    if len(indices) > cfg.max_samples:
        selected = np.linspace(0, len(indices) - 1, cfg.max_samples).round().astype(int)
        indices = [indices[int(position)] for position in selected]

    features = []
    labels = []
    constraints = MissionConstraints(
        desired_range_m=cfg.desired_range_m,
        minimum_obstacle_margin_m=0.0,
    )
    for index in indices:
        future_index = index + cfg.horizon_frames
        state = episode.tracking_state(index, constraints=constraints)
        future = episode.tracking_state(future_index, constraints=constraints)
        features.append(feature_vector(state, episode.camera_velocities[index]))
        labels.append(
            (
                future.target.visibility_probability,
                future.target.image_center_error,
                abs(future.target.range_m - cfg.desired_range_m),
                future.target.covariance_trace,
            )
        )

    if not features:
        raise ValueError("Episode does not contain enough frames for the requested horizon")
    return RolloutTrainingDataset(
        features=np.asarray(features, dtype=np.float64),
        labels=np.asarray(labels, dtype=np.float64),
    )


@dataclass
class LinearOutcomeModel:
    """Standardized ridge model for four predicted rollout outcomes."""

    feature_mean: Any
    feature_scale: Any
    weights: Any
    feature_names: tuple[str, ...] = FEATURE_NAMES
    output_names: tuple[str, ...] = OUTPUT_NAMES
    training_summary: Mapping[str, Any] | None = None

    def _design(self, features: Any) -> Any:
        np = _numpy()
        values = np.asarray(features, dtype=np.float64)
        if values.ndim == 1:
            values = values.reshape(1, -1)
        normalized = (values - self.feature_mean) / self.feature_scale
        return np.column_stack([np.ones(values.shape[0]), normalized])

    def predict_array(self, features: Any) -> Any:
        return self._design(features) @ self.weights

    def predict_state(
        self,
        state: TrackingState,
        command_mps: Sequence[float],
        yaw_rate_dps: float = 0.0,
    ) -> Dict[str, float]:
        np = _numpy()
        prediction = self.predict_array(np.asarray(feature_vector(state, command_mps, yaw_rate_dps)))[0]
        return {name: float(prediction[index]) for index, name in enumerate(self.output_names)}

    def save(self, path: str | Path) -> Path:
        np = _numpy()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            destination,
            feature_mean=self.feature_mean,
            feature_scale=self.feature_scale,
            weights=self.weights,
            feature_names=np.asarray(self.feature_names),
            output_names=np.asarray(self.output_names),
            training_summary_json=json.dumps(dict(self.training_summary or {}), ensure_ascii=True),
        )
        return destination

    @classmethod
    def load(cls, path: str | Path) -> "LinearOutcomeModel":
        np = _numpy()
        source = Path(path)
        with np.load(source, allow_pickle=False) as data:
            summary_raw = str(data["training_summary_json"])
            try:
                summary = json.loads(summary_raw)
            except json.JSONDecodeError:
                summary = {}
            return cls(
                feature_mean=data["feature_mean"],
                feature_scale=data["feature_scale"],
                weights=data["weights"],
                feature_names=tuple(str(item) for item in data["feature_names"].tolist()),
                output_names=tuple(str(item) for item in data["output_names"].tolist()),
                training_summary=summary,
            )


def _rmse(predictions: Any, labels: Any) -> list[float]:
    np = _numpy()
    if len(labels) == 0:
        return []
    return [float(value) for value in np.sqrt(np.mean((predictions - labels) ** 2, axis=0))]


def fit_outcome_model(
    dataset: RolloutTrainingDataset,
    config: RolloutTrainingConfig | None = None,
) -> tuple[LinearOutcomeModel, Dict[str, Any]]:
    """Fit on the early portion and report a chronological holdout."""

    np = _numpy()
    cfg = config or RolloutTrainingConfig()
    count = dataset.sample_count
    split = max(1, min(count - 1, int(round(count * cfg.train_fraction)))) if count > 1 else count
    train_x = dataset.features[:split]
    train_y = dataset.labels[:split]
    feature_mean = train_x.mean(axis=0)
    feature_scale = train_x.std(axis=0)
    feature_scale = np.where(feature_scale < 1e-8, 1.0, feature_scale)
    normalized = (train_x - feature_mean) / feature_scale
    design = np.column_stack([np.ones(len(normalized)), normalized])
    regularizer = np.eye(design.shape[1], dtype=np.float64) * float(cfg.ridge_alpha)
    regularizer[0, 0] = 0.0
    lhs = design.T @ design + regularizer
    rhs = design.T @ train_y
    try:
        weights = np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        weights = np.linalg.lstsq(lhs, rhs, rcond=None)[0]

    model = LinearOutcomeModel(
        feature_mean=feature_mean,
        feature_scale=feature_scale,
        weights=weights,
        training_summary={"config": cfg.__dict__, "dataset": dataset.to_summary()},
    )
    train_predictions = model.predict_array(train_x)
    eval_predictions = model.predict_array(dataset.features[split:]) if split < count else np.empty((0, dataset.labels.shape[1]))
    metrics = {
        "sample_count": count,
        "train_count": split,
        "eval_count": count - split,
        "train_rmse": dict(zip(dataset.output_names, _rmse(train_predictions, train_y))),
        "eval_rmse": dict(zip(dataset.output_names, _rmse(eval_predictions, dataset.labels[split:]))),
        "feature_names": list(dataset.feature_names),
        "output_names": list(dataset.output_names),
    }
    return model, metrics
