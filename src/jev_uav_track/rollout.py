"""A transparent kinematic rollout used for development and tests.

This is intentionally not a flight dynamics simulator. Replace this module
with AirSim, Isaac Sim, Gazebo, ROS 2 NMPC, or a validated vehicle model before
hardware deployment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Sequence

from .models import CandidateAction, CandidateOutcome, TrackingState, TrajectoryPoint
from .primitives import BehaviorPrimitive


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(v) ** 2 for v in vector))


@dataclass(frozen=True)
class RolloutConfig:
    horizon_s: float = 1.5
    dt_s: float = 0.25
    obstacle_margin_decay: float = 0.08
    base_collision_probability: float = 0.01
    history_acceleration_covariance_weight: float = 0.015
    history_identity_covariance_weight: float = 0.20
    identity_information_gain_weight: float = 0.30


class KinematicRolloutModel:
    """Predict candidate consequences with a simple relative-motion model."""

    def __init__(self, config: RolloutConfig | None = None) -> None:
        self.config = config or RolloutConfig()
        if self.config.horizon_s <= 0 or self.config.dt_s <= 0:
            raise ValueError("Rollout horizon and dt must be positive")
        if any(
            value < 0.0
            for value in (
                self.config.history_acceleration_covariance_weight,
                self.config.history_identity_covariance_weight,
                self.config.identity_information_gain_weight,
            )
        ):
            raise ValueError("history rollout weights cannot be negative")

    @staticmethod
    def _history_value(history: Mapping[str, Any] | None, key: str, default: float) -> float:
        if not history:
            return default
        try:
            value = float(history.get(key, default))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    def simulate(self, primitive: BehaviorPrimitive, state: TrackingState) -> CandidateAction:
        cfg = self.config
        steps = max(1, int(round(cfg.horizon_s / cfg.dt_s)))
        vx, vy, vz = primitive.command_mps
        speed = _norm(primitive.command_mps)
        target = state.target
        uav = state.uav
        history = state.environment.get("belief_history")
        history = history if isinstance(history, Mapping) else None
        history_visibility = _clamp(
            self._history_value(history, "mean_visibility", target.visibility_probability),
            0.0,
            1.0,
        )
        history_occlusion = _clamp(
            self._history_value(history, "occlusion_fraction", 0.0),
            0.0,
            1.0,
        )
        history_identity_confidence = _clamp(
            self._history_value(
                history,
                "mean_identity_confidence",
                target.identity_confidence,
            ),
            0.0,
            1.0,
        )
        history_acceleration = max(
            0.0,
            self._history_value(history, "target_acceleration_mps2", 0.0),
        )
        identity_need = 1.0 - min(target.identity_confidence, history_identity_confidence)
        trajectory: List[TrajectoryPoint] = []

        start_uav = uav.position_m
        end_uav = tuple(start_uav[i] + primitive.command_mps[i] * cfg.horizon_s for i in range(3))
        end_target = tuple(target.position_m[i] + target.velocity_mps[i] * cfg.horizon_s for i in range(3))
        relative = tuple(end_target[i] - end_uav[i] for i in range(3))
        predicted_range = _norm(relative)
        range_error = abs(predicted_range - state.constraints.desired_range_m)

        # A simple visual-center proxy: lateral motion and yaw improve bearing
        # error; forward motion can make a badly centered target worse.
        center_error = _clamp(
            abs(target.image_center_error)
            + 0.04 * abs(vx)
            - 0.06 * abs(vy)
            - 0.015 * abs(primitive.yaw_rate_dps),
            0.0,
            1.0,
        )
        visibility_is_low = (
            primitive.preferred_when_visibility_below > 0.0
            and (
                target.visibility_probability < primitive.preferred_when_visibility_below
                or history_visibility < primitive.preferred_when_visibility_below
                or history_occlusion >= 0.25
            )
        )
        occlusion_relief = (
            0.18
            if primitive.id in {"orbit_left", "orbit_right", "climb"}
            and (target.occlusion_risk >= 0.25 or visibility_is_low)
            else 0.0
        )
        scan_relief = 0.25 if primitive.id == "scan_reacquire" and visibility_is_low else 0.0
        scan_penalty = 0.08 if primitive.id == "scan_reacquire" and not visibility_is_low else 0.0
        visibility = _clamp(
            target.visibility_probability
            + occlusion_relief
            + scan_relief
            - scan_penalty
            - 0.02 * range_error
            - 0.01 * abs(vz),
            0.0,
            1.0,
        )
        covariance = max(
            0.05,
            target.covariance_trace
            * (1.0 - 0.18 * visibility)
            + 0.04 * range_error
            + cfg.history_acceleration_covariance_weight * min(history_acceleration, 10.0)
            + cfg.history_identity_covariance_weight * identity_need
            - (0.45 if primitive.id in {"orbit_left", "orbit_right", "scan_reacquire"} else 0.0),
        )
        margin = max(
            0.0,
            uav.minimum_obstacle_margin_m - cfg.obstacle_margin_decay * speed * cfg.horizon_s,
        )
        collision_probability = _clamp(
            cfg.base_collision_probability
            + max(0.0, state.constraints.minimum_obstacle_margin_m - margin) * 0.04
            + speed * 0.003,
            0.0,
            1.0,
        )
        energy = 0.015 * speed * speed * cfg.horizon_s + 0.004 * abs(primitive.yaw_rate_dps) * cfg.horizon_s
        information_gain = _clamp(
            0.4 * (visibility - target.visibility_probability)
            + 0.25 * max(0.0, target.covariance_trace - covariance),
            0.0,
            1.0,
        )
        if primitive.id == "scan_reacquire" and visibility_is_low:
            information_gain = _clamp(information_gain + 0.15, 0.0, 1.0)
        if identity_need > 0.0 and primitive.id in {
            "scan_reacquire",
            "orbit_left",
            "orbit_right",
            "climb",
        }:
            information_gain = _clamp(
                information_gain + cfg.identity_information_gain_weight * identity_need,
                0.0,
                1.0,
            )

        for step in range(steps + 1):
            time_s = min(cfg.horizon_s, step * cfg.dt_s)
            position = tuple(start_uav[i] + primitive.command_mps[i] * time_s for i in range(3))
            trajectory.append(
                TrajectoryPoint(
                    time_s=time_s,
                    position_m=position,
                    velocity_mps=primitive.command_mps,
                )
            )

        outcome = CandidateOutcome(
            visibility_probability=visibility,
            image_center_error=center_error,
            range_error_m=range_error,
            covariance_trace=covariance,
            collision_probability=collision_probability,
            minimum_obstacle_margin_m=margin,
            energy_cost_wh=energy,
            information_gain=information_gain,
            feasible=speed <= state.constraints.maximum_speed_mps
            and cfg.horizon_s <= state.constraints.maximum_action_duration_s,
        )
        return CandidateAction(
            id=primitive.id,
            name=primitive.name,
            description=primitive.description,
            duration_s=cfg.horizon_s,
            command_mps=primitive.command_mps,
            yaw_rate_dps=primitive.yaw_rate_dps,
            outcome=outcome,
            trajectory=tuple(trajectory),
            metadata={"model": "kinematic-rollout-v0", "target_prediction": "constant-velocity"},
        )

    def generate(self, primitives: Iterable[BehaviorPrimitive], state: TrackingState) -> List[CandidateAction]:
        return [self.simulate(primitive, state) for primitive in primitives]
