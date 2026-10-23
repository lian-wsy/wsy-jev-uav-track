"""Short-term target-belief history used by the high-level selector.

The history is intentionally a typed, compact state window rather than a raw
image buffer.  It keeps the fields needed to reason about target identity,
occlusion, target motion, and UAV self-motion while remaining serializable for
Jev or a local policy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence, Tuple

from .models import TrackingState, Vec3


def _norm(vector: Vec3) -> float:
    return math.sqrt(sum(float(value) ** 2 for value in vector))


@dataclass(frozen=True)
class BeliefHistoryEntry:
    """One timestamped target/UAV snapshot in a short history window."""

    timestamp_s: float
    target_actor_id: int | None
    target_position_m: Vec3
    target_velocity_mps: Vec3
    target_visibility_probability: float
    target_occlusion_risk: float
    identity_confidence: float
    uav_position_m: Vec3
    uav_velocity_mps: Vec3
    uav_yaw_deg: float

    @classmethod
    def from_state(cls, state: TrackingState) -> "BeliefHistoryEntry":
        actor_id = state.environment.get("target_actor_id")
        try:
            actor_id = int(actor_id) if actor_id is not None else None
        except (TypeError, ValueError):
            actor_id = None
        return cls(
            timestamp_s=float(state.target.timestamp_s),
            target_actor_id=actor_id,
            target_position_m=state.target.position_m,
            target_velocity_mps=state.target.velocity_mps,
            target_visibility_probability=float(state.target.visibility_probability),
            target_occlusion_risk=float(state.target.occlusion_risk),
            identity_confidence=float(state.target.identity_confidence),
            uav_position_m=state.uav.position_m,
            uav_velocity_mps=state.uav.velocity_mps,
            uav_yaw_deg=float(state.uav.yaw_deg),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_s": self.timestamp_s,
            "target_actor_id": self.target_actor_id,
            "target_position_m": list(self.target_position_m),
            "target_velocity_mps": list(self.target_velocity_mps),
            "target_visibility_probability": self.target_visibility_probability,
            "target_occlusion_risk": self.target_occlusion_risk,
            "identity_confidence": self.identity_confidence,
            "uav_position_m": list(self.uav_position_m),
            "uav_velocity_mps": list(self.uav_velocity_mps),
            "uav_yaw_deg": self.uav_yaw_deg,
        }


@dataclass(frozen=True)
class TargetBeliefHistory:
    """A bounded, chronological window of belief snapshots."""

    entries: Tuple[BeliefHistoryEntry, ...]
    max_length: int

    def __post_init__(self) -> None:
        if self.max_length <= 0:
            raise ValueError("max_length must be positive")
        if not self.entries:
            raise ValueError("At least one history entry is required")
        if len(self.entries) > self.max_length:
            raise ValueError("entries cannot exceed max_length")
        timestamps = [entry.timestamp_s for entry in self.entries]
        if timestamps != sorted(timestamps):
            raise ValueError("history entries must be chronological")

    @property
    def latest(self) -> BeliefHistoryEntry:
        return self.entries[-1]

    @property
    def length(self) -> int:
        return len(self.entries)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.latest.timestamp_s - self.entries[0].timestamp_s)

    @property
    def mean_visibility(self) -> float:
        return sum(entry.target_visibility_probability for entry in self.entries) / self.length

    @property
    def mean_identity_confidence(self) -> float:
        return sum(entry.identity_confidence for entry in self.entries) / self.length

    @property
    def occlusion_fraction(self) -> float:
        occluded = sum(entry.target_occlusion_risk >= 0.5 for entry in self.entries)
        return occluded / self.length

    @property
    def identity_consistency(self) -> float:
        """Fraction of known IDs equal to the latest known actor ID."""

        latest_id = self.latest.target_actor_id
        known = [entry.target_actor_id for entry in self.entries if entry.target_actor_id is not None]
        if latest_id is None or not known:
            return 0.0
        return sum(actor_id == latest_id for actor_id in known) / len(known)

    @property
    def target_speed_mps(self) -> float:
        return _norm(self.latest.target_velocity_mps)

    @property
    def uav_speed_mps(self) -> float:
        return _norm(self.latest.uav_velocity_mps)

    @property
    def target_acceleration_mps2(self) -> float:
        """Magnitude of the latest finite-difference target acceleration."""

        if self.length < 2:
            return 0.0
        previous = self.entries[-2]
        dt = self.latest.timestamp_s - previous.timestamp_s
        if dt <= 1e-9:
            return 0.0
        delta = tuple(
            self.latest.target_velocity_mps[index] - previous.target_velocity_mps[index]
            for index in range(3)
        )
        return _norm(delta) / dt

    def with_identity_confidence(self, confidence: float) -> "TargetBeliefHistory":
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        return replace(
            self,
            entries=tuple(
                replace(entry, identity_confidence=confidence) for entry in self.entries
            ),
        )

    def summary(self) -> str:
        actor = self.latest.target_actor_id
        actor_text = str(actor) if actor is not None else "unknown"
        return (
            f"history_len={self.length}; duration={self.duration_s:.2f}s; "
            f"visible_mean={self.mean_visibility:.2f}; occlusion_fraction={self.occlusion_fraction:.2f}; "
            f"identity_confidence={self.mean_identity_confidence:.2f}; "
            f"identity_consistency={self.identity_consistency:.2f}; actor={actor_text}; "
            f"target_speed={self.target_speed_mps:.2f}m/s; "
            f"target_acceleration={self.target_acceleration_mps2:.2f}m/s^2; "
            f"uav_speed={self.uav_speed_mps:.2f}m/s"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_length": self.max_length,
            "length": self.length,
            "duration_s": self.duration_s,
            "mean_visibility": self.mean_visibility,
            "mean_identity_confidence": self.mean_identity_confidence,
            "occlusion_fraction": self.occlusion_fraction,
            "identity_consistency": self.identity_consistency,
            "target_speed_mps": self.target_speed_mps,
            "target_acceleration_mps2": self.target_acceleration_mps2,
            "uav_speed_mps": self.uav_speed_mps,
            "entries": [entry.to_dict() for entry in self.entries],
        }


def build_target_belief_history(
    states: Sequence[TrackingState],
    max_length: int = 8,
) -> TargetBeliefHistory:
    """Keep the most recent states as a bounded chronological history."""

    if max_length <= 0:
        raise ValueError("max_length must be positive")
    if not states:
        raise ValueError("At least one TrackingState is required")
    entries = tuple(BeliefHistoryEntry.from_state(state) for state in states[-max_length:])
    return TargetBeliefHistory(entries=entries, max_length=max_length)


def build_episode_belief_history(
    episode: Any,
    frame_index: int,
    window_size: int = 8,
    constraints: Any | None = None,
) -> TargetBeliefHistory:
    """Build a history window ending at ``frame_index`` for an episode adapter."""

    if frame_index < 0 or frame_index >= episode.frame_count:
        raise IndexError(frame_index)
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    start = max(0, frame_index - window_size + 1)
    states = [
        episode.tracking_state(index, constraints=constraints)
        for index in range(start, frame_index + 1)
    ]
    return build_target_belief_history(states, max_length=window_size)


def attach_history(state: TrackingState, history: TargetBeliefHistory) -> TrackingState:
    """Attach a serializable history summary and derived stats to a state."""

    environment: Mapping[str, Any] = {
        **dict(state.environment),
        "belief_history": history.to_dict(),
    }
    return replace(
        state,
        history_summary=history.summary(),
        environment=environment,
    )
