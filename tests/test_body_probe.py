"""Read-only own-body pose diagnostics with raw RPC boundary doubles."""
from copy import deepcopy
import importlib
import importlib.util
import json
from types import SimpleNamespace as NS

import pytest


def module():
    name = 'jev_uav_track.body_probe'
    assert importlib.util.find_spec(name), 'Own-body geometry/pose diagnostic missing'
    return importlib.import_module(name)


def vector(x=0., y=0., z=0.): return {'x_val': x, 'y_val': y, 'z_val': z}
def pose(z=6.): return {'position': vector(z=z), 'orientation': dict(vector(), w_val=1.)}


class Clock:
    def __init__(self): self.value = 1.
    def __call__(self): return self.value
    def sleep(self, delay): self.value += delay


class RPC:
    def __init__(self, change=None):
        self.clock = Clock(); self.calls = []; self.change = change
        self.stamp = 1_793_446_280_000_000_000
    def __call__(self, name, *args):
        self.calls.append((name, args)); self.clock.value += .001
        assert (name, args) in [
            ('simListSceneObjects', ('JevDrone',)), ('getMultirotorState', ('JevDrone',)),
            ('simGetObjectPose', ('JevDrone',)), ('simGetVehiclePose', ('JevDrone',)),
            ('simGetCollisionInfo', ('JevDrone',))]
        if self.change == 'error': raise RuntimeError('PRIVATE_EXCEPTION_CONTENT')
        if name == 'simListSceneObjects': return [] if self.change == 'missing_actor' else ['JevDrone']
        if name == 'getMultirotorState':
            if self.change != 'repeated_time': self.stamp += 1_000_000
            data = {'timestamp': self.stamp, 'landed_state': 0,
                    'kinematics_estimated': dict(pose(), linear_velocity=vector(), angular_velocity=vector())}
            if self.change == 'missing_state': del data['kinematics_estimated']
            if self.change == 'moving': data['kinematics_estimated']['linear_velocity']['x_val'] = .1
            return data
        if name in ('simGetObjectPose', 'simGetVehiclePose'):
            data = pose(z=3. if name == 'simGetObjectPose' else 6.)
            if self.change == 'mismatch' and name == 'simGetVehiclePose': data['position']['z_val'] = 6.1
            if self.change == 'missing_pose': del data['orientation']
            if self.change == 'nan': data['position']['z_val'] = float('nan')
            if self.change == 'nonunit': data['orientation']['w_val'] = 2.
            if self.change == 'slow': self.clock.value += .2
            return data
        data = dict(has_collided=True, time_stamp=self.stamp - 100_000_000,
                    penetration_depth=.001, object_name='Road_Test', object_id=1,
                    normal=vector(z=-1.), impact_point=vector(z=6.2), position=vector(z=6.))
        if self.change == 'future_collision': data['time_stamp'] = self.stamp + 1_000_000_000
        data['private'] = 'DO_NOT_ARCHIVE'
        return data


class World:
    def __init__(self, objects=()): self.objects = list(objects); self.calls = []
    def get_map(self): return NS(name='Carla/Maps/Town02')
    def get_environment_objects(self, label): self.calls.append(label); return self.objects


def catalog_entry(name):
    vec = lambda x=0., y=0., z=0.: NS(x=x, y=y, z=z)
    rotation = NS(pitch=0., yaw=0., roll=0.)
    return NS(name=name, id=2**63 + 1, transform=NS(location=vec(), rotation=rotation),
        bounding_box=NS(location=vec(z=4.), extent=vec(.4, .5, .2), rotation=rotation))


def run(rpc, world=None):
    world = world or World()
    return module().probe_own_body(world, rpc, NS(CityObjectLabel=NS(Any=99)),
                                  clock=rpc.clock, sleep=rpc.clock.sleep)


def test_records_same_local_frame_discrepancy_without_certifying_collision_body():
    rpc = RPC()
    result = run(rpc)
    assert result['status'] == 'DIAGNOSTIC_COMPLETED'
    assert len(result['samples']) == 6 and len(rpc.calls) == 31
    assert result['control_ready'] is False and result['clearance_m'] is None
    assert result['body_envelope_status'] == 'NOT_CERTIFIED'
    assert result['catalog']['matching_objects'] == []
    for row in result['samples']:
        assert row['comparison']['status'] == 'STATIC_POSE_CONSISTENT_NOT_CALIBRATION'
        assert row['comparison']['maximum_local_position_error_m'] == 0.
        assert row['comparison']['local_minus_global_m'] == [0., 0., 3.]
        assert row['collision']['payload']['has_collided'] is True
        assert type(row['state_before']['payload']['timestamp']) is int
    assert 'DO_NOT_ARCHIVE' not in json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('change,status', [('mismatch', 'LOCAL_POSE_DISAGREEMENT'),
    ('moving', 'NOT_STATIC'), ('slow', 'WINDOW_EXPIRED'), ('future_collision', 'FUTURE_COLLISION_EVENT')])
def test_valid_negative_measurements_remain_diagnostic_not_success(change, status):
    result = run(RPC(change))
    assert result['status'] == 'DIAGNOSTIC_COMPLETED'
    assert all(row['comparison']['status'] == status for row in result['samples'])
    assert result['control_ready'] is False


@pytest.mark.parametrize('change', ['missing_state', 'missing_pose', 'nan', 'nonunit',
                                   'error', 'missing_actor', 'repeated_time'])
def test_invalid_or_absent_received_fields_abort_without_defaults_or_retries(change):
    rpc = RPC(change)
    result = run(rpc)
    assert result['status'] == 'DIAGNOSTIC_ABORTED'
    assert len(rpc.calls) < 10
    assert result['control_ready'] is False
    encoded = json.dumps(result, allow_nan=False)
    assert 'PRIVATE_EXCEPTION_CONTENT' not in encoded


def test_catalog_is_exact_prefix_only_bounded_and_not_current_collision_geometry():
    world = World([catalog_entry('JevDrone_SM_0'), catalog_entry('JevDroneTwin_SM_0'),
                   catalog_entry('Road_Test')])
    result = run(RPC(), world)
    catalog = result['catalog']
    assert catalog['queried_count'] == 3
    assert [x['name'] for x in catalog['matching_objects']] == ['JevDrone_SM_0']
    assert catalog['matching_objects'][0]['id_decimal'] == '9223372036854775809'
    assert catalog['temporal_semantics'] == 'REGISTRATION_CATALOG_NOT_CURRENT_POSE'
    assert result['body_envelope_status'] == 'NOT_CERTIFIED'


def test_too_many_matching_catalog_entries_are_not_truncated_as_complete():
    world = World([catalog_entry('JevDrone_SM_' + str(i)) for i in range(65)])
    rpc = RPC()
    result = run(rpc, world)
    assert result['status'] == 'DIAGNOSTIC_ABORTED'
    assert result['catalog']['status'] == 'MATCH_LIMIT_EXCEEDED'
    assert result['catalog']['matching_objects'] == []


def test_reserved_map_refused_before_catalog_or_rpc():
    rpc, world = RPC(), World()
    world.get_map = lambda: NS(name='Town03')
    with pytest.raises(ValueError, match='map'): run(rpc, world)
    assert not rpc.calls and not world.calls


def test_owner_accepts_raw_valid_negative_measurement_without_promoting_ready():
    data = run(RPC('mismatch'))
    assert hasattr(module(), 'require_body_receipt'), 'Owner raw receipt validation missing'
    module().require_body_receipt(data)


@pytest.mark.parametrize('change', ['ready', 'missing_null', 'short', 'forged_comparison',
    'bad_clock', 'bool_stamp', 'reversed_stamp', 'unlinked_query', 'extra_rpc', 'schema_bool',
    'query_overlap', 'elapsed', 'nonunit_state', 'invalid_collision', 'catalog_count',
    'catalog_identity', 'catalog_nonfinite', 'catalog_semantics'])
def test_owner_rejects_unverifiable_body_receipts(change):
    data = run(RPC(), World([catalog_entry('JevDrone_SM_0')]))
    assert hasattr(module(), 'require_body_receipt'), 'Owner raw receipt validation missing'
    if change == 'ready': data['control_ready'] = True
    elif change == 'missing_null': del data['clearance_m']
    elif change == 'short': data['samples'].pop()
    elif change == 'forged_comparison': data['samples'][0]['comparison']['status'] = 'READY'
    elif change == 'bad_clock': data['queries'][3]['wall_end_s'] = -1.
    elif change == 'bool_stamp': data['samples'][0]['state_before']['payload']['timestamp'] = True
    elif change == 'reversed_stamp': data['samples'][1]['state_before']['payload']['timestamp'] = 1
    elif change == 'unlinked_query':
        data['samples'][0]['state_before'] = deepcopy(data['samples'][0]['state_before'])
        data['samples'][0]['state_before']['payload']['timestamp'] += 1
    elif change == 'extra_rpc': data['queries'][1]['rpc'] = 'armDisarm'
    elif change == 'schema_bool': data['schema_version'] = True
    elif change == 'query_overlap': data['queries'][3]['wall_start_s'] -= .05
    elif change == 'elapsed': data['elapsed_s'] = 9.
    elif change == 'nonunit_state':
        data['samples'][0]['state_before']['geometry']['kinematics_estimated']['orientation']['w_val'] = 2.
    elif change == 'invalid_collision': data['samples'][0]['collision']['payload']['has_collided'] = 1
    elif change == 'catalog_count': data['catalog']['queried_count'] = True
    elif change == 'catalog_identity': data['catalog']['matching_objects'][0]['id_decimal'] = '92233720368547758090'
    elif change == 'catalog_nonfinite': data['catalog']['matching_objects'][0]['reported_box_extent_m'][0] = float('nan')
    elif change == 'catalog_semantics': data['catalog']['temporal_semantics'] = 'CURRENT_COLLISION'
    with pytest.raises(ValueError): module().require_body_receipt(data)


@pytest.mark.parametrize('change', ['nan_initial', 'negative_initial', 'bool_initial',
                                  'between_samples', 'nan_rpc', 'reverse_rpc'])
def test_invalid_host_clock_aborts_immediately_with_serializable_evidence(change):
    rpc, world = RPC(), World()
    if change.endswith('_initial'):
        rpc.clock.value = {'nan_initial': float('nan'), 'negative_initial': -1., 'bool_initial': True}[change]
    original_call = rpc.__call__
    def call(name, *args):
        result = original_call(name, *args)
        if change == 'nan_rpc': rpc.clock.value = float('nan')
        if change == 'reverse_rpc': rpc.clock.value -= 1.
        return result
    def sleep(delay):
        rpc.clock.value += -1. if change == 'between_samples' else delay
    result = module().probe_own_body(world, call, NS(CityObjectLabel=NS(Any=99)),
                                     clock=rpc.clock, sleep=sleep)
    assert result['status'] == 'DIAGNOSTIC_ABORTED'
    assert len(rpc.calls) <= (0 if change.endswith('_initial') else 6 if change == 'between_samples' else 1)
    if change.endswith('_initial'): assert world.calls == []
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('change', ['collision_bool', 'landed_bool', 'stamp_float', 'comparison_bool'])
def test_json_decoded_sample_and_summary_must_be_type_exact_with_raw_queries(change):
    rpc = RPC()
    rpc.stamp = 1_000_000_000  # Exactly float-representable, so only the type changes.
    data = json.loads(json.dumps(run(rpc)))  # Independent copies as on disk, not Python aliases.
    sample = data['samples'][0]
    if change == 'collision_bool': sample['collision']['payload']['has_collided'] = 1
    elif change == 'landed_bool': sample['state_before']['geometry']['landed_state'] = False
    elif change == 'stamp_float': sample['state_before']['payload']['timestamp'] = float(sample['state_before']['payload']['timestamp'])
    elif change == 'comparison_bool': sample['comparison']['maximum_local_position_error_m'] = False
    with pytest.raises(ValueError): module().require_body_receipt(data)


def test_finite_received_coordinates_cannot_overflow_into_unserializable_comparison():
    rpc = RPC()
    original_call = rpc.__call__
    def call(name, *args):
        result = original_call(name, *args)
        if name == 'getMultirotorState': result['kinematics_estimated']['position']['z_val'] = 1e308
        elif name == 'simGetVehiclePose': result['position']['z_val'] = 1e308
        elif name == 'simGetObjectPose': result['position']['z_val'] = -1e308
        return result
    result = module().probe_own_body(World(), call, NS(CityObjectLabel=NS(Any=99)),
                                     clock=rpc.clock, sleep=rpc.clock.sleep)
    assert result['status'] == 'DIAGNOSTIC_ABORTED'
    assert len(rpc.calls) == 6
    json.dumps(result, allow_nan=False)
