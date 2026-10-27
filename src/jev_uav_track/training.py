"""Small, dependency-light rollout outcome calibration utilities.

This is an offline baseline, not a policy-gradient trainer. It fits a ridge
regressor to observed state transitions so the candidate rollout can be
calibrated before a learned dynamics/world model is introduced.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from .data.hdf5_adapter import HDF5Episode
from .models import MissionConstraints, TrackingState
from .primitives import BehaviorPrimitive
from .rollout import KinematicRolloutModel, RolloutConfig


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

TARGET_MODES = ("absolute", "baseline_residual")


ABLATION_DISABLED_FEATURES = {
    "full": (),
    "no_motion": (
        "target_vx_mps",
        "target_vy_mps",
        "target_vz_mps",
        "uav_vx_mps",
        "uav_vy_mps",
        "uav_vz_mps",
        "uav_speed_mps",
    ),
    "no_action": (
        "command_vx_mps",
        "command_vy_mps",
        "command_vz_mps",
        "command_yaw_rate_dps",
    ),
    "no_visibility": (
        "visibility_probability",
        "occlusion_risk",
        "image_center_error",
    ),
}


def feature_mask_for_ablation(name: str) -> tuple[bool, ...]:
    """Return the input-feature mask for a named ablation."""

    if name not in ABLATION_DISABLED_FEATURES:
        raise ValueError(
            f"Unknown ablation {name!r}; choose from {sorted(ABLATION_DISABLED_FEATURES)}"
        )
    disabled = set(ABLATION_DISABLED_FEATURES[name])
    return tuple(feature_name not in disabled for feature_name in FEATURE_NAMES)


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
    target_mode: str = "absolute"
    # None derives the exact future span from each read-only episode.  An
    # explicit value is useful for a simulator contract and is checked.
    rollout_horizon_s: float | None = None
    timing_tolerance_s: float = 0.05


@dataclass
class RolloutTrainingDataset:
    features: Any
    labels: Any
    feature_names: tuple[str, ...] = FEATURE_NAMES
    output_names: tuple[str, ...] = OUTPUT_NAMES
    episode_paths: tuple[str, ...] = ()
    target_mode: str = "absolute"
    resolved_rollout_horizon_s: float | None = None

    def __post_init__(self) -> None:
        if self.target_mode not in TARGET_MODES:
            raise ValueError(
                f"Unknown dataset target_mode {self.target_mode!r}; choose from {TARGET_MODES}"
            )

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
            "episode_paths": list(self.episode_paths),
            "target_mode": self.target_mode,
            "resolved_rollout_horizon_s": self.resolved_rollout_horizon_s,
        }


def build_training_dataset(
    episode: HDF5Episode,
    config: RolloutTrainingConfig | None = None,
) -> RolloutTrainingDataset:
    """Create chronological state/action -> future-outcome samples from PASS data.

    The observed camera velocity is used as the action feature. This is a
    calibration baseline: it learns the relationship present in the recorded
    behavior and should not be mistaken for counterfactual dynamics coverage.
    """

    _require_verified_episode(episode)
    np = _numpy()
    cfg = config or RolloutTrainingConfig()
    if cfg.target_mode not in TARGET_MODES:
        raise ValueError(
            f"Unknown target_mode {cfg.target_mode!r}; choose from {TARGET_MODES}"
        )
    if cfg.horizon_frames <= 0 or cfg.stride <= 0 or cfg.max_samples <= 0:
        raise ValueError("horizon_frames, stride, and max_samples must be positive")
    if not 0.0 < cfg.train_fraction < 1.0:
        raise ValueError("train_fraction must be in (0, 1)")
    effective_horizon_s = _validate_horizon_alignment(episode, cfg)

    last_start = episode.frame_count - cfg.horizon_frames - 1
    indices = list(range(0, max(0, last_start + 1), cfg.stride))
    if len(indices) > cfg.max_samples:
        selected = np.linspace(0, len(indices) - 1, cfg.max_samples).round().astype(int)
        indices = [indices[int(position)] for position in selected]

    features = []
    labels = []
    baseline_rollout = KinematicRolloutModel(
        config=RolloutConfig(horizon_s=effective_horizon_s)
    )
    constraints = MissionConstraints(
        desired_range_m=cfg.desired_range_m,
        minimum_obstacle_margin_m=0.0,
    )
    for index in indices:
        future_index = index + cfg.horizon_frames
        state = episode.tracking_state(index, constraints=constraints)
        future = episode.tracking_state(future_index, constraints=constraints)
        features.append(feature_vector(state, episode.camera_velocities[index]))
        future_outcome = (
            future.target.visibility_probability,
            future.target.image_center_error,
            abs(future.target.range_m - cfg.desired_range_m),
            future.target.covariance_trace,
        )
        if cfg.target_mode == "baseline_residual":
            observed_primitive = BehaviorPrimitive(
                id="observed_camera_motion",
                name="Observed camera motion",
                description="Recorded camera velocity used for calibration",
                command_mps=tuple(float(value) for value in episode.camera_velocities[index]),
            )
            baseline = baseline_rollout.simulate(observed_primitive, state).outcome
            baseline_outcome = (
                baseline.visibility_probability,
                baseline.image_center_error,
                baseline.range_error_m,
                baseline.covariance_trace,
            )
            labels.append(
                tuple(
                    float(target) - float(reference)
                    for target, reference in zip(future_outcome, baseline_outcome)
                )
            )
        else:
            labels.append(future_outcome)

    if not features:
        raise ValueError("Episode does not contain enough frames for the requested horizon")
    return RolloutTrainingDataset(
        features=np.asarray(features, dtype=np.float64),
        labels=np.asarray(labels, dtype=np.float64),
        episode_paths=(str(episode.path),),
        target_mode=cfg.target_mode,
        resolved_rollout_horizon_s=effective_horizon_s,
    )


def _validate_horizon_alignment(
    episode: HDF5Episode,
    config: RolloutTrainingConfig,
) -> float:
    """Return the episode's future span and check the configured contract.

    ``horizon_frames`` is a frame count, not a duration.  Using the timestamp
    span prevents a 5 Hz recording from silently being trained against a 1.5 s
    rollout.  The source episode remains read-only.
    """

    if config.timing_tolerance_s <= 0.0:
        raise ValueError("timing_tolerance_s must be positive")
    last_start = episode.frame_count - config.horizon_frames - 1
    if last_start < 0:
        raise ValueError("Episode does not contain enough frames for the requested horizon")
    spans = []
    for index in range(0, last_start + 1, max(1, config.stride)):
        span = float(episode.image_timestamps[index + config.horizon_frames]) - float(
            episode.image_timestamps[index]
        )
        if math.isfinite(span) and span > 0.0:
            spans.append(span)
    if not spans:
        raise ValueError("Episode timestamps do not define a positive future horizon")
    effective = float(statistics.median(spans))
    if config.rollout_horizon_s is not None:
        if config.rollout_horizon_s <= 0.0:
            raise ValueError("rollout_horizon_s must be positive when set")
        if abs(float(config.rollout_horizon_s) - effective) > config.timing_tolerance_s:
            raise ValueError(
                "Rollout horizon does not match episode timestamps: "
                f"configured={config.rollout_horizon_s:.6f}s, observed={effective:.6f}s, "
                f"tolerance={config.timing_tolerance_s:.6f}s"
            )
    return effective


def _require_verified_episode(episode: HDF5Episode) -> None:
    status = str(episode.metadata.get("validation_status", "")).upper()
    if status != "PASS":
        raise ValueError(
            "Episode training requires validation_status=PASS; "
            f"{episode.path} has {status or 'unreported'}"
        )


def build_training_dataset_from_episodes(
    episodes: Sequence[HDF5Episode],
    config: RolloutTrainingConfig | None = None,
    max_samples_per_episode: int | None = None,
) -> RolloutTrainingDataset:
    """Concatenate state/action samples from multiple verified episodes.

    ``max_samples_per_episode`` keeps a long episode or a large scene family
    from dominating the cross-episode fit. Samples remain chronological within
    each episode; no frames are mixed across episode boundaries.
    """

    np = _numpy()
    episode_list = list(episodes)
    if not episode_list:
        raise ValueError("At least one episode is required")
    for episode in episode_list:
        _require_verified_episode(episode)
    paths = tuple(str(episode.path) for episode in episode_list)
    if len(set(paths)) != len(paths):
        raise ValueError("Training episodes must have unique paths")

    cfg = config or RolloutTrainingConfig()
    if max_samples_per_episode is not None:
        if max_samples_per_episode <= 0:
            raise ValueError("max_samples_per_episode must be positive")
        cfg = replace(cfg, max_samples=max_samples_per_episode)
    datasets = [build_training_dataset(episode, cfg) for episode in episode_list]
    return RolloutTrainingDataset(
        features=np.concatenate([dataset.features for dataset in datasets], axis=0),
        labels=np.concatenate([dataset.labels for dataset in datasets], axis=0),
        feature_names=datasets[0].feature_names,
        output_names=datasets[0].output_names,
        episode_paths=paths,
        target_mode=datasets[0].target_mode,
        resolved_rollout_horizon_s=datasets[0].resolved_rollout_horizon_s,
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
    input_mask: Any | None = None
    target_mode: str = "absolute"

    def _design(self, features: Any) -> Any:
        np = _numpy()
        values = np.asarray(features, dtype=np.float64)
        if values.ndim == 1:
            values = values.reshape(1, -1)
        if values.shape[1] != len(self.feature_mean):
            raise ValueError(
                f"Expected {len(self.feature_mean)} features, got {values.shape[1]}"
            )
        if self.input_mask is not None:
            mask = np.asarray(self.input_mask, dtype=bool).reshape(-1)
            if len(mask) != values.shape[1]:
                raise ValueError(
                    f"Feature mask length {len(mask)} does not match {values.shape[1]} features"
                )
            values = values.copy()
            values[:, ~mask] = 0.0
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
            input_mask=np.asarray(
                self.input_mask
                if self.input_mask is not None
                else np.ones(len(self.feature_names), dtype=bool)
            ),
            training_summary_json=json.dumps(dict(self.training_summary or {}), ensure_ascii=True),
            target_mode=np.asarray(self.target_mode),
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
                input_mask=data["input_mask"] if "input_mask" in data.files else None,
                target_mode=(
                    str(data["target_mode"].item())
                    if "target_mode" in data.files
                    else "absolute"
                ),
            )


def _rmse(predictions: Any, labels: Any) -> list[float]:
    np = _numpy()
    if len(labels) == 0:
        return []
    return [float(value) for value in np.sqrt(np.mean((predictions - labels) ** 2, axis=0))]


def _normalise_feature_mask(
    feature_mask: Sequence[bool] | None,
    feature_count: int,
) -> tuple[bool, ...] | None:
    if feature_mask is None:
        return None
    mask = tuple(bool(value) for value in feature_mask)
    if len(mask) != feature_count:
        raise ValueError(
            f"Feature mask length {len(mask)} does not match {feature_count} features"
        )
    if not any(mask):
        raise ValueError("At least one input feature must remain enabled")
    return mask


def _masked_dataset(
    dataset: RolloutTrainingDataset,
    feature_mask: tuple[bool, ...] | None,
) -> RolloutTrainingDataset:
    if feature_mask is None:
        return dataset
    np = _numpy()
    values = np.asarray(dataset.features, dtype=np.float64).copy()
    values[:, [not enabled for enabled in feature_mask]] = 0.0
    return RolloutTrainingDataset(
        features=values,
        labels=dataset.labels,
        feature_names=dataset.feature_names,
        output_names=dataset.output_names,
        episode_paths=dataset.episode_paths,
        target_mode=dataset.target_mode,
        resolved_rollout_horizon_s=dataset.resolved_rollout_horizon_s,
    )


def _fit_ridge_model(
    features: Any,
    labels: Any,
    config: RolloutTrainingConfig,
    training_summary: Mapping[str, Any],
    feature_mask: Sequence[bool] | None = None,
) -> LinearOutcomeModel:
    np = _numpy()
    values = np.asarray(features, dtype=np.float64)
    targets = np.asarray(labels, dtype=np.float64)
    if values.ndim != 2 or targets.ndim != 2 or len(values) == 0:
        raise ValueError("Training features and labels must be non-empty 2D arrays")
    mask = _normalise_feature_mask(feature_mask, values.shape[1])
    if mask is not None:
        values = values.copy()
        values[:, [not enabled for enabled in mask]] = 0.0
    feature_mean = values.mean(axis=0)
    feature_scale = values.std(axis=0)
    feature_scale = np.where(feature_scale < 1e-8, 1.0, feature_scale)
    normalized = (values - feature_mean) / feature_scale
    design = np.column_stack([np.ones(len(normalized)), normalized])
    regularizer = np.eye(design.shape[1], dtype=np.float64) * float(config.ridge_alpha)
    regularizer[0, 0] = 0.0
    lhs = design.T @ design + regularizer
    rhs = design.T @ targets
    try:
        weights = np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        weights = np.linalg.lstsq(lhs, rhs, rcond=None)[0]
    summary = dict(training_summary)
    if mask is not None:
        summary["feature_mask"] = list(mask)
    return LinearOutcomeModel(
        feature_mean=feature_mean,
        feature_scale=feature_scale,
        weights=weights,
        training_summary=summary,
        input_mask=mask,
        target_mode=config.target_mode,
    )


def _dataset_metrics(model: LinearOutcomeModel, dataset: RolloutTrainingDataset) -> Dict[str, Any]:
    predictions = model.predict_array(dataset.features)
    return {
        "sample_count": dataset.sample_count,
        "rmse": dict(zip(dataset.output_names, _rmse(predictions, dataset.labels))),
    }


def fit_outcome_model(
    dataset: RolloutTrainingDataset,
    config: RolloutTrainingConfig | None = None,
    feature_mask: Sequence[bool] | None = None,
) -> tuple[LinearOutcomeModel, Dict[str, Any]]:
    """Fit on the early portion and report a chronological holdout."""

    np = _numpy()
    cfg = config or RolloutTrainingConfig(target_mode=dataset.target_mode)
    if cfg.target_mode != dataset.target_mode:
        raise ValueError(
            "Training config target_mode does not match dataset: "
            f"config={cfg.target_mode!r}, dataset={dataset.target_mode!r}"
        )
    mask = _normalise_feature_mask(feature_mask, dataset.features.shape[1])
    fit_dataset = _masked_dataset(dataset, mask)
    count = dataset.sample_count
    split = max(1, min(count - 1, int(round(count * cfg.train_fraction)))) if count > 1 else count
    train_x = fit_dataset.features[:split]
    train_y = fit_dataset.labels[:split]
    model = _fit_ridge_model(
        train_x,
        train_y,
        cfg,
        {
            "config": cfg.__dict__,
            "dataset": dataset.to_summary(),
            "split": "chronological_within_episode",
        },
        feature_mask=mask,
    )
    train_predictions = model.predict_array(dataset.features[:split])
    eval_predictions = model.predict_array(dataset.features[split:]) if split < count else np.empty((0, dataset.labels.shape[1]))
    metrics = {
        "sample_count": count,
        "target_mode": cfg.target_mode,
        "resolved_rollout_horizon_s": dataset.resolved_rollout_horizon_s,
        "train_count": split,
        "eval_count": count - split,
        "train_rmse": dict(zip(dataset.output_names, _rmse(train_predictions, train_y))),
        "eval_rmse": dict(zip(dataset.output_names, _rmse(eval_predictions, dataset.labels[split:]))),
        "feature_names": list(dataset.feature_names),
        "output_names": list(dataset.output_names),
    }
    return model, metrics


def fit_outcome_model_cross_episode(
    train_episodes: Sequence[HDF5Episode],
    validation_episodes: Sequence[HDF5Episode],
    config: RolloutTrainingConfig | None = None,
    max_samples_per_episode: int | None = None,
    feature_mask: Sequence[bool] | None = None,
) -> tuple[LinearOutcomeModel, Dict[str, Any]]:
    """Fit on complete episodes and evaluate on disjoint complete episodes."""

    train_list = list(train_episodes)
    validation_list = list(validation_episodes)
    if not train_list or not validation_list:
        raise ValueError("Both train_episodes and validation_episodes are required")
    for episode in train_list + validation_list:
        _require_verified_episode(episode)
    train_paths = {str(episode.path) for episode in train_list}
    validation_paths = {str(episode.path) for episode in validation_list}
    overlap = sorted(train_paths & validation_paths)
    if overlap:
        raise ValueError(f"Train/validation episode overlap: {overlap}")

    np = _numpy()
    cfg = config or RolloutTrainingConfig()
    horizons = [
        _validate_horizon_alignment(episode, cfg)
        for episode in train_list + validation_list
    ]
    if max(horizons) - min(horizons) > cfg.timing_tolerance_s:
        raise ValueError(
            "Train/validation episodes use inconsistent future horizons: "
            f"min={min(horizons):.6f}s, max={max(horizons):.6f}s, "
            f"tolerance={cfg.timing_tolerance_s:.6f}s"
        )
    train_dataset = build_training_dataset_from_episodes(
        train_list,
        cfg,
        max_samples_per_episode=max_samples_per_episode,
    )
    validation_datasets = [
        build_training_dataset_from_episodes(
            [episode],
            cfg,
            max_samples_per_episode=max_samples_per_episode,
        )
        for episode in validation_list
    ]
    validation_dataset = RolloutTrainingDataset(
        features=np.concatenate([dataset.features for dataset in validation_datasets], axis=0),
        labels=np.concatenate([dataset.labels for dataset in validation_datasets], axis=0),
        feature_names=train_dataset.feature_names,
        output_names=train_dataset.output_names,
        episode_paths=tuple(str(episode.path) for episode in validation_list),
        target_mode=train_dataset.target_mode,
        resolved_rollout_horizon_s=train_dataset.resolved_rollout_horizon_s,
    )
    mask = _normalise_feature_mask(feature_mask, train_dataset.features.shape[1])
    model = _fit_ridge_model(
        train_dataset.features,
        train_dataset.labels,
        cfg,
        {
            "config": cfg.__dict__,
            "split": "cross_episode",
            "train_episodes": list(train_paths),
            "validation_episodes": list(validation_paths),
            "max_samples_per_episode": max_samples_per_episode,
            "train_dataset": train_dataset.to_summary(),
            "validation_dataset": validation_dataset.to_summary(),
        },
        feature_mask=mask,
    )
    train_metrics = _dataset_metrics(model, train_dataset)
    validation_metrics = _dataset_metrics(model, validation_dataset)
    validation_by_episode = []
    for episode, dataset in zip(validation_list, validation_datasets):
        metrics_for_episode = _dataset_metrics(model, dataset)
        validation_by_episode.append(
            {"episode": str(episode.path), **metrics_for_episode}
        )
    metrics = {
        "split": "cross_episode",
        "target_mode": cfg.target_mode,
        "resolved_rollout_horizon_s": train_dataset.resolved_rollout_horizon_s,
        "train_episode_count": len(train_list),
        "validation_episode_count": len(validation_list),
        "train_episodes": [str(episode.path) for episode in train_list],
        "validation_episodes": [str(episode.path) for episode in validation_list],
        "sample_count": train_dataset.sample_count + validation_dataset.sample_count,
        "train_count": train_dataset.sample_count,
        "validation_count": validation_dataset.sample_count,
        "train_rmse": train_metrics["rmse"],
        "validation_rmse": validation_metrics["rmse"],
        "validation_by_episode": validation_by_episode,
        "feature_names": list(train_dataset.feature_names),
        "output_names": list(train_dataset.output_names),
        "max_samples_per_episode": max_samples_per_episode,
        "feature_mask": list(mask) if mask is not None else None,
    }
    return model, metrics
