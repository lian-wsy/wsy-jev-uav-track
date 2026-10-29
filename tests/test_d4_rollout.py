import numpy as np
import pytest

from jev_uav_track.rollout_diagnostics import support_report, compare_predictions


def test_constant_yaw_axis_is_outside_support_for_turning_candidates():
    train = np.zeros((3, 15))
    train[:, 11] = [-1, 0, 1]
    query = np.zeros((2, 15))
    query[1, 14] = 12
    report = support_report(train, query)
    assert report['action_outside_count'] == 1
    assert report['state_outside_count'] == 0
    assert report['outside_count_by_feature']['command_yaw_rate_dps'] == 1
    assert report['joint_box_inside_count'] == 1


def test_axis_box_does_not_claim_joint_observed_support():
    train = np.zeros((2, 15))
    train[:, 11:13] = [[-1, -1], [1, 1]]
    query = np.zeros((1, 15))
    query[0, 11:13] = [1, -1]
    report = support_report(train, query)
    assert report['joint_box_inside_count'] == 1
    assert report['nearest_train_standardized_l2']['min'] == pytest.approx(2)


def test_raw_residual_predictions_are_compared_in_same_absolute_outcome_space():
    truth = np.array([[1., .2, 4., .1], [0., .4, 2., .3]])
    baseline = truth + 2
    absolute = truth + 1
    residual = np.full((2, 4), -2.)
    scores = compare_predictions(truth, baseline, absolute, residual)
    assert scores['transparent']['range_error_m'] == 2
    assert scores['absolute']['range_error_m'] == 1
    assert scores['residual']['range_error_m'] == 0


@pytest.mark.parametrize('values', [np.empty((0, 15)), np.full((2, 15), np.nan)])
def test_support_rejects_empty_or_nonfinite_input(values):
    with pytest.raises(ValueError):
        support_report(values, np.zeros((1, 15)))


def test_runner_trains_verified_files_without_opening_nonexistent_stress(tmp_path):
    import hashlib
    import json
    import h5py
    from test_optional_data import make_synthetic_episode
    from scripts.run_d4_rollout import run

    entries = []
    for seed, group in [(1, 'train'), (2, 'validation')]:
        path = tmp_path / f'episode{seed}.hdf5'
        make_synthetic_episode(path)
        with h5py.File(path, 'r+') as h5:
            h5.attrs['synthetic_seed'] = seed
        entries.append({'path': str(path), 'filename': path.name, 'split': group,
                        'seed': seed, 'validation_status': 'PASS', 'frame_count': 12,
                        'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    entries.append({'path': str(tmp_path / 'do_not_open.hdf5'), 'split': 'development_stress',
                    'seed': 3, 'validation_status': 'PASS', 'sha256': 'a' * 64})
    manifest = tmp_path / 'split.json'
    manifest.write_text(json.dumps({'episodes': entries}))
    report = run(manifest, tmp_path / 'out', samples=3, rate=1000)
    assert report['train_samples'] == 3
    assert report['validation_samples'] == 3
    assert len(report['provenance']) == 2
    assert report['candidate_support']['scan_reacquire']['action_outside_count'] == 3
    assert (tmp_path / 'out/baseline_residual.npz').is_file()
    with pytest.raises(FileExistsError):
        run(manifest, tmp_path / 'out', samples=3, rate=1000)
    entries[0]['sha256'] = 'b' * 64
    manifest.write_text(json.dumps({'episodes': entries}))
    with pytest.raises(ValueError, match='SHA/size'):
        run(manifest, tmp_path / 'bad_sha', samples=3, rate=1000)
    assert not (tmp_path / 'bad_sha/report.json').exists()
