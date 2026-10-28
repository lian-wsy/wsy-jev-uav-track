import importlib.util
import json
from pathlib import Path


def load_summary():
    path = Path(__file__).resolve().parents[1] / 'scripts/summarize_carla_probe.py'
    spec = importlib.util.spec_from_file_location('probe_summary', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_summary_keeps_invalid_coordinates_and_unverified_rpc_explicit():
    module = load_summary()
    api = {'contract': {'coordinate_mapping': {'samples': [
        {'airsim_ned_m': [float('nan'), 0, 0], 'carla_xyz_m': [0, 0, 0]}]},
        'sync_begin': {'wall_s': 1, 'carla_s': 10, 'airsim_s': 100},
        'sync_end': {'wall_s': 1.3, 'carla_s': 11, 'airsim_s': 100.3}}}
    result = module.summarize(api)
    assert result['coordinate_status'] == 'INVALID'
    assert result['rpc_success_checked'] is False
    assert result['unpaced_delta_s']['carla_s'] == 1
    assert abs(result['unpaced_delta_s']['airsim_s'] - 0.3) < 1e-9
    assert module.finite_json(api)['contract']['coordinate_mapping']['samples'][0]['airsim_ned_m'][0] is None
    json.dumps(module.finite_json(api), allow_nan=False)


def test_summary_missing_measurement_is_not_zero_or_pass():
    result = load_summary().summarize({})
    assert result['coordinate_status'] == 'NOT_AUDITED'
    assert result['paced_delta_s'] is None
    assert result['command_refresh'] is None


def test_prefix_export_preserves_preflight_only_attempt_instead_of_hiding_it(tmp_path):
    module = load_summary()
    root = tmp_path / 'runtime'
    for name in ('origin01', 'origin02', 'other01'):
        (root / 'runs' / name).mkdir(parents=True)
    (root / 'runs/origin01/smoke_result.json').write_text(json.dumps({'status': 'FAILED'}))
    (root / 'runs/origin02/preflight.json').write_text(json.dumps({'free_memory_kib': 4111356}))
    (root / 'runs/other01/smoke_result.json').write_text(json.dumps({'status': 'FAILED'}))
    output = tmp_path / 'export'
    module.export(root, output, run_prefix='origin')
    report = json.loads((output / 'summary.json').read_text())
    assert [x['run'] for x in report['runs']] == ['origin01', 'origin02']
    assert report['runs'][1]['status'] == 'INCOMPLETE_CONTROLLER_RECORD'
    evidence = json.loads((output / 'origin02.json').read_text())
    assert evidence['preflight']['free_memory_kib'] == 4111356
    assert 'preflight.json' in evidence['source_sha256']
    module.export(root, tmp_path / 'filtered', profile='observation', run_prefix='origin')
    filtered = json.loads((tmp_path / 'filtered/summary.json').read_text())
    assert [x['run'] for x in filtered['runs']] == ['origin02']


def test_summary_distinguishes_object_origin_from_vehicle_origin():
    api = {'contract': {'coordinate_mapping': {'target_frame': 'airsim_global_object_ned'},
                       'origin_bridge': {'local_minus_global_m': [0, 0, 3]}}}
    result = load_summary().summarize(api)
    assert result['coordinate_target_frame'] == 'airsim_global_object_ned'
    assert result['origin_bridge']['local_minus_global_m'] == [0, 0, 3]


def test_export_preserves_belief_evidence_and_source_hashes(tmp_path):
    module = load_summary()
    root = tmp_path / 'runtime'
    folder = root / 'runs/belief01'
    folder.mkdir(parents=True)
    (folder / 'smoke_result.json').write_text(json.dumps({'status': 'FAILED', 'profile': 'belief',
        'belief_probe_sha256': 'probe-sha', 'belief_modules_sha256': {'live_belief.py': 'module-sha'}}))
    (folder / 'api_probe.json').write_text(json.dumps({'contract': {'belief_contract': {
        'dispatched_actions': 0, 'accepted_count': 2, 'frames': []}}}))
    (folder / 'belief_frame_00.png').write_bytes(b'fixture-image')
    module.export(root, tmp_path / 'out', profile='belief')
    evidence = json.loads((tmp_path / 'out/belief01.json').read_text())
    summary = json.loads((tmp_path / 'out/summary.json').read_text())
    assert evidence['controller']['belief_modules_sha256'] == {'live_belief.py': 'module-sha'}
    assert summary['runs'][0]['belief_contract']['dispatched_actions'] == 0
    assert (tmp_path / 'out/belief01_belief_frame_00.png').read_bytes() == b'fixture-image'


def test_profile_export_keeps_failed_runs_and_their_provenance(tmp_path):
    module = load_summary()
    root = tmp_path / 'runtime'
    (root / 'runs/observation01').mkdir(parents=True)
    (root / 'runs/contract01').mkdir()
    for name, profile, status in [('observation01', 'observation', 'FAILED'),
                                  ('contract01', 'contract', 'API_SMOKE_COMPLETED')]:
        (root / 'runs' / name / 'smoke_result.json').write_text(json.dumps({
            'profile': profile, 'status': status, 'observation_gate_sha256': 'sample-hash'}))
    output = tmp_path / 'export'
    module.export(root, output, profile='observation')
    summary = json.loads((output / 'summary.json').read_text())
    assert len(summary['runs']) == 1
    assert summary['runs'][0]['run'] == 'observation01'
    assert summary['runs'][0]['status'] == 'FAILED'
    evidence = json.loads((output / 'observation01.json').read_text())
    assert evidence['controller']['observation_gate_sha256'] == 'sample-hash'
