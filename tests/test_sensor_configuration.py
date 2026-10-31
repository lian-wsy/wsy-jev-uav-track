"""Real filesystem tests for temporary owned sensor settings; never launches UE4."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from jev_uav_track.isolated_runtime import RuntimePorts, write_settings


PORTS = RuntimePorts(3200, 3201, 3202, 42461)
RELATIVE = ('settings.json', 'package/settings.json',
            'package/CarlaUE4/Binaries/Win64/settings.json')


def owned(tmp_path):
    source, task = tmp_path / 'source', tmp_path / 'owned'
    source.mkdir()
    write_settings(task, ports=PORTS)
    output = task / 'runs/sensors01'
    output.mkdir(parents=True)
    paths = [task / relative for relative in RELATIVE]
    return source, task, output, paths, [path.read_bytes() for path in paths]


def transaction(source, task, output, **kwargs):
    name = 'jev_uav_track.sensor_configuration'
    assert importlib.util.find_spec(name) is not None, 'Reversible sensor configuration is missing'
    module = importlib.import_module(name)
    return module.OwnedSensorConfiguration(source, task, output, ports=PORTS,
        map_name=kwargs.pop('map_name', 'Town02'), profile=kwargs.pop('profile', 'safety'), **kwargs)


def test_three_copies_restore_exact_bytes_and_only_sensor_block_changes(tmp_path):
    source, task, output, paths, before = owned(tmp_path)
    config = transaction(source, task, output)
    config.apply()
    assert len(list(output.glob('sensor_settings_before_*.json'))) == 3
    for i, path in enumerate(paths):
        assert (output / f'sensor_settings_before_{i}.json').read_bytes() == before[i]
        settings = json.loads(path.read_bytes())
        sensors = settings['Vehicles']['JevDrone'].pop('Sensors')
        assert settings == json.loads(before[i])
        assert {name: sensors[name] for name in ('barometer', 'imu', 'gps', 'magnetometer')} == {
            'barometer': {'SensorType': 1, 'Enabled': True},
            'imu': {'SensorType': 2, 'Enabled': True},
            'gps': {'SensorType': 3, 'Enabled': True},
            'magnetometer': {'SensorType': 4, 'Enabled': True}}
        assert sensors['JevDistanceFront'] == {'SensorType': 5, 'Enabled': True,
            'MinDistance': 0.2, 'MaxDistance': 40, 'X': 0, 'Y': 0, 'Z': -0.5,
            'Roll': 0, 'Pitch': 0, 'Yaw': 0, 'DrawDebugPoints': False, 'ExternalController': False}
        assert sensors['JevLidarDiagnostic'] == {'SensorType': 6, 'Enabled': True,
            'NumberOfChannels': 4, 'PointsPerSecond': 4000, 'RotationsPerSecond': 10,
            'Range': 40, 'X': 0, 'Y': 0, 'Z': -0.5, 'Roll': 0, 'Pitch': 0, 'Yaw': 0,
            'VerticalFOVUpper': 10, 'VerticalFOVLower': -30,
            'HorizontalFOVStart': 0, 'HorizontalFOVEnd': 359,
            'DataFrame': 'SensorLocalFrame', 'DrawDebugPoints': False, 'ExternalController': False}
    assert len({p.read_bytes() for p in paths}) == 1
    with pytest.raises(ValueError, match='once'):
        config.apply()
    config.restore(owned_stopped=True)
    assert [p.read_bytes() for p in paths] == before
    assert config.audit['restored'] is True
    assert json.loads((output / 'sensor_config_restored.json').read_bytes())['restored'] is True


@pytest.mark.parametrize('profile,map_name', [('api', 'Town02'), ('contract', 'Town02'),
    ('observation', 'Town02'), ('belief', 'Town02'), ('safety', 'Town03')])
def test_opt_in_cannot_escape_no_motion_development_scope(tmp_path, profile, map_name):
    source, task, output, paths, before = owned(tmp_path)
    with pytest.raises(ValueError):
        transaction(source, task, output, profile=profile, map_name=map_name)
    assert [p.read_bytes() for p in paths] == before
    assert list(output.iterdir()) == []


def test_disagreeing_originals_fail_without_any_settings_change(tmp_path):
    source, task, output, paths, before = owned(tmp_path)
    paths[1].write_bytes(before[1] + b'\n')
    actual = [p.read_bytes() for p in paths]
    with pytest.raises(ValueError, match='identical'):
        transaction(source, task, output).apply()
    assert [p.read_bytes() for p in paths] == actual
    assert list(output.iterdir()) == []


@pytest.mark.parametrize('change', ['vehicle_sensors', 'default_sensors', 'vehicle', 'port', 'duplicate', 'nonfinite'])
def test_unsupported_original_settings_are_not_rewritten(tmp_path, change):
    source, task, output, paths, before = owned(tmp_path)
    data = json.loads(before[0])
    if change == 'vehicle_sensors': data['Vehicles']['JevDrone']['Sensors'] = {}
    if change == 'default_sensors': data['DefaultSensors'] = {}
    if change == 'vehicle': data['Vehicles']['JevDrone']['VehicleType'] = 'PX4Multirotor'
    if change == 'port': data['ApiServerPort'] = 41451
    if change == 'nonfinite': data['ClockSpeed'] = float('nan')
    payload = json.dumps(data).encode()
    if change == 'duplicate': payload = b'{"SimMode":"Car",' + payload[1:]
    for path in paths: path.write_bytes(payload)
    with pytest.raises(ValueError):
        transaction(source, task, output).apply()
    assert all(p.read_bytes() == payload for p in paths)


@pytest.mark.parametrize('existing', ['sensor_settings_before_1.json', 'sensor_config_prepared.json'])
def test_existing_backup_or_receipt_is_never_overwritten(tmp_path, existing):
    source, task, output, paths, before = owned(tmp_path)
    (output / existing).write_bytes(b'keep other evidence')
    with pytest.raises((FileExistsError, ValueError)):
        transaction(source, task, output).apply()
    assert (output / existing).read_bytes() == b'keep other evidence'
    assert [p.read_bytes() for p in paths] == before


def test_settings_hardlink_cannot_modify_shared_source(tmp_path):
    source, task, output, paths, before = owned(tmp_path)
    external = source / 'settings.json'
    external.write_bytes(before[0])
    paths[0].unlink()
    os.link(external, paths[0])
    with pytest.raises(ValueError, match='hardlink'):
        transaction(source, task, output).apply()
    assert external.read_bytes() == before[0]
    assert [p.read_bytes() for p in paths] == before


def test_output_escape_is_rejected(tmp_path):
    source, task, output, paths, before = owned(tmp_path)
    with pytest.raises(ValueError, match='inside'):
        transaction(source, task, source)
    assert [p.read_bytes() for p in paths] == before


def test_backups_are_all_durable_before_any_config_replace(tmp_path, monkeypatch):
    source, task, output, paths, before = owned(tmp_path)
    config = transaction(source, task, output)
    fsync, replace = os.fsync, os.replace
    syncs = []
    def sync(fd):
        syncs.append(fd)
        fsync(fd)
    def guarded_replace(src, dst):
        if Path(dst) in paths:
            assert len(syncs) >= 5  # three backups, prepared receipt, staged settings
            assert all((output / f'sensor_settings_before_{i}.json').read_bytes() == before[i]
                       for i in range(3))
        replace(src, dst)
    monkeypatch.setattr(os, 'fsync', sync)
    monkeypatch.setattr(os, 'replace', guarded_replace)
    config.apply()
    config.restore(owned_stopped=True)
    assert [p.read_bytes() for p in paths] == before


def test_failed_backup_fsync_prevents_any_settings_mutation(tmp_path, monkeypatch):
    source, task, output, paths, before = owned(tmp_path)
    config = transaction(source, task, output)
    def failed_sync(fd): raise OSError('injected backup failure')
    monkeypatch.setattr(os, 'fsync', failed_sync)
    with pytest.raises(OSError): config.apply()
    assert [p.read_bytes() for p in paths] == before


def test_partial_apply_failure_can_restore_the_changed_copy(tmp_path, monkeypatch):
    source, task, output, paths, before = owned(tmp_path)
    config = transaction(source, task, output)
    replace = os.replace
    def fail_second(src, dst):
        if Path(dst) == paths[1]: raise OSError('injected apply failure')
        replace(src, dst)
    monkeypatch.setattr(os, 'replace', fail_second)
    with pytest.raises(OSError): config.apply()
    assert paths[0].read_bytes() != before[0]
    assert [p.read_bytes() for p in paths[1:]] == before[1:]
    config.restore(owned_stopped=True)
    assert [p.read_bytes() for p in paths] == before


def test_restore_failure_does_not_abandon_other_copies_or_backups(tmp_path, monkeypatch):
    source, task, output, paths, before = owned(tmp_path)
    config = transaction(source, task, output)
    config.apply()
    configured = paths[0].read_bytes()
    replace = os.replace
    def fail_first(src, dst):
        if Path(dst) == paths[0]: raise OSError('injected restore failure')
        replace(src, dst)
    monkeypatch.setattr(os, 'replace', fail_first)
    with pytest.raises(RuntimeError, match='restoration'): config.restore(owned_stopped=True)
    assert paths[0].read_bytes() == configured
    assert [p.read_bytes() for p in paths[1:]] == before[1:]
    assert all((output / f'sensor_settings_before_{i}.json').read_bytes() == before[i] for i in range(3))
    assert config.audit['restored'] is False


def test_restore_refuses_external_change_but_restores_unaffected_files(tmp_path):
    source, task, output, paths, before = owned(tmp_path)
    config = transaction(source, task, output)
    config.apply()
    paths[1].write_bytes(b'concurrent external edit')
    with pytest.raises(RuntimeError, match='restoration'): config.restore(owned_stopped=True)
    assert paths[1].read_bytes() == b'concurrent external edit'
    assert paths[0].read_bytes() == before[0] and paths[2].read_bytes() == before[2]


def test_restore_cannot_run_while_processes_remain_live(tmp_path):
    source, task, output, paths, before = owned(tmp_path)
    config = transaction(source, task, output)
    config.apply()
    configured = [p.read_bytes() for p in paths]
    with pytest.raises(RuntimeError, match='stopped'): config.restore(owned_stopped=False)
    assert [p.read_bytes() for p in paths] == configured
    config.restore(owned_stopped=True)
    assert [p.read_bytes() for p in paths] == before


def test_changed_backup_is_not_used_for_restore(tmp_path):
    source, task, output, paths, before = owned(tmp_path)
    config = transaction(source, task, output)
    config.apply()
    configured = paths[0].read_bytes()
    (output / 'sensor_settings_before_0.json').write_bytes(b'tampered')
    with pytest.raises(RuntimeError, match='restoration'): config.restore(owned_stopped=True)
    assert paths[0].read_bytes() == configured
    assert [p.read_bytes() for p in paths[1:]] == before[1:]


def test_reparse_ancestor_is_rejected_before_backup_or_write(tmp_path, monkeypatch):
    source, task, output, paths, before = owned(tmp_path)
    original_lstat = Path.lstat
    def reparse_info(path):
        info = original_lstat(path)
        if path == task / 'package':
            return SimpleNamespace(st_mode=info.st_mode,
                st_file_attributes=getattr(info, 'st_file_attributes', 0) | stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return info
    monkeypatch.setattr(Path, 'lstat', reparse_info)
    with pytest.raises(ValueError, match='reparse'): transaction(source, task, output).apply()
    assert [p.read_bytes() for p in paths] == before
    assert list(output.iterdir()) == []


def test_change_during_staging_is_detected_before_replace(tmp_path, monkeypatch):
    source, task, output, paths, before = owned(tmp_path)
    config = transaction(source, task, output)
    fsync, syncs = os.fsync, []
    def concurrent_sync(fd):
        fsync(fd)
        syncs.append(fd)
        if len(syncs) == 5:  # three backups + prepared receipt + first staged settings
            paths[0].write_bytes(b'concurrent update during staging')
    monkeypatch.setattr(os, 'fsync', concurrent_sync)
    with pytest.raises(RuntimeError, match='concurrently'): config.apply()
    with pytest.raises(RuntimeError, match='restoration'): config.restore(owned_stopped=True)
    assert paths[0].read_bytes() == b'concurrent update during staging'
    assert [p.read_bytes() for p in paths[1:]] == before[1:]


def test_applied_receipt_failure_does_not_lose_restore_state(tmp_path, monkeypatch):
    source, task, output, paths, before = owned(tmp_path)
    config = transaction(source, task, output)
    receipt = config._receipt
    def fail_receipt(phase):
        if phase == 'applied': raise OSError('injected applied receipt failure')
        receipt(phase)
    monkeypatch.setattr(config, '_receipt', fail_receipt)
    with pytest.raises(OSError): config.apply()
    assert all(path.read_bytes() != original for path, original in zip(paths, before))
    config.restore(owned_stopped=True)
    assert [p.read_bytes() for p in paths] == before
