"""Apply an offline-trained outcome model on top of the baseline rollout."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Iterable, List

from .models import CandidateAction, CandidateOutcome, TrackingState
from .primitives import BehaviorPrimitive
from .rollout import KinematicRolloutModel, RolloutConfig
from .training import FEATURE_NAMES, TARGET_MODES, LinearOutcomeModel, feature_vector


class LearnedOutcomeRolloutModel:
    """Use learned outcome estimates while retaining baseline safety fields."""

    def __init__(
        self,
        predictor: LinearOutcomeModel,
        baseline: KinematicRolloutModel | None = None,
        max_feature_zscore: float | None = None,
        max_state_feature_zscore: float | None = None,
        prediction_blend: float = 1.0,
    ) -> None:
        self.predictor = predictor
        summary = predictor.training_summary or {}
        dataset = summary.get("train_dataset", summary.get("dataset", {}))
        horizon = dataset.get("resolved_rollout_horizon_s")
        if horizon is None:
            raise ValueError("Model has no resolved training horizon; retrain before rollout inference")
        horizon = float(horizon)
        if not math.isfinite(horizon) or horizon <= 0:
            raise ValueError("Model training horizon must be finite and positive")
        if baseline is not None and not math.isclose(
            baseline.config.horizon_s, horizon, rel_tol=0.0, abs_tol=1e-6
        ):
            raise ValueError("Baseline horizon does not match model training horizon")
        self.baseline = baseline or KinematicRolloutModel(RolloutConfig(horizon_s=horizon))
        if predictor.target_mode not in TARGET_MODES:
            raise ValueError(f"Unknown predictor target_mode: {predictor.target_mode!r}")
        if max_feature_zscore is not None and max_feature_zscore <= 0.0:
            raise ValueError("max_feature_zscore must be positive when set")
        if max_state_feature_zscore is not None and max_state_feature_zscore <= 0.0:
            raise ValueError("max_state_feature_zscore must be positive when set")
        if not 0.0 <= prediction_blend <= 1.0:
            raise ValueError("prediction_blend must be in [0, 1]")
        self.max_feature_zscore = max_feature_zscore
        self.max_state_feature_zscore = max_state_feature_zscore
        self.prediction_blend = float(prediction_blend)

    def _feature_zscore(
        self,
        state: TrackingState,
        primitive: BehaviorPrimitive,
        state_only: bool = False,
    ) -> float:
        values = feature_vector(state, primitive.command_mps, primitive.yaw_rate_dps)
        mask = self.predictor.input_mask
        zscores = []
        for index, value in enumerate(values):
            if state_only and FEATURE_NAMES[index].startswith("command_"):
                continue
            if mask is not None and not bool(mask[index]):
                continue
            mean = float(self.predictor.feature_mean[index])
            scale = max(abs(float(self.predictor.feature_scale[index])), 1e-8)
            normalized = (float(value) - mean) / scale
            zscores.append(abs(normalized) if math.isfinite(normalized) else math.inf)
        return max(zscores, default=0.0)

    def simulate(self, primitive: BehaviorPrimitive, state: TrackingState) -> CandidateAction:
        baseline_action = self.baseline.simulate(primitive, state)
        feature_zscore = self._feature_zscore(state, primitive)
        state_feature_zscore = self._feature_zscore(state, primitive, state_only=True)
        ood_reason = None
        if (
            self.max_state_feature_zscore is not None
            and state_feature_zscore > self.max_state_feature_zscore
        ):
            ood_reason = "state_feature_zscore"
        elif (
            self.max_feature_zscore is not None
            and feature_zscore > self.max_feature_zscore
        ):
            ood_reason = "feature_zscore"
        if (
            ood_reason is not None
        ):
            return replace(
                baseline_action,
                metadata={
                    **dict(baseline_action.metadata),
                    "model": "kinematic-rollout-ood-fallback",
                    "learned_model": "linear-outcome-calibrator-v0",
                    "learned_model_ood": True,
                    "learned_model_ood_reason": ood_reason,
                    "max_feature_zscore": feature_zscore,
                    "state_feature_zscore": state_feature_zscore,
                    "prediction_blend": 0.0,
                    "prediction_target_mode": self.predictor.target_mode,
                },
            )
        baseline_outcome = baseline_action.outcome
        prediction = self.predictor.predict_state(
            state,
            primitive.command_mps,
            primitive.yaw_rate_dps,
        )
        if self.predictor.target_mode == "baseline_residual":
            prediction = {
                name: float(getattr(baseline_outcome, name)) + float(value)
                for name, value in prediction.items()
            }
        blend = self.prediction_blend
        blended = {
            name: (1.0 - blend) * float(getattr(baseline_outcome, name)) + blend * float(value)
            for name, value in prediction.items()
        }
        outcome = CandidateOutcome(
            visibility_probability=max(0.0, min(1.0, blended["visibility_probability"])),
            image_center_error=max(0.0, min(1.0, blended["image_center_error"])),
            range_error_m=max(0.0, blended["range_error_m"]),
            covariance_trace=max(0.05, blended["covariance_trace"]),
            collision_probability=baseline_outcome.collision_probability,
            minimum_obstacle_margin_m=baseline_outcome.minimum_obstacle_margin_m,
            energy_cost_wh=baseline_outcome.energy_cost_wh,
            information_gain=baseline_outcome.information_gain,
            feasible=baseline_outcome.feasible,
        )
        return replace(
            baseline_action,
            outcome=outcome,
            metadata={
                **dict(baseline_action.metadata),
                "model": "linear-outcome-calibrator-v0",
                "baseline_model": baseline_action.metadata.get("model"),
                "learned_model_ood": False,
                "learned_model_ood_reason": None,
                "max_feature_zscore": feature_zscore,
                "state_feature_zscore": state_feature_zscore,
                "prediction_blend": blend,
                "prediction_target_mode": self.predictor.target_mode,
            },
        )

    def generate(
        self,
        primitives: Iterable[BehaviorPrimitive],
        state: TrackingState,
    ) -> List[CandidateAction]:
        return [self.simulate(primitive, state) for primitive in primitives]
