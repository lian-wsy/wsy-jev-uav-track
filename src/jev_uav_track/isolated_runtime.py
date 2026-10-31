"""Task-owned Windows runtime preparation. Never invokes the shared launcher."""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path

BINARY = Path('CarlaUE4/Binaries/Win64/CarlaUE4-Win64-Shipping.exe')
PROBE_MAPS = ('Town02', 'Town03')


@dataclass(frozen=True)
class RuntimePorts:
    carla_rpc: int = 3100
    carla_streaming: int = 3101
    carla_secondary: int = 3102
    airsim_rpc: int = 42451

    def __post_init__(self):
        values = tuple(asdict(self).values())
        if any(type(value) is not int or not 1024 <= value <= 65535 for value in values):
            raise ValueError('Runtime ports must be integers in 1024..65535')
        if len(set(values)) != 4:
            raise ValueError('Runtime ports must be distinct')


DEFAULT_PORTS = RuntimePorts()
PORTS = tuple(asdict(DEFAULT_PORTS).values())


def add_port_arguments(parser):
    for name, value in asdict(DEFAULT_PORTS).items():
        parser.add_argument('--' + name.replace('_', '-') + '-port', type=int, default=value)


def ports_from_arguments(args):
    return RuntimePorts(**{name: getattr(args, name + '_port') for name in asdict(DEFAULT_PORTS)})


def validate_probe_map(map_name: str, profile: str = 'api') -> None:
    if map_name not in PROBE_MAPS:
        raise ValueError('Unsupported probe map; use exactly Town02 or Town03')
    if profile not in ('api', 'contract', 'observation', 'belief', 'safety', 'sweep'):
        raise ValueError('Unsupported probe profile')
    if map_name == 'Town03' and profile != 'api':
        raise ValueError('Reserved Town03 permits API-only checks, not development motion probes')


def require_loaded_map(actual: str, expected: str) -> None:
    validate_probe_map(expected)
    if actual not in (expected, f'/Game/Carla/Maps/{expected}', f'Carla/Maps/{expected}'):
        raise ValueError(f'Loaded map mismatch: expected {expected}, observed {actual}')


def require_rpc_success(future, observation=None):
    """msgpackrpc Future.join() does not raise server/transport errors; get() does."""
    result = future.get()
    if observation is not None:
        observation.update(response_type=type(result).__name__, acknowledged=result is True)
    if result is not True:
        raise RuntimeError(f'Movement RPC did not acknowledge success (type={type(result).__name__})')
    return result


def command_cadence(command, brake, result, *, clock=time.perf_counter, sleep=time.sleep):
    """One-second 20 Hz diagnostic; await each RPC, skip expired slots, then brake.

    Command requests last <=40 ms. This measures acknowledged client cadence,
    not the internal flight controller or a hard real-time execution guarantee.
    """
    start = clock()
    result.update(period_s=0.05, window_s=1.0, commands=[], skipped_slots=0)
    try:
        for i in range(20):
            scheduled = start + i * 0.05
            sleep(max(0, scheduled - clock()))
            now = clock()
            if now >= scheduled + 0.05 or now >= start + 1.0:
                result['skipped_slots'] += 1
                continue
            duration = min(0.04, start + 1.0 - now)
            record = {'slot': i, 'scheduled_s': scheduled - start,
                      'sent_s': now - start, 'duration_s': duration,
                      'acknowledged': False}
            result['commands'].append(record)
            command(duration)  # Caller must surface both RPC errors and rejection.
            record.update(acknowledged=True, completed_s=clock() - start)
        sleep(max(0, start + 1.0 - clock()))
    finally:
        result['brake_sent_s'] = clock() - start
        brake()
        result['brake_completed_s'] = clock() - start


def validate_roots(source: Path, task: Path) -> None:
    source, task = source.resolve(), task.resolve()
    if source == task or source.is_relative_to(task) or task.is_relative_to(source):
        raise ValueError('Task runtime and source package must have disjoint roots')


class OwnedMapConfiguration:
    """Temporarily select a map in an independent package, then restore bytes.

    The packaged CarlaAir launcher updates these defaults as well as its map
    argument. Never call that shared launcher or edit its source config here.
    The caller must first prove process/resource isolation and stop its owned
    process before restoring. This class enforces only the file boundary.
    """

    def __init__(self, source: Path, task: Path, output: Path, map_name: str):
        validate_roots(source, task)
        validate_probe_map(map_name)
        self.task = task.absolute()
        self.output = output.absolute()
        if not self.output.is_relative_to(self.task) or self.output == self.task:
            raise ValueError('Map evidence must be inside the owned runtime')
        self.path = self.task / 'package/CarlaUE4/Config/DefaultEngine.ini'
        self.map_name = map_name
        self.original = None
        self.configured = None
        self.applied = False
        self._audit_bytes = None
        self.audit = {'requested_map': map_name, 'path': str(self.path),
                      'applied': False, 'restored': False}

    def _check_path(self, path):
        for item in (path, *path.parents):
            if item == self.task.parent:
                break
            info = item.lstat()
            if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Owned map paths cannot contain links or reparse points')
        if not path.resolve().is_relative_to(self.task.resolve()):
            raise ValueError('Owned map path escapes the runtime')
        if path.is_file() and path.stat().st_nlink != 1:
            raise ValueError('Owned map file cannot be a shared hardlink')

    def _save_audit(self):
        self._check_path(self.output)
        path = self.output / 'map_config.json'
        data = json.dumps(self.audit, indent=2).encode('utf-8')
        if self._audit_bytes is None:
            # An existing receipt, including a link, is never ours to overwrite.
            with path.open('xb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        else:
            self._check_path(path)
            if path.read_bytes() != self._audit_bytes:
                raise RuntimeError('Map audit receipt changed during probe; refusing overwrite')
            temporary = self.output / 'map_config.update.tmp'
            with temporary.open('xb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._check_path(path)
            os.replace(temporary, path)
        self._audit_bytes = data

    def _replace_config(self, data, phase):
        # Exclusive task-local temporary file plus replace avoids half-written
        # defaults. A failed temporary write is retained for diagnosis.
        self._check_path(self.path)
        temporary = self.path.with_name(self.path.name + '.' + self.output.name + '.' + phase + '.tmp')
        with temporary.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self._check_path(self.path)
        os.replace(temporary, self.path)

    def apply(self):
        if self.original is not None:
            raise ValueError('Map configuration may only be applied once')
        self._check_path(self.path)
        self._check_path(self.output)
        original = self.path.read_bytes()
        text = original.decode('utf-8')
        keys = {'EditorStartupMap': 0, 'GameDefaultMap': 0, 'ServerDefaultMap': 0}
        section, sections, lines = False, 0, []
        for line in text.splitlines(keepends=True):
            header = line.strip().lstrip('\ufeff')
            if header.startswith('['):
                section = header == '[/Script/EngineSettings.GameMapsSettings]'
                sections += int(section)
            match = re.match(r'^(\s*)(EditorStartupMap|GameDefaultMap|ServerDefaultMap)\s*=([^\r\n]*)(\r?\n)?$', line)
            if section and match:
                key = match[2]
                keys[key] += 1
                line = f'{match[1]}{key}=/Game/Carla/Maps/{self.map_name}{match[4] or ""}'
            lines.append(line)
        if sections != 1 or set(keys.values()) != {1}:
            raise ValueError('Map defaults missing or ambiguous; no configuration changed')
        configured = ''.join(lines).encode('utf-8')
        backup = self.output / 'map_config_before.ini'
        with backup.open('xb') as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        self.original, self.configured = original, configured
        self.audit.update(before_sha256=hashlib.sha256(original).hexdigest(),
                          configured_sha256=hashlib.sha256(configured).hexdigest(), backup=str(backup))
        self._save_audit()
        self._replace_config(configured, 'apply')
        self.applied = True
        self.audit['applied'] = True
        self._save_audit()

    def restore(self):
        if not self.applied:
            return
        self._check_path(self.path)
        if self.path.read_bytes() != self.configured:
            raise RuntimeError('Owned map configuration changed during probe; refusing overwrite')
        self._replace_config(self.original, 'restore')
        restored = self.path.read_bytes()
        self.audit.update(restored=restored == self.original,
                          restored_sha256=hashlib.sha256(restored).hexdigest())
        self._save_audit()
        if not self.audit['restored']:
            raise RuntimeError('Owned map configuration restoration failed')


def write_settings(task: Path, *, ports: RuntimePorts = DEFAULT_PORTS) -> Path:
    task = task.resolve()
    settings = {
        'SettingsVersion': 1.2, 'SimMode': 'Multirotor', 'ClockType': 'SteppableClock',
        'ClockSpeed': 1.0, 'LocalHostIp': '127.0.0.1', 'ApiServerPort': ports.airsim_rpc,
        'ViewMode': 'NoDisplay', 'EnableRpc': True,
        'Recording': {'Enabled': False, 'Folder': str(task / 'recordings')},
        'Vehicles': {'JevDrone': {'VehicleType': 'SimpleFlight', 'AutoCreate': True,
            'X': 0, 'Y': 0, 'Z': -3, 'AllowAPIAlways': True,
            'Cameras': {'front_center': {'X': 0, 'Y': 0, 'Z': 0,
                'CaptureSettings': [{'ImageType': 0, 'Width': 320, 'Height': 240,
                                     'FOV_Degrees': 90}]}}}},
    }
    payload = json.dumps(settings, indent=2)
    path = task / 'settings.json'
    # Both documented fallback locations are inside the independent package.
    for target in (path, task / 'package/settings.json',
                   task / 'package' / BINARY.parent / 'settings.json'):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as handle:
            handle.write(payload)
    return path


def launch_arguments(task: Path, output: Path | None = None, *, map_name: str = 'Town02',
                     ports: RuntimePorts = DEFAULT_PORTS) -> list[str]:
    validate_probe_map(map_name)
    task = task.resolve()
    output = output.resolve() if output is not None else task
    if not output.is_relative_to(task):
        raise ValueError('Run output must be inside task runtime')
    # This is the direct Shipping binary, not the root CarlaUE4.exe bootstrap.
    # Match the packaged Windows launcher's NeedsProjectArg branch; otherwise
    # the map URL occupies the project slot and the default town may be loaded.
    return [str(task / 'package' / BINARY), 'CarlaUE4', f'/Game/Carla/Maps/{map_name}',
            '-windowed', '-ResX=640', '-ResY=360', '-quality-level=Low',
            '-nosound', '-unattended', '-NoCrashDialog', '-nocrashreports',
            f'-carla-rpc-port={ports.carla_rpc}', f'-carla-streaming-port={ports.carla_streaming}',
            f'-carla-secondary-port={ports.carla_secondary}', '-TexturePoolSize=1024',
            '-SaveToUserDir', f'-UserDir={output / "user"}',
            f'-abslog={output / "logs/CarlaUE4.log"}',
            '--settings', str(task / 'settings.json')]
