"""Training features must not inherit hidden truth from recorded supervision."""
from dataclasses import replace

import h5py
import numpy as np
import pytest

from jev_uav_track.data import HDF5Episode
from jev_uav_track.training import (
    RolloutTrainingConfig, build_training_dataset, fit_outcome_model,
    LinearOutcomeModel,
)
from test_optional_data import make_synthetic_episode


def episode_fixture(tmp_path):
    path = tmp_path / 'episode.hdf5'
    make_synthetic_episode(path)
    with h5py.File(path, 'r+') as h5:
        h5.create_dataset('action/camera_rotations', data=np.zeros((24, 3)))
        h5['visibility/target_visible'][[0, 1, 4, 5]] = False
        h5['visibility/target_occluded'][[4, 5]] = True
    return HDF5Episode.open(path)


@pytest.mark.parametrize('target_mode', ['absolute', 'baseline_residual'])
@pytest.mark.parametrize('visible,in_frame,occluded', [
    (False, True, False), (True, False, False), (True, True, True)])
def test_hidden_truth_cannot_change_any_causal_training_feature(
        tmp_path, target_mode, visible, in_frame, occluded):
    episode = episode_fixture(tmp_path)
    hidden = [0, 1, 4, 5]
    episode.target_visible[hidden] = visible
    episode.target_in_frame[hidden] = in_frame
    episode.target_occluded[hidden] = occluded
    changed = replace(episode,
        target_positions=episode.target_positions.copy(),
        target_velocities=episode.target_velocities.copy(),
        target_ranges_m=episode.target_ranges_m.copy(),
        target_actor_ids=episode.target_actor_ids.copy())
    changed.target_positions[hidden] += 1000
    changed.target_ranges_m[hidden] += 2000
    changed.target_actor_ids[hidden] = 999
    # Even visible oracle velocity may use hidden state-axis rows. Do not use it.
    changed.target_velocities[:] = -3000
    cfg = RolloutTrainingConfig(stride=1, input_mode='causal_visible', target_mode=target_mode)
    left, right = [build_training_dataset(e, cfg) for e in (episode, changed)]
    np.testing.assert_array_equal(left.features, right.features)
    assert left.features[0, 0] == pytest.approx(20.)  # Unseen prior, not true range.
    np.testing.assert_array_equal(left.features[0, 1:4], [0., 0., 0.])
    assert left.features[4, 7] == .05
    assert left.to_summary()['input_mode'] == 'causal_visible'
    assert not np.array_equal(left.labels, right.labels)  # Truth is legal as label.


def test_residual_baseline_also_ignores_hidden_truth(tmp_path):
    episode = episode_fixture(tmp_path)
    changed = replace(episode, target_positions=episode.target_positions.copy(),
                      target_ranges_m=episode.target_ranges_m.copy())
    changed.target_positions[[0, 1, 4, 5]] += 1000
    changed.target_ranges_m[[0, 1, 4, 5]] += 2000
    cfg = RolloutTrainingConfig(stride=1, input_mode='causal_visible')
    base_a, base_b = [] , []
    for current, destination in ((episode, base_a), (changed, base_b)):
        absolute = build_training_dataset(current, cfg)
        residual = build_training_dataset(current, replace(cfg, target_mode='baseline_residual'))
        destination.extend(absolute.labels - residual.labels)
    np.testing.assert_allclose(base_a, base_b, atol=1e-10, rtol=0)


def test_future_changes_only_supervision_not_prefix_features(tmp_path):
    episode = episode_fixture(tmp_path)
    changed = replace(episode, target_positions=episode.target_positions.copy(),
                      target_ranges_m=episode.target_ranges_m.copy())
    changed.target_positions[8:] += 500
    changed.target_ranges_m[8:] += 100
    cfg = RolloutTrainingConfig(stride=1, input_mode='causal_visible')
    left, right = [build_training_dataset(e, cfg) for e in (episode, changed)]
    np.testing.assert_array_equal(left.features, right.features)
    assert not np.array_equal(left.labels, right.labels)


def test_subsampled_training_still_consumes_all_past_visible_frames(tmp_path):
    episode = episode_fixture(tmp_path)
    cfg = RolloutTrainingConfig(stride=1, input_mode='causal_visible')
    full = build_training_dataset(episode, cfg)
    sparse = build_training_dataset(episode, replace(cfg, stride=2, max_samples=3))
    np.testing.assert_array_equal(sparse.features, full.features[[0, 4, 6]])


@pytest.mark.parametrize('missing', ['visibility/target_visible', 'visibility/target_in_frame',
                                    'visibility/target_occluded', 'action/camera_positions',
                                    'action/camera_rotations'])
def test_missing_observation_contract_cannot_be_assumed_visible(tmp_path, missing):
    episode = episode_fixture(tmp_path)
    with h5py.File(episode.path, 'r+') as h5:
        del h5[missing]
    episode = HDF5Episode.open(episode.path)
    with pytest.raises(ValueError, match='Causal training requires'):
        build_training_dataset(episode, RolloutTrainingConfig(input_mode='causal_visible'))


def test_saved_model_retains_causal_input_contract_and_rejects_mislabeled_fit(tmp_path):
    episode = episode_fixture(tmp_path)
    cfg = RolloutTrainingConfig(stride=1, input_mode='causal_visible')
    data = build_training_dataset(episode, cfg)
    with pytest.raises(ValueError, match='input_mode'):
        fit_outcome_model(data, replace(cfg, input_mode='oracle'))
    model, metrics = fit_outcome_model(data)
    model.save(tmp_path / 'causal.npz')
    loaded = LinearOutcomeModel.load(tmp_path / 'causal.npz')
    assert loaded.training_summary['config']['input_mode'] == 'causal_visible'
    assert loaded.training_summary['dataset']['input_mode'] == 'causal_visible'
    assert metrics['input_mode'] == 'causal_visible'
    np.testing.assert_array_equal(loaded.predict_array(data.features), model.predict_array(data.features))
