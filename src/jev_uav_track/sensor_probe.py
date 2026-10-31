"""Bounded raw-RPC diagnostics, NOT a safety provider or clearance estimator.

The caller must own the instance. Collision reads may consume an event flag.
The real client must enforce its own RPC timeout; the owner process supplies a
hard termination deadline. No workers, retries, settings changes or flight RPCs.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import time


_STATE = 'getMultirotorState'
_COLLISION = 'simGetCollisionInfo'
_DISTANCE = 'getDistanceSensorData'
_LIDAR = 'getLidarData'
_FIELDS = {
    _STATE: ('timestamp',),
    _COLLISION: ('has_collided', 'time_stamp', 'penetration_depth'),
    _DISTANCE: ('time_stamp', 'distance', 'min_distance', 'max_distance', 'relative_pose'),
    _LIDAR: ('time_stamp', 'point_cloud', 'pose'),
}


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _stamp(value, *, zero=False):
    return type(value) is int and (0 if zero else 1) <= value < 2**64


def _pose(value):
    if type(value) is not dict:
        return False
    for part, fields in [('position', ('x_val', 'y_val', 'z_val')),
                         ('orientation', ('w_val', 'x_val', 'y_val', 'z_val'))]:
        item = value.get(part)
        if type(item) is not dict or any(not _number(item.get(key)) for key in fields):
            return False
    return any(value['orientation'][key] != 0 for key in ('w_val', 'x_val', 'y_val', 'z_val'))


def _pose_projection(value):
    return {part: {key: value[part][key] for key in fields} for part, fields in (
        ('position', ('x_val', 'y_val', 'z_val')),
        ('orientation', ('w_val', 'x_val', 'y_val', 'z_val')))}


def _project(method, raw):
    """Require received keys; never read SDK class-default attributes or log errors."""
    result = {'payload': None, 'payload_status': 'INVALID_PAYLOAD', 'missing_fields': []}
    if type(raw) is not dict:
        return result
    missing = [key for key in _FIELDS[method] if key not in raw]
    if missing:
        return dict(result, payload_status='MISSING_FIELDS', missing_fields=missing)
    result['payload_status'] = 'INVALID_VALUE'
    if method == _STATE:
        if not _stamp(raw['timestamp']):
            return result
        payload = {'timestamp': raw['timestamp']}
    elif method == _COLLISION:
        if (type(raw['has_collided']) is not bool or not _stamp(raw['time_stamp'], zero=True)
                or (raw['has_collided'] and raw['time_stamp'] == 0)
                or not _number(raw['penetration_depth']) or raw['penetration_depth'] < 0):
            return result
        payload = {key: raw[key] for key in _FIELDS[method]}
    elif method == _DISTANCE:
        if (not _stamp(raw['time_stamp']) or not _pose(raw['relative_pose'])
                or any(not _number(raw[key]) for key in ('distance', 'min_distance', 'max_distance'))
                or raw['distance'] < 0 or not 0 <= raw['min_distance'] < raw['max_distance']):
            return result
        payload = {key: raw[key] for key in _FIELDS[method] if key != 'relative_pose'}
        payload['relative_pose'] = _pose_projection(raw['relative_pose'])
    else:
        points = raw['point_cloud']
        if (not _stamp(raw['time_stamp']) or not _pose(raw['pose'])
                or type(points) not in (list, tuple) or len(points) % 3):
            return result
        if len(points) > 30000:
            return dict(result, payload_status='PAYLOAD_TOO_LARGE')
        if any(not _number(point) for point in points):
            return result
        encoded = json.dumps(list(points), allow_nan=False, separators=(',', ':')).encode('utf-8')
        payload = {'time_stamp': raw['time_stamp'], 'pose': _pose_projection(raw['pose']),
                   'point_count': len(points) // 3, 'point_cloud_preview': list(points[:12]),
                   'point_cloud_sha256': hashlib.sha256(encoded).hexdigest()}
    result.update(payload=deepcopy(payload), payload_status='VALID_FIELDS')
    if method == _LIDAR and not points:
        result['payload_status'] = 'EMPTY_POINT_CLOUD'
    if method == _DISTANCE:
        if raw['distance'] >= raw['max_distance']:
            result['payload_status'] = 'SATURATED_RANGE'
        elif raw['distance'] < raw['min_distance']:
            result['payload_status'] = 'BELOW_MIN_RANGE'
    return result


def probe_safety_inputs(rpc_call, *, clock=time.monotonic, sleep=time.sleep, sample_count=6):
    """At most six samples / 30 RPCs / 8s soft budget. Never authorize control.

    Each RPC is made once. A transport error disables that optional sensor;
    timeouts, state errors, or host/state clock reversal abort the diagnostic.
    Unexpected exception text and unrequested payload fields are not persisted.
    """
    if type(sample_count) is not int or not 1 <= sample_count <= 6:
        raise ValueError('sample_count must be an integer in 1..6')
    report = {'kind': 'SAFETY_SENSOR_DIAGNOSTIC_NO_MOTION', 'status': 'DIAGNOSTIC_COMPLETED',
              'sample_count_requested': sample_count, 'completed_samples': 0,
              'rpc_count': 0, 'queries': [], 'samples': [], 'abort_reason': None,
              'control_ready': False, 'clearance_m': None,
              'collision_event_observed': None, 'state_clock_advanced': False,
              'source_clock_domain': 'AIRSIM_REPORTED_NS_NOT_CALIBRATED',
              'collision_read_semantics': 'MAY_CONSUME_EVENT_FLAG_LOCAL_SEMANTICS_UNVERIFIED',
              'measurement_readiness': 'NOT_CERTIFIED',
              'budget_s': 8., 'rpc_timeout_required_s': 1.}
    floor = None
    last_state = None
    last_sensor = {}
    disabled = set()

    def abort(reason):
        report.update(status='DIAGNOSTIC_ABORTED', abort_reason=reason)

    def now():
        nonlocal floor
        value = clock()
        if not _number(value) or value < 0 or (floor is not None and value < floor):
            abort('HOST_CLOCK_INVALID')
            return None
        floor = value
        return value

    beginning = now()
    if beginning is None:
        return report
    deadline = beginning + report['budget_s']

    def read(method, index):
        nonlocal last_state
        start = now()
        if start is None:
            return None
        if start >= deadline:
            abort('DIAGNOSTIC_BUDGET_EXHAUSTED')
            return None
        row = {'rpc': method, 'sample_index': index, 'wall_start_s': start, 'wall_end_s': None,
               'transport_status': 'RECEIVED', 'payload_status': 'NOT_RECEIVED', 'payload': None,
               'temporal_status': 'NOT_ASSESSED', 'missing_fields': []}
        report['queries'].append(row)
        report['rpc_count'] += 1
        args = ('', 'JevDrone') if method in (_DISTANCE, _LIDAR) else ('JevDrone',)
        raw = None
        try:
            raw = rpc_call(method, *args)
        except Exception as exc:
            # msgpackrpc.TimeoutError is not necessarily builtins.TimeoutError.
            row['transport_status'] = ('RPC_TIMEOUT' if isinstance(exc, TimeoutError)
                or type(exc).__name__ in ('TimeoutError', 'TimeoutException') else 'RPC_ERROR')
        end = now()
        row['wall_end_s'] = end
        if end is None:
            return row
        row['elapsed_s'] = end - start
        row['receipt_latency_status'] = 'WITHIN_200MS' if end - start < .2 else 'EXCEEDS_200MS'
        if row['transport_status'] == 'RECEIVED':
            row.update(_project(method, raw))
        if end >= deadline:
            abort('DIAGNOSTIC_BUDGET_EXHAUSTED')
        elif row['transport_status'] == 'RPC_TIMEOUT':
            abort('RPC_TIMEOUT')
        elif method == _STATE:
            if row['payload'] is None:
                abort('STATE_PAYLOAD_INVALID')
            else:
                stamp = row['payload']['timestamp']
                if last_state is not None and stamp < last_state:
                    abort('STATE_CLOCK_REVERSED')
                elif last_state is not None and stamp > last_state:
                    report['state_clock_advanced'] = True
                last_state = stamp
        elif row['transport_status'] == 'RPC_ERROR':
            disabled.add(method)
        if method == _COLLISION and row['payload'] is not None:
            report['collision_event_observed'] = bool(
                report['collision_event_observed'] or row['payload']['has_collided'])
        return row

    for index in range(sample_count):
        before = read(_STATE, index)
        if report['abort_reason']:
            break
        sample = {'index': index, 'state_before_ns': before['payload']['timestamp'],
                  'state_after_ns': None, 'timing_status': 'INCOMPLETE'}
        report['samples'].append(sample)
        measurements = []
        for method in (_COLLISION, _DISTANCE, _LIDAR):
            if method not in disabled:
                row = read(method, index)
                if row is not None:
                    measurements.append(row)
                if report['abort_reason']:
                    break
        if report['abort_reason']:
            break
        after = read(_STATE, index)
        if report['abort_reason']:
            break
        after_ns = after['payload']['timestamp']
        sample['state_after_ns'] = after_ns
        sample['acquisition_elapsed_s'] = after['wall_end_s'] - before['wall_start_s']
        sample['timing_status'] = ('WITHIN_200MS' if sample['acquisition_elapsed_s'] < .2
                                   else 'EXCEEDS_200MS')
        for row in measurements:
            if row['payload'] is None:
                continue
            stamp = row['payload']['time_stamp']
            method = row['rpc']
            if method == _COLLISION:
                row['temporal_status'] = ('FUTURE_EVENT_TIMESTAMP' if stamp > after_ns
                                          else 'EVENT_TIME_NOT_SAMPLE_FRESHNESS')
            elif method in last_sensor and stamp < last_sensor[method]:
                row['temporal_status'] = 'SENSOR_TIME_REVERSED'
            else:
                age = after_ns - stamp
                row['numeric_age_to_after_state_ns'] = age
                row['temporal_status'] = ('FUTURE_SENSOR_TIMESTAMP' if age < 0 else
                    'STALE_SENSOR_TIMESTAMP' if age >= 200_000_000 else
                    'REPEATED_SENSOR_TIMESTAMP' if last_sensor.get(method) == stamp else
                    'NUMERIC_RECENT_NOT_CLOCK_CALIBRATED')
            last_sensor[method] = stamp
        report['completed_samples'] += 1
        if index + 1 < sample_count:
            sleep(.1)
    return report
