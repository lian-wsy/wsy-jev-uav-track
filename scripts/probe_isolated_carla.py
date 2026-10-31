"""Bounded Windows feasibility probe, not a collector or production launcher.

Requires an explicitly task-owned directory, fresh for prepare and each smoke.
No API key, original data, shared launcher or external process control is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from dataclasses import asdict

from jev_uav_track.isolated_runtime import (BINARY, validate_roots, write_settings, launch_arguments,
    validate_probe_map, require_loaded_map, PROBE_MAPS, RuntimePorts, DEFAULT_PORTS,
    add_port_arguments, ports_from_arguments, OwnedMapConfiguration)
from jev_uav_track.windows_job import OwnedJob
from jev_uav_track.sensor_configuration import (OwnedSensorConfiguration, validate_sensor_opt_in,
                                                DISTANCE_NAME, LIDAR_NAME)
from jev_uav_track.obstacle_probe import require_sweep_receipt
from jev_uav_track.body_probe import require_body_receipt


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


def snapshot(*, ports=DEFAULT_PORTS):
    watched = ','.join(str(port) for port in sorted({2000, 2001, 2002, 3100, 3101, 3102,
                                                   41451, 42451, *asdict(ports).values()}))
    result = ps_json("$p=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'Carla|UE4|Unreal|python' } | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath); "
                   "$n=@(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(" + watched + ") } | Select-Object LocalPort,OwningProcess); "
                   "$m=(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory; "
                   "@{processes=$p;listeners=$n;free_memory_kib=$m} | ConvertTo-Json -Compress -Depth 5")
    if (not isinstance(result, dict) or not isinstance(result.get('processes'), list)
            or not isinstance(result.get('listeners'), list)
            or type(result.get('free_memory_kib')) not in (int, float)
            or not math.isfinite(result['free_memory_kib'])
            or result['free_memory_kib'] <= 0):
        raise ValueError('Invalid preflight snapshot; refusing to infer idle state')
    return result


def protected_files(source):
    documents = ps_json("[Environment]::GetFolderPath('MyDocuments') | ConvertTo-Json -Compress")
    paths = list((source / 'CarlaUE4/Config').glob('*'))
    paths += [Path(documents) / 'AirSim/settings.json']
    return {str(p): sha(p) for p in paths if not p.is_dir()}


def prepare(source, task, *, ports=DEFAULT_PORTS):
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
    write_settings(task, ports=ports)
    for folder in ('logs', 'user', 'temp', 'appdata', 'localappdata', 'recordings'):
        (task / folder).mkdir()
    save(task / 'prepared.json', {'source': str(source), 'task': str(task),
                                 'binary_sha256': copy_hash, 'copy_exit_code': result.returncode,
                                 'created_at': time.time(), 'kind': 'D2_DEVELOPMENT_PROBE',
                                 'ports': asdict(ports)})
    print(json.dumps({'prepared': str(task), 'binary_sha256': copy_hash}), flush=True)


def smoke(source, task, client_python, run_name, profile, bootstrap, map_name='Town02', *, ports=DEFAULT_PORTS,
          configure_diagnostic_sensors=False, capture_geometry=False):
    validate_sensor_opt_in(configure_diagnostic_sensors, map_name, profile, capture_geometry=capture_geometry)
    validate_probe_map(map_name, profile)
    if profile == 'follow':
        from jev_uav_track.current_follow import validate_follow_endpoint
        validate_follow_endpoint(ports)
    validate_roots(source, task)
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', run_name):
        raise ValueError('Invalid run name')
    prepared = json.loads((task / 'prepared.json').read_text())
    if 'ports' not in prepared:
        frozen_ports = DEFAULT_PORTS
    else:
        values = prepared['ports']
        if not isinstance(values, dict) or set(values) != set(asdict(DEFAULT_PORTS)):
            raise ValueError('Invalid prepared ports receipt')
        frozen_ports = RuntimePorts(**values)
    if frozen_ports != ports:
        raise ValueError('Prepared ports mismatch; legacy receipts permit only original defaults')
    output = task / 'runs' / run_name
    output.mkdir(parents=True, exist_ok=False)
    for folder in ('logs', 'user', 'temp', 'appdata', 'localappdata'):
        (output / folder).mkdir()
    save(output / 'run_request.json', {'requested_map': map_name, 'profile': profile,
         'kind': ('API_INFRASTRUCTURE_ONLY' if profile == 'api' else
                  'SAFETY_SENSOR_DIAGNOSTIC_NO_MOTION' if profile == 'safety' else
                  'OWN_BODY_DIAGNOSTIC_NO_MOTION' if profile == 'body' else
                  'CURRENT_CAUSAL_BOUNDED_FOLLOW' if profile == 'follow' else
                  'OBSTACLE_SWEEP_FIXTURE_NO_FLIGHT' if profile == 'sweep' else 'DEVELOPMENT_CONTRACT'),
         'created_at': time.time(), 'not_final_test': True, 'ports': asdict(ports),
         'configure_diagnostic_sensors': configure_diagnostic_sensors, 'capture_geometry': capture_geometry})
    if Path(prepared['source']) != source or prepared['binary_sha256'] != sha(task / 'package' / BINARY):
        raise ValueError('Prepared source/executable mismatch')
    before = protected_files(source)
    if before != json.loads((task / 'protected_before.json').read_text()):
        raise ValueError('Protected configuration changed since preparation; audit before reuse')
    preflight = snapshot(ports=ports)
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
    args = launch_arguments(task, output, map_name=map_name, ports=ports)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = None
    client_process = None
    map_configuration = None
    sensor_configuration = None
    job = OwnedJob()
    result = {'status': 'FAILED', 'started_at': time.time(), 'client_probe': None,
              'requested_map': map_name, 'not_final_test': True, 'ports': asdict(ports),
              'profile': profile, 'bootstrap': bootstrap, 'client_python': str(client_python),
              'configure_diagnostic_sensors': configure_diagnostic_sensors,
              'capture_geometry': capture_geometry,
              'probe_sha256': sha(Path(__file__)),
              'package_init_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/__init__.py'),
              'api_probe_sha256': sha(Path(__file__).with_name('probe_carla_apis.py')),
              'runtime_module_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/isolated_runtime.py'),
              'sensor_probe_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/sensor_probe.py'),
              'obstacle_probe_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/obstacle_probe.py'),
              'body_probe_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/body_probe.py'),
              'sensor_configuration_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/sensor_configuration.py'),
              'observation_probe_sha256': sha(Path(__file__).with_name('probe_observation_contract.py')),
              'observation_gate_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/live_observation.py'),
              'frames_module_sha256': sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track/frames.py'),
              'belief_probe_sha256': sha(Path(__file__).with_name('probe_belief_contract.py')),
              'belief_modules_sha256': {name: sha(Path(__file__).resolve().parents[1] / 'src/jev_uav_track' / name)
                  for name in ('live_belief.py', 'causal_belief.py', 'history.py', 'pipeline.py', 'rollout.py',
                               'selector.py', 'safety.py', 'jev_client.py', 'models.py', 'primitives.py')},
              'binary_sha256': prepared['binary_sha256']}
    if profile == 'follow':
        result['follow_source_sha256'] = {str(path.relative_to(Path(__file__).resolve().parents[1])):sha(path)
            for path in (Path(__file__).with_name('probe_current_follow.py'),
                         Path(__file__).resolve().parents[1] / 'src/jev_uav_track/current_follow.py')}
    try:
        map_configuration = OwnedMapConfiguration(source, task, output, map_name)
        map_configuration.apply()
        if configure_diagnostic_sensors:
            sensor_configuration = OwnedSensorConfiguration(source, task, output, ports=ports,
                                                            map_name=map_name, profile=profile)
            sensor_configuration.apply()
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
            observed = snapshot(ports=ports)
            save(output / 'last_runtime_snapshot.json', observed)
            if observed['free_memory_kib'] < 768 * 1024:
                raise RuntimeError('Physical memory reserve below 768 MiB; stopping own probe')
            owned_ports = {x['LocalPort'] for x in observed['listeners'] if x['OwningProcess'] == process.pid}
            if {ports.carla_rpc, ports.airsim_rpc}.issubset(owned_ports):
                ready = True
                break
            time.sleep(2)
        if not ready:
            raise TimeoutError('Owned CARLA and AirSim ports not ready within 150 s')
        print('Both dedicated ports owned by expected process; running API probe', flush=True)
        client_process = subprocess.Popen([str(client_python), str(Path(__file__).with_name('probe_carla_apis.py')),
                                 '--output', str(output / 'api_probe.json'), '--profile', profile,
                                 '--bootstrap', bootstrap,
                                 '--expected-map', map_name,
                                 '--expected-settings', str(task / 'settings.json'),
                                 *(['--configure-diagnostic-sensors'] if configure_diagnostic_sensors else []),
                                 *(['--capture-sensor-geometry'] if capture_geometry else []),
                                 *[argument for name, value in asdict(ports).items()
                                   for argument in ('--' + name.replace('_', '-') + '-port', str(value))]],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        job.assign(client_process)
        client_deadline = time.monotonic() + 90
        while True:
            observed = snapshot(ports=ports)
            save(output / 'last_runtime_snapshot.json', observed)
            if observed['free_memory_kib'] < 768 * 1024:
                raise RuntimeError('Physical memory reserve below 768 MiB; stopping own probe')
            if process.poll() is not None:
                raise RuntimeError(f'Owned process exited with {process.returncode}')
            remaining = client_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('API probe did not finish within 90 s')
            try:
                stdout, stderr = client_process.communicate(timeout=min(2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        result['client_exit_code'] = client_process.returncode
        (output / 'client_stdout.txt').write_text(stdout, encoding='utf-8')
        (output / 'client_stderr.txt').write_text(stderr, encoding='utf-8')
        observed = snapshot(ports=ports)
        save(output / 'last_runtime_snapshot.json', observed)
        if observed['free_memory_kib'] < 768 * 1024:
            raise RuntimeError('Physical memory reserve below 768 MiB; stopping own probe')
        if client_process.returncode:
            raise RuntimeError('API probe failed; inspect task-local client logs')
        result['client_probe'] = json.loads((output / 'api_probe.json').read_text())
        require_loaded_map(result['client_probe'].get('map'), map_name)
        if result['client_probe'].get('map_match') is not True or result['client_probe'].get('requested_map') != map_name:
            raise ValueError('Client report lacks verified requested map identity')
        if profile == 'safety':
            diagnostic = result['client_probe'].get('safety_probe')
            if (result['client_probe'].get('kind') != 'LIVE_OWNED_SAFETY_SENSOR_DIAGNOSTIC'
                    or not isinstance(diagnostic, dict)
                    or diagnostic.get('kind') != 'SAFETY_SENSOR_DIAGNOSTIC_NO_MOTION'
                    or diagnostic.get('status') != 'DIAGNOSTIC_COMPLETED'
                    or diagnostic.get('control_ready') is not False
                    or 'clearance_m' not in diagnostic or diagnostic['clearance_m'] is not None):
                raise ValueError('Invalid or incomplete safety diagnostic receipt; no control certification')
            if configure_diagnostic_sensors and (
                    result['client_probe'].get('settings_match') is not True
                    or diagnostic.get('sensor_names') != {'distance': DISTANCE_NAME, 'lidar': LIDAR_NAME}):
                raise ValueError('Diagnostic sensor names or loaded settings not verified')
            if capture_geometry and (diagnostic.get('capture_geometry') is not True
                                     or type(diagnostic.get('geometry_schema_version')) is not int
                                     or diagnostic['geometry_schema_version'] != 1):
                raise ValueError('Requested diagnostic geometry archival not verified')
        if profile == 'sweep':
            if (result['client_probe'].get('kind') != 'LIVE_OWNED_OBSTACLE_SWEEP_FIXTURE'
                    or result['client_probe'].get('settings_match') is not True):
                raise ValueError('Invalid owned sweep fixture receipt')
            require_sweep_receipt(result['client_probe'].get('sweep_probe'))
        if profile == 'body':
            if (result['client_probe'].get('kind') != 'LIVE_OWNED_BODY_DIAGNOSTIC'
                    or result['client_probe'].get('settings_match') is not True):
                raise ValueError('Invalid owned body diagnostic receipt')
            require_body_receipt(result['client_probe'].get('body_probe'))
        if profile == 'follow':
            from jev_uav_track.current_follow import require_follow_receipt
            if (result['client_probe'].get('kind') != 'LIVE_OWNED_CURRENT_FOLLOW'
                    or result['client_probe'].get('settings_match') is not True):
                raise ValueError('Invalid current-model following receipt')
            require_follow_receipt(result['client_probe'].get('follow_probe'))
        result['status'] = ('SAFETY_DIAGNOSTIC_COMPLETED' if profile == 'safety' else
                            'BODY_DIAGNOSTIC_COMPLETED' if profile == 'body' else
                            'FOLLOW_SMOKE_COMPLETED' if profile == 'follow' else
                            'SWEEP_FIXTURE_COMPLETED' if profile == 'sweep' else 'API_SMOKE_COMPLETED')
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['cleanup_errors'] = {}
        try:
            job.close()
        except Exception as exc:
            result['cleanup_errors']['job'] = f'{type(exc).__name__}: {exc}'
        owned_stopped = True
        for name, owned in (('client', client_process), ('owned_process', process)):
            if owned is None:
                continue
            try:
                # Retained handles identify only this launch, never a reused PID.
                if owned.poll() is None:
                    owned.terminate()
                owned.wait(timeout=15)
                result[name + '_exit_code'] = owned.returncode
            except Exception as exc:
                result['cleanup_errors'][name] = f'{type(exc).__name__}: {exc}'
            try:
                owned_stopped = (owned.poll() is not None) and owned_stopped
            except Exception as exc:
                owned_stopped = False
                result['cleanup_errors'][name + '_poll'] = f'{type(exc).__name__}: {exc}'
        result['owned_handles_stopped'] = owned_stopped
        if client_process is not None:
            if not (output / 'client_stdout.txt').exists():
                try:
                    stdout, stderr = client_process.communicate(timeout=5)
                    (output / 'client_stdout.txt').write_text(stdout, encoding='utf-8')
                    (output / 'client_stderr.txt').write_text(stderr, encoding='utf-8')
                except Exception as exc:
                    result['client_log_error'] = f'{type(exc).__name__}: {exc}'
        result['sensor_config_restored'] = sensor_configuration is None
        if sensor_configuration is not None:
            result['sensor_configuration'] = sensor_configuration.audit
            try:
                sensor_configuration.restore(owned_stopped=owned_stopped)
                result['sensor_config_restored'] = sensor_configuration.audit['restored']
            except Exception as exc:
                result['sensor_restore_error'] = f'{type(exc).__name__}: {exc}'
        result['map_config_restored'] = False
        try:
            if map_configuration is not None:
                if not owned_stopped:
                    raise RuntimeError('Owned processes not confirmed stopped; map backup retained')
                map_configuration.restore()
                result['map_configuration'] = map_configuration.audit
                result['map_config_restored'] = (not map_configuration.applied
                                                 or map_configuration.audit['restored'])
        except Exception as exc:
            result['map_restore_error'] = f'{type(exc).__name__}: {exc}'
        # Audit reads can fail for the same reason as the runtime monitor.
        # Keep each verdict fail-closed and preserve the original probe error.
        result.update(protected_unchanged=False, postflight=None, cleanup_confirmed=False)
        try:
            result['protected_unchanged'] = before == protected_files(source)
        except Exception as exc:
            result['protected_audit_error'] = f'{type(exc).__name__}: {exc}'
        try:
            result['postflight'] = snapshot(ports=ports)
            owned_pids = {owned.pid for owned in (process, client_process) if owned is not None}
            result['cleanup_confirmed'] = (owned_stopped and not result['postflight']['listeners']
                and not any(p['ProcessId'] in owned_pids for p in result['postflight']['processes']))
        except Exception as exc:
            result['postflight_error'] = f'{type(exc).__name__}: {exc}'
        if (result['cleanup_errors'] or not result['cleanup_confirmed']
                or not result['protected_unchanged'] or not result['map_config_restored']
                or not result['sensor_config_restored']):
            result['status'] = 'AUDIT_FAILED'
        result['ended_at'] = time.time()
        save(output / 'smoke_result.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'client_probe'}, indent=2), flush=True)
    if result['status'] not in ('API_SMOKE_COMPLETED', 'SAFETY_DIAGNOSTIC_COMPLETED',
                               'SWEEP_FIXTURE_COMPLETED', 'BODY_DIAGNOSTIC_COMPLETED',
                               'FOLLOW_SMOKE_COMPLETED') or not result['protected_unchanged']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'smoke'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--client-python', type=Path)
    parser.add_argument('--run-name', default='smoke01')
    parser.add_argument('--profile', choices=['api', 'contract', 'observation', 'belief', 'safety', 'sweep', 'body', 'follow'], default='api')
    parser.add_argument('--bootstrap', choices=['takeoff', 'velocity'], default='takeoff')
    parser.add_argument('--map', choices=PROBE_MAPS, default='Town02', dest='map_name')
    parser.add_argument('--configure-diagnostic-sensors', action='store_true',
                        help='Temporarily enable owned named sensors; Town02 safety smoke only')
    parser.add_argument('--capture-sensor-geometry', action='store_true',
                        help='Archive bounded raw geometry; requires configured diagnostic sensors')
    add_port_arguments(parser)
    args = parser.parse_args()
    ports = ports_from_arguments(args)
    if (args.configure_diagnostic_sensors or args.capture_sensor_geometry) and args.mode != 'smoke':
        parser.error('Diagnostic sensors and geometry capture require smoke mode')
    if os.name != 'nt':
        raise SystemExit('Windows only')
    source, task = args.source.resolve(), args.task.resolve()
    if args.mode == 'prepare':
        prepare(source, task, ports=ports)
    else:
        if not args.client_python or not args.client_python.is_file():
            raise SystemExit('--client-python must be an existing CARLA/AirSim Python runtime')
        smoke(source, task, args.client_python, args.run_name, args.profile, args.bootstrap, args.map_name,
              ports=ports, configure_diagnostic_sensors=args.configure_diagnostic_sensors,
              capture_geometry=args.capture_sensor_geometry)
