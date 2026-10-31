"""Read terminal video-run telemetry; write results only outside its runtime.

The complete seven-attempt continuous/turning inventory is retrospective, not
a preregistered experiment or final-test admission. Never imports run_source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jev_uav_track.recorded_follow_metrics import summarize_follow


RUNS = ('continuous01_20261030', 'continuous_fast01_20261030',
        'turning_left01_20261030', 'turning_left02_20261030', 'turning_left03_20261030',
        'turning_left04_20261030', 'turning_complex01_20261030')


def read_stable(path, inventory):
    before = path.stat()
    if before.st_size > 128 * 1024**2:
        raise ValueError('Telemetry file exceeds bounded reader')
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_mtime_ns, before.st_size, before.st_ino) != (after.st_mtime_ns, after.st_size, after.st_ino):
        raise ValueError('Input changed while reading')
    inventory.append({'path': str(path), 'bytes': len(raw), 'mtime_ns': before.st_mtime_ns,
                      'sha256': hashlib.sha256(raw).hexdigest()})
    return raw


def inspect_run(path):
    row = {'run': path.name, 'status': 'NOT_EVALUATED', 'files': [], 'metrics': None,
           'training_admission': 'NOT_AUDITED'}
    if not path.exists():
        row['status'] = 'RUN_MISSING'
        return row
    receipt_path, tracking_path = path/'smoke_result.json', path/'tracking_result.json'
    if not receipt_path.exists():
        row['status'] = 'NO_TERMINAL_RECEIPT'
        if (path/'preflight.json').exists(): read_stable(path/'preflight.json', row['files'])
        return row
    receipt = json.loads(read_stable(receipt_path, row['files']))
    if type(receipt) is not dict:
        raise ValueError('Terminal receipt must be a JSON object')
    row.update(owner_status=receipt.get('status'), cleanup_confirmed=receipt.get('cleanup_confirmed'),
               protected_unchanged=receipt.get('protected_unchanged'))
    if receipt.get('cleanup_confirmed') is not True:
        row['status'] = 'TERMINATION_NOT_CONFIRMED'
        return row
    if not tracking_path.exists():
        row['status'] = 'NO_TRACKING_RECORD'
        return row
    data = json.loads(read_stable(tracking_path, row['files']))
    if type(data) is not dict:
        raise ValueError('Tracking receipt must be a JSON object')
    row.update(recorded_phase=data.get('phase'), recorded_kind=data.get('kind'),
               recorded_frames=len(data.get('frames', [])))
    for filename, key in [('continuous_prefixes.json', 'prefixes'), ('continuous_advice.json', 'decisions')]:
        if (path/filename).exists():
            duplicate = json.loads(read_stable(path/filename, row['files']))
            if duplicate != data.get(key):
                row['status'] = 'CONFLICTING_TELEMETRY_COPIES'
                return row
    sources = data.get('source_sha256', {})
    if type(sources) is not dict:
        raise ValueError('Source inventory must be a JSON object')
    source_root = (path/'run_source').resolve()
    matches, mismatches = [], []
    for name, expected in sources.items():
        source = (source_root / name.replace('\\', '/')).resolve()
        if not source.is_relative_to(source_root) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('Invalid source snapshot reference')
        if not source.is_file():
            mismatches.append(name)
            continue
        items = []
        digest = hashlib.sha256(read_stable(source, items)).hexdigest()
        (matches if digest == expected else mismatches).append(name)
    row.update(source_snapshot_status='MATCHED' if sources and not mismatches else
               'MISMATCH' if mismatches else 'NOT_AVAILABLE',
               source_snapshot_files_matched=len(matches), source_snapshot_mismatches=mismatches)
    if mismatches:
        row['status'] = 'SOURCE_SNAPSHOT_MISMATCH'
        return row
    try:
        row['metrics'] = summarize_follow(data)
        row['status'] = 'METRICS_RECOMPUTED'
    except ValueError as exc:
        # A failed preparation does not disappear from the attempt inventory.
        row.update(status='NO_EVALUABLE_FOLLOW_TRAJECTORY' if not data.get('frames') else 'INVALID_TELEMETRY',
                   metric_error_type=type(exc).__name__)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.run_root.resolve(), args.output.resolve()
    if output.is_relative_to(root.parent):
        parser.error('output must be outside the entire read-only runtime')
    if output.exists():
        parser.error('output exists; preserve prior evidence')
    reports = []
    for name in RUNS:
        try:
            reports.append(inspect_run(root/name))
        except (OSError, ValueError, TypeError) as exc:
            reports.append({'run': name, 'status': 'READ_OR_SCHEMA_ERROR', 'error_type': type(exc).__name__,
                            'metrics': None, 'training_admission': 'NOT_AUDITED'})
    result = {'schema_version': 1, 'evidence_class': 'READ_ONLY_RETROSPECTIVE_VIDEO_RUN_REANALYSIS',
        'run_root': str(root), 'selection': 'all seven continuous and turning attempts listed before recomputation; retrospective, not preregistered',
        'runs': reports, 'new_simulator_launches': 0, 'new_jev_calls': 0, 'new_training_runs': 0,
        'new_PASS_episodes': 0, 'paired_baseline_comparison': 'NOT_AVAILABLE',
        'current_d6_controller_verified_by_these_runs': False,
        'recipe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'metrics_source_sha256': hashlib.sha256((Path(__file__).resolve().parents[1]/'src/jev_uav_track/recorded_follow_metrics.py').read_bytes()).hexdigest()}
    text = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    print(json.dumps({'output': str(output), 'attempts': len(reports),
        'recomputed': sum(r['status'] == 'METRICS_RECOMPUTED' for r in reports), 'new_PASS_episodes': 0}))


if __name__ == '__main__':
    main()
