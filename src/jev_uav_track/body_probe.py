"""Own-body pose/catalog diagnostic; no arming, flight, pause or scene writes.

CARLA's environment catalog is registration-time render geometry, not a
current collision envelope. Pose agreement at rest is not origin calibration.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
import time

from .frames import camera_mount_error
from .isolated_runtime import require_loaded_map
from .sensor_probe import _project, _geometry_projection, _pose_projection, _pose


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Invalid received number')
    return value


def _xyz(value):
    return [_number(value[key]) for key in ('x_val', 'y_val', 'z_val')]


def _q(value):
    result = [_number(value[key]) for key in ('x_val', 'y_val', 'z_val', 'w_val')]
    if abs(math.hypot(*result) - 1.) > .001:
        raise ValueError('Nonunit received orientation')
    return result


def _received_pose(raw):
    if not _pose(raw):
        raise ValueError('Missing or invalid received pose')
    _q(raw['orientation'])
    return _pose_projection(raw)


def _catalog(world, carla):
    result = {'status': 'NOT_READ', 'matching_objects': [],
              'temporal_semantics': 'REGISTRATION_CATALOG_NOT_CURRENT_POSE'}
    try:
        objects = world.get_environment_objects(carla.CityObjectLabel.Any)
        result['queried_count'] = len(objects)
        if len(objects) > 50000:
            result['status'] = 'CATALOG_LIMIT_EXCEEDED'
            return result
        matches = [obj for obj in objects if re.fullmatch(r'JevDrone(?:_[A-Za-z0-9]+)*', obj.name)]
        if len(matches) > 64:
            result['status'] = 'MATCH_LIMIT_EXCEEDED'
            return result
        def vec(value): return [_number(value.x), _number(value.y), _number(value.z)]
        def rot(value): return [_number(value.pitch), _number(value.yaw), _number(value.roll)]
        for obj in sorted(matches, key=lambda item: item.name):
            if type(obj.id) is not int or not 0 <= obj.id < 2**64 or len(obj.name) > 128:
                raise ValueError('Invalid catalog identity')
            bb, transform = obj.bounding_box, obj.transform
            extents = vec(bb.extent)
            if any(e < 0 for e in extents):
                raise ValueError('Invalid catalog bounds')
            result['matching_objects'].append({'name': obj.name, 'id_decimal': str(obj.id),
                'registration_transform_xyz_pitch_yaw_roll': vec(transform.location) + rot(transform.rotation),
                'reported_box_center_m': vec(bb.location), 'reported_box_extent_m': extents,
                'reported_box_rotation_deg': rot(bb.rotation)})
        result['status'] = 'CATALOG_READ_NOT_COLLISION_CERTIFIED'
    except Exception as exc:
        result.update(status='CATALOG_ERROR', error_type=type(exc).__name__, matching_objects=[])
    return result


def compare_body_sample(row):
    """Compare only same-local-frame state/vehicle positions; retain other offset.

    A consistent STATIC sample is explicitly not full dynamic calibration and
    does not erase prior contact or authorize support-contact exceptions.
    """
    before, after = row['state_before'], row['state_after']
    kin = [state['geometry']['kinematics_estimated'] for state in (before, after)]
    local, glob = row['vehicle_pose']['payload'], row['object_pose']['payload']
    local_p, local_q = _xyz(local['position']), _q(local['orientation'])
    errors = [camera_mount_error(_xyz(k['position']), _q(k['orientation']), (0, 0, 0),
                                local_p, local_q) for k in kin]
    global_rotation = camera_mount_error((0, 0, 0), _q(glob['orientation']), (0, 0, 0),
                                        (0, 0, 0), local_q)['orientation_error_deg']
    result = {'maximum_local_position_error_m': max(e['position_error_m'] for e in errors),
              'maximum_local_orientation_error_deg': max(e['orientation_error_deg'] for e in errors),
              'global_local_orientation_error_deg': global_rotation,
              'local_minus_global_m': [a - b for a, b in zip(local_p, _xyz(glob['position']))],
              'acquisition_elapsed_s': after['wall_end_s'] - before['wall_start_s']}
    for value in result.values():
        for scalar in value if isinstance(value, list) else [value]:
            _number(scalar)  # Finite inputs can still overflow during subtraction.
    if result['acquisition_elapsed_s'] >= .2:
        status = 'WINDOW_EXPIRED'
    elif row['collision']['payload']['time_stamp'] > after['payload']['timestamp']:
        status = 'FUTURE_COLLISION_EVENT'
    elif any(math.hypot(*_xyz(k[field])) > .05 for k in kin for field in ('linear_velocity', 'angular_velocity')):
        status = 'NOT_STATIC'
    elif result['maximum_local_position_error_m'] > .02 or result['maximum_local_orientation_error_deg'] > 1.:
        status = 'LOCAL_POSE_DISAGREEMENT'
    elif global_rotation > 1.:
        status = 'GLOBAL_LOCAL_ORIENTATION_DISAGREEMENT'
    else:
        status = 'STATIC_POSE_CONSISTENT_NOT_CALIBRATION'
    return dict(result, status=status)


def probe_own_body(world, rpc_call, carla, *, clock=time.perf_counter, sleep=time.sleep):
    require_loaded_map(world.get_map().name, 'Town02')
    started = last_now = None
    report = {'kind': 'OWN_BODY_DIAGNOSTIC_NO_MOTION', 'schema_version': 1,
              'status': 'DIAGNOSTIC_ABORTED', 'control_ready': False, 'clearance_m': None,
              'body_envelope_status': 'NOT_CERTIFIED', 'samples': [], 'queries': [],
              'catalog': {'status': 'NOT_READ'}}

    def now():
        nonlocal last_now
        value = _number(clock())
        if value < 0 or (last_now is not None and value < last_now):
            raise ValueError('Host clock invalid or reversed')
        last_now = value
        return value

    def read(method):
        start = now()
        if start - started >= 8.:
            raise TimeoutError('Own-body soft budget exceeded')
        row = {'rpc': method, 'wall_start_s': start, 'wall_end_s': None,
               'transport_status': 'NOT_RETURNED', 'payload': None}
        report['queries'].append(row)
        try:
            raw = rpc_call(method, 'JevDrone')
            row['wall_end_s'] = now()
            row['transport_status'] = 'RETURNED'
            if method == 'simListSceneObjects':
                if raw != ['JevDrone']:
                    raise ValueError('Exact owned actor not uniquely present')
                row['payload'] = ['JevDrone']
            elif method in ('simGetObjectPose', 'simGetVehiclePose'):
                row['payload'] = _received_pose(raw)
            else:
                base = _project(method, raw)
                row.update(base)
                row.update(_geometry_projection(method, raw, base))
                if row['payload'] is None or row['geometry'] is None:
                    raise ValueError('Received state or contact geometry incomplete')
            if row['wall_end_s'] - started >= 8.:
                raise TimeoutError('Own-body soft budget exceeded')
        except Exception as exc:
            if row['wall_end_s'] is None:
                try:
                    row['wall_end_s'] = now()
                except (ValueError, OverflowError, TypeError):
                    pass  # Keep null, never persist a nonfinite or reversed clock.
            row['error_type'] = type(exc).__name__
            raise
        return row

    try:
        started = now()
        report['catalog'] = _catalog(world, carla)
        if report['catalog']['status'] != 'CATALOG_READ_NOT_COLLISION_CERTIFIED':
            raise ValueError('Catalog unavailable')
        read('simListSceneObjects')
        last_stamp = None
        for index in range(6):
            before = read('getMultirotorState')
            stamp = before['payload']['timestamp']
            if last_stamp is not None and stamp <= last_stamp:
                raise ValueError('State source clock did not advance across samples')
            row = {'index': index, 'state_before': before}
            report['samples'].append(row)
            row['object_pose'] = read('simGetObjectPose')
            row['vehicle_pose'] = read('simGetVehiclePose')
            row['collision'] = read('simGetCollisionInfo')
            after = read('getMultirotorState')
            row['state_after'] = after
            last_stamp = after['payload']['timestamp']
            if last_stamp < stamp:
                raise ValueError('State source clock reversed inside sample')
            row['comparison'] = compare_body_sample(row)
            if index < 5: sleep(.1)
        if now() - started >= 8.:
            raise TimeoutError('Own-body soft budget exceeded')
        report['status'] = 'DIAGNOSTIC_COMPLETED'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
    report['elapsed_s'] = None if started is None else last_now - started
    return deepcopy(report)


def _same_typed(left, right):
    """JSON value equality with bool/int/float distinctions preserved."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_typed(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_typed(a, b) for a, b in zip(left, right))
    return left == right


def require_body_receipt(report):
    """Recompute a complete diagnostic receipt, never certify body clearance.

    Negative comparisons are legitimate results. Missing observations, invalid
    source fields, mismatched query/sample copies and forged summaries are not.
    """
    try:
        if (report['kind'] != 'OWN_BODY_DIAGNOSTIC_NO_MOTION'
                or type(report['schema_version']) is not int or report['schema_version'] != 1
                or report['status'] != 'DIAGNOSTIC_COMPLETED'
                or report['control_ready'] is not False or report['clearance_m'] is not None
                or report['body_envelope_status'] != 'NOT_CERTIFIED' or 'error_type' in report
                or not 0 <= _number(report['elapsed_s']) < 8.):
            raise ValueError('Invalid diagnostic envelope')
        catalog = report['catalog']
        if (catalog['status'] != 'CATALOG_READ_NOT_COLLISION_CERTIFIED'
                or catalog['temporal_semantics'] != 'REGISTRATION_CATALOG_NOT_CURRENT_POSE'
                or type(catalog['queried_count']) is not int
                or not 0 <= catalog['queried_count'] <= 50000
                or type(catalog['matching_objects']) is not list
                or len(catalog['matching_objects']) > min(64, catalog['queried_count'])
                or 'error_type' in catalog):
            raise ValueError('Invalid catalog receipt')
        for obj in catalog['matching_objects']:
            if (type(obj['name']) is not str or len(obj['name']) > 128
                    or not re.fullmatch(r'JevDrone(?:_[A-Za-z0-9]+)*', obj['name'])
                    or type(obj['id_decimal']) is not str
                    or not re.fullmatch(r'0|[1-9][0-9]{0,19}', obj['id_decimal'])
                    or not 0 <= int(obj['id_decimal']) < 2**64):
                raise ValueError('Invalid catalog identity')
            for field, size in [('registration_transform_xyz_pitch_yaw_roll', 6),
                                ('reported_box_center_m', 3), ('reported_box_extent_m', 3),
                                ('reported_box_rotation_deg', 3)]:
                if type(obj[field]) is not list or len(obj[field]) != size:
                    raise ValueError('Invalid catalog vector')
                for value in obj[field]:
                    if _number(value) < 0 and field == 'reported_box_extent_m':
                        raise ValueError('Invalid catalog extent')

        methods = ['getMultirotorState', 'simGetObjectPose', 'simGetVehiclePose',
                   'simGetCollisionInfo', 'getMultirotorState']
        queries, samples = report['queries'], report['samples']
        if type(queries) is not list or len(queries) != 31 or type(samples) is not list or len(samples) != 6:
            raise ValueError('Incomplete body diagnostic')
        last_end = 0.
        for query, method in zip(queries, ['simListSceneObjects'] + methods * 6):
            start, end = _number(query['wall_start_s']), _number(query['wall_end_s'])
            if (start < last_end or end < start or query['rpc'] != method
                    or query['transport_status'] != 'RETURNED' or 'error_type' in query):
                raise ValueError('Invalid query transport or time')
            last_end = end
            if method == 'simListSceneObjects':
                if query['payload'] != ['JevDrone']:
                    raise ValueError('Invalid actor inventory')
            elif method in ('simGetObjectPose', 'simGetVehiclePose'):
                if _received_pose(query['payload']) != query['payload']:
                    raise ValueError('Invalid pose projection')
            else:
                raw = dict(query['payload'], **query['geometry'])
                projected = _project(method, raw)
                geometry = _geometry_projection(method, raw, projected)
                if (projected['payload'] is None or geometry['geometry'] is None
                        or any(query[key] != value for key, value in dict(projected, **geometry).items())):
                    raise ValueError('Invalid received fields')
        span = last_end - queries[0]['wall_start_s']
        if span >= 8. or span > report['elapsed_s'] + 1e-9:
            raise ValueError('Invalid elapsed budget')
        roles = ('state_before', 'object_pose', 'vehicle_pose', 'collision', 'state_after')
        last_stamp = None
        for index, sample in enumerate(samples):
            if (type(sample['index']) is not int or sample['index'] != index
                    or any(not _same_typed(sample[role], query) for role, query in
                           zip(roles, queries[1 + 5*index:6 + 5*index]))):
                raise ValueError('Samples not linked to raw query sequence')
            before = sample['state_before']['payload']['timestamp']
            after = sample['state_after']['payload']['timestamp']
            if after < before or (last_stamp is not None and before <= last_stamp):
                raise ValueError('Invalid source clock progression')
            last_stamp = after
            if not _same_typed(sample['comparison'], compare_body_sample(sample)):
                raise ValueError('Unverifiable comparison')
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError('Malformed own-body receipt') from exc
