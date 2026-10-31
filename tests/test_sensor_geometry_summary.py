"""Offline geometry summaries must keep raw-hit and clock limits explicit."""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path

import pytest

from test_sensor_geometry import geometry_rpc, run_geometry


def module():
    path = Path(__file__).parents[1] / 'scripts/summarize_sensor_geometry.py'
    assert path.is_file(), 'Reproducible geometry analysis is missing'
    spec = importlib.util.spec_from_file_location('sensor_geometry_summary', path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def summarize(api):
    return module().summarize(api)


def api_fixture():
    rpc = geometry_rpc()
    def lidar(data):
        data['pose']['position']['z_val'] = -.5
        data['point_cloud'] = [4 * math.cos(math.radians(10)), 0., -4 * math.sin(math.radians(10))]
        return data
    rpc.overrides['getLidarData'] = lidar
    return {'kind': 'LIVE_OWNED_SAFETY_SENSOR_DIAGNOSTIC', 'map': 'Town02',
            'settings_match': True, 'safety_probe': run_geometry(rpc)}


def test_summary_keeps_all_points_and_checks_mount_without_claiming_calibration():
    api = api_fixture()
    before = deepcopy(api)
    result = summarize(api)
    assert result['evidence_class'] == 'OFFLINE_ANALYSIS_OF_RAW_DIAGNOSTIC'
    sample = result['samples'][0]
    assert sample['lidar']['point_count'] == 1
    assert sample['lidar']['nearest_return_m_not_clearance'] == pytest.approx(4.)
    assert sample['lidar']['expected_pitch_band_hit_counts'] == [1, 0, 0, 0]
    assert sample['lidar']['mount_comparison_before']['position_error_m'] == 0.
    assert sample['lidar']['numeric_age_to_after_state_ns'] == 2_000_000
    assert sample['state_before']['speed_mps'] == pytest.approx(math.sqrt(.14))
    assert result['clearance_m'] is None and result['control_ready'] is False
    assert result['frame_alignment_status'] == 'NOT_CALIBRATED'
    assert api == before


@pytest.mark.parametrize('corruption', ['hash', 'count', 'missing_geometry', 'incomplete_round', 'unsafe'])
def test_summary_rejects_tampered_or_incomplete_authority(corruption):
    api = api_fixture()
    report = api['safety_probe']
    lidar = next(row for row in report['queries'] if row['rpc'] == 'getLidarData')
    if corruption == 'hash': lidar['payload']['point_cloud_sha256'] = '0' * 64
    if corruption == 'count': lidar['payload']['point_count'] = 2
    if corruption == 'missing_geometry': report['capture_geometry'] = False
    if corruption == 'incomplete_round': report['queries'].pop()
    if corruption == 'unsafe': report['control_ready'] = True
    with pytest.raises(ValueError): summarize(api)


def test_missing_contact_is_not_inferred_from_event_flag():
    api = api_fixture()
    collision = next(row for row in api['safety_probe']['queries'] if row['rpc'] == 'simGetCollisionInfo')
    collision.update(geometry=None, geometry_status='MISSING_FIELDS', geometry_missing_fields=['object_name'])
    result = summarize(api)
    contact = result['samples'][0]['collision']
    assert contact['has_collided'] is False
    assert contact['contact_geometry'] is None
    assert contact['geometry_status'] == 'MISSING_FIELDS'


def test_pitch_rotation_and_translation_are_used_in_mount_comparison():
    api = api_fixture()
    for row in api['safety_probe']['queries']:
        if row['rpc'] == 'getMultirotorState':
            kin = row['geometry']['kinematics_estimated']
            kin['position'] = {'x_val': 10., 'y_val': -2., 'z_val': 3.}
            kin['orientation'] = {'w_val': math.sqrt(.5), 'x_val': 0., 'y_val': math.sqrt(.5), 'z_val': 0.}
        if row['rpc'] == 'getLidarData':
            row['payload']['pose']['position'] = {'x_val': 9.5, 'y_val': -2., 'z_val': 3.}
            row['payload']['pose']['orientation'] = {'w_val': math.sqrt(.5), 'x_val': 0., 'y_val': math.sqrt(.5), 'z_val': 0.}
    comparison = summarize(api)['samples'][0]['lidar']['mount_comparison_before']
    assert comparison['position_error_m'] == pytest.approx(0., abs=1e-12)
    assert comparison['orientation_error_deg'] == pytest.approx(0., abs=1e-5)


def test_cli_preserves_input_and_refuses_existing_output(tmp_path, monkeypatch):
    import json
    import sys
    source, output = tmp_path / 'raw.json', tmp_path / 'summary.json'
    original = json.dumps(api_fixture()).encode()
    source.write_bytes(original)
    monkeypatch.setattr(sys, 'argv', ['summary', '--input', str(source), '--output', str(output)])
    runner = module()
    runner.main()
    first = output.read_bytes()
    with pytest.raises(FileExistsError): runner.main()
    assert output.read_bytes() == first and source.read_bytes() == original
