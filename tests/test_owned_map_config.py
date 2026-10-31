"""The packaged game reads defaults; edits must stay in the owned copy."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from jev_uav_track import isolated_runtime as runtime


CONFIG = Path('CarlaUE4/Config/DefaultEngine.ini')
ORIGINAL = (b'; keep this comment\r\n[/Script/EngineSettings.GameMapsSettings]\r\n'
            b'EditorStartupMap=/Game/Carla/Maps/Town02\r\n'
            b'GameDefaultMap=/Game/Carla/Maps/Town02\r\n'
            b'ServerDefaultMap=/Game/Carla/Maps/Town02\r\n'
            b'GlobalDefaultGameMode=/Script/AirSim.SimWorldGameMode\r\n'
            b'[Unrelated]\r\nGameDefaultMap=do-not-touch\r\n')


def fixture_paths(tmp_path):
    source, task = tmp_path / 'source', tmp_path / 'owned'
    for root in (source, task / 'package'):
        config = root / CONFIG
        config.parent.mkdir(parents=True)
        config.write_bytes(ORIGINAL)
    output = task / 'runs/api01'
    output.mkdir(parents=True)
    return source, task, output


def guard_for(source, task, output, map_name='Town03'):
    assert hasattr(runtime, 'OwnedMapConfiguration'), 'Owned map configuration is missing'
    return runtime.OwnedMapConfiguration(source, task, output, map_name)


def test_map_defaults_change_only_in_owned_copy_and_restore_exact_bytes(tmp_path):
    source, task, output = fixture_paths(tmp_path)
    guard = guard_for(source, task, output)
    guard.apply()
    configured = (task / 'package' / CONFIG).read_bytes()
    assert configured.count(b'/Game/Carla/Maps/Town03') == 3
    assert b'[Unrelated]\r\nGameDefaultMap=do-not-touch\r\n' in configured
    assert (source / CONFIG).read_bytes() == ORIGINAL
    assert (output / 'map_config_before.ini').read_bytes() == ORIGINAL
    guard.restore()
    assert (task / 'package' / CONFIG).read_bytes() == ORIGINAL
    receipt = json.loads((output / 'map_config.json').read_text())
    assert receipt['restored'] is True
    assert receipt['before_sha256'] == hashlib.sha256(ORIGINAL).hexdigest()
    assert receipt['configured_sha256'] == hashlib.sha256(configured).hexdigest()
    assert receipt['restored_sha256'] == receipt['before_sha256']


@pytest.mark.parametrize('broken', [
    ORIGINAL.replace(b'GameDefaultMap=/Game/Carla/Maps/Town02\r\n', b''),
    ORIGINAL.replace(b'GameDefaultMap=/Game/Carla/Maps/Town02\r\n',
                     b'GameDefaultMap=a\r\nGameDefaultMap=b\r\n'),
    ORIGINAL + b'[/Script/EngineSettings.GameMapsSettings]\r\n',
])
def test_incomplete_or_ambiguous_config_is_not_mutated(tmp_path, broken):
    source, task, output = fixture_paths(tmp_path)
    path = task / 'package' / CONFIG
    path.write_bytes(broken)
    guard = guard_for(source, task, output)
    with pytest.raises(ValueError):
        guard.apply()
    assert path.read_bytes() == broken
    assert (source / CONFIG).read_bytes() == ORIGINAL
    assert not (output / 'map_config_before.ini').exists()


def test_shared_hardlink_is_rejected_without_touching_source(tmp_path):
    source, task, output = fixture_paths(tmp_path)
    path = task / 'package' / CONFIG
    path.unlink()
    os.link(source / CONFIG, path)
    guard = guard_for(source, task, output)
    with pytest.raises(ValueError, match='link'):
        guard.apply()
    assert (source / CONFIG).read_bytes() == ORIGINAL


def test_outside_output_and_overlapping_roots_rejected_before_writes(tmp_path):
    source, task, output = fixture_paths(tmp_path)
    with pytest.raises(ValueError):
        guard_for(source, task, tmp_path / 'outside').apply()
    with pytest.raises(ValueError):
        guard_for(source, source, output).apply()
    assert (source / CONFIG).read_bytes() == ORIGINAL


def test_restore_does_not_clobber_an_unexpected_edit(tmp_path):
    source, task, output = fixture_paths(tmp_path)
    guard = guard_for(source, task, output)
    guard.apply()
    path = task / 'package' / CONFIG
    path.write_bytes(b'unexpected edit')
    with pytest.raises(RuntimeError, match='changed'):
        guard.restore()
    assert path.read_bytes() == b'unexpected edit'
    assert (output / 'map_config_before.ini').read_bytes() == ORIGINAL
    assert (source / CONFIG).read_bytes() == ORIGINAL


def test_backup_must_be_fresh_before_any_configuration_write(tmp_path):
    source, task, output = fixture_paths(tmp_path)
    (output / 'map_config_before.ini').write_bytes(b'prior evidence')
    guard = guard_for(source, task, output)
    with pytest.raises(FileExistsError):
        guard.apply()
    assert (task / 'package' / CONFIG).read_bytes() == ORIGINAL
    assert (output / 'map_config_before.ini').read_bytes() == b'prior evidence'


def test_audit_hardlink_cannot_overwrite_source_or_apply_map(tmp_path):
    source, task, output = fixture_paths(tmp_path)
    os.link(source / CONFIG, output / 'map_config.json')
    guard = guard_for(source, task, output)
    with pytest.raises((FileExistsError, ValueError)):
        guard.apply()
    assert (source / CONFIG).read_bytes() == ORIGINAL
    assert (task / 'package' / CONFIG).read_bytes() == ORIGINAL


def test_replaced_audit_link_is_not_followed_during_restore(tmp_path):
    source, task, output = fixture_paths(tmp_path)
    guard = guard_for(source, task, output)
    guard.apply()
    receipt = output / 'map_config.json'
    receipt.unlink()
    os.link(source / CONFIG, receipt)
    with pytest.raises(ValueError, match='link'):
        guard.restore()
    assert (source / CONFIG).read_bytes() == ORIGINAL
    assert (task / 'package' / CONFIG).read_bytes() == ORIGINAL
