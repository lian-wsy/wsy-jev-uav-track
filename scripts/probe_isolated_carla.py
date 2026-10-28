"""Bounded Windows feasibility probe, not a collector or production launcher.

Requires an explicitly task-owned directory, fresh for prepare and each smoke.
No API key, original data, shared launcher or external process control is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from jev_uav_track.isolated_runtime import BINARY, validate_roots, write_settings, launch_arguments
from jev_uav_track.windows_job import OwnedJob


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=True), encoding='utf-8')


def sha(path):
    if not path.exists():
        return None
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def ps_json(command):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                             "$ErrorActionPreference='Stop'; " + command],
                            check=True, capture_output=True, text=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(result.stdout) if result.stdout.strip() else None


def snapshot():
    result = ps_json("$p=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'Carla|UE4|Unreal|python' } | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath); "
                   "$n=@(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(2000,2001,2002,3100,3101,3102,41451,42451) } | Select-Object LocalPort,OwningProcess); "
                   "$m=(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory; "
                   "@{processes=$p;listeners=$n;free_memory_kib=$m} | ConvertTo-Json -Compress -Depth 5")
    if (not isinstance(result, dict) or not isinstance(result.get('processes'), list)
            or not isinstance(result.get('listeners'), list)
            or not isinstance(result.get('free_memory_kib'), (int, float))
            or result['free_memory_kib'] <= 0):
        raise ValueError('Invalid preflight snapshot; refusing to infer idle state')
    return result


def protected_files(source):
    documents = ps_json("[Environment]::GetFolderPath('MyDocuments') | ConvertTo-Json -Compress")
    paths = list((source / 'CarlaUE4/Config').glob('*'))
    paths += [Path(documents) / 'AirSim/settings.json']
    return {str(p): sha(p) for p in paths if not p.is_dir()}


def prepare(source, task):
    validate_roots(source, task)
    if not (source / BINARY).is_file():
        raise ValueError('Source Shipping executable missing')
    if task.exists():
        raise ValueError('Prepare requires a new task directory; no overwrite')
    if shutil.disk_usage(task.anchor).free < 40 * 1024**3:
        raise ValueError('Need at least 40 GiB free before full package copy')
    task.mkdir(parents=True)
    before = protected_files(source)
    save(task / 'protected_before.json', before)
    print('Copying independent runtime; no links to shared package', flush=True)
    result = subprocess.run(['robocopy', str(source), str(task / 'package'), '/E', '/COPY:DAT',
                             '/R:0', '/W:0', '/XJ', '/XF', '*.pdb', '/XD', 'Saved',
                             '/NFL', '/NDL', '/NP', f'/LOG:{task / "copy.log"}'],
                            timeout=1200, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode >= 8:
        raise RuntimeError(f'Package copy failed: robocopy {result.returncode}')
    source_hash, copy_hash = sha(source / BINARY), sha(task / 'package' / BINARY)
    if source_hash != copy_hash:
        raise ValueError('Copied executable hash differs from source')
    write_settings(task)
    for folder in ('logs', 'user', 'temp', 'appdata', 'localappdata', 'recordings'):
        (task / folder).mkdir()
    save(task / 'prepared.json', {'source': str(source), 'task': str(task),
                                 'binary_sha256': copy_hash, 'copy_exit_code': result.returncode,
                                 'created_at': time.time(), 'kind': 'D2_DEVELOPMENT_PROBE'})
    print(json.dumps({'prepared': str(task), 'binary_sha256': copy_hash}), flush=True)


def smoke(source, task, client_python, run_name, profile, bootstrap):
    validate_roots(source, task)
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', run_name):
        raise ValueError('Invalid run name')
    output = task / 'runs' / run_name
    output.mkdir(parents=True, exist_ok=False)
    for folder in ('logs', 'user', 'temp', 'appdata', 'localappdata'):
        (output / folder).mkdir()
    prepared = json.loads((task / 'prepared.json').read_text())
    if Path(prepared['source']) != source or prepared['binary_sha256'] != sha(task / 'package' / BINARY):
        raise ValueError('Prepared source/executable mismatch')
    before = protected_files(source)
    if before != json.loads((task / 'protected_before.json').read_text()):
        raise ValueError('Protected configuration changed since preparation; audit before reuse')
    preflight = snapshot()
    save(output / 'preflight.json', preflight)
    external = [p for p in preflight['processes'] if p['ProcessId'] != os.getpid()]
    if external or preflight['listeners']:
        raise ValueError('External Python/simulator process or simulator listener present; no launch')
    if preflight['free_memory_kib'] < 4 * 1024**2:
        raise ValueError('Less than 4 GiB physical memory free; no launch')
    env = os.environ.copy()
    # Remove credentials, and confine ordinary process caches/temp files.
    for name in list(env):
        if any(token in name.upper() for token in ('API_KEY', 'TOKEN', 'SECRET')):
            env.pop(name)
    for name, folder in [('TEMP', 'temp'), ('TMP', 'temp'), ('APPDATA', 'appdata'),
                         ('LOCALAPPDATA', 'localappdata'), ('UE-LocalDataCachePath', 'localappdata/ddc')]:
        env[name] = str(output / folder)
    args = launch_arguments(task, output)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = None
    client_process = None
    job = OwnedJob()
    result = {'status': 'FAILED', 'started_at': time.time(), 'client_probe': None,
              'profile': profile, 'bootstrap': bootstrap, 'client_python': str(client_python),
              'probe_sha256': sha(Path(__file__)),
              'api_probe_sha256': sha(Path(__file__).with_name('probe_carla_apis.py')),
              'runtime_module_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/isolated_runtime.py'),
              'observation_probe_sha256': sha(Path(__file__).with_name('probe_observation_contract.py')),
              'observation_gate_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/live_observation.py'),
              'frames_module_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/frames.py'),
              'binary_sha256': prepared['binary_sha256']}
    try:
        process = subprocess.Popen(args, cwd=task / 'package', env=env, startupinfo=startup)
        job.assign(process)
        save(output / 'owner.json', {'pid': process.pid, 'started_at': result['started_at'],
                                  'executable': args[0], 'arguments': args[1:]})
        print(f'Owned Shipping process PID={process.pid}; bounded startup begins', flush=True)
        deadline = time.monotonic() + 150
        ready = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'Owned process exited with {process.returncode}')
            observed = snapshot()
            save(output / 'last_runtime_snapshot.json', observed)
            if observed['free_memory_kib'] < 768 * 1024:
                raise RuntimeError('Physical memory reserve below 768 MiB; stopping own probe')
            owned_ports = {x['LocalPort'] for x in observed['listeners'] if x['OwningProcess'] == process.pid}
            if {3100, 42451}.issubset(owned_ports):
                ready = True
                break
            time.sleep(2)
        if not ready:
            raise TimeoutError('Owned CARLA and AirSim ports not ready within 150 s')
        print('Both dedicated ports owned by expected process; running API probe', flush=True)
        client_process = subprocess.Popen([str(client_python), str(Path(__file__).with_name('probe_carla_apis.py')),
                                 '--output', str(output / 'api_probe.json'), '--profile', profile,
                                 '--bootstrap', bootstrap,
                                 '--expected-settings', str(task / 'settings.json')],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        job.assign(client_process)
        stdout, stderr = client_process.communicate(timeout=90)
        result['client_exit_code'] = client_process.returncode
        (output / 'client_stdout.txt').write_text(stdout, encoding='utf-8')
        (output / 'client_stderr.txt').write_text(stderr, encoding='utf-8')
        if client_process.returncode:
            raise RuntimeError('API probe failed; inspect task-local client logs')
        result['client_probe'] = json.loads((output / 'api_probe.json').read_text())
        result['status'] = 'API_SMOKE_COMPLETED'
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        job.close()
        if client_process is not None:
            if client_process.poll() is None:
                client_process.terminate()
            client_process.wait(timeout=15)
        if process is not None:
            # Popen retains the original Windows process handle: no PID/name reuse.
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)
            result['owned_process_exit_code'] = process.returncode
        result['protected_unchanged'] = before == protected_files(source)
        result['postflight'] = snapshot()
        result['cleanup_confirmed'] = (not result['postflight']['listeners']
            and not any(p['ProcessId'] == process.pid for p in result['postflight']['processes'])
            if process is not None else True)
        if not result['cleanup_confirmed'] or not result['protected_unchanged']:
            result['status'] = 'AUDIT_FAILED'
        result['ended_at'] = time.time()
        save(output / 'smoke_result.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'client_probe'}, indent=2), flush=True)
    if result['status'] != 'API_SMOKE_COMPLETED' or not result['protected_unchanged']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'smoke'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--client-python', type=Path)
    parser.add_argument('--run-name', default='smoke01')
    parser.add_argument('--profile', choices=['api', 'contract', 'observation'], default='api')
    parser.add_argument('--bootstrap', choices=['takeoff', 'velocity'], default='takeoff')
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('Windows only')
    source, task = args.source.resolve(), args.task.resolve()
    if args.mode == 'prepare':
        prepare(source, task)
    else:
        if not args.client_python or not args.client_python.is_file():
            raise SystemExit('--client-python must be an existing CARLA/AirSim Python runtime')
        smoke(source, task, args.client_python, args.run_name, args.profile, args.bootstrap)
