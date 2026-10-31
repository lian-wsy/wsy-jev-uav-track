"""Positive-hit fixture evidence; no simulator, sockets, or flight commands."""
from copy import deepcopy
import importlib
import importlib.util
import json

import pytest


def module():
    name = 'jev_uav_track.obstacle_probe'
    assert importlib.util.find_spec(name) is not None, 'Positive-witness sweep diagnostic missing'
    return importlib.import_module(name)


def actor(identity, x, y):
    return {'id': identity, 'type_id': 'static.prop.box01',
            'pose': [x, y, 40., 0., 0., 0.],
            'vertices': [[x + dx, y + dy, 40. + dz]
                         for dx in (-.25, .25) for dy in (-.25, .25) for dz in (-.25, .25)]}


def fixture(name='baseline'):
    blocker_y = {'center': 0., 'off_axis': 2., 'outside': 6.}.get(name)
    before = {'witness': actor(11, 12., 0.),
              'blocker': None if blocker_y is None else actor(12, 6., blocker_y),
              'sensor': {'id': 13, 'type_id': 'sensor.other.obstacle', 'pose': [0., 0., 40., 0., 0., 0.]}}
    expected_id = 12 if name in ('center', 'off_axis') else 11
    return {'name': name, 'before': before, 'after': deepcopy(before),
            'floor': {'frame': 100, 'time_s': 10.}, 'ceiling': {'frame': 104, 'time_s': 10.2},
            'started_s': 20., 'ended_s': 20.3, 'overflow': False, 'callback_errors': 0,
            'listening_started_s': 20.01, 'listening_deadline_s': 22.01, 'listening_closed_s': 20.21,
            'events': [{'frame': f, 'time_s': t, 'received_s': r,
                        'actor_id': 13, 'other_actor_id': expected_id,
                        'distance_m': 3. if expected_id == 12 else 9.,
                        'sensor_pose': [0., 0., 40., 0., 0., 0.]}
                       for f, t, r in ((101, 10.05, 20.1), (102, 10.1, 20.15), (103, 10.15, 20.2))],
            'cleanup': [{'id': identity, 'destroyed': True} for identity in
                        ([13, 11] if blocker_y is None else [13, 12, 11])],
            'sensor_stopped': True}


@pytest.mark.parametrize('name', ['baseline', 'center', 'off_axis', 'outside'])
def test_fresh_expected_hits_verify_fixture_response_but_do_not_certify_space(name):
    row = fixture(name)
    saved = deepcopy(row)
    assert module().evaluate_sweep_case(row) == {'status': 'FIXTURE_RESPONSE_VERIFIED', 'reasons': []}
    assert row == saved


@pytest.mark.parametrize('change', ['empty', 'one', 'duplicate', 'stale_frame', 'stale_time',
    'future_frame', 'future_time', 'wrong_hit', 'wrong_sensor', 'negative_distance', 'far_distance',
    'nan_distance', 'bool_frame', 'short_span', 'shifted_sensor', 'reversed_receive',
    'overflow', 'callback_error', 'not_stopped', 'destroy_failed', 'missing_cleanup', 'duplicate_cleanup'])
def test_missing_stale_misidentified_or_failed_cleanup_never_verifies(change):
    row = fixture('off_axis')
    events = row['events']
    if change == 'empty': row['events'] = []
    elif change == 'one': row['events'] = events[:1]
    elif change == 'duplicate': events[1] = deepcopy(events[0])
    elif change == 'stale_frame': events[0]['frame'] = 100
    elif change == 'stale_time': events[0]['time_s'] = 10.
    elif change == 'future_frame': events[-1]['frame'] = 105
    elif change == 'future_time': events[-1]['time_s'] = 11.
    elif change == 'wrong_hit': events[1]['other_actor_id'] = 11
    elif change == 'wrong_sensor': events[1]['actor_id'] = 99
    elif change == 'negative_distance': events[0]['distance_m'] = -.1
    elif change == 'far_distance': events[0]['distance_m'] = 17.
    elif change == 'nan_distance': events[0]['distance_m'] = float('nan')
    elif change == 'bool_frame': events[0]['frame'] = True
    elif change == 'short_span':
        for i, event in enumerate(events): event['time_s'] = 10.01 + i * .001
    elif change == 'shifted_sensor': events[0]['sensor_pose'][0] = .01
    elif change == 'reversed_receive': events[1]['received_s'] = 19.
    elif change == 'overflow': row['overflow'] = True
    elif change == 'callback_error': row['callback_errors'] = 1
    elif change == 'not_stopped': row['sensor_stopped'] = False
    elif change == 'destroy_failed': row['cleanup'][0]['destroyed'] = False
    elif change == 'missing_cleanup': row['cleanup'].pop()
    elif change == 'duplicate_cleanup': row['cleanup'].append(deepcopy(row['cleanup'][0]))
    result = module().evaluate_sweep_case(row)
    assert result['status'] == 'NOT_VERIFIED'
    assert result['reasons']


@pytest.mark.parametrize('change', ['line_intersects', 'outside_radius', 'moved_after',
    'wrong_witness', 'degenerate', 'missing_vertices', 'nan_pose', 'same_id', 'bool_id'])
def test_geometry_must_demonstrate_off_axis_inside_sweep_and_stability(change):
    row = fixture('off_axis')
    blocker = row['before']['blocker']
    if change == 'line_intersects': blocker['vertices'][0][1] = -.1
    elif change == 'outside_radius': blocker['vertices'][0][1] = 3.1
    elif change == 'moved_after': row['after']['blocker']['pose'][0] += .01
    elif change == 'wrong_witness': row['before']['witness']['pose'][0] = 2.
    elif change == 'degenerate': blocker['vertices'] = [blocker['vertices'][0]] * 8
    elif change == 'missing_vertices': del blocker['vertices']
    elif change == 'nan_pose': blocker['pose'][0] = float('nan')
    elif change == 'same_id': blocker['id'] = 11
    elif change == 'bool_id': blocker['id'] = True
    assert module().evaluate_sweep_case(row)['status'] == 'NOT_VERIFIED'


def report():
    return {'kind': 'OBSTACLE_SWEEP_FIXTURE_NO_FLIGHT', 'schema_version': 1,
            'control_ready': False, 'clearance_m': None,
            'config': {'radius_m': 3., 'distance_m': 16., 'prop': 'static.prop.box01',
                       'origin_xyz_m': [0., 0., 40.], 'only_dynamics': False},
            'status': 'FIXTURE_RESPONSE_VERIFIED',
            'cases': [dict(fixture(name), assessment={'status': 'FIXTURE_RESPONSE_VERIFIED', 'reasons': []})
                      for name in ('baseline', 'center', 'off_axis', 'outside')]}


@pytest.mark.parametrize('change', ['ready', 'clearance', 'missing_case', 'changed_radius',
    'forged_assessment', 'schema_bool', 'missing_null'])
def test_owner_rechecks_raw_evidence_not_reported_success(change):
    data = report()
    if change == 'ready': data['control_ready'] = True
    elif change == 'clearance': data['clearance_m'] = 3.
    elif change == 'missing_case': data['cases'].pop()
    elif change == 'changed_radius': data['config']['radius_m'] = .1
    elif change == 'forged_assessment': data['cases'][1]['events'] = []
    elif change == 'schema_bool': data['schema_version'] = True
    elif change == 'missing_null': del data['clearance_m']
    with pytest.raises(ValueError): module().require_sweep_receipt(data)


def test_owner_accepts_only_complete_matrix_and_retains_non_certification():
    data = report()
    module().require_sweep_receipt(data)
    assert data['control_ready'] is False and data['clearance_m'] is None
    json.dumps(data, allow_nan=False)


@pytest.mark.parametrize('change', ['before_start', 'at_deadline', 'after_deadline',
                                   'after_closed', 'oversized_window', 'missing_window'])
def test_owner_rejects_callbacks_outside_declared_listening_window(change):
    row = fixture()
    if change == 'before_start': row['events'][0]['received_s'] = 20.
    elif change in ('at_deadline', 'after_deadline'):
        row['ended_s'] = 23.
        row['listening_closed_s'] = 22.5
        for i, event in enumerate(row['events']):
            event['received_s'] = 22.01 + i * .05 + (.1 if change == 'after_deadline' else 0.)
    elif change == 'after_closed': row['listening_closed_s'] = 20.16
    elif change == 'oversized_window': row['listening_deadline_s'] = 25.
    elif change == 'missing_window': del row['listening_deadline_s']
    assert module().evaluate_sweep_case(row)['status'] == 'NOT_VERIFIED'
