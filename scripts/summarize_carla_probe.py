"""Export all owned probe runs, including failures; no simulator or HDF access."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import shutil


def finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_json(v) for v in value]
    return value


def summarize(api):
    contract = api.get('contract', {})

    def delta(begin, end):
        if begin not in contract or end not in contract:
            return None
        return {key: contract[end][key] - contract[begin][key]
                for key in ('wall_s', 'carla_s', 'airsim_s')}

    mapping = contract.get('coordinate_mapping', {})
    invalid = any(not isinstance(v, (int, float)) or not math.isfinite(v)
                  for sample in mapping.get('samples', [])
                  for key in ('carla_xyz_m', 'airsim_ned_m') for v in sample.get(key, []))
    status = ('INVALID' if invalid else 'PASS' if mapping.get('mapping_pass') is True
              else 'NOT_AUDITED' if not mapping else 'NOT_PASSED')
    axes = []
    for axis in contract.get('axis_commands', []):
        trace = axis.get('trace', [])
        entry = {key: axis[key] for key in ('velocity_command_ned', 'yaw_rate_dps') if key in axis}
        if 'command_completed_wall_s' in axis:
            entry['command_completion_wall_s'] = axis['command_completed_wall_s'] - axis['command_started_wall_s']
        if len(trace) >= 2:
            entry.update(observed_samples=len(trace),
                         telemetry_hz=(len(trace) - 1) / (trace[-1]['wall_s'] - trace[0]['wall_s']),
                         displacement_including_brake_m=[trace[-1]['position'][i] - trace[0]['position'][i] for i in range(3)],
                         yaw_change_including_brake_rad=math.atan2(
                             math.sin(trace[-1]['yaw_rad'] - trace[0]['yaw_rad']),
                             math.cos(trace[-1]['yaw_rad'] - trace[0]['yaw_rad'])))
        axes.append(entry)
    cadence = contract.get('command_refresh')
    cadence_summary = None
    if cadence:
        commands = cadence.get('commands', [])
        acknowledged = [x for x in commands if x.get('acknowledged')]
        intervals = [b['sent_s'] - a['sent_s'] for a, b in zip(commands, commands[1:])]
        cadence_summary = {'sent_count': len(commands), 'acknowledged_count': len(acknowledged),
                           'skipped_slots': cadence.get('skipped_slots'),
                           'brake_sent_s': cadence.get('brake_sent_s'),
                           'mean_dispatch_hz': 1 / statistics.mean(intervals) if intervals else None,
                           'max_dispatch_interval_s': max(intervals) if intervals else None,
                           'max_ack_latency_s': max((x['completed_s'] - x['sent_s'] for x in acknowledged), default=None)}
    return {'phase': contract.get('phase'), 'error': api.get('contract_error'),
            'rpc_success_checked': api.get('rpc_success_checked', False),
            'coordinate_status': status,
            'coordinate_target_frame': mapping.get('target_frame'),
            'origin_bridge': contract.get('origin_bridge'),
            'offset_m': mapping.get('offset_first_m'), 'offset_spread_m': mapping.get('offset_spread_m'),
            'pause_delta_s': delta('pause_start', 'pause_end'),
            'unpaced_delta_s': delta('sync_begin', 'sync_end'),
            'paced_delta_s': delta('paced_begin', 'paced_end'),
            'axis_commands': axes, 'command_refresh': cadence_summary,
            'new_collision_axes': contract.get('new_collision_axes'), 'camera': contract.get('camera'),
            'observation_contract': contract.get('observation_contract')}


def export(root, output, profile=None, run_prefix=None):
    output.mkdir(parents=True, exist_ok=False)
    runs = []
    for folder in [root, *sorted((root / 'runs').iterdir())]:
        name = 'initial_api' if folder == root else folder.name
        if run_prefix is not None and not name.startswith(run_prefix):
            continue
        source = folder / 'smoke_result.json'
        preflight = folder / 'preflight.json'
        if not source.is_file() and not preflight.is_file():
            continue
        raw = (json.loads(source.read_text(encoding='utf-8')) if source.is_file()
               else {'status': 'INCOMPLETE_CONTROLLER_RECORD'})
        # A prefix explicitly scopes an experiment series. Its preflight-only
        # attempts have no known profile; retain them as unknown, never as PASS.
        prefix_selected_incomplete = run_prefix is not None and not source.is_file()
        if profile is not None and raw.get('profile') != profile and not prefix_selected_incomplete:
            continue
        api_file = folder / 'api_probe.json'
        api = json.loads(api_file.read_text(encoding='utf-8')) if api_file.exists() else raw.get('client_probe') or {}
        evidence = {'run': name, 'controller': {k: raw.get(k) for k in (
            'status', 'started_at', 'ended_at', 'profile', 'client_exit_code', 'owned_process_exit_code',
            'protected_unchanged', 'cleanup_confirmed', 'probe_sha256', 'api_probe_sha256',
            'runtime_module_sha256', 'binary_sha256', 'bootstrap', 'observation_probe_sha256',
            'observation_gate_sha256', 'frames_module_sha256')},
            'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (source, preflight, api_file, folder / 'camera.png', folder / 'client_stderr.txt',
                                        folder / 'camera_before.png', folder / 'camera_after.png') if p.exists()},
            'preflight': json.loads(preflight.read_text(encoding='utf-8')) if preflight.exists() else None,
            'api_probe': finite_json(api),
            'nonfinite_policy': 'Nonfinite raw measurements exported as null; raw source hash retained.'}
        (output / f'{name}.json').write_text(json.dumps(evidence, indent=2, allow_nan=False), encoding='utf-8')
        for image_name in ('camera_before.png', 'camera_after.png'):
            if (folder / image_name).exists():
                shutil.copyfile(folder / image_name, output / f'{name}_{image_name}')
        runs.append({'run': name, 'status': raw.get('status'),
                     'protected_unchanged': raw.get('protected_unchanged'),
                     'cleanup_confirmed': raw.get('cleanup_confirmed'), **finite_json(summarize(api))})
    report = {'kind': 'LIVE_OWNED_DEVELOPMENT_DIAGNOSTICS', 'selection_profile': profile,
              'selection_run_prefix': run_prefix, 'runs': runs,
              'not_tracking_evaluation': True, 'real_jev_requests': 0,
              'source_hdf_files_opened': 0}
    (output / 'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'runs': len(runs), 'output': str(output)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=['api', 'contract', 'observation'])
    parser.add_argument('--run-prefix', help='Select a named experiment series, retaining its failed attempts')
    args = parser.parse_args()
    export(args.root, args.output, args.profile, args.run_prefix)
