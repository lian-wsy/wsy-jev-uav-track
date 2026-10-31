"""Owner integration: validate receipt, restore config, terminate own handles."""
import json
from pathlib import Path
import sys

import pytest

from scripts import probe_isolated_carla as launcher
from test_obstacle_probe import report
from test_runtime_endpoints import owned_receipt, ports


@pytest.mark.parametrize('failure', [None, 'forged_success', 'wrong_kind', 'client_failure'])
def test_owner_accepts_only_raw_verified_sweep_matrix(tmp_path, monkeypatch, failure):
    source, task = owned_receipt(tmp_path, {'carla_rpc': 3200, 'carla_streaming': 3201,
        'carla_secondary': 3202, 'airsim_rpc': 42461})
    processes = []
    config = task / 'package/CarlaUE4/Config/DefaultEngine.ini'
    original = config.read_bytes()
    class Process:
        def __init__(self, command, **kwargs):
            self.command, self.returncode, self.pid = command, None, 8000 + len(processes)
            processes.append(self)
        def poll(self): return self.returncode
        def terminate(self): self.returncode = -1
        def wait(self, **kwargs): return self.returncode
        def communicate(self, **kwargs):
            assert self.command[self.command.index('--profile') + 1] == 'sweep'
            assert '--configure-diagnostic-sensors' not in self.command
            assert '--capture-sensor-geometry' not in self.command
            self.returncode = 2 if failure == 'client_failure' else 0
            payload = {'map': 'Town02', 'requested_map': 'Town02', 'map_match': True,
                'settings_match': True, 'kind': 'WRONG' if failure == 'wrong_kind' else
                'LIVE_OWNED_OBSTACLE_SWEEP_FIXTURE', 'sweep_probe': report()}
            if failure == 'forged_success': payload['sweep_probe']['cases'][2]['events'] = []
            Path(self.command[self.command.index('--output') + 1]).write_text(json.dumps(payload))
            return 'synthetic fixture summary', ''
    class Job:
        def assign(self, process): pass
        def close(self):
            for process in processes: process.returncode = -1
    calls = []
    def snapshot(**kwargs):
        calls.append(True)
        return {'processes': [], 'listeners': [
            {'LocalPort': port, 'OwningProcess': 8000} for port in (3200, 42461)
        ] if len(calls) == 2 else [], 'free_memory_kib': 5 * 1024**2}
    monkeypatch.setattr(launcher, 'protected_files', lambda _: {})
    monkeypatch.setattr(launcher, 'snapshot', snapshot)
    monkeypatch.setattr(launcher, 'OwnedJob', Job)
    monkeypatch.setattr(launcher.subprocess, 'Popen', Process)
    args = (source, task, Path(sys.executable), 'sweep', 'sweep', 'takeoff')
    if failure:
        with pytest.raises(SystemExit): launcher.smoke(*args, ports=ports())
    else:
        launcher.smoke(*args, ports=ports())
    result = json.loads((task / 'runs/sweep/smoke_result.json').read_text())
    assert result['status'] == ('FAILED' if failure else 'SWEEP_FIXTURE_COMPLETED')
    assert result['cleanup_confirmed'] and result['protected_unchanged'] and result['map_config_restored']
    assert config.read_bytes() == original
    assert result['obstacle_probe_sha256'] == launcher.sha(Path(launcher.__file__).resolve().parents[1] /
                                                         'src/jev_uav_track/obstacle_probe.py')
    request = json.loads((task / 'runs/sweep/run_request.json').read_text())
    assert request['kind'] == 'OBSTACLE_SWEEP_FIXTURE_NO_FLIGHT'
    assert request['not_final_test'] is True
