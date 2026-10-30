import hashlib
import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from test_d5_runner import manifest_fixture


def test_study_freezes_partition_and_runs_all_models_without_stress(tmp_path):
    from scripts.run_rollout_optimization import run
    split, entries = manifest_fixture(tmp_path)
    for entry in entries[:6]:
        path = Path(entry['path'])
        with h5py.File(path, 'r+') as h5:
            h5.create_dataset('action/camera_rotations', data=np.zeros((24, 3)))
        entry.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    split.write_text(json.dumps({'episodes': entries}))
    r = run(split, tmp_path/'out', sizes=(1,), calibration_count=4, alpha=.2, samples=3, rate=1000.)
    assert r['status'] == 'COMPLETED'
    assert len(r['experiments']) == 2
    assert r['protocol']['input_mode'] == 'causal_visible'
    assert r['protocol']['test_or_stress_opened'] == 0
    assert len(r['provenance']) == 6
    for experiment in r['experiments']:
        assert len(experiment['validation']) == 1
        assert experiment['calibration']['selection_data'] == 'calibration_only'
        assert experiment['candidate_diagnostics']['count'] == 24
        assert experiment['candidate_diagnostics']['executed_actions'] == 0
        assert len(experiment['fit_episodes']) == 1
    with pytest.raises(FileExistsError):
        run(split, tmp_path/'out', sizes=(1,), calibration_count=4, alpha=.2, samples=3, rate=1000.)


def test_study_rejects_oversized_learning_curve_before_source_read(tmp_path):
    from scripts.run_rollout_optimization import run
    split, _ = manifest_fixture(tmp_path)
    with pytest.raises(ValueError, match='fit sizes'):
        run(split, tmp_path/'out', sizes=(2,), calibration_count=4, alpha=.2)
    assert not (tmp_path/'out').exists()
