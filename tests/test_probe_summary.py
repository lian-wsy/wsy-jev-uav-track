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
