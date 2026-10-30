"""Regression tests suggested by the user-supplied causal-model guide."""
from dataclasses import replace

import h5py
import numpy as np
import pytest

from jev_uav_track.data import HDF5Episode
from jev_uav_track.training import RolloutTrainingConfig, build_training_dataset
from jev_uav_track.closed_loop import ClosedLoopReplayConfig, run_closed_loop_replay
from test_optional_data import make_synthetic_episode
from test_closed_loop import FakeEpisode
from test_causal_belief import SpyUtility


def test_episode_global_clearance_cannot_enter_current_state(tmp_path):
    path = tmp_path / 'episode.hdf5'
    make_synthetic_episode(path)
    first = HDF5Episode.open(path)
    second = replace(first, metadata={**first.metadata, 'minimum_static_clearance_m': 0.01})
    assert first.tracking_state(0) == second.tracking_state(0)
    state = first.tracking_state(0)
    assert state.uav.minimum_obstacle_margin_m == 0
    assert state.environment['obstacle_margin_source'] == 'unobserved_fail_closed'


def test_replay_does_not_read_episode_global_clearance():
    first, second = FakeEpisode(), FakeEpisode()
    second.metadata = {**second.metadata, 'minimum_static_clearance_m': 0.01}
    left, right = SpyUtility(), SpyUtility()
    cfg = ClosedLoopReplayConfig(stride=1, max_decisions=6, minimum_obstacle_margin_m=0)
    a = run_closed_loop_replay(first, config=cfg, client=left)
    b = run_closed_loop_replay(second, config=cfg, client=right)
    assert left.states and left.states == right.states
    assert [r['choice'] for r in a['records']] == [r['choice'] for r in b['records']]


def test_hdf_missing_timestamped_clearance_rejects_all_under_default_safety(tmp_path):
    from jev_uav_track.rollout import KinematicRolloutModel
    from jev_uav_track.primitives import DEFAULT_PRIMITIVES
    from jev_uav_track.safety import filter_safe_actions
    path = tmp_path / 'episode.hdf5'
    make_synthetic_episode(path)
    state = HDF5Episode.open(path).tracking_state(0)
    candidates = KinematicRolloutModel().generate(DEFAULT_PRIMITIVES, state)
    safe, rejected = filter_safe_actions(candidates, state.uav, state.constraints)
    assert not safe
    assert len(rejected) == len(DEFAULT_PRIMITIVES)


@pytest.mark.parametrize('mode', ['absolute', 'baseline_residual'])
def test_future_rows_change_labels_but_not_current_features(tmp_path, mode):
    path = tmp_path / 'episode.hdf5'
    make_synthetic_episode(path)
    first = HDF5Episode.open(path)
    cutoff = 4  # image at 0.8s; state rows > 8 are strictly in the future.
    with h5py.File(path, 'r+') as h5:  # synthetic fixture only, never source data
        h5['state/positions'][9:] = 100
        h5['action/camera_positions'][9:] = 50
        h5['action/camera_target_distance'][5:] = 90
        h5['visibility/target_visible'][5:] = False
        h5['visibility/target_occluded'][5:] = True
        h5['visibility/target_in_frame'][5:] = False
        h5['image_state/actor_ids'][5:] = 999
    second = HDF5Episode.open(path)
    assert [first.tracking_state(i) for i in range(cutoff + 1)] == [
        second.tracking_state(i) for i in range(cutoff + 1)]
    cfg = RolloutTrainingConfig(max_samples=10, target_mode=mode, rollout_horizon_s=1)
    a, b = build_training_dataset(first, cfg), build_training_dataset(second, cfg)
    # Starts are 0,2,4,6; first three inputs predate the perturbation.
    np.testing.assert_array_equal(a.features[:3], b.features[:3])
    assert not np.array_equal(a.labels[:3], b.labels[:3])
    from jev_uav_track.history import build_episode_belief_history
    assert build_episode_belief_history(first, cutoff) == build_episode_belief_history(second, cutoff)


def test_frozen_predictor_does_not_normalize_over_query_sequence():
    from jev_uav_track.training import LinearOutcomeModel
    # Fixed training statistics; do not refit after perturbing evaluation data.
    model = LinearOutcomeModel(np.arange(15.), np.ones(15), np.arange(64.).reshape(16, 4))
    query = np.arange(90.).reshape(6, 15)
    before = model.predict_array(query)
    query[3:] = 1e6
    after = model.predict_array(query)
    np.testing.assert_array_equal(before[:3], after[:3])
    np.testing.assert_allclose(before[2], model.predict_array(query[2])[0], atol=1e-12)


def test_validation_labels_cannot_change_fitted_statistics_or_weights(tmp_path):
    from jev_uav_track.training import fit_outcome_model_cross_episode
    paths = [tmp_path / name for name in ('fit.hdf5', 'validation.hdf5')]
    for path in paths:
        make_synthetic_episode(path)
    fit, validation = map(HDF5Episode.open, paths)
    poisoned = replace(validation, target_ranges_m=np.full(validation.frame_count, 999.),
                       target_positions=validation.target_positions + 999.)
    cfg = RolloutTrainingConfig(max_samples=3, target_mode='baseline_residual')
    a, _ = fit_outcome_model_cross_episode([fit], [validation], cfg)
    b, _ = fit_outcome_model_cross_episode([fit], [poisoned], cfg)
    np.testing.assert_array_equal(a.feature_mean, b.feature_mean)
    np.testing.assert_array_equal(a.feature_scale, b.feature_scale)
    np.testing.assert_array_equal(a.weights, b.weights)
