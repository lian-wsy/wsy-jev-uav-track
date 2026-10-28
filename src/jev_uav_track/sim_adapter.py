"""Small, simulator-neutral contract for a future CARLA/AirSim runner.

The protocol deliberately contains no process launch, world reset, port
mutation, or flight-control side effects.  An adapter owned by this project
may implement it once an isolated simulator instance has been proven.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .frames import CoordinateFrame, CarlaAirAlignment
from .models import CandidateAction, Vec3


@dataclass(frozen=True)
class AirSimWorldVelocityCommand:
    velocity_mps: Vec3
    yaw_rate_dps: float
    duration_s: float


def compile_carlaair_candidate(
    action: CandidateAction, alignment: CarlaAirAlignment,
) -> AirSimWorldVelocityCommand:
    """Compile legacy world-axis candidates; never reinterpret them as FLU."""
    import math
    if action.metadata.get("command_frame") != "world_z_up":
        raise ValueError("Candidate must declare world_z_up")
    if not math.isfinite(action.duration_s) or not 0 < action.duration_s <= 10:
        raise ValueError("Candidate duration must be finite and in (0, 10]")
    if not math.isfinite(action.yaw_rate_dps):
        raise ValueError("Candidate yaw rate must be finite")
    return AirSimWorldVelocityCommand(
        alignment.velocity_to_airsim(action.command_mps),
        action.yaw_rate_dps, action.duration_s,
    )


@dataclass(frozen=True)
class SimAction:
    """One bounded high-level UAV action issued at a simulator tick."""

    name: str
    duration_s: float
    yaw_rate_dps: float = 0.0
    forward_mps: float = 0.0
    lateral_mps: float = 0.0
    vertical_mps: float = 0.0
    command_frame: str = CoordinateFrame.BODY_FLU.value

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("action name must be non-empty")
        if self.duration_s <= 0.0:
            raise ValueError("action duration must be positive")
        try:
            CoordinateFrame(self.command_frame)
        except ValueError as exc:
            raise ValueError(f"unsupported action command_frame: {self.command_frame!r}") from exc


@dataclass(frozen=True)
class SimObservation:
    """Timestamped observation returned by an adapter after an action."""

    timestamp_s: float
    target_visible: bool
    target_in_frame: bool
    target_actor_id: int | None
    image_center_error: float | None = None
    range_m: float | None = None
    minimum_clearance_m: float | None = None
    collision: bool = False
    metadata: Mapping[str, object] | None = None


class SimulatorSession(Protocol):
    """Lifecycle contract; implementation owns only its isolated instance."""

    def reset(self, *, seed: int, scenario: str) -> SimObservation:
        """Reset one owned scenario and return its first observation."""

    def step(self, action: SimAction) -> SimObservation:
        """Apply one action and return the next observation."""

    def close(self) -> None:
        """Release only resources created by this session."""


def validate_observation(observation: SimObservation) -> None:
    """Reject malformed adapter output before it reaches selector metrics."""

    if observation.timestamp_s < 0.0:
        raise ValueError("observation timestamp must be non-negative")
    if observation.target_visible and not observation.target_in_frame:
        raise ValueError("a visible target must be in frame")
    if observation.range_m is not None and observation.range_m < 0.0:
        raise ValueError("range must be non-negative")
    if observation.minimum_clearance_m is not None and observation.minimum_clearance_m < 0.0:
        raise ValueError("minimum clearance must be non-negative")


def validate_action_sequence(actions: Sequence[SimAction]) -> None:
    """Check the finite action vocabulary before a simulator run."""

    if not actions:
        raise ValueError("at least one candidate action is required")
    for action in actions:
        if action.duration_s > 10.0:
            raise ValueError("action duration exceeds the 10 s adapter contract")
