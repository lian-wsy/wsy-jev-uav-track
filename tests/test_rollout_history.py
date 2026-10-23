from __future__ import annotations

from dataclasses import replace

import pytest

from jev_uav_track import (
    KinematicRolloutModel,
    TargetBelief,
    TrackingState,
    UAVState,
    attach_history,
    build_target_belief_history,
)
from jev_uav_track.primitives import DEFAULT_PRIMITIVES


def make_state(**target_changes: object) -> TrackingState:
    target_values = {
        "position_m": (18.0, 2.0, 0.0),
        "velocity_mps": (1.0, 0.2, 0.0),
        "range_m": 18.1,
        "image_center_error": 0.35,
        "covariance_trace": 1.6,
        "visibility_probability": 0.9,
    }
    target_values.update(target_changes)
    return TrackingState(
        target=TargetBelief(**target_values),
        uav=UAVState(position_m=(0.0, 0.0, 0.0), minimum_obstacle_margin_m=12.0),
    )


def action(state: TrackingState, primitive_id: str):
    primitive = next(item for item in DEFAULT_PRIMITIVES if item.id == primitive_id)
    return KinematicRolloutModel().simulate(primitive, state)


def with_history(current: TrackingState, previous: TrackingState) -> TrackingState:
    history = build_target_belief_history([previous, current], max_length=8)
    return attach_history(current, history)


def test_low_identity_history_increases_reacquisition_information_gain() -> None:
    current = make_state(identity_confidence=1.0)
    stable_history = with_history(current, replace(current, target=replace(current.target, identity_confidence=1.0)))
    uncertain_history = with_history(current, replace(current, target=replace(current.target, identity_confidence=0.2)))

    stable_scan = action(stable_history, "scan_reacquire")
    uncertain_scan = action(uncertain_history, "scan_reacquire")

    assert uncertain_scan.outcome.information_gain > stable_scan.outcome.information_gain
    assert uncertain_scan.outcome.covariance_trace > stable_scan.outcome.covariance_trace


def test_occlusion_history_triggers_scan_when_current_frame_is_visible() -> None:
    current = make_state(visibility_probability=0.9, occlusion_risk=0.0)
    no_history_scan = action(current, "scan_reacquire")
    occluded_history = with_history(
        current,
        replace(current, target=replace(current.target, visibility_probability=0.2, occlusion_risk=0.8)),
    )
    history_scan = action(occluded_history, "scan_reacquire")

    assert history_scan.outcome.visibility_probability > no_history_scan.outcome.visibility_probability
    assert history_scan.outcome.information_gain > no_history_scan.outcome.information_gain


def test_target_acceleration_history_increases_predicted_covariance() -> None:
    current = make_state()
    no_history = action(current, "hold_center")
    previous = replace(
        current,
        target=replace(
            current.target,
            timestamp_s=-0.5,
            velocity_mps=(0.0, 0.0, 0.0),
        ),
    )
    accelerating = with_history(current, previous)
    accelerating_hold = action(accelerating, "hold_center")

    assert accelerating.environment["belief_history"]["target_acceleration_mps2"] == pytest.approx(2.039607805437114)
    assert accelerating_hold.outcome.covariance_trace > no_history.outcome.covariance_trace

