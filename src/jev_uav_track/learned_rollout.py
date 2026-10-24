"""Apply an offline-trained outcome model on top of the baseline rollout."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Iterable, List

from .models import CandidateAction, CandidateOutcome, TrackingState
from .primitives import BehaviorPrimitive
from .rollout import KinematicRolloutModel
from .training import LinearOutcomeModel, feature_vector


class LearnedOutcomeRolloutModel:
    """Use learned outcome estimates while retaining baseline safety fields."""

    def __init__(
        self,
        predictor: LinearOutcomeModel,
        baseline: KinematicRolloutModel | None = None,
        max_feature_zscore: float | None = None,
    ) -> None:
        self.predictor = predictor
        self.baseline = baseline or KinematicRolloutModel()
        if max_feature_zscore is not None and max_feature_zscore <= 0.0:
            raise ValueError("max_feature_zscore must be positive when set")
        self.max_feature_zscore = max_feature_zscore

    def _feature_zscore(self, state: TrackingState, primitive: BehaviorPrimitive) -> float:
        values = feature_vector(state, primitive.command_mps, primitive.yaw_rate_dps)
        mask = self.predictor.input_mask
        zscores = []
        for index, value in enumerate(values):
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
        if (
            self.max_feature_zscore is not None
            and feature_zscore > self.max_feature_zscore
        ):
            return replace(
                baseline_action,
                metadata={
                    **dict(baseline_action.metadata),
                    "model": "kinematic-rollout-ood-fallback",
                    "learned_model": "linear-outcome-calibrator-v0",
                    "learned_model_ood": True,
                    "max_feature_zscore": feature_zscore,
                },
            )
        prediction = self.predictor.predict_state(
            state,
            primitive.command_mps,
            primitive.yaw_rate_dps,
        )
        baseline_outcome = baseline_action.outcome
        outcome = CandidateOutcome(
            visibility_probability=max(0.0, min(1.0, prediction["visibility_probability"])),
            image_center_error=max(0.0, min(1.0, prediction["image_center_error"])),
            range_error_m=max(0.0, prediction["range_error_m"]),
            covariance_trace=max(0.05, prediction["covariance_trace"]),
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
                "max_feature_zscore": feature_zscore,
            },
        )

    def generate(
        self,
        primitives: Iterable[BehaviorPrimitive],
        state: TrackingState,
    ) -> List[CandidateAction]:
        return [self.simulate(primitive, state) for primitive in primitives]
