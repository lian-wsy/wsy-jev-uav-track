"""Independent endpoints, receipts and owned-process memory monitoring (no live RPC)."""
import json
import runpy
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_uav_track import isolated_runtime as runtime
from scripts import probe_isolated_carla as launcher
from test_map_probe import load_client_probe


def ports(*values):
    assert hasattr(runtime, 'RuntimePorts'), 'Explicit immutable runtime endpoints are missing'
    return runtime.RuntimePorts(*(values or (3200, 3201, 3202, 42461)))


def test_settings_and_launch_use_independent_endpoints(tmp_path):
    endpoints = ports()
    path = runtime.write_settings(tmp_path, ports=endpoints)
    assert json.loads(path.read_text())['ApiServerPort'] == 42461
    assert (tmp_path / 'package/settings.json').read_bytes() == path.read_bytes()
    assert (tmp_path / 'package' / runtime.BINARY.parent / 'settings.json').read_bytes() == path.read_bytes()
    args = runtime.launch_arguments(tmp_path, ports=endpoints, map_name='Town03')
    assert '-carla-rpc-port=3200' in args
    assert '-carla-streaming-port=3201' in args
    assert '-carla-secondary-port=3202' in args
    with pytest.raises(FrozenInstanceError):
        endpoints.airsim_rpc = 42451


@pytest.mark.parametrize('values', [(3200, 3200, 3202, 42461), (True, 3201, 3202, 42461),
    (3200, 3201.0, 3202, 42461), (1023, 3201, 3202, 42461), (3200, 3201, 3202, 65536)])
def test_invalid_endpoints_fail_before_settings_writes(tmp_path, values):
    with pytest.raises(ValueError):
        runtime.write_settings(tmp_path / 'never-created', ports=ports(*values))
    assert not (tmp_path / 'never-created').exists()


def owned_receipt(tmp_path, frozen_ports):
    source, task = tmp_path / 'source', tmp_path / 'owned'
    binary = task / 'package' / runtime.BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'synthetic executable, never executed')
    receipt = {'source': str(source), 'binary_sha256': launcher.sha(binary)}
    if frozen_ports is not None:
        receipt['ports'] = frozen_ports
    (task / 'prepared.json').write_text(json.dumps(receipt))
    (task / 'protected_before.json').write_text('{}')
    return source, task


@pytest.mark.parametrize('frozen_ports', [None, {'carla_rpc': 3100, 'carla_streaming': 3101,
    'carla_secondary': 3102, 'airsim_rpc': 42451}])
def test_mismatched_or_legacy_receipt_refuses_independent_launch(tmp_path, monkeypatch, frozen_ports):
    endpoints = ports()
    source, task = owned_receipt(tmp_path, frozen_ports)
    def forbidden(*args, **kwargs):
        pytest.fail('Receipt mismatch reached an OS boundary')
    monkeypatch.setattr(launcher.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(launcher, 'ps_json', forbidden)
    with pytest.raises(ValueError, match='[Pp]ort'):
        launcher.smoke(source, task, Path(sys.executable), 'mismatch', 'api', 'takeoff', ports=endpoints)
    assert not (task / 'runs/mismatch/owner.json').exists()


def test_prepare_freezes_ports_in_receipt(tmp_path, monkeypatch):
    endpoints = ports()
    source, task = tmp_path / 'source', tmp_path / 'owned'
    binary = source / runtime.BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'fake executable')
    monkeypatch.setattr(launcher, 'protected_files', lambda _: {})
    monkeypatch.setattr(launcher.shutil, 'disk_usage', lambda _: SimpleNamespace(free=50 * 1024**3))
    def copy(command, **kwargs):
        target = Path(command[2]) / runtime.BINARY
        target.parent.mkdir(parents=True)
        target.write_bytes(binary.read_bytes())
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(launcher.subprocess, 'run', copy)
    launcher.prepare(source, task, ports=endpoints)
    assert json.loads((task / 'prepared.json').read_text())['ports'] == {
        'carla_rpc': 3200, 'carla_streaming': 3201, 'carla_secondary': 3202, 'airsim_rpc': 42461}


def test_snapshot_watches_chosen_and_protected_ports(monkeypatch):
    endpoints = ports()
    def query(command):
        # Assert the OS query contract, keeping real snapshot validation in place.
        listener_clause = command.split('LocalPort -in @(')[1].split(')')[0]
        watched = {int(value) for value in listener_clause.split(',')}
        assert {2000, 2001, 2002, 41451, 3100, 3101, 3102, 42451,
                3200, 3201, 3202, 42461} <= watched
        return {'processes': [], 'listeners': [], 'free_memory_kib': 5 * 1024**2}
    monkeypatch.setattr(launcher, 'ps_json', query)
    assert launcher.snapshot(ports=endpoints)['listeners'] == []


def test_main_client_uses_independent_ports(tmp_path, monkeypatch):
    endpoints = ports()
    settings = {'ApiServerPort': 42461, 'Vehicles': {'JevDrone': {}}}
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(settings))
    module, counts = load_client_probe(monkeypatch, 'Town03', settings, carla_port=3200, airsim_port=42461)
    module.main(tmp_path / 'result.json', 'api', path, 'takeoff', 'Town03', ports=endpoints)
    assert counts['connections'] == 2
    assert json.loads((tmp_path / 'result.json').read_text())['ports']['airsim_rpc'] == 42461


def test_wrong_loaded_settings_fail_before_observation(tmp_path, monkeypatch):
    endpoints = ports()
    expected = {'ApiServerPort': 42461, 'Vehicles': {'JevDrone': {}}}
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(expected))
    module, counts = load_client_probe(monkeypatch, 'Town03', dict(expected, ApiServerPort=42451),
                                       carla_port=3200, airsim_port=42461)
    with pytest.raises(RuntimeError, match='settings'):
        module.main(tmp_path / 'result.json', 'api', path, 'takeoff', 'Town03', ports=endpoints)
    assert counts['observations'] == 0


def test_all_contract_telemetry_connections_use_independent_port(tmp_path, monkeypatch):
    endpoints = ports()
    module, _ = load_client_probe(monkeypatch, 'Town02', {}, carla_port=3200, airsim_port=42461)
    connections = []
    state = SimpleNamespace(timestamp=1000000000, kinematics_estimated=SimpleNamespace(
        position=(0, 0, 0), linear_velocity=(0, 0, 0), orientation=None))
    collision = SimpleNamespace(time_stamp=0, has_collided=False, object_name='', penetration_depth=0,
                                position=(0, 0, 0), normal=(0, 0, 0))
    class Air:
        def getMultirotorState(self, **kwargs): return state
        def simPause(self, value): pass
        def enableApiControl(self, value, **kwargs): pass
        def armDisarm(self, value, **kwargs): return True
        def takeoffAsync(self, **kwargs): return SimpleNamespace(get=lambda: True)
        def moveByVelocityAsync(self, *args, **kwargs): return SimpleNamespace(get=lambda: True)
        def simGetCollisionInfo(self, **kwargs): return collision
        def cancelLastTask(self, **kwargs): pass
        def simListSceneObjects(self): return ['a', 'b', 'c', 'd']
        def simGetObjectPose(self, name):
            i = ord(name) - ord('a')
            return SimpleNamespace(position=(i, i, -i))
        def simGetImages(self, *args, **kwargs):
            return [SimpleNamespace(time_stamp=0, width=0, height=0, camera_position=(0, 0, 0), image_data_uint8=b'')]
    def client(**kwargs):
        connections.append(kwargs['port'])
        assert kwargs['port'] == 42461
        return Air()
    module.airsim.MultirotorClient = client
    module.airsim.to_eularian_angles = lambda _: (0, 0, 0)
    module.airsim.DrivetrainType = SimpleNamespace(MaxDegreeOfFreedom=0)
    module.airsim.YawMode = lambda **kwargs: None
    module.airsim.ImageRequest = lambda *args, **kwargs: None
    module.airsim.ImageType = SimpleNamespace(Scene=0)
    module.carla.CityObjectLabel = SimpleNamespace(Any=0)
    world = SimpleNamespace(get_snapshot=lambda: SimpleNamespace(frame=1,
        timestamp=SimpleNamespace(elapsed_seconds=1)), get_settings=lambda: SimpleNamespace(),
        apply_settings=lambda _: None, tick=lambda _: None,
        get_environment_objects=lambda _: [SimpleNamespace(name=name, transform=SimpleNamespace(
            location=SimpleNamespace(x=i, y=i, z=i))) for i, name in enumerate('abcd')])
    result = {}
    module.contract_probe(world, Air(), tmp_path / 'result.json', result, 'takeoff', ports=endpoints)
    assert result['phase'] == 'completed'
    assert connections == [42461] * 5  # one velocity reader plus four axis readers


@pytest.mark.parametrize('phase', ['waiting', 'complete', 'success', 'legacy'])
def test_client_memory_guard_and_owned_cleanup(tmp_path, monkeypatch, phase):
    endpoints = ports() if phase != 'legacy' else ports(3100, 3101, 3102, 42451)
    frozen = None if phase == 'legacy' else {'carla_rpc': 3200, 'carla_streaming': 3201,
        'carla_secondary': 3202, 'airsim_rpc': 42461}
    source, task = owned_receipt(tmp_path, frozen)
    monkeypatch.setattr(launcher, 'protected_files', lambda _: {})
    launched, assigned, closed = [], [], []
    class Process:
        def __init__(self, command, **kwargs):
            self.pid = 9000 + len(launched)
            self.returncode = None
            self.command = command
            launched.append(self)
        def poll(self): return self.returncode
        def terminate(self): self.returncode = -1
        def wait(self, **kwargs): return self.returncode
        def communicate(self, **kwargs):
            if self.returncode is not None:
                return 'synthetic stdout', 'synthetic stderr'
            if phase == 'waiting':
                raise launcher.subprocess.TimeoutExpired(self.command, kwargs['timeout'])
            self.returncode = 0
            Path(self.command[self.command.index('--output') + 1]).write_text(json.dumps({
                'map': 'Town02', 'map_match': True, 'requested_map': 'Town02'}))
            return 'synthetic stdout', 'synthetic stderr'
    class Job:
        def assign(self, process): assigned.append(process.pid)
        def close(self):
            closed.append(True)
            for process in launched: process.returncode = -1
    monkeypatch.setattr(launcher.subprocess, 'Popen', Process)
    monkeypatch.setattr(launcher, 'OwnedJob', Job)
    samples = [
        {'processes': [], 'listeners': [], 'free_memory_kib': 5 * 1024**2},
        {'processes': [], 'listeners': [{'LocalPort': endpoints.carla_rpc, 'OwningProcess': 9000},
                                       {'LocalPort': endpoints.airsim_rpc, 'OwningProcess': 9000}], 'free_memory_kib': 2 * 1024**2},
        {'processes': [], 'listeners': [], 'free_memory_kib': 2 * 1024**2},
        {'processes': [], 'listeners': [], 'free_memory_kib': 768 * 1024 - 1 if phase in ('waiting', 'complete') else 2 * 1024**2},
        {'processes': [], 'listeners': [], 'free_memory_kib': 2 * 1024**2}]
    def query(command):
        assert str(endpoints.carla_rpc) in command and str(endpoints.airsim_rpc) in command
        return samples.pop(0)
    monkeypatch.setattr(launcher, 'ps_json', query)
    if phase in ('waiting', 'complete'):
        with pytest.raises(SystemExit):
            launcher.smoke(source, task, Path(sys.executable), 'memory', 'api', 'takeoff', ports=endpoints)
    else:
        launcher.smoke(source, task, Path(sys.executable), 'memory', 'api', 'takeoff', ports=endpoints)
    result = json.loads((task / 'runs/memory/smoke_result.json').read_text())
    if phase in ('waiting', 'complete'):
        assert '768 MiB' in result['error']
        assert result['status'] == 'FAILED'
        assert json.loads((task / 'runs/memory/last_runtime_snapshot.json').read_text())['free_memory_kib'] == 768 * 1024 - 1
    else:
        assert result['status'] == 'API_SMOKE_COMPLETED'
    assert result['cleanup_confirmed'] is True
    assert assigned == [9000, 9001] and closed == [True]
    assert all(process.returncode == -1 for process in launched)
    assert (task / 'runs/memory/client_stdout.txt').read_text() == 'synthetic stdout'
    assert (task / 'runs/memory/client_stderr.txt').read_text() == 'synthetic stderr'
    command = launched[1].command
    for flag, value in [('--carla-rpc-port', str(endpoints.carla_rpc)),
                        ('--carla-streaming-port', str(endpoints.carla_streaming)),
                        ('--carla-secondary-port', str(endpoints.carla_secondary)),
                        ('--airsim-rpc-port', str(endpoints.airsim_rpc))]:
        assert command[command.index(flag) + 1] == value


@pytest.mark.parametrize('memory', [float('nan'), float('inf'), True, None])
def test_unknown_memory_cannot_pass_snapshot_validation(monkeypatch, memory):
    monkeypatch.setattr(launcher, 'ps_json', lambda _: {
        'processes': [], 'listeners': [], 'free_memory_kib': memory})
    with pytest.raises(ValueError, match='snapshot'):
        launcher.snapshot(ports=ports())


def test_client_cli_parses_all_four_explicit_endpoints(tmp_path, monkeypatch):
    settings = {'ApiServerPort': 42461, 'Vehicles': {'JevDrone': {}}}
    settings_path = tmp_path / 'settings.json'
    settings_path.write_text(json.dumps(settings))
    load_client_probe(monkeypatch, 'Town03', settings, carla_port=3200, airsim_port=42461)
    output = tmp_path / 'api.json'
    monkeypatch.setattr(sys, 'argv', ['probe_carla_apis.py', '--output', str(output),
        '--expected-settings', str(settings_path), '--expected-map', 'Town03',
        '--carla-rpc-port', '3200', '--carla-streaming-port', '3201',
        '--carla-secondary-port', '3202', '--airsim-rpc-port', '42461'])
    runpy.run_path(str(Path(launcher.__file__).with_name('probe_carla_apis.py')), run_name='__main__')
    assert json.loads(output.read_text())['ports'] == {
        'carla_rpc': 3200, 'carla_streaming': 3201, 'carla_secondary': 3202, 'airsim_rpc': 42461}


def test_prepare_cli_parses_and_freezes_all_four_explicit_endpoints(tmp_path, monkeypatch):
    source, task = tmp_path / 'source', tmp_path / 'owned'
    binary = source / runtime.BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'fake executable')
    monkeypatch.setattr(launcher.shutil, 'disk_usage', lambda _: SimpleNamespace(free=50 * 1024**3))
    def os_run(command, **kwargs):
        if command[0] == 'robocopy':
            target = Path(command[2]) / runtime.BINARY
            target.parent.mkdir(parents=True)
            target.write_bytes(binary.read_bytes())
            return SimpleNamespace(returncode=1)
        assert command[0] == 'powershell.exe'
        return SimpleNamespace(stdout=json.dumps(str(tmp_path / 'documents')))
    monkeypatch.setattr(launcher.subprocess, 'run', os_run)
    monkeypatch.setattr(sys, 'argv', ['probe_isolated_carla.py', 'prepare', '--source', str(source),
        '--task', str(task), '--carla-rpc-port', '3200', '--carla-streaming-port', '3201',
        '--carla-secondary-port', '3202', '--airsim-rpc-port', '42461'])
    runpy.run_path(launcher.__file__, run_name='__main__')
    assert json.loads((task / 'prepared.json').read_text())['ports'] == {
        'carla_rpc': 3200, 'carla_streaming': 3201, 'carla_secondary': 3202, 'airsim_rpc': 42461}
