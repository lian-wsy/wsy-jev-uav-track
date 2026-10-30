import hashlib
import json

import h5py
import pytest

from test_optional_data import make_synthetic_episode


def manifest_fixture(tmp_path):
    entries = []
    for seed in range(1, 7):
        path = tmp_path / f'episode{seed}.hdf5'
        make_synthetic_episode(path)
        with h5py.File(path, 'r+') as h5:
            h5.attrs['synthetic_seed'] = seed
        entries.append({'path': str(path), 'filename': path.name,
            'split': 'train' if seed <= 5 else 'validation', 'seed': seed,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes': path.stat().st_size, 'frame_count': 12, 'validation_status': 'PASS'})
    entries.append({'path': str(tmp_path / 'do_not_open.hdf5'), 'split': 'development_stress',
        'seed': 7, 'sha256': 'a'*64, 'validation_status': 'PASS'})
    path = tmp_path / 'split.json'
    path.write_text(json.dumps({'episodes': entries}))
    return path, entries


def test_d5_runner_heldout_calibration_and_no_stress_read(tmp_path):
    from scripts.run_d5_calibration import run
    split, _ = manifest_fixture(tmp_path)
    report = run(split, tmp_path / 'out', samples=3, rate=1000, calibration_count=4, alpha=.2, input_mode='oracle')
    assert report['status'] == 'COMPLETED'
    groups = report['partition']
    assert len(groups['fit']) == 1 and len(groups['calibration']) == 4
    assert not set(groups['fit']) & set(groups['calibration'])
    assert len(report['provenance']) == 6
    assert report['test_or_stress_opened'] == 0
    assert report['validation_samples'] == 3
    assert report['validation_strata']['occluded']['status'] == 'NOT_EVALUATED'
    assert report['validation_strata']['out_of_frame']['count'] == 0
    assert report['action_diagnostics']['no_safe_candidate_frames'] == 3
    assert report['action_diagnostics']['real_jev_requests'] == 0
    assert report['action_diagnostics']['safe_selection_frames'] == 0
    assert (tmp_path / 'out/records.jsonl').is_file()
    with pytest.raises(FileExistsError):
        run(split, tmp_path / 'out', samples=3, rate=1000, calibration_count=4, alpha=.2)


def test_partition_uses_only_frozen_train_and_is_order_independent(tmp_path):
    from scripts.run_d5_calibration import partition_entries
    _, entries = manifest_fixture(tmp_path)
    a = partition_entries(entries, 4)
    b = partition_entries(list(reversed(entries)), 4)
    assert a == b
    assert all(e['split'] == 'train' for e in a['fit'] + a['calibration'])
    with pytest.raises(ValueError):
        partition_entries(entries, 5)
    entries[0]['town'] = 'Town03'
    with pytest.raises(ValueError, match='Town03'):
        partition_entries(entries, 4)


def test_bad_sha_does_not_produce_completed_report(tmp_path):
    from scripts.run_d5_calibration import run
    split, entries = manifest_fixture(tmp_path)
    entries[0]['sha256'] = 'b'*64
    split.write_text(json.dumps({'episodes': entries}))
    with pytest.raises(ValueError, match='SHA/size'):
        run(split, tmp_path / 'out', samples=3, rate=1000, calibration_count=4, alpha=.2)
    assert not (tmp_path / 'out/report.json').exists()


def test_missing_visibility_annotations_report_unknown_not_visible(tmp_path):
    from scripts.run_d5_calibration import run
    split, entries = manifest_fixture(tmp_path)
    e = entries[5]
    with h5py.File(e['path'], 'r+') as h5:
        del h5['visibility/target_occluded']
    from pathlib import Path
    p = Path(e['path'])
    e.update(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    split.write_text(json.dumps({'episodes': entries}))
    report = run(split, tmp_path / 'out', samples=3, rate=1000, calibration_count=4, alpha=.2, input_mode='oracle')
    assert report['validation_strata']['unknown']['count'] == 3
    assert report['validation_strata']['visible']['status'] == 'NOT_EVALUATED'
