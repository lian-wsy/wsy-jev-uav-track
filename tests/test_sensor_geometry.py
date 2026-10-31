"""Geometry archival never invents missing fields or authorizes motion."""
from copy import deepcopy
import hashlib
import inspect
import json

import pytest

from jev_uav_track.sensor_probe import probe_safety_inputs
from test_sensor_probe import Clock, SensorRpc, pose, queries


def geometry_rpc():
    rpc = SensorRpc(Clock())
    rpc.overrides['getMultirotorState'] = lambda data: dict(data,
        kinematics_estimated=dict(pose(), linear_velocity={'x_val': .1, 'y_val': .2, 'z_val': .3},
            angular_velocity={'x_val': 0., 'y_val': 0., 'z_val': .01}), landed_state=0)
    rpc.overrides['getLidarData'] = lambda data: dict(data, point_cloud=[3., 4., 0.] * 5)
    return rpc


def run_geometry(rpc, count=1):
    assert 'capture_geometry' in inspect.signature(probe_safety_inputs).parameters, 'Geometry archival missing'
    return probe_safety_inputs(rpc, clock=rpc.clock, sleep=rpc.clock.sleep,
                              sample_count=count, capture_geometry=True)


def test_archives_full_bounded_cloud_own_pose_and_contact_without_extra_rpcs():
    rpc = geometry_rpc()
    report = run_geometry(rpc, 6)
    assert report['rpc_count'] == len(rpc.calls) == 30
    assert report['capture_geometry'] is True
    assert report['geometry_schema_version'] == 1
    state = queries(report, 'getMultirotorState')[0]
    assert state['geometry_status'] == 'VALID_FIELDS_NOT_CALIBRATED'
    assert state['geometry']['kinematics_estimated']['linear_velocity'] == {
        'x_val': .1, 'y_val': .2, 'z_val': .3}
    assert state['geometry']['landed_state'] == 0
    collision = queries(report, 'simGetCollisionInfo')[0]
    assert collision['geometry']['object_name'] == ''
    assert collision['geometry']['object_id'] == -1
    assert collision['geometry']['normal'] == {'x_val': 0., 'y_val': 0., 'z_val': 0.}
    lidar = queries(report, 'getLidarData')[0]
    assert lidar['geometry']['point_cloud'] == [3., 4., 0.] * 5
    encoded = json.dumps(lidar['geometry']['point_cloud'], separators=(',', ':')).encode()
    assert hashlib.sha256(encoded).hexdigest() == lidar['payload']['point_cloud_sha256']
    assert lidar['payload']['point_count'] == 5
    assert report['control_ready'] is False and report['clearance_m'] is None
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('method,field', [('getMultirotorState', 'kinematics_estimated'),
    ('getMultirotorState', 'landed_state'), ('simGetCollisionInfo', 'normal'),
    ('simGetCollisionInfo', 'object_name'), ('simGetCollisionInfo', 'impact_point'),
    ('simGetCollisionInfo', 'position'), ('simGetCollisionInfo', 'object_id')])
def test_missing_geometry_keeps_base_timestamp_or_event_not_fake_defaults(method, field):
    rpc = geometry_rpc()
    original = rpc.overrides.get(method, lambda data: data)
    rpc.overrides[method] = lambda data: {k: v for k, v in original(data).items() if k != field}
    row = queries(run_geometry(rpc), method)[0]
    assert row['payload_status'] == 'VALID_FIELDS'
    assert row['geometry_status'] == 'MISSING_FIELDS'
    assert field in row['geometry_missing_fields']
    assert row['geometry'] is None


@pytest.mark.parametrize('field,value', [('object_name', 'x' * 257), ('object_name', 'bad\nname'),
    ('object_id', True), ('object_id', 2**31), ('normal', {'x_val': float('nan')}),
    ('impact_point', {}), ('position', None)])
def test_bad_contact_geometry_is_not_logged_as_valid(field, value):
    rpc = geometry_rpc()
    rpc.overrides['simGetCollisionInfo'] = lambda data: dict(data, **{field: value})
    report = run_geometry(rpc)
    row = queries(report, 'simGetCollisionInfo')[0]
    assert row['geometry_status'] == 'INVALID_VALUE' and row['geometry'] is None
    assert row['payload']['has_collided'] is False
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('field,value', [('linear_velocity', {}), ('angular_velocity', None),
    ('orientation', {'w_val': 2., 'x_val': 0., 'y_val': 0., 'z_val': 0.}),
    ('position', {'x_val': 0., 'y_val': float('inf'), 'z_val': 0.})])
def test_invalid_kinematics_cannot_be_used_for_geometric_alignment(field, value):
    rpc = geometry_rpc()
    original = rpc.overrides['getMultirotorState']
    def corrupted(data):
        data = original(data)
        data['kinematics_estimated'][field] = value
        return data
    rpc.overrides['getMultirotorState'] = corrupted
    report = run_geometry(rpc)
    row = queries(report, 'getMultirotorState')[0]
    assert row['payload']['timestamp'] == 1_002_000_000
    assert row['geometry'] is None and row['geometry_status'] == 'INVALID_VALUE'
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('count,status', [(0, 'EMPTY_POINT_CLOUD'), (10000, 'VALID_FIELDS_NOT_CALIBRATED'),
                                        (10001, 'PAYLOAD_TOO_LARGE')])
def test_point_cloud_limit_does_not_silently_truncate_or_certify_empty_space(count, status):
    rpc = geometry_rpc()
    rpc.overrides['getLidarData'] = lambda data: dict(data, point_cloud=[1., 2., 3.] * count)
    report = run_geometry(rpc)
    row = queries(report, 'getLidarData')[0]
    assert row['geometry_status'] == status
    if count <= 10000:
        assert len(row['geometry']['point_cloud']) == count * 3
    else:
        assert row['geometry'] is None and row['payload'] is None
    assert report['clearance_m'] is None


def test_unknown_fields_are_omitted_and_archived_values_are_detached():
    rpc = geometry_rpc()
    retained = []
    original = rpc.overrides['getMultirotorState']
    def extra(data):
        data = original(data)
        data['private_unrequested'] = 'UNEXPECTED_PRIVATE_VALUE'
        data['kinematics_estimated']['private_unrequested'] = 'UNEXPECTED_PRIVATE_VALUE'
        retained.append(data)
        return data
    rpc.overrides['getMultirotorState'] = extra
    report = run_geometry(rpc)
    saved = deepcopy(report)
    for raw in retained:
        raw['kinematics_estimated']['position']['x_val'] = 999.
    assert report == saved
    assert 'UNEXPECTED_PRIVATE_VALUE' not in json.dumps(report)


def test_geometry_default_off_keeps_preview_only():
    rpc = geometry_rpc()
    report = probe_safety_inputs(rpc, clock=rpc.clock, sleep=rpc.clock.sleep, sample_count=1)
    assert report.get('capture_geometry') is False
    row = queries(report, 'getLidarData')[0]
    assert row.get('geometry') is None
    assert row['payload']['point_cloud_preview'] == [3., 4., 0.] * 4


@pytest.mark.parametrize('scale', [2., 1e308])
def test_nonunit_lidar_pose_is_not_valid_alignment_geometry(scale):
    rpc = geometry_rpc()
    def invalid(data):
        data['pose']['orientation']['w_val'] = scale
        data['point_cloud'] = [1., 0., 0.]
        return data
    rpc.overrides['getLidarData'] = invalid
    row = queries(run_geometry(rpc), 'getLidarData')[0]
    assert row['payload'] is not None  # Numeric raw fields remain diagnostic evidence.
    assert row['geometry'] is None
    assert row['geometry_status'] == 'INVALID_VALUE'


@pytest.mark.parametrize('enabled', [1, 'true', None])
def test_geometry_flag_requires_boolean_before_any_rpc(enabled):
    assert 'capture_geometry' in inspect.signature(probe_safety_inputs).parameters
    rpc = geometry_rpc()
    with pytest.raises(ValueError):
        probe_safety_inputs(rpc, capture_geometry=enabled)
    assert rpc.calls == []
