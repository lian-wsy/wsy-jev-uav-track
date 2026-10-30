"""D5 fitting, calibration and action diagnostics must share causal inputs."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from scripts import run_d5_calibration as runner
from jev_uav_track.models import MissionConstraints
from jev_uav_track.training import RolloutTrainingConfig, build_training_dataset, LinearOutcomeModel
from test_causal_training import episode_fixture
from test_d5_runner import manifest_fixture


def test_d5_samples_and_candidate_states_ignore_hidden_and_future_truth(tmp_path):
    episode = episode_fixture(tmp_path)
    cfg = RolloutTrainingConfig(input_mode='causal_visible', max_samples=3,
                               desired_range_m=25., stride=2)
    data = build_training_dataset(episode, cfg)
    changed = replace(episode, target_positions=episode.target_positions.copy(),
                      target_ranges_m=episode.target_ranges_m.copy(),
                      target_velocities=episode.target_velocities.copy(),
                      target_actor_ids=episode.target_actor_ids.copy())
    changed.target_positions[[0, 1, 4, 5]] += 1000
    changed.target_positions[8:] += 1000
    changed.target_ranges_m[[0, 1, 4, 5]] += 2000
    changed.target_actor_ids[[0, 1, 4, 5]] = 999
    changed.target_velocities[:] = -3000
    constraints = MissionConstraints(desired_range_m=25.)
    indices, states = runner.sampled_inputs(episode, cfg, data.features, constraints)
    other_indices, other_states = runner.sampled_inputs(changed, cfg, data.features, constraints)
    assert indices == other_indices == [0, 4, 6]
    assert states == other_states
    assert states[0].target.range_m == 25.  # own-pose prior, never hidden truth
    assert states[0].uav.battery_percent == 0.
    assert states[0].constraints.minimum_obstacle_margin_m == 3.
    assert states[-1].target.timestamp_s == episode.image_timestamps[6]
    with pytest.raises(AssertionError):
        runner.sampled_inputs(episode, cfg, data.features + 1., constraints)


def test_d5_default_causal_full_run_and_saved_contract(tmp_path):
    split, entries = manifest_fixture(tmp_path)
    for entry in entries[:6]:
        path = Path(entry['path'])
        with h5py.File(path, 'r+') as h5:
            h5.create_dataset('action/camera_rotations', data=np.zeros((24, 3)))
            h5['visibility/target_visible'][[0, 1, 4, 5]] = False
            h5['visibility/target_occluded'][[4, 5]] = True
        entry.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size)
    split.write_text(json.dumps({'episodes': entries}))
    report = runner.run(split, tmp_path / 'out', samples=3, rate=1000,
                        calibration_count=4, alpha=.2)
    assert report['config']['input_mode'] == 'causal_visible'
    assert report['test_or_stress_opened'] == 0
    assert report['validation_used_for_fit_or_thresholds'] is False
    model = LinearOutcomeModel.load(tmp_path / 'out/d5_residual.npz')
    assert model.training_summary['config']['input_mode'] == 'causal_visible'
    assert model.training_summary['train_dataset']['input_mode'] == 'causal_visible'
    rows = [json.loads(line) for line in (tmp_path / 'out/records.jsonl').read_text().splitlines()]
    assert [r['frame_index'] for r in rows] == [0, 4, 6]
    assert all(r['input_mode'] == 'causal_visible' for r in rows)
    assert all(r['missing_safety_measurements'] == ['minimum_clearance_m', 'battery_percent'] for r in rows)
    assert report['validation_strata']['occluded']['count'] == 1
    assert all(not r['safe_candidate_ids'] for r in rows)


def test_causal_d5_rejects_missing_measurements_without_completing(tmp_path):
    split, _ = manifest_fixture(tmp_path)  # no recorded yaw
    with pytest.raises(ValueError, match='Causal training requires'):
        runner.run(split, tmp_path / 'out', samples=3, rate=1000,
                   calibration_count=4, alpha=.2)
    assert not (tmp_path / 'out/report.json').exists()


def test_bad_input_mode_rejects_before_reading_manifest(tmp_path):
    with pytest.raises(ValueError, match='input_mode'):
        runner.run(tmp_path / 'missing.json', tmp_path / 'out', input_mode='invalid')
    assert not (tmp_path / 'out').exists()
