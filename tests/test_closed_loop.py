from pathlib import Path

import pytest

from jev_uav_track.closed_loop import (
    ClosedLoopReplayConfig,
    _IdentityTracker,
    aggregate_closed_loop_reports,
    compare_closed_loop_reports,
    run_closed_loop_replay,
)
from jev_uav_track.jev_client import MockJevClient, UtilityRankingClient
from jev_uav_track.models import MissionConstraints, TargetBelief, TrackingState, UAVState


class FakeEpisode:
    frame_count = 6
    image_timestamps = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    target_visible = [True, False, False, True, True, True]
    target_in_frame = [True] * frame_count
    target_occluded = [False, True, True, False, False, False]
    target_actor_ids = [7] * frame_count
    metadata = {
        "schema": "carlaair_uav_v2_candidate",
        "validation_status": "PASS",
        "minimum_static_clearance_m": 5.0,
    }
    path = Path("fake_episode.hdf5")

    def tracking_state(self, index, constraints=None):
        constraints = constraints or MissionConstraints()
        return TrackingState(
            target=TargetBelief(
                position_m=(20.0 + index, 0.0, 0.0),
                velocity_mps=(1.0, 0.0, 0.0),
                range_m=20.0,
                bearing_deg=0.0,
                timestamp_s=self.image_timestamps[index],
            ),
            uav=UAVState(
                position_m=(0.0, 0.0, 0.0),
                minimum_obstacle_margin_m=5.0,
                battery_capacity_wh=20.0,
                energy_source="SIMULATED_RESOURCE",
            ),
            constraints=constraints,
            environment={"target_actor_id": 7},
        )


class CrossingEpisode(FakeEpisode):
    target_visible = [True] * 6
    target_occluded = [False] * 6
    target_actor_ids = [7, 7, 8, 8, 8, 8]


def test_closed_loop_reports_reacquisition_and_identity_metrics():
    config = ClosedLoopReplayConfig(
        start_frame=0,
        end_frame=6,
        stride=1,
        max_decisions=16,
        minimum_obstacle_margin_m=0.0,
    )
    report = run_closed_loop_replay(
        FakeEpisode(),
        client=UtilityRankingClient(),
        config=config,
        model_name="utility-ranking-v0",
    )

    assert report["metrics"]["reacquisition_count"] == 1
    assert report["metrics"]["mean_reacquisition_time_s"] == 2.0
    assert report["metrics"]["identity_retention_rate"] == 1.0
    assert report["identity_coverage"]["status"] == "NOT_AUDITED"
    assert report["metrics"]["selected_unsafe_count"] == 0
    assert report["metrics"]["safety_event_count"] == 0


def test_identity_tracker_is_sticky_at_crossing():
    tracker = _IdentityTracker()
    assert tracker.update(1, True) == (1, 1.0)
    tracked_id, confidence = tracker.update(2, True)
    assert tracked_id == 1
    assert confidence == 0.25
    assert tracker.switch_events == 1
    tracked_id, confidence = tracker.update(2, True)
    assert tracked_id == 2
    assert confidence == 0.60
    assert tracker.confirmed_switch_events == 1


def test_crossing_episode_reports_audited_identity_and_confirmed_switch():
    config = ClosedLoopReplayConfig(
        start_frame=0,
        end_frame=6,
        stride=1,
        max_decisions=16,
        minimum_obstacle_margin_m=0.0,
    )
    report = run_closed_loop_replay(
        CrossingEpisode(),
        client=UtilityRankingClient(),
        config=config,
        model_name="utility-ranking-v0",
    )

    assert report["identity_coverage"]["status"] == "AUDITED"
    assert report["identity_coverage"]["ground_truth_actor_ids"] == [7, 8]
    assert report["metrics"]["identity_confirmed_switch_events"] == 1
    assert report["metrics"]["identity_retention_rate"] == 5 / 6


def test_closed_loop_pairwise_comparison_is_deterministic():
    config = ClosedLoopReplayConfig(
        start_frame=0,
        end_frame=6,
        stride=2,
        max_decisions=16,
        minimum_obstacle_margin_m=0.0,
    )
    mock = run_closed_loop_replay(
        FakeEpisode(),
        client=MockJevClient(),
        config=config,
        model_name="mock-jev-v0",
    )
    utility = run_closed_loop_replay(
        FakeEpisode(),
        client=UtilityRankingClient(),
        config=config,
        model_name="utility-ranking-v0",
    )
    comparison = compare_closed_loop_reports({"mock": mock, "utility": utility})[
        "mock_vs_utility"
    ]
    assert comparison["shared_frames"] == 3
    assert 0.0 <= comparison["choice_agreement_rate"] <= 1.0


def test_closed_loop_aggregate_weights_identity_and_reacquisition_events():
    config = ClosedLoopReplayConfig(
        start_frame=0,
        end_frame=6,
        stride=1,
        max_decisions=16,
        minimum_obstacle_margin_m=0.0,
    )
    first = run_closed_loop_replay(
        FakeEpisode(),
        client=UtilityRankingClient(),
        config=config,
        model_name="utility-ranking-v0",
    )
    second = run_closed_loop_replay(
        FakeEpisode(),
        client=MockJevClient(),
        config=config,
        model_name="mock-jev-v0",
    )
    aggregate = aggregate_closed_loop_reports([first, second])
    assert aggregate["episode_count"] == 2
    assert aggregate["metrics"]["reacquisition_count"] == 2
    assert aggregate["metrics"]["mean_reacquisition_time_s"] == 2.0
    assert aggregate["metrics"]["identity_retention_rate"] == 1.0
    assert aggregate["identity_coverage_statuses"] == ["NOT_AUDITED"]
    assert aggregate["metrics"]["near_miss_rate"] is not None
    stats = aggregate["episode_statistics"]["proxy_visible_fraction"]
    assert stats["n"] == 2
    assert stats["ci95"][0] <= stats["mean"] <= stats["ci95"][1]
    assert 0.0 <= stats["ci95"][0] <= 1.0
    assert 0.0 <= stats["ci95"][1] <= 1.0


def test_near_miss_probability_fraction_is_explicit_and_validated():
    config = ClosedLoopReplayConfig(near_miss_collision_probability_fraction=0.25)
    assert config.near_miss_collision_probability_fraction == 0.25
    with pytest.raises(ValueError):
        ClosedLoopReplayConfig(near_miss_collision_probability_fraction=1.1)
    with pytest.raises(ValueError):
        ClosedLoopReplayConfig(max_learned_feature_zscore=0.0)
    with pytest.raises(ValueError):
        ClosedLoopReplayConfig(learned_prediction_blend=1.1)
    with pytest.raises(ValueError):
        ClosedLoopReplayConfig(max_learned_state_feature_zscore=0.0)
