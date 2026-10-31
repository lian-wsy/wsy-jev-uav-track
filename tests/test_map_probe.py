"""Town03 API-only probe contract; no simulator or network in these tests."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_launcher_accepts_only_explicit_map_and_preserves_isolation(tmp_path):
    from jev_uav_track.isolated_runtime import launch_arguments
    task = tmp_path / 'owned'
    args = launch_arguments(task, task / 'runs/api01', map_name='Town03')
    # The direct Shipping executable needs the project token before the URL.
    # The package's root bootstrap executable is not used by this owner.
    assert args[1:3] == ['CarlaUE4', '/Game/Carla/Maps/Town03']
    assert '-carla-rpc-port=3100' in args
    assert args[-2:] == ['--settings', str(task / 'settings.json')]
    assert launch_arguments(task)[1:3] == ['CarlaUE4', '/Game/Carla/Maps/Town02']


@pytest.mark.parametrize('map_name', ['Town3', 'Town03_Opt', 'Town03?listen', '../Town03',
                                     '/Game/Carla/Maps/Town03', '', 'Town04'])
def test_launcher_rejects_unapproved_map_or_extra_url_options(tmp_path, map_name):
    from jev_uav_track.isolated_runtime import launch_arguments
    with pytest.raises(ValueError, match='map'):
        launch_arguments(tmp_path, map_name=map_name)


def load_client_probe(monkeypatch, world_name, settings, *, carla_port=3100, airsim_port=42451):
    """Strict fake RPC surface: any motion/reset/image call raises AttributeError."""
    counts = {'connections': 0, 'observations': 0}
    world = SimpleNamespace(
        get_map=lambda: SimpleNamespace(name=world_name),
        get_snapshot=lambda: SimpleNamespace(frame=1, timestamp=SimpleNamespace(elapsed_seconds=1.0)))

    def carla_client(ip, port):
        assert (ip, port) == ('127.0.0.1', carla_port)
        counts['connections'] += 1
        return SimpleNamespace(set_timeout=lambda _: None, get_world=lambda: world,
                               get_server_version=lambda: 'fake', get_client_version=lambda: 'fake')

    def state(*, vehicle_name):
        assert vehicle_name == 'JevDrone'
        counts['observations'] += 1
        return SimpleNamespace(timestamp=1000000000,
            kinematics_estimated=SimpleNamespace(position=(0, 0, 0)))

    def air_client(*, ip, port, timeout_value):
        assert (ip, port, timeout_value) == ('127.0.0.1', airsim_port, 5)
        counts['connections'] += 1
        return SimpleNamespace(ping=lambda: True, getSettingsString=lambda: json.dumps(settings),
            getMultirotorState=state, getServerVersion=lambda: 1, listVehicles=lambda: ['JevDrone'])

    monkeypatch.setitem(sys.modules, 'carla', SimpleNamespace(Client=carla_client))
    monkeypatch.setitem(sys.modules, 'airsim', SimpleNamespace(MultirotorClient=air_client))
    path = Path(__file__).resolve().parents[1] / 'scripts/probe_carla_apis.py'
    spec = importlib.util.spec_from_file_location('map_client_probe_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    return module, counts


@pytest.fixture
def settings_path(tmp_path):
    settings = {'ApiServerPort': 42451, 'Vehicles': {'JevDrone': {}}}
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(settings))
    return path, settings


@pytest.mark.parametrize('world_name', ['/Game/Carla/Maps/Town02', 'Town030',
                                      '/Game/Carla/Maps/Town03_Opt'])
def test_client_rejects_map_mismatch_before_sampling(tmp_path, monkeypatch, settings_path, world_name):
    path, settings = settings_path
    module, counts = load_client_probe(monkeypatch, world_name, settings)
    with pytest.raises(ValueError, match='map'):
        module.main(tmp_path / 'result.json', 'api', path, 'takeoff', expected_map='Town03')
    assert counts['observations'] == 0
    assert not (tmp_path / 'result.json').exists()


@pytest.mark.parametrize('world_name', ['Town03', '/Game/Carla/Maps/Town03', 'Carla/Maps/Town03'])
def test_town03_api_probe_records_verified_map_without_motion_surface(tmp_path, monkeypatch, settings_path, world_name):
    path, settings = settings_path
    module, counts = load_client_probe(monkeypatch, world_name, settings)
    output = tmp_path / 'result.json'
    module.main(output, 'api', path, 'takeoff', expected_map='Town03')
    result = json.loads(output.read_text())
    assert result['map'] == world_name
    assert result['requested_map'] == 'Town03'
    assert result['map_match'] is True
    assert counts['observations'] == 12
    assert 'contract' not in result


@pytest.mark.parametrize('profile', ['contract', 'observation', 'belief'])
def test_reserved_map_rejects_motion_profiles_before_rpc(tmp_path, monkeypatch, settings_path, profile):
    path, settings = settings_path
    module, counts = load_client_probe(monkeypatch, '/Game/Carla/Maps/Town03', settings)
    with pytest.raises(ValueError, match='API-only'):
        module.main(tmp_path / 'result.json', profile, path, 'velocity', expected_map='Town03')
    assert counts['connections'] == 0


def test_launcher_rejects_town03_motion_profile_before_creating_run(tmp_path):
    from scripts.probe_isolated_carla import smoke
    task = tmp_path / 'owned'
    with pytest.raises(ValueError, match='API-only'):
        smoke(tmp_path / 'source', task, Path(sys.executable), 'bad', 'belief', 'velocity', map_name='Town03')
    assert not (task / 'runs').exists()


def test_low_memory_keeps_map_request_evidence_without_starting_process(tmp_path, monkeypatch):
    from scripts import probe_isolated_carla as launcher
    from jev_uav_track.isolated_runtime import BINARY
    source, task = tmp_path / 'source', tmp_path / 'owned'
    binary = task / 'package' / BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'synthetic executable, never run')
    (task / 'prepared.json').write_text(json.dumps({'source': str(source),
                                                   'binary_sha256': launcher.sha(binary)}))
    (task / 'protected_before.json').write_text('{}')
    monkeypatch.setattr(launcher, 'protected_files', lambda _: {})
    monkeypatch.setattr(launcher, 'snapshot', lambda **kwargs: {
        'processes': [], 'listeners': [], 'free_memory_kib': 4 * 1024**2 - 1})

    def forbidden_launch(*args, **kwargs):
        pytest.fail('Low-memory preflight must never reach process creation')

    monkeypatch.setattr(launcher.subprocess, 'Popen', forbidden_launch)
    with pytest.raises(ValueError, match='Less than 4 GiB'):
        launcher.smoke(source, task, Path(sys.executable), 'low_memory', 'api', 'takeoff', map_name='Town03')
    request = json.loads((task / 'runs/low_memory/run_request.json').read_text())
    assert request['requested_map'] == 'Town03'
    assert request['kind'] == 'API_INFRASTRUCTURE_ONLY'
    assert request['not_final_test'] is True
    assert (task / 'runs/low_memory/preflight.json').exists()
    assert not (task / 'runs/low_memory/owner.json').exists()
