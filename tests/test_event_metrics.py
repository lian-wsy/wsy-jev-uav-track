import pytest


def test_confirmation_duration_and_failures_enter_restricted_mean():
    from jev_uav_track.event_metrics import score_reacquisition
    # Success at t=1.5 confirms 0.5 s; second event never recovers.
    result = score_reacquisition(
        [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5],
        [False, False, True, True, False, False, False, False],
        event_starts=[0, 2], reference_end_s=5, window_s=2, stable_s=0.5, max_gap_s=0.5,
    )
    assert result["recovery_rate"] == 0.5
    assert result["restricted_mean_s"] == 1.75
    assert result["success_mean_s"] == 1.5
    assert result["events"][1]["status"] == "FAILED"


def test_sparse_samples_do_not_prove_continuous_recovery():
    from jev_uav_track.event_metrics import score_reacquisition
    result = score_reacquisition([0, 3, 6, 10], [True, True, True, True], event_starts=[0],
        reference_end_s=10, max_gap_s=0.25)
    assert result["restricted_mean_s"] == 10
    assert result["recovery_rate"] == 0


def test_ineligible_end_window_excluded_independent_of_method():
    from jev_uav_track.event_metrics import score_reacquisition
    result = score_reacquisition([], [], event_starts=[0, 15], reference_end_s=20)
    assert result["eligible_events"] == 1
    assert result["ineligible_events"] == 1
    assert result["restricted_mean_s"] == 10


def test_early_termination_after_brief_recovery_still_counts_failure():
    from jev_uav_track.event_metrics import score_reacquisition
    result = score_reacquisition([0, 0.25, 0.5, 0.75], [False, True, True, True],
        event_starts=[0], reference_end_s=20)
    assert result["recovery_rate"] == 0
    assert result["restricted_mean_s"] == 10


def test_loss_onsets_use_reference_and_nonoverlapping_windows():
    from jev_uav_track.event_metrics import reference_loss_events
    assert reference_loss_events([0, 1, 2, 4, 12, 13],
        [True, False, True, False, True, False], window_s=10) == [1.0, 13.0]


@pytest.mark.parametrize("times", [[0, 0], [1, 0], [0, float("nan")]])
def test_event_metrics_reject_invalid_time_axis(times):
    from jev_uav_track.event_metrics import score_reacquisition
    with pytest.raises(ValueError):
        score_reacquisition(times, [True, True], event_starts=[0], reference_end_s=20)
