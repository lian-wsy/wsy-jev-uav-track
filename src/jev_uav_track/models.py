"""Typed state and decision objects used by the Jev-Track prototype."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]


def _vec3(value: Sequence[float]) -> Vec3:
    if len(value) != 3:
        raise ValueError("A 3D vector must contain exactly three values")
    return (float(value[0]), float(value[1]), float(value[2]))


@dataclass(frozen=True)
class TargetBelief:
    """A compact belief state produced by the local perception stack."""

    position_m: Vec3 = (0.0, 0.0, 0.0)
    velocity_mps: Vec3 = (0.0, 0.0, 0.0)
    range_m: float = 20.0
    bearing_deg: float = 0.0
    image_center_error: float = 0.0
    covariance_trace: float = 1.0
    visibility_probability: float = 1.0
    occlusion_risk: float = 0.0
    identity_confidence: float = 1.0
    timestamp_s: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_m", _vec3(self.position_m))
        object.__setattr__(self, "velocity_mps", _vec3(self.velocity_mps))
        for name in (
            "range_m",
            "image_center_error",
            "covariance_trace",
            "visibility_probability",
            "occlusion_risk",
            "identity_confidence",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        for name in ("image_center_error", "visibility_probability", "occlusion_risk", "identity_confidence"):
            if getattr(self, name) > 1.0:
                raise ValueError(f"{name} must be in [0, 1]")


@dataclass(frozen=True)
class UAVState:
    """The part of the vehicle state needed by the high-level selector."""

    position_m: Vec3 = (0.0, 0.0, 0.0)
    velocity_mps: Vec3 = (0.0, 0.0, 0.0)
    yaw_deg: float = 0.0
    battery_percent: float = 100.0
    minimum_obstacle_margin_m: float = 10.0
    gps_health: float = 1.0
    communication_health: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_m", _vec3(self.position_m))
        object.__setattr__(self, "velocity_mps", _vec3(self.velocity_mps))
        if not 0.0 <= self.battery_percent <= 100.0:
            raise ValueError("battery_percent is outside its valid range")
        if not 0.0 <= self.gps_health <= 1.0:
            raise ValueError("gps_health is outside its valid range")
        if not 0.0 <= self.communication_health <= 1.0:
            raise ValueError("communication_health is outside its valid range")
        if self.minimum_obstacle_margin_m < 0:
            raise ValueError("minimum_obstacle_margin_m cannot be negative")


@dataclass(frozen=True)
class MissionConstraints:
    """Hard constraints enforced before any Jev choice is accepted."""

    desired_range_m: float = 20.0
    reserve_battery_percent: float = 20.0
    minimum_obstacle_margin_m: float = 3.0
    maximum_collision_probability: float = 0.05
    maximum_speed_mps: float = 8.0
    maximum_action_duration_s: float = 2.0


@dataclass(frozen=True)
class TrackingState:
    """State passed to rollout and serialized for the decision model."""

    target: TargetBelief
    uav: UAVState
    constraints: MissionConstraints = field(default_factory=MissionConstraints)
    mission_goal: str = "Maintain target identity and visual visibility"
    obstacle_context: str = "No obstacle map supplied"
    history_summary: str = ""
    environment: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["mission_goal"] = self.mission_goal
        payload["obstacle_context"] = self.obstacle_context
        payload["history_summary"] = self.history_summary
        payload["environment"] = dict(self.environment)
        return payload


@dataclass(frozen=True)
class TrajectoryPoint:
    time_s: float
    position_m: Vec3
    velocity_mps: Vec3


@dataclass(frozen=True)
class CandidateOutcome:
    """Predicted consequence of a behavior, computed outside Jev."""

    visibility_probability: float
    image_center_error: float
    range_error_m: float
    covariance_trace: float
    collision_probability: float
    minimum_obstacle_margin_m: float
    energy_cost_wh: float
    information_gain: float
    feasible: bool = True

    def utility(self, constraints: MissionConstraints) -> float:
        """A transparent baseline utility used for fallback and mock Jev."""

        return (
            3.0 * self.visibility_probability
            - 1.8 * self.image_center_error
            - 0.08 * self.range_error_m
            - 0.12 * self.covariance_trace
            + 0.8 * self.information_gain
            - 0.5 * self.energy_cost_wh
            - 8.0 * self.collision_probability
            + (0.5 if self.minimum_obstacle_margin_m >= constraints.minimum_obstacle_margin_m else -2.0)
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateAction:
    id: str
    name: str
    description: str
    duration_s: float
    command_mps: Vec3
    yaw_rate_dps: float
    outcome: CandidateOutcome
    trajectory: Tuple[TrajectoryPoint, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def option_description(self) -> str:
        """Human-readable consequence description supplied to Jev."""

        o = self.outcome
        return (
            f"{self.name}: {self.description}. "
            f"After {self.duration_s:.2f}s, predicted visibility="
            f"{o.visibility_probability:.2f}, image-center-error={o.image_center_error:.2f}, "
            f"range-error={o.range_error_m:.2f}m, covariance-trace={o.covariance_trace:.2f}, "
            f"collision-probability={o.collision_probability:.3f}, "
            f"obstacle-margin={o.minimum_obstacle_margin_m:.2f}m, "
            f"energy={o.energy_cost_wh:.3f}Wh, information-gain={o.information_gain:.2f}."
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "duration_s": self.duration_s,
            "command_mps": list(self.command_mps),
            "yaw_rate_dps": self.yaw_rate_dps,
            "outcome": self.outcome.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class JevDecision:
    choice: str
    probabilities: Mapping[str, float]
    confidence: Optional[float] = None
    model_version: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def top_probability(self) -> float:
        return max(self.probabilities.values(), default=0.0)

    @property
    def margin(self) -> float:
        values = sorted(self.probabilities.values(), reverse=True)
        return values[0] - values[1] if len(values) > 1 else values[0]


@dataclass(frozen=True)
class SelectionResult:
    action: CandidateAction
    decision: JevDecision
    used_fallback: bool
    reason: str
    rejected_action_ids: Tuple[str, ...] = ()

