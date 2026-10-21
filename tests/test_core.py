from __future__ import annotations

import json
from dataclasses import replace

import pytest

from jev_uav_track import (
    JevDecision,
    JevTrackPipeline,
    JevTrackSelector,
    KinematicRolloutModel,
    MissionConstraints,
    MockJevClient,
    TargetBelief,
    TrackingState,
    UAVState,
    build_choice_request,
)
from jev_uav_track.safety import filter_safe_actions


def make_state(**uav_changes: object) -> TrackingState:
    uav_values = {
        "position_m": (0.0, 0.0, 0.0),
        "minimum_obstacle_margin_m": 12.0,
    }
    uav_values.update(uav_changes)
    uav = UAVState(
        **uav_values,
    )
    return TrackingState(
        target=TargetBelief(
            position_m=(18.0, 2.0, 0.0),
            velocity_mps=(1.0, 0.2, 0.0),
            range_m=18.1,
            image_center_error=0.35,
            covariance_trace=1.6,
            visibility_probability=0.7,
        ),
        uav=uav,
        constraints=MissionConstraints(),
    )


def test_rollout_has_trajectory_and_serializable_choice_payload() -> None:
    state = make_state()
    actions = KinematicRolloutModel().generate(
        JevTrackPipeline(MockJevClient()).primitives,
        state,
    )
    assert len(actions) == 8
    assert actions[0].trajectory
    payload = build_choice_request(state, actions)
    json.dumps(payload)
    assert payload["questions"]["next_behavior"]["type"] == "choice"


def test_hard_safety_filter_rejects_actions_inside_obstacle_margin() -> None:
    state = make_state(minimum_obstacle_margin_m=0.5)
    actions = KinematicRolloutModel().generate(
        JevTrackPipeline(MockJevClient()).primitives,
        state,
    )
    safe, rejected = filter_safe_actions(actions, state.uav, state.constraints)
    assert not safe
    assert len(rejected) == len(actions)


def test_scan_reacquire_is_contextual_to_low_visibility() -> None:
    high_visibility_state = make_state()
    low_visibility_state = replace(
        high_visibility_state,
        target=replace(high_visibility_state.target, visibility_probability=0.3, occlusion_risk=0.8),
    )
    primitives = JevTrackPipeline(MockJevClient()).primitives
    high_actions = KinematicRolloutModel().generate(primitives, high_visibility_state)
    low_actions = KinematicRolloutModel().generate(primitives, low_visibility_state)
    high_scan = next(action for action in high_actions if action.id == "scan_reacquire")
    low_scan = next(action for action in low_actions if action.id == "scan_reacquire")
    assert low_scan.outcome.information_gain > high_scan.outcome.information_gain


def test_mock_pipeline_returns_a_safe_action() -> None:
    state = make_state()
    result = JevTrackPipeline(MockJevClient(temperature=0.15)).step(state)
    assert result.action.id in {action.id for action in KinematicRolloutModel().generate(JevTrackPipeline(MockJevClient()).primitives, state)}
    assert result.action.outcome.collision_probability <= state.constraints.maximum_collision_probability
    assert result.rejected_action_ids == ()


class LowConfidenceClient:
    def choose(self, state: TrackingState, actions: list) -> JevDecision:
        probability = 1.0 / len(actions)
        return JevDecision(
            choice=actions[0].id,
            probabilities={action.id: probability for action in actions},
            confidence=0.1,
            model_version="test-low-confidence",
        )


def test_low_confidence_uses_deterministic_fallback() -> None:
    state = make_state()
    actions = KinematicRolloutModel().generate(
        JevTrackPipeline(MockJevClient()).primitives,
        state,
    )
    result = JevTrackSelector(LowConfidenceClient()).select(state, actions)
    assert result.used_fallback is True
    assert result.reason.startswith("confidence_gate")
    assert result.decision.model_version == "test-low-confidence"
    assert result.decision.confidence == 0.1


def test_invalid_uav_health_is_rejected() -> None:
    with pytest.raises(ValueError):
        UAVState(gps_health=1.2)

