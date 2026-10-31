"""Read-only body profile wired through real client and owner validation."""
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace as NS

import pytest

from scripts import probe_isolated_carla as launcher
from test_body_probe import RPC, World, run, module as body_module
from test_map_probe import load_client_probe
from test_runtime_endpoints import owned_receipt, ports


def test_body_map_profile_rejects_reserved_town_before_files_or_rpc(tmp_path, monkeypatch):
    from jev_uav_track.isolated_runtime import validate_probe_map
    validate_probe_map('Town02', 'body')
    client, counts = load_client_probe(monkeypatch, 'Town03', {})
    with pytest.raises(ValueError, match='API-only'):
        client.main(tmp_path / 'never.json', 'body', tmp_path / 'missing.json', 'takeoff', 'Town03')
    assert counts['connections'] == 0
    with pytest.raises(ValueError, match='API-only'):
        launcher.smoke(tmp_path / 'source', tmp_path / 'owned', Path(sys.executable),
                       'body', 'body', 'takeoff', 'Town03', ports=ports())
    assert not (tmp_path / 'owned').exists()


@pytest.mark.parametrize('failure', [None, 'missing_state'])
def test_client_body_cli_reads_only_raw_bounded_diagnostic(tmp_path, monkeypatch, failure):
    settings = {'ApiServerPort': 42461, 'Vehicles': {'JevDrone': {}}}
    settings_path = tmp_path / 'settings.json'
    settings_path.write_text(json.dumps(settings))
    original = settings_path.read_bytes()
    client, counts = load_client_probe(monkeypatch, 'Town02', settings, carla_port=3200, airsim_port=42461)
    rpc, world = RPC(failure), World()
    carla_factory = client.carla.Client
    def carla_client(ip, port):
        instance = carla_factory(ip, port)
        instance.get_world = lambda: world
        return instance
    client.carla.Client = carla_client
    client.carla.CityObjectLabel = NS(Any=99)
    air_factory = client.airsim.MultirotorClient
    def air_client(**kwargs):
        assert kwargs['timeout_value'] == 1
        instance = air_factory(**dict(kwargs, timeout_value=5))
        instance.client = NS(call=rpc)
        return instance
    client.airsim.MultirotorClient = air_client
    original_probe = body_module().probe_own_body
    monkeypatch.setattr(body_module(), 'probe_own_body', lambda w, call, sdk:
        original_probe(w, call, sdk, clock=rpc.clock, sleep=rpc.clock.sleep))
    output = tmp_path / 'body.json'
    monkeypatch.setattr(sys, 'argv', ['probe_carla_apis.py', '--output', str(output),
        '--expected-settings', str(settings_path), '--expected-map', 'Town02', '--profile', 'body',
        '--carla-rpc-port', '3200', '--carla-streaming-port', '3201',
        '--carla-secondary-port', '3202', '--airsim-rpc-port', '42461'])
    if failure:
        with pytest.raises(SystemExit) as error:
            runpy.run_path(str(Path(launcher.__file__).with_name('probe_carla_apis.py')), run_name='__main__')
        assert error.value.code == 2
    else:
        runpy.run_path(str(Path(launcher.__file__).with_name('probe_carla_apis.py')), run_name='__main__')
    data = json.loads(output.read_text())
    assert data['kind'] == 'LIVE_OWNED_BODY_DIAGNOSTIC'
    assert data['body_probe']['status'] == ('DIAGNOSTIC_ABORTED' if failure else 'DIAGNOSTIC_COMPLETED')
    assert data['body_probe']['control_ready'] is False
    assert counts['observations'] == 0 and 'contract' not in data
    assert settings_path.read_bytes() == original
    assert len(rpc.calls) == (2 if failure else 31)


@pytest.mark.parametrize('failure', [None, 'forged_success', 'wrong_kind', 'client_failure', 'settings'])
def test_body_owner_revalidates_raw_receipt_and_preserves_cleanup(tmp_path, monkeypatch, failure):
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
            assert self.command[self.command.index('--profile') + 1] == 'body'
            assert '--configure-diagnostic-sensors' not in self.command
            self.returncode = 2 if failure == 'client_failure' else 0
            payload = {'map': 'Town02', 'requested_map': 'Town02', 'map_match': True,
                'settings_match': failure != 'settings', 'kind': 'WRONG' if failure == 'wrong_kind' else
                'LIVE_OWNED_BODY_DIAGNOSTIC', 'body_probe': run(RPC('mismatch'))}
            if failure == 'forged_success': payload['body_probe']['samples'][0]['comparison']['status'] = 'READY'
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
    args = (source, task, Path(sys.executable), 'body', 'body', 'takeoff')
    if failure:
        with pytest.raises(SystemExit): launcher.smoke(*args, ports=ports())
    else:
        launcher.smoke(*args, ports=ports())
    result = json.loads((task / 'runs/body/smoke_result.json').read_text())
    assert result['status'] == ('FAILED' if failure else 'BODY_DIAGNOSTIC_COMPLETED')
    assert result['cleanup_confirmed'] and result['protected_unchanged'] and result['map_config_restored']
    assert config.read_bytes() == original
    assert result['body_probe_sha256'] == launcher.sha(Path(launcher.__file__).resolve().parents[1] /
                                                     'src/jev_uav_track/body_probe.py')
    request = json.loads((task / 'runs/body/run_request.json').read_text())
    assert request['kind'] == 'OWN_BODY_DIAGNOSTIC_NO_MOTION'
    assert request['not_final_test'] is True


def test_owner_cli_recognizes_body_and_refuses_town03_before_io(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['probe_isolated_carla.py', 'smoke', '--source', str(tmp_path / 'source'),
        '--task', str(tmp_path / 'owned'), '--profile', 'body', '--map', 'Town03',
        '--client-python', sys.executable, '--run-name', 'body'])
    with pytest.raises(ValueError, match='API-only'):
        runpy.run_path(launcher.__file__, run_name='__main__')
    assert not (tmp_path / 'owned').exists()
