import numpy as np
import pytest


def test_episode_scores_do_not_treat_many_frames_as_independent_calibration():
    from jev_uav_track.residual_intervals import episode_max_radius
    errors = [np.full((n, 4), v) for n, v in [(100, 1), (1, 2), (1, 3), (1, 9)]]
    radius = episode_max_radius(errors, alpha=.2)  # ceil((4+1)*.8)=4
    np.testing.assert_array_equal(radius, [9, 9, 9, 9])
    with pytest.raises(ValueError, match='calibration episodes'):
        episode_max_radius(errors, alpha=.1)


@pytest.mark.parametrize('alpha', [0, 1, float('nan'), -.1])
def test_invalid_interval_alpha_rejected(alpha):
    from jev_uav_track.residual_intervals import episode_max_radius
    with pytest.raises(ValueError):
        episode_max_radius([np.zeros((3, 4))], alpha=alpha)


def test_interval_metrics_inclusive_boundary_and_empty_stratum():
    from jev_uav_track.residual_intervals import interval_metrics
    report = interval_metrics(np.array([[1, 2, 3, 4], [1, 2, 4, 4]]),
                              np.zeros((2, 4)), np.array([1, 2, 3, 4]))
    assert report['coverage']['range_error_m'] == .5
    assert report['width']['range_error_m'] == 6
    assert report['coverage']['visibility_probability'] == 1
    empty = interval_metrics(np.empty((0, 4)), np.empty((0, 4)), np.ones(4))
    assert empty['status'] == 'NOT_EVALUATED' and empty['count'] == 0
    assert empty['coverage'] is None


def test_uncertain_or_unsupported_outputs_fall_back_without_changing_baseline():
    from jev_uav_track.residual_intervals import correction_mask
    fit = np.zeros((2, 15)); fit[1, 0] = 1
    query = np.zeros((3, 15)); query[1, 14] = 12; query[2, 0] = 2
    residual = np.tile([0, .1, 2, -.2], (3, 1))
    accepted, inside = correction_mask(fit, query, residual, np.array([0, .1, 1, .1]))
    np.testing.assert_array_equal(inside, [True, False, False])
    np.testing.assert_array_equal(accepted, [[False, False, True, True], [False]*4, [False]*4])


def test_nonfinite_values_and_shape_mismatch_are_rejected():
    from jev_uav_track.residual_intervals import episode_max_radius, interval_metrics, correction_mask
    with pytest.raises(ValueError):
        episode_max_radius([np.full((3, 4), np.nan)], alpha=.5)
    with pytest.raises(ValueError):
        interval_metrics(np.zeros((2, 4)), np.zeros((1, 4)), np.ones(4))
    with pytest.raises(ValueError):
        correction_mask(np.zeros((2, 15)), np.zeros((1, 15)), np.zeros((1, 4)), [-1]*4)


def test_interval_rollout_preserves_safety_and_exact_baseline_on_full_fallback():
    from jev_uav_track.residual_intervals import IntervalGatedRolloutModel
    from jev_uav_track.training import LinearOutcomeModel, feature_vector
    from jev_uav_track.models import TrackingState, TargetBelief, UAVState
    from jev_uav_track.primitives import DEFAULT_PRIMITIVES
    state = TrackingState(TargetBelief(), UAVState())
    model = LinearOutcomeModel(np.zeros(15), np.ones(15), np.zeros((16, 4)),
        target_mode='baseline_residual', training_summary={'train_dataset': {'resolved_rollout_horizon_s': 1}})
    fit = np.array([feature_vector(state, DEFAULT_PRIMITIVES[0].command_mps)])
    gated = IntervalGatedRolloutModel(model, fit, np.ones(4))
    primitive = DEFAULT_PRIMITIVES[0]
    base = gated.baseline.simulate(primitive, state)
    outcome = gated.simulate(primitive, state)
    assert outcome.outcome == base.outcome
    assert outcome.trajectory == base.trajectory
    assert outcome.metadata['interval_full_fallback'] is True
    model.weights[0, 2] = 2
    outcome = gated.simulate(primitive, state)
    assert outcome.outcome.range_error_m == base.outcome.range_error_m + 2
    for name in ['collision_probability', 'minimum_obstacle_margin_m', 'energy_cost_wh', 'feasible']:
        assert getattr(outcome.outcome, name) == getattr(base.outcome, name)
    assert outcome.metadata['interval_full_fallback'] is False
