from __future__ import annotations

import pytest

from jev_uav_track import (
    TargetBelief,
    TrackingState,
    UAVState,
    attach_history,
    build_target_belief_history,
)


def make_state(
    timestamp: float,
    actor_id: int | None,
    velocity_x: float,
    visibility: float,
    occlusion_risk: float,
) -> TrackingState:
    return TrackingState(
        target=TargetBelief(
            position_m=(timestamp, 0.0, 5.0),
            velocity_mps=(velocity_x, 0.0, 0.0),
            timestamp_s=timestamp,
            visibility_probability=visibility,
            occlusion_risk=occlusion_risk,
            identity_confidence=1.0 if actor_id is not None else 0.3,
        ),
        uav=UAVState(
            position_m=(0.0, 0.0, 10.0),
            velocity_mps=(0.5, 0.0, 0.0),
            yaw_deg=4.0,
        ),
        environment={"target_actor_id": actor_id, "frame_index": int(timestamp * 10)},
    )


def test_history_keeps_recent_identity_visibility_and_motion() -> None:
    states = [
        make_state(0.0, 7, 0.0, 1.0, 0.0),
        make_state(0.1, 7, 1.0, 0.35, 0.9),  # out of frame / occluded proxy
        make_state(0.2, 8, 3.0, 0.8, 0.0),  # identity switch
        make_state(0.3, 8, 3.0, 1.0, 0.0),
    ]
    history = build_target_belief_history(states, max_length=3)

    assert history.length == 3
    assert history.latest.target_actor_id == 8
    assert history.duration_s == pytest.approx(0.2)
    assert history.mean_visibility == pytest.approx((0.35 + 0.8 + 1.0) / 3)
    assert history.occlusion_fraction == pytest.approx(1 / 3)
    assert history.identity_consistency == pytest.approx(2 / 3)
    assert history.target_acceleration_mps2 == pytest.approx(0.0)
    assert "identity_consistency=0.67" in history.summary()


def test_history_reports_latest_acceleration_and_can_neutralize_identity() -> None:
    states = [
        make_state(0.0, 11, 0.0, 1.0, 0.0),
        make_state(0.1, 11, 1.0, 1.0, 0.0),
        make_state(0.2, 11, 3.0, 1.0, 0.0),
    ]
    history = build_target_belief_history(states, max_length=8)
    assert history.target_acceleration_mps2 == pytest.approx(20.0)

    neutral = history.with_identity_confidence(0.5)
    assert neutral.mean_identity_confidence == pytest.approx(0.5)
    assert all(entry.identity_confidence == 0.5 for entry in neutral.entries)


def test_attach_history_preserves_state_and_serializes_summary() -> None:
    state = make_state(0.0, 3, 0.0, 1.0, 0.0)
    history = build_target_belief_history([state], max_length=4)
    attached = attach_history(state, history)

    assert attached.target == state.target
    assert attached.history_summary == history.summary()
    assert attached.environment["belief_history"]["length"] == 1


def test_history_rejects_invalid_window() -> None:
    state = make_state(0.0, 3, 0.0, 1.0, 0.0)
    with pytest.raises(ValueError, match="max_length"):
        build_target_belief_history([state], max_length=0)
    with pytest.raises(ValueError, match="At least one"):
        build_target_belief_history([], max_length=4)
