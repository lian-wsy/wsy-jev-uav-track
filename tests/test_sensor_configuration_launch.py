"""Settings opt-in crosses real launcher/client code; only OS/RPC edges are fake."""
import inspect
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from jev_uav_track.isolated_runtime import write_settings
from scripts import probe_isolated_carla as launcher
from test_runtime_endpoints import owned_receipt, ports
from test_map_probe import load_client_probe
from test_sensor_configuration import RELATIVE


def has_opt_in(function):
    assert 'configure_diagnostic_sensors' in inspect.signature(function).parameters, 'Explicit sensor opt-in missing'


@pytest.mark.parametrize('profile,map_name', [('api', 'Town02'), ('contract', 'Town02'),
    ('belief', 'Town02'), ('safety', 'Town03')])
def test_sensor_opt_in_rejects_other_profiles_before_any_process_or_file(tmp_path, profile, map_name):
    has_opt_in(launcher.smoke)
    with pytest.raises(ValueError):
        launcher.smoke(tmp_path / 'source', tmp_path / 'owned', Path(sys.executable),
            'no', profile, 'takeoff', map_name, ports=ports(), configure_diagnostic_sensors=True)
    assert not (tmp_path / 'owned').exists()


@pytest.mark.parametrize('phase,capture_geometry', [(phase, capture) for phase in (
    'success', 'launch_failure', 'client_failure', 'not_stopped', 'wrong_names',
    'settings_unverified', 'low_memory') for capture in (False, True)] + [
        ('missing_geometry_receipt', True), ('wrong_geometry_schema', True)])
def test_opt_in_settings_lifecycle_and_failed_cleanup_are_audited(tmp_path, monkeypatch, phase, capture_geometry):
    has_opt_in(launcher.smoke)
    assert 'capture_geometry' in inspect.signature(launcher.smoke).parameters
    source, task = owned_receipt(tmp_path, {'carla_rpc': 3200, 'carla_streaming': 3201,
        'carla_secondary': 3202, 'airsim_rpc': 42461})
    write_settings(task, ports=ports())
    paths = [task / relative for relative in RELATIVE]
    before = [p.read_bytes() for p in paths]
    processes = []
    class Process:
        def __init__(self, command, **kwargs):
            assert all('JevDistanceFront' in p.read_text() for p in paths)
            assert all((task / 'runs/sensors' / f'sensor_settings_before_{i}.json').read_bytes() == before[i]
                       for i in range(3))
            if phase == 'launch_failure': raise OSError('injected launch failure')
            self.pid, self.returncode, self.command = 8000 + len(processes), None, command
            processes.append(self)
        def poll(self): return self.returncode
        def terminate(self):
            if phase == 'not_stopped' and self.pid == 8000: raise OSError('injected running process')
            self.returncode = -1
        def wait(self, **kwargs):
            if self.returncode is None: raise TimeoutError('injected running process')
            return self.returncode
        def communicate(self, **kwargs):
            assert '--configure-diagnostic-sensors' in self.command
            assert ('--capture-sensor-geometry' in self.command) == capture_geometry
            self.returncode = 1 if phase == 'client_failure' else 0
            payload = {'map': 'Town02', 'requested_map': 'Town02', 'map_match': True,
                'settings_match': phase != 'settings_unverified',
                'kind': 'LIVE_OWNED_SAFETY_SENSOR_DIAGNOSTIC', 'safety_probe': {
                    'kind': 'SAFETY_SENSOR_DIAGNOSTIC_NO_MOTION', 'status': 'DIAGNOSTIC_COMPLETED',
                    'control_ready': False, 'clearance_m': None,
                    'capture_geometry': capture_geometry and phase != 'missing_geometry_receipt',
                    'geometry_schema_version': 0 if phase == 'wrong_geometry_schema' else 1,
                    'sensor_names': {'distance': '' if phase == 'wrong_names' else 'JevDistanceFront',
                                     'lidar': 'JevLidarDiagnostic'}}}
            Path(self.command[self.command.index('--output') + 1]).write_text(json.dumps(payload))
            return 'synthetic diagnostic', ''
    class Job:
        def assign(self, process): pass
        def close(self):
            if phase == 'not_stopped': raise OSError('injected close failure')
            for process in processes: process.returncode = -1
    calls = []
    def snapshot(**kwargs):
        calls.append(True)
        return {'processes': [], 'listeners': [
            {'LocalPort': port, 'OwningProcess': 8000} for port in (3200, 42461)
        ] if len(calls) == 2 and phase != 'launch_failure' else [],
            'free_memory_kib': 4 * 1024**2 - 1 if phase == 'low_memory' else 5 * 1024**2}
    monkeypatch.setattr(launcher, 'protected_files', lambda _: {})
    monkeypatch.setattr(launcher, 'snapshot', snapshot)
    monkeypatch.setattr(launcher, 'OwnedJob', Job)
    monkeypatch.setattr(launcher.subprocess, 'Popen', Process)
    arguments = (source, task, Path(sys.executable), 'sensors', 'safety', 'takeoff')
    if phase == 'success':
        launcher.smoke(*arguments, ports=ports(), configure_diagnostic_sensors=True,
                       capture_geometry=capture_geometry)
    else:
        with pytest.raises(ValueError if phase == 'low_memory' else SystemExit):
            launcher.smoke(*arguments, ports=ports(), configure_diagnostic_sensors=True,
                           capture_geometry=capture_geometry)
    if phase == 'low_memory':
        assert not processes
        assert [p.read_bytes() for p in paths] == before
        assert not (task / 'runs/sensors/sensor_config_prepared.json').exists()
        return
    result = json.loads((task / 'runs/sensors/smoke_result.json').read_text())
    if phase == 'not_stopped':
        assert result['status'] == 'AUDIT_FAILED'
        assert result['sensor_config_restored'] is False
        assert all(p.read_bytes() != b for p, b in zip(paths, before))
    else:
        assert result['sensor_config_restored'] is True
        assert [p.read_bytes() for p in paths] == before
        assert result['status'] == ('SAFETY_DIAGNOSTIC_COMPLETED' if phase == 'success' else 'FAILED')
    assert result['configure_diagnostic_sensors'] is True
    assert result['capture_geometry'] is capture_geometry
    assert result['sensor_configuration']['control_ready'] is False


def test_client_opt_in_uses_named_raw_queries_and_checks_configuration(tmp_path, monkeypatch):
    from jev_uav_track.sensor_configuration import diagnostic_sensors
    settings = {'ApiServerPort': 42461, 'Vehicles': {'JevDrone': {'Sensors': diagnostic_sensors()}}}
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(settings))
    module, _ = load_client_probe(monkeypatch, 'Town02', settings, carla_port=3200, airsim_port=42461)
    has_opt_in(module.main)
    calls = []
    def raw(method, *args):
        calls.append((method, args))
        if method == 'getMultirotorState': return {'timestamp': 1_000_000_000 + len(calls) * 1_000_000}
        if method == 'getDistanceSensorData': assert args == ('JevDistanceFront', 'JevDrone')
        elif method == 'getLidarData': assert args == ('JevLidarDiagnostic', 'JevDrone')
        else: assert method == 'simGetCollisionInfo'
        raise RuntimeError('synthetic missing return')
    original = module.airsim.MultirotorClient
    def client(**kwargs):
        assert kwargs['timeout_value'] == 1
        air = original(**dict(kwargs, timeout_value=5))
        air.client = SimpleNamespace(call=raw)
        return air
    module.airsim.MultirotorClient = client
    output = tmp_path / 'result.json'
    module.main(output, 'safety', path, 'takeoff', 'Town02', ports=ports(), configure_diagnostic_sensors=True)
    result = json.loads(output.read_text())
    assert result['safety_probe']['sensor_names'] == {'distance': 'JevDistanceFront', 'lidar': 'JevLidarDiagnostic'}
    assert [(method, args) for method, args in calls if method in ('getDistanceSensorData', 'getLidarData')] == [
        ('getDistanceSensorData', ('JevDistanceFront', 'JevDrone')),
        ('getLidarData', ('JevLidarDiagnostic', 'JevDrone'))]
    assert 'contract' not in result
    # Unknown configuration is not silently accepted or converted to default-name RPCs.
    settings['Vehicles']['JevDrone']['Sensors']['JevLidarDiagnostic']['PointsPerSecond'] = 400000
    path.write_text(json.dumps(settings))
    count = len(calls)
    with pytest.raises(ValueError, match='diagnostic sensors'):
        module.main(output, 'safety', path, 'takeoff', 'Town02', ports=ports(), configure_diagnostic_sensors=True)
    assert len(calls) == count


@pytest.mark.parametrize('profile,map_name,configured,enabled', [
    ('safety', 'Town02', False, True), ('safety', 'Town03', True, True),
    ('api', 'Town02', True, True), ('safety', 'Town02', True, 1)])
def test_geometry_scope_rejects_before_any_files_or_connections(tmp_path, monkeypatch,
                                                            profile, map_name, configured, enabled):
    assert 'capture_geometry' in inspect.signature(launcher.smoke).parameters
    module, counts = load_client_probe(monkeypatch, map_name, {})
    assert 'capture_geometry' in inspect.signature(module.main).parameters
    with pytest.raises(ValueError):
        launcher.smoke(tmp_path / 'source', tmp_path / 'owned', Path(sys.executable),
            'no', profile, 'takeoff', map_name, ports=ports(),
            configure_diagnostic_sensors=configured, capture_geometry=enabled)
    with pytest.raises(ValueError):
        module.main(tmp_path / 'result.json', profile, tmp_path / 'missing.json', 'takeoff',
            map_name, ports=ports(), configure_diagnostic_sensors=configured, capture_geometry=enabled)
    assert not (tmp_path / 'owned').exists()
    assert counts['connections'] == 0


def test_client_archives_geometry_but_stdout_is_a_small_receipt(tmp_path, monkeypatch, capsys):
    from jev_uav_track.sensor_configuration import diagnostic_sensors
    from test_sensor_geometry import geometry_rpc
    settings = {'ApiServerPort': 42461, 'Vehicles': {'JevDrone': {'Sensors': diagnostic_sensors()}}}
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(settings))
    module, _ = load_client_probe(monkeypatch, 'Town02', settings, carla_port=3200, airsim_port=42461)
    assert 'capture_geometry' in inspect.signature(module.main).parameters
    rpc = geometry_rpc()
    original = module.airsim.MultirotorClient
    def client(**kwargs):
        assert kwargs['timeout_value'] == 1
        air = original(**dict(kwargs, timeout_value=5))
        def raw(method, *args):
            if method in ('getDistanceSensorData', 'getLidarData'):
                expected = 'JevDistanceFront' if method == 'getDistanceSensorData' else 'JevLidarDiagnostic'
                assert args == (expected, 'JevDrone')
                return rpc(method, '', 'JevDrone')
            return rpc(method, *args)
        air.client = SimpleNamespace(call=raw)
        return air
    module.airsim.MultirotorClient = client
    output = tmp_path / 'result.json'
    module.main(output, 'safety', path, 'takeoff', 'Town02', ports=ports(),
                configure_diagnostic_sensors=True, capture_geometry=True)
    result = json.loads(output.read_text())
    rows = result['safety_probe']['queries']
    assert len(rows) == 30
    assert len([row for row in rows if row['geometry'] is not None]) == 24
    stdout = capsys.readouterr().out
    assert len(stdout) < 4096 and 'point_cloud' not in stdout
    assert json.loads(stdout)['safety_probe']['rpc_count'] == 30
    assert result['safety_probe']['control_ready'] is False
    assert 'contract' not in result
