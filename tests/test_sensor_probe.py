"""Actual diagnostic parser/runner; only the unavailable simulator RPC is doubled."""
import importlib
import importlib.util
import json
import inspect

import pytest


def probe():
    name = 'jev_uav_track.sensor_probe'
    assert importlib.util.find_spec(name) is not None, 'Safety sensor diagnostic is missing'
    return importlib.import_module(name).probe_safety_inputs


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def pose():
    return {'position': {'x_val': 0., 'y_val': 0., 'z_val': 0.},
            'orientation': {'w_val': 1., 'x_val': 0., 'y_val': 0., 'z_val': 0.}}


class SensorRpc:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.overrides = {}

    def __call__(self, method, *args):
        self.calls.append((method, args))
        self.clock.now += .002
        stamp = 1_000_000_000 + round(self.clock.now * 1e9)
        if method == 'getMultirotorState':
            assert args == ('JevDrone',)
            result = {'timestamp': stamp, 'kinematics_estimated': pose(), 'landed_state': 0,
                      'collision': {}, 'gps_location': {}, 'rc_data': {}, 'ready': True,
                      'ready_message': '', 'can_arm': True}
        elif method == 'simGetCollisionInfo':
            assert args == ('JevDrone',)
            result = {'has_collided': False, 'time_stamp': 500_000_000, 'penetration_depth': 0.,
                      'normal': pose()['position'], 'impact_point': pose()['position'],
                      'position': pose()['position'], 'object_name': '', 'object_id': -1}
        elif method == 'getDistanceSensorData':
            assert args == ('', 'JevDrone')
            result = {'time_stamp': stamp, 'distance': 20., 'min_distance': .2,
                      'max_distance': 40., 'relative_pose': pose()}
        elif method == 'getLidarData':
            assert args == ('', 'JevDrone')
            result = {'time_stamp': stamp, 'point_cloud': [], 'pose': pose(), 'segmentation': []}
        else:
            pytest.fail('Forbidden RPC: ' + method)
        override = self.overrides.get(method)
        return override(result) if override else result


def run_rpc(rpc, count=2):
    return probe()(rpc, clock=rpc.clock, sleep=rpc.clock.sleep, sample_count=count)


def queries(report, method):
    return [item for item in report['queries'] if item['rpc'] == method]


def test_explicit_sensor_names_reach_rpc_and_receipt_without_extra_calls():
    runner = probe()
    assert 'distance_sensor_name' in inspect.signature(runner).parameters, 'Named distance query missing'
    rpc = SensorRpc(Clock())
    named_calls = []
    def named_rpc(method, *args):
        named_calls.append((method, args))
        if method == 'getDistanceSensorData':
            assert args == ('JevDistanceFront', 'JevDrone')
            return rpc(method, '', 'JevDrone')
        if method == 'getLidarData':
            assert args == ('JevLidarDiagnostic', 'JevDrone')
            return rpc(method, '', 'JevDrone')
        return rpc(method, *args)
    result = runner(named_rpc, clock=rpc.clock, sleep=rpc.clock.sleep,
        distance_sensor_name='JevDistanceFront', lidar_name='JevLidarDiagnostic')
    assert result['rpc_count'] == len(named_calls) == 30
    assert result['sensor_names'] == {'distance': 'JevDistanceFront', 'lidar': 'JevLidarDiagnostic'}
    assert queries(result, 'getLidarData')[0]['sensor_name'] == 'JevLidarDiagnostic'
    assert result['control_ready'] is False and result['clearance_m'] is None


@pytest.mark.parametrize('name', [None, 5, 'bad/name', 'line\nbreak', 'x' * 65])
def test_invalid_sensor_name_rejects_before_rpc(name):
    runner = probe()
    assert 'distance_sensor_name' in inspect.signature(runner).parameters, 'Named distance query missing'
    def forbidden(*args): pytest.fail('Bad name reached RPC')
    with pytest.raises(ValueError): runner(forbidden, distance_sensor_name=name)


def test_only_four_read_interfaces_are_used_and_no_control_clearance_is_invented():
    clock = Clock()
    rpc = SensorRpc(clock)
    report = run_rpc(rpc)
    assert report['status'] == 'DIAGNOSTIC_COMPLETED'
    assert report['completed_samples'] == 2
    assert report['rpc_count'] == 10
    assert set(name for name, _ in rpc.calls) == {
        'getMultirotorState', 'simGetCollisionInfo', 'getDistanceSensorData', 'getLidarData'}
    assert report['control_ready'] is False and report['clearance_m'] is None
    assert report['state_clock_advanced'] is True
    distance = queries(report, 'getDistanceSensorData')[0]
    assert distance['payload']['distance'] == 20.
    assert distance['temporal_status'] == 'NUMERIC_RECENT_NOT_CLOCK_CALIBRATED'
    assert distance['wall_start_s'] < distance['wall_end_s']
    assert queries(report, 'getLidarData')[0]['payload_status'] == 'EMPTY_POINT_CLOUD'
    json.dumps(report, allow_nan=False)


def test_collision_event_time_is_preserved_and_seen_event_is_not_cleared_by_next_read():
    rpc = SensorRpc(Clock())
    remaining = [True, False]
    rpc.overrides['simGetCollisionInfo'] = lambda data: dict(data, has_collided=remaining.pop(0))
    report = run_rpc(rpc)
    observations = queries(report, 'simGetCollisionInfo')
    assert [row['payload']['has_collided'] for row in observations] == [True, False]
    assert all(row['payload']['time_stamp'] == 500_000_000 for row in observations)
    assert all(row['temporal_status'] == 'EVENT_TIME_NOT_SAMPLE_FRESHNESS' for row in observations)
    assert report['collision_event_observed'] is True
    assert report['control_ready'] is False


@pytest.mark.parametrize('method', ['simGetCollisionInfo', 'getDistanceSensorData', 'getLidarData'])
def test_missing_payload_fields_are_never_replaced_by_class_defaults(method):
    rpc = SensorRpc(Clock())
    rpc.overrides[method] = lambda _: {}
    report = run_rpc(rpc)
    row = queries(report, method)[0]
    assert row['payload_status'] == 'MISSING_FIELDS'
    assert row['missing_fields'] and row['payload'] is None
    if method == 'simGetCollisionInfo':
        assert report['collision_event_observed'] is None
    assert report['control_ready'] is False


@pytest.mark.parametrize('field,value', [('time_stamp', True), ('time_stamp', 0),
    ('distance', float('nan')), ('distance', float('inf')), ('distance', -1),
    ('max_distance', 0), ('relative_pose', {}), ('relative_pose', {'position': {}})])
def test_invalid_distance_values_do_not_escape_into_valid_measurement_or_json(field, value):
    rpc = SensorRpc(Clock())
    rpc.overrides['getDistanceSensorData'] = lambda data: dict(data, **{field: value})
    report = run_rpc(rpc)
    row = queries(report, 'getDistanceSensorData')[0]
    assert row['payload_status'] == 'INVALID_VALUE' and row['payload'] is None
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('age,expected', [(200_000_000, 'STALE_SENSOR_TIMESTAMP'),
                                        (-100_000_000, 'FUTURE_SENSOR_TIMESTAMP')])
def test_numeric_age_rejects_stale_or_future_sensor_timestamps(age, expected):
    rpc = SensorRpc(Clock())
    # In the first sample the after-state is 1.010s, independently hand-counted.
    rpc.overrides['getDistanceSensorData'] = lambda data: dict(data, time_stamp=1_010_000_000-age)
    report = run_rpc(rpc, 1)
    assert queries(report, 'getDistanceSensorData')[0]['temporal_status'] == expected


def test_saturated_distance_is_recorded_but_never_promoted_to_clearance():
    rpc = SensorRpc(Clock())
    rpc.overrides['getDistanceSensorData'] = lambda data: dict(data, distance=40.)
    report = run_rpc(rpc, 1)
    assert queries(report, 'getDistanceSensorData')[0]['payload_status'] == 'SATURATED_RANGE'
    assert report['clearance_m'] is None


@pytest.mark.parametrize('points', [[1., 2.], [1., 2., float('nan')], [True, 0., 0.]])
def test_lidar_invalid_shape_and_nonfinite_points_are_rejected(points):
    rpc = SensorRpc(Clock())
    rpc.overrides['getLidarData'] = lambda data: dict(data, point_cloud=points)
    report = run_rpc(rpc, 1)
    assert queries(report, 'getLidarData')[0]['payload_status'] == 'INVALID_VALUE'
    json.dumps(report, allow_nan=False)


def test_nonempty_lidar_is_bounded_diagnostic_data_not_minimum_clearance():
    rpc = SensorRpc(Clock())
    rpc.overrides['getLidarData'] = lambda data: dict(data, point_cloud=[3., 4., 0.])
    report = run_rpc(rpc, 1)
    row = queries(report, 'getLidarData')[0]
    assert row['payload']['point_count'] == 1
    assert row['payload']['point_cloud_preview'] == [3., 4., 0.]
    assert report['clearance_m'] is None and report['control_ready'] is False


def test_unavailable_sensor_is_called_once_without_retry_or_exception_text_leakage():
    rpc = SensorRpc(Clock())
    def unavailable(_):
        raise RuntimeError('private arbitrary exception text')
    rpc.overrides['getDistanceSensorData'] = unavailable
    report = run_rpc(rpc, 3)
    assert len(queries(report, 'getDistanceSensorData')) == 1
    assert queries(report, 'getDistanceSensorData')[0]['transport_status'] == 'RPC_ERROR'
    assert len(queries(report, 'simGetCollisionInfo')) == 3
    assert 'private arbitrary' not in json.dumps(report)


def test_timeout_aborts_without_more_reads_or_implicit_retry():
    rpc = SensorRpc(Clock())
    def timeout(_):
        rpc.clock.now += 1.
        raise TimeoutError('not logged')
    rpc.overrides['simGetCollisionInfo'] = timeout
    report = run_rpc(rpc)
    assert report['status'] == 'DIAGNOSTIC_ABORTED'
    assert report['abort_reason'] == 'RPC_TIMEOUT'
    assert [name for name, _ in rpc.calls] == ['getMultirotorState', 'simGetCollisionInfo']


def test_budget_overrun_stops_at_return_even_if_transport_did_not_raise():
    rpc = SensorRpc(Clock())
    def slow(data):
        rpc.clock.now += 9.
        return data
    rpc.overrides['simGetCollisionInfo'] = slow
    report = run_rpc(rpc)
    assert report['abort_reason'] == 'DIAGNOSTIC_BUDGET_EXHAUSTED'
    assert len(rpc.calls) == 2
    assert report['control_ready'] is False


def test_state_clock_reversal_aborts_before_next_consuming_collision_read():
    rpc = SensorRpc(Clock())
    states = [1_000_000_000, 1_100_000_000, 900_000_000]
    rpc.overrides['getMultirotorState'] = lambda data: dict(data, timestamp=states.pop(0))
    report = run_rpc(rpc)
    assert report['abort_reason'] == 'STATE_CLOCK_REVERSED'
    assert len(queries(report, 'simGetCollisionInfo')) == 1


def test_invalid_host_clock_aborts_without_writing_nonfinite_json():
    rpc = SensorRpc(Clock())
    def reverse(data):
        rpc.clock.now = -1.
        return data
    rpc.overrides['simGetCollisionInfo'] = reverse
    report = run_rpc(rpc)
    assert report['abort_reason'] == 'HOST_CLOCK_INVALID'
    assert len(rpc.calls) == 2
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('count', [0, 7, True, 1.5])
def test_sample_budget_is_bounded_before_rpc(count):
    rpc = SensorRpc(Clock())
    with pytest.raises(ValueError):
        run_rpc(rpc, count)
    assert rpc.calls == []


def test_default_diagnostic_clock_preserves_subtick_rtt_and_records_its_resolution(monkeypatch):
    module = importlib.import_module('jev_uav_track.sensor_probe')
    clock = Clock()
    rpc = SensorRpc(clock)
    # A 2ms RPC must not disappear into Windows Python3.10's 15.625ms tick clock.
    monkeypatch.setattr(module.time, 'perf_counter', clock)
    report = probe()(rpc, sleep=clock.sleep, sample_count=1)
    row = report['queries'][0]
    assert row['wall_start_s'] == 0.0
    assert row['elapsed_s'] == .002
    assert report['host_clock']['source'] == 'time.perf_counter'
    assert report['host_clock']['monotonic'] is True
    assert report['host_clock']['resolution_s'] > 0
