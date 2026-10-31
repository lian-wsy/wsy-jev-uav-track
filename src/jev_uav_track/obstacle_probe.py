"""Owned Town02 collision-query characterization, never a flight safety sensor.

A known end witness makes silence a failure, not an empty-space observation.
Even a complete fixture matrix does not establish Pawn/channel equivalence,
UAV body clearance, map coverage, or permission to arm/move.
"""
from __future__ import annotations

import math
import threading
import time

from .isolated_runtime import require_loaded_map


CASE_NAMES = ('baseline', 'center', 'off_axis', 'outside')


def sweep_configuration():
    return {'radius_m': 3., 'distance_m': 16., 'prop': 'static.prop.box01',
            'origin_xyz_m': [0., 0., 40.], 'only_dynamics': False}


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Invalid number')
    return value


def _integer(value):
    if type(value) is not int or value < 0:
        raise ValueError('Invalid identifier or frame')
    return value


def _vector(value, length):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError('Invalid vector')
    return [_number(x) for x in value]


def _near(actual, expected):
    return max(abs(a - b) for a, b in zip(actual, expected)) <= .001


def _geometry(record, expected_pose):
    _integer(record['id'])
    if record['type_id'] != 'static.prop.box01' or not _near(_vector(record['pose'], 6), expected_pose):
        raise ValueError('Unexpected fixture pose or type')
    points = record['vertices']
    if not isinstance(points, list) or len(points) != 8:
        raise ValueError('Missing bounding vertices')
    points = [_vector(point, 3) for point in points]
    lo = [min(point[i] for point in points) for i in range(3)]
    hi = [max(point[i] for point in points) for i in range(3)]
    if any(not .01 <= hi[i] - lo[i] <= 2. for i in range(3)):
        raise ValueError('Degenerate or oversized fixture')
    if any(abs(point[i] - expected_pose[i]) > 1. for point in points for i in range(3)):
        raise ValueError('Fixture bounds shifted from requested pose')
    return lo, hi, points


def evaluate_sweep_case(row):
    """Recompute an evidence verdict, without trusting a stored assessment."""
    reasons = []
    try:
        name = row['name']
        if name not in CASE_NAMES:
            raise ValueError('Unknown fixture')
        before, after = row['before'], row['after']
        witness_bounds = _geometry(before['witness'], [12., 0., 40., 0., 0., 0.])
        expected_hit = before['witness']['id']
        expected_bounds = witness_bounds
        ids = [expected_hit]
        if name == 'baseline':
            if before['blocker'] is not None or after['blocker'] is not None:
                raise ValueError('Unexpected baseline blocker')
        else:
            y = {'center': 0., 'off_axis': 2., 'outside': 6.}[name]
            lo, hi, points = _geometry(before['blocker'], [6., y, 40., 0., 0., 0.])
            ids.append(before['blocker']['id'])
            if name == 'center' and not (lo[1] < 0 < hi[1] and lo[2] < 40 < hi[2]):
                raise ValueError('Center control does not cross centerline')
            if name == 'off_axis' and not (lo[1] > .1 and
                    all(math.hypot(p[1], p[2] - 40.) < 3. for p in points)):
                raise ValueError('Off-axis control not wholly inside sweep and outside line')
            if name == 'outside' and lo[1] <= 3.1:
                raise ValueError('Outside control overlaps sweep')
            if name in ('center', 'off_axis'):
                expected_hit, expected_bounds = before['blocker']['id'], (lo, hi, points)
        sensor = before['sensor']
        _integer(sensor['id'])
        ids.append(sensor['id'])
        if len(set(ids)) != len(ids) or sensor['type_id'] != 'sensor.other.obstacle':
            raise ValueError('Invalid actor identities')
        if not _near(_vector(sensor['pose'], 6), [0., 0., 40., 0., 0., 0.]):
            raise ValueError('Sensor not at fixed fixture origin')
        # Re-read the same actor geometry after callbacks; no teleport/reuse.
        for role in ('sensor', 'witness', 'blocker'):
            if before[role] is None:
                continue
            if (after[role]['id'] != before[role]['id'] or after[role]['type_id'] != before[role]['type_id']
                    or not _near(_vector(after[role]['pose'], 6), before[role]['pose'])):
                raise ValueError('Fixture moved or changed identity')
            if role != 'sensor':
                points = after[role]['vertices']
                if len(points) != 8 or any(not _near(_vector(p, 3), q)
                        for p, q in zip(points, before[role]['vertices'])):
                    raise ValueError('Fixture bounds changed')
        if row['overflow'] is not False or type(row['callback_errors']) is not int or row['callback_errors'] != 0:
            reasons.append('CALLBACK_DATA_INCOMPLETE')
        if row['sensor_stopped'] is not True:
            reasons.append('SENSOR_STOP_UNCONFIRMED')
        cleanup = row['cleanup']
        if (not isinstance(cleanup, list) or len(cleanup) != len(ids)
                or sorted(_integer(item['id']) for item in cleanup) != sorted(ids)
                or any(item['destroyed'] is not True for item in cleanup)):
            reasons.append('OWNED_ACTOR_CLEANUP_UNCONFIRMED')
        floor, ceiling = row['floor'], row['ceiling']
        frame, source_s = _integer(floor['frame']), _number(floor['time_s'])
        ceiling_frame, ceiling_s = _integer(ceiling['frame']), _number(ceiling['time_s'])
        received_s, end_s = _number(row['started_s']), _number(row['ended_s'])
        if ceiling_frame <= frame or ceiling_s <= source_s or end_s < received_s:
            raise ValueError('Invalid sampling interval')
        opened_s, deadline_s, closed_s = (_number(row[key]) for key in
            ('listening_started_s', 'listening_deadline_s', 'listening_closed_s'))
        if not (received_s <= opened_s <= closed_s <= end_s and 0 < deadline_s - opened_s <= 2. + 1e-9):
            raise ValueError('Invalid listening window')
        events = row['events']
        if not isinstance(events, list) or not 3 <= len(events) <= 128:
            reasons.append('INSUFFICIENT_OR_EXCESS_EVENTS')
        else:
            for event in events:
                f, t, r = _integer(event['frame']), _number(event['time_s']), _number(event['received_s'])
                if not (frame < f <= ceiling_frame and source_s < t <= ceiling_s and received_s <= r <= end_s):
                    reasons.append('STALE_OR_INCONSISTENT_EVENT')
                if not (opened_s <= r < deadline_s and r <= closed_s):
                    reasons.append('EVENT_OUTSIDE_LISTENING_WINDOW')
                if (_integer(event['actor_id']) != sensor['id'] or
                        _integer(event['other_actor_id']) != expected_hit):
                    reasons.append('UNEXPECTED_HIT_IDENTITY')
                distance = _number(event['distance_m'])
                lo, hi, _ = expected_bounds
                if not (0. < distance <= 16. and lo[0] - 3.1 <= distance <= hi[0] + .1):
                    reasons.append('HIT_DISTANCE_INCONSISTENT')
                if not _near(_vector(event['sensor_pose'], 6), sensor['pose']):
                    reasons.append('SENSOR_POSE_INCONSISTENT')
                frame, source_s, received_s = f, t, r
            if events[-1]['time_s'] - events[0]['time_s'] < .05 - 1e-9:
                reasons.append('INSUFFICIENT_SOURCE_SPAN')
    except (KeyError, ValueError, TypeError, OverflowError):
        reasons.append('MALFORMED_OR_INVALID_FIXTURE_EVIDENCE')
    return {'status': 'NOT_VERIFIED' if reasons else 'FIXTURE_RESPONSE_VERIFIED',
            'reasons': sorted(set(reasons))}


def require_sweep_receipt(report):
    """Owner-side validation; a forged success string cannot unlock the receipt."""
    try:
        valid = (report['kind'] == 'OBSTACLE_SWEEP_FIXTURE_NO_FLIGHT'
            and type(report['schema_version']) is int and report['schema_version'] == 1
            and report['control_ready'] is False and report['clearance_m'] is None
            and report['config'] == sweep_configuration()
            and report['status'] == 'FIXTURE_RESPONSE_VERIFIED'
            and [row['name'] for row in report['cases']] == list(CASE_NAMES)
            and all(evaluate_sweep_case(row)['status'] == 'FIXTURE_RESPONSE_VERIFIED' for row in report['cases']))
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError('Sweep fixture receipt incomplete or invalid; no control certification')


def _pose(transform):
    p, r = transform.location, transform.rotation
    return [_number(value) for value in (p.x, p.y, p.z, r.pitch, r.yaw, r.roll)]


def _actor_record(actor, *, sensor=False):
    transform = actor.get_transform()
    result = {'id': _integer(actor.id), 'type_id': actor.type_id, 'pose': _pose(transform)}
    if not sensor:
        result['vertices'] = [[_number(v.x), _number(v.y), _number(v.z)]
                              for v in actor.bounding_box.get_world_vertices(transform)]
    return result


def _snapshot_record(snapshot):
    return {'frame': _integer(snapshot.frame), 'time_s': _number(snapshot.timestamp.elapsed_seconds)}


def _run_case(world, carla, prop_blueprint, sensor_blueprint, name, deadline, clock, sleep):
    row = {'name': name, 'started_s': clock(), 'events': [], 'overflow': False,
           'callback_errors': 0, 'cleanup': [], 'sensor_stopped': False, 'created_actor_ids': []}
    actors, sensor = [], None
    lock, accepting = threading.Lock(), [True]

    def receive(event):
        # A bounded snapshot of SDK fields only. No arbitrary exception strings.
        try:
            data = {'frame': _integer(event.frame), 'time_s': _number(event.timestamp),
                    'received_s': _number(clock()), 'actor_id': _integer(event.actor.id),
                    'other_actor_id': _integer(event.other_actor.id),
                    'distance_m': _number(event.distance), 'sensor_pose': _pose(event.transform)}
        except Exception:
            with lock:
                if accepting[0]: row['callback_errors'] += 1
            return
        with lock:
            if not accepting[0]: return
            if not (row['listening_started_s'] <= data['received_s'] < row['listening_deadline_s']):
                row['callback_errors'] += 1
                return
            if len(row['events']) >= 128:
                row['overflow'] = True
            else:
                row['events'].append(data)

    def spawn(blueprint, x, y):
        if clock() >= deadline:
            raise TimeoutError('Fixture soft budget exceeded')
        actor = world.try_spawn_actor(blueprint, carla.Transform(
            carla.Location(x=x, y=y, z=40.), carla.Rotation()))
        if actor is None:
            raise RuntimeError('Fixture spawn refused')
        actors.append(actor)  # Retain handle before subsequent operations can fail.
        row['created_actor_ids'].append(_integer(actor.id))
        if blueprint.id == 'static.prop.box01':
            actor.set_simulate_physics(False)
        return actor

    def geometry(witness, blocker):
        return {'witness': _actor_record(witness),
                'blocker': None if blocker is None else _actor_record(blocker),
                'sensor': _actor_record(sensor, sensor=True)}

    try:
        witness = spawn(prop_blueprint, 12., 0.)
        blocker = None if name == 'baseline' else spawn(prop_blueprint, 6.,
                    {'center': 0., 'off_axis': 2., 'outside': 6.}[name])
        sensor = spawn(sensor_blueprint, 0., 0.)
        row['floor'] = _snapshot_record(world.wait_for_tick(2.))
        row['before'] = geometry(witness, blocker)
        row['listening_started_s'] = clock()
        row['listening_deadline_s'] = min(deadline, row['listening_started_s'] + 2.)
        if row['listening_deadline_s'] <= row['listening_started_s']:
            raise TimeoutError('No listening budget remains')
        sensor.listen(receive)
        wait_deadline = row['listening_deadline_s']
        while clock() < wait_deadline:
            with lock:
                events = row['events']
                enough = (len(events) >= 3 and events[-1]['time_s'] - events[0]['time_s'] >= .05)
                failed = row['overflow'] or row['callback_errors'] > 0
            if enough or failed: break
            sleep(min(.02, max(0., wait_deadline - clock())))
        with lock:
            accepting[0] = False
            row['listening_closed_s'] = clock()
        sensor.stop()
        row['sensor_stopped'] = True
        row['ceiling'] = _snapshot_record(world.get_snapshot())
        row['after'] = geometry(witness, blocker)
    except Exception as exc:
        row['error_type'] = type(exc).__name__
    finally:
        # Freeze before every stop path: stop RPC/late callbacks cannot rescue
        # a timed-out case or extend its acquisition window through cleanup.
        with lock:
            accepting[0] = False
            row.setdefault('listening_closed_s', clock())
        if sensor is not None and not row['sensor_stopped']:
            try:
                sensor.stop()
                row['sensor_stopped'] = True
            except Exception as exc:
                row['stop_error_type'] = type(exc).__name__
        for actor in reversed(actors):
            receipt = {'id': actor.id, 'destroyed': False}
            try:
                receipt['destroyed'] = actor.destroy() is True
            except Exception as exc:
                receipt['error_type'] = type(exc).__name__
            row['cleanup'].append(receipt)
        row['ended_s'] = clock()
    row['assessment'] = evaluate_sweep_case(row)
    return row


def probe_obstacle_sweep(world, carla, *, clock=time.perf_counter, sleep=time.sleep):
    """Called only inside the owned launcher. Never receives an AirSim client.

    Fixed coordinates/prop/order, 20 s soft budget (RPC bounded by owner), at
    most 11 actor creations and 128 events/case. No alternate prop/seed retries.
    The first failed case ends the matrix after retaining cleanup receipts.
    """
    require_loaded_map(world.get_map().name, 'Town02')
    report = {'kind': 'OBSTACLE_SWEEP_FIXTURE_NO_FLIGHT', 'schema_version': 1,
              'config': sweep_configuration(), 'control_ready': False, 'clearance_m': None,
              'status': 'NOT_VERIFIED', 'cases': []}
    started = clock()
    try:
        library = world.get_blueprint_library()
        prop = library.find('static.prop.box01')
        sensor = library.find('sensor.other.obstacle')
        for name, value in {'distance': '16.0', 'hit_radius': '3.0', 'only_dynamics': 'false',
                            'debug_linetrace': 'false', 'sensor_tick': '0.05'}.items():
            sensor.set_attribute(name, value)
        for name in CASE_NAMES:
            if clock() >= started + 20.:
                raise TimeoutError('Fixture soft budget exceeded')
            row = _run_case(world, carla, prop, sensor, name, started + 20., clock, sleep)
            report['cases'].append(row)
            if row['assessment']['status'] != 'FIXTURE_RESPONSE_VERIFIED':
                break
        if len(report['cases']) == 4 and all(row['assessment']['status'] == 'FIXTURE_RESPONSE_VERIFIED'
                                            for row in report['cases']):
            report['status'] = 'FIXTURE_RESPONSE_VERIFIED'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
    report['elapsed_s'] = clock() - started
    return report
