"""Task-owned Windows runtime preparation. Never invokes the shared launcher."""
from __future__ import annotations

import json
import time
from pathlib import Path

BINARY = Path('CarlaUE4/Binaries/Win64/CarlaUE4-Win64-Shipping.exe')
PORTS = (3100, 3101, 3102, 42451)


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


def write_settings(task: Path) -> Path:
    task = task.resolve()
    settings = {
        'SettingsVersion': 1.2, 'SimMode': 'Multirotor', 'ClockType': 'SteppableClock',
        'ClockSpeed': 1.0, 'LocalHostIp': '127.0.0.1', 'ApiServerPort': 42451,
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


def launch_arguments(task: Path, output: Path | None = None) -> list[str]:
    task = task.resolve()
    output = output.resolve() if output is not None else task
    if not output.is_relative_to(task):
        raise ValueError('Run output must be inside task runtime')
    return [str(task / 'package' / BINARY), '/Game/Carla/Maps/Town02',
            '-windowed', '-ResX=640', '-ResY=360', '-quality-level=Low',
            '-nosound', '-unattended', '-NoCrashDialog', '-nocrashreports',
            '-carla-rpc-port=3100', '-carla-streaming-port=3101',
            '-carla-secondary-port=3102', '-TexturePoolSize=1024',
            '-SaveToUserDir', f'-UserDir={output / "user"}',
            f'-abslog={output / "logs/CarlaUE4.log"}',
            '--settings', str(task / 'settings.json')]
