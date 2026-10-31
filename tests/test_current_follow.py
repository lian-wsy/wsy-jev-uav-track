"""Current-version following: measured motion, not just a visibility percentage."""
import importlib
import math
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


def module():
    from jev_uav_track import __path__
    assert (Path(next(iter(__path__))) / 'current_follow.py').is_file(), 'current-follow integration missing'
    return importlib.import_module('jev_uav_track.current_follow')


def box(center=(0., 0., 0.), extent=(1., 2., 3.), yaw=0.):
    return NS(location=NS(x=center[0], y=center[1], z=center[2]),
              extent=NS(x=extent[0], y=extent[1], z=extent[2]),
              rotation=NS(pitch=0., yaw=yaw, roll=0.))


def test_registered_geometry_uses_oriented_box_distance_and_preserves_signed_source():
    m = module()
    geometry = m.RegisteredGeometry([box(extent=(-1., 2., 3.), yaw=90.)])
    # After yaw 90, the x half extent is 2. Point (5,0,0) is 3 m outside.
    assert geometry.distance((5., 0., 0.)) == pytest.approx(3.)
    assert geometry.raw[0]['signed_extent_m'] == [-1., 2., 3.]
    assert geometry.distance((0., 0., 0.)) == 0.


@pytest.mark.parametrize('bad', [[], [box(extent=(0., 0., 0.))], [box(center=(math.nan, 0., 0.))]])
def test_missing_or_bad_geometry_never_means_clear(bad):
    with pytest.raises(ValueError):
        module().RegisteredGeometry(bad)


class BoxEntity:
    id=7
    type_id='vehicle.tesla.model3'
    def __init__(self): self.reads=0
    @property
    def bounding_box(self):
        self.reads+=1
        if self.reads>1: raise AssertionError('Repeated fixed geometry RPC inside a live packet')
        return NS(get_world_vertices=lambda offset:[NS(x=x+offset,y=y,z=z)
            for x in (-1.,1.) for y in (-1.,1.) for z in (-1.,1.)])


def test_actor_geometry_reads_fixed_shape_once_but_uses_each_new_snapshot_pose():
    m=module()
    assert callable(getattr(m,'ActorGeometryCache',None)), 'Fixed actor geometry cache missing'
    entity=BoxEntity()
    cache=m.ActorGeometryCache([entity],required_actor_id=7)
    first=NS(find=lambda identity:NS(get_transform=lambda:0.))
    second=NS(find=lambda identity:NS(get_transform=lambda:1.))
    assert cache.distance(first,(5.,0.,0.),[entity])[0]==4.
    assert cache.distance(second,(5.,0.,0.),[entity])[0]==3.
    assert entity.reads==1


@pytest.mark.parametrize('failure',['new_actor','missing_actor','missing_snapshot','changed_type'])
def test_actor_geometry_cache_rejects_changed_inventory_or_missing_pose(failure):
    m=module()
    assert callable(getattr(m,'ActorGeometryCache',None)), 'Fixed actor geometry cache missing'
    entity=BoxEntity()
    cache=m.ActorGeometryCache([entity],required_actor_id=7)
    inventory=[entity]
    snapshot=NS(find=lambda identity:NS(get_transform=lambda:0.))
    if failure=='new_actor': inventory.append(NS(id=8,type_id='vehicle.other'))
    elif failure=='missing_actor': inventory=[]
    elif failure=='missing_snapshot': snapshot=NS(find=lambda identity:None)
    else: inventory=[NS(id=7,type_id='walker.person')]
    with pytest.raises(ValueError): cache.distance(snapshot,(5.,0.,0.),inventory)


def frames(moving=True):
    return [{'wall_s': 10. + i, 'visible': True,
             'uav_world_m': [i * .1 if moving else 0., 0., 18.],
             'target_measurement_world_m': [i * .15 + 12., 0., 2.],
             'center_error': .2, 'collision_new': False} for i in range(10)]


def test_visibility_without_uav_motion_is_not_demonstrated_following():
    m = module()
    summary = m.follow_metrics(frames(False))
    assert summary['visible_fraction'] == 1.
    assert summary['uav_path_m'] == 0.
    assert summary['target_visible_path_m'] == pytest.approx(1.35)
    assert summary['motion_observed'] is False
    assert summary['range_rmse_m'] > 0.


def test_follow_metrics_include_hidden_frames_in_denominator_and_exclude_hidden_truth():
    m = module()
    rows = frames()
    rows[3].update(visible=False, target_measurement_world_m=None, center_error=None)
    summary = m.follow_metrics(rows)
    assert summary['visible_fraction'] == .9
    assert summary['uav_path_m'] == pytest.approx(.9)
    assert summary['center_error']['median'] == .2
    assert summary['motion_observed'] is True
    rows[3]['target_measurement_world_m'] = [20., 0., 0.]
    with pytest.raises(ValueError, match='Hidden'):
        m.follow_metrics(rows)


def test_frozen_causal_model_is_required_and_original_interval_gate_is_retained(tmp_path):
    m = module()
    root = Path(__file__).resolve().parents[1]
    directory = root / 'artifacts/d5_causal_calibration_20261030'
    if not directory.exists():
        pytest.skip('Local trained weights are not in Git')
    rollout, evidence = m.load_current_rollout(directory)
    assert evidence['model_sha256'] == 'b67c0a70e50426dcebfff6a97adfaa3f286e1d26fe6a05e4f26e44a97f8ee189'
    assert evidence['input_mode'] == 'causal_visible'
    assert evidence['interval_gate'] == 'ORIGINAL_UNCHANGED'
    from jev_uav_track.residual_intervals import IntervalGatedRolloutModel
    assert isinstance(rollout, IntervalGatedRolloutModel)
    from jev_uav_track.async_control import _candidate_code
    from jev_uav_track.mock_simulator import DeterministicMockSession, BOUNDED_PRIMITIVES
    state = DeterministicMockSession().reset(seed=17, scenario='normal').state
    assert [_candidate_code(a) for a in rollout.generate(BOUNDED_PRIMITIVES, state)] == [None]*6
    (tmp_path / 'd5_residual.npz').write_bytes(b'wrong model')
    with pytest.raises(ValueError, match='fingerprint'):
        m.load_current_rollout(tmp_path)


@pytest.mark.parametrize('heading', [0., 31., 90., -123., 179.99])
def test_heading_rotation_keeps_frozen_actions_within_strict_transport_limits(heading):
    m = module()
    assert callable(getattr(m, 'route_primitives', None)), 'Route primitive bridge missing'
    primitives = m.route_primitives(heading)
    follow = next(p for p in primitives if p.id == 'follow_predictive')
    assert math.hypot(*follow.command_mps) <= .3
    assert follow.command_mps[0] == pytest.approx(.3*math.cos(math.radians(heading)))
    assert follow.command_mps[1] == pytest.approx(.3*math.sin(math.radians(heading)))
    assert all(math.hypot(*p.command_mps) <= .3 and abs(p.yaw_rate_dps) <= 15. for p in primitives)


def test_follow_profile_cannot_access_town03_or_protected_ports(tmp_path):
    from jev_uav_track.isolated_runtime import validate_probe_map, RuntimePorts
    validate_probe_map('Town02', 'follow')
    with pytest.raises(ValueError, match='API-only'):
        validate_probe_map('Town03', 'follow')
    with pytest.raises(ValueError, match='owned endpoint'):
        module().validate_follow_endpoint(RuntimePorts())
    module().validate_follow_endpoint(RuntimePorts(3200, 3201, 3202, 42461))


def test_forged_follow_success_does_not_satisfy_owner_acceptance():
    m = module()
    with pytest.raises(ValueError):
        m.require_follow_receipt({'status': 'FOLLOW_SMOKE_COMPLETED', 'frames': [],
                                 'prefixes': [], 'stop_verdict': 'STOP_VERIFIED'})


def completed_receipt():
    from dataclasses import asdict
    from jev_uav_track.actuation import ActuationReceipt, FlightTelemetry
    from jev_uav_track.sim_adapter import AirSimWorldVelocityCommand
    baseline=FlightTelemetry(18.999,19.,19000000000,1,(0.,0.,0.),(0.,0.,0.),False,0)
    prefix=asdict(ActuationReceipt(0,1,19.01,'PREFIX_AND_NEUTRAL_ACKNOWLEDGED',
        AirSimWorldVelocityCommand((.2,0.,0.),0.,.05),19.015,True,True,True,19.12,19.12))
    prefix['baseline_telemetry']=asdict(baseline)
    trace=[{'telemetry':asdict(FlightTelemetry(end-.001,end,int(round(end*1e9)),1,
               (0.,0.,0.),(0.,0.,0.),False,0)), 'now_s':end+.001,
            'verdict':'OBSERVING' if i<2 else 'TELEMETRY_SETTLED'}
           for i,end in enumerate((19.17,19.22,19.28))]
    return {'kind':'CURRENT_CAUSAL_BOUNDED_FOLLOW','status':'FOLLOW_SMOKE_COMPLETED',
            'model':{'model_sha256':module().MODEL_SHA},'method':'mock_jev','real_jev_calls':0,
            'frames':frames(), 'metrics':module().follow_metrics(frames()),'prefixes':[prefix],
            'stop_verdict':'STOP_VERIFIED','stop_trace':trace,'cleanup_errors':[],'target_destroyed':True}


@pytest.mark.parametrize('failure', ['moving', 'missing', 'collision', 'changed_verdict'])
def test_owner_recomputes_stop_evidence_instead_of_trusting_success_string(failure):
    m=module()
    receipt=completed_receipt()
    m.require_follow_receipt(receipt)
    if failure=='missing': receipt['stop_trace']=[]
    elif failure=='moving': receipt['stop_trace'][-1]['telemetry']['linear_velocity_mps']=(.1,0.,0.)
    elif failure=='collision': receipt['stop_trace'][-1]['telemetry']['collision_event_ns']=19000000001
    else: receipt['stop_trace'][-1]['verdict']='OBSERVING'
    with pytest.raises(ValueError): m.require_follow_receipt(receipt)


@pytest.mark.parametrize('rollout_kind', ['transparent', 'current_causal'])
def test_session_connects_real_coordinator_controller_and_transport_without_network(rollout_kind, monkeypatch):
    import time
    import threading
    from dataclasses import replace
    from jev_uav_track.actuation import FlightTelemetry
    from jev_uav_track.async_control import MeasuredSafety, BoundedControlLoop
    from jev_uav_track.frames import CarlaAirAlignment
    from jev_uav_track.jev_client import MockJevClient
    from jev_uav_track.live_observation import CaptureWindow
    from jev_uav_track.mock_simulator import DeterministicMockSession, BOUNDED_PRIMITIVES
    from jev_uav_track.pipeline import JevTrackPipeline
    m = module()
    assert callable(getattr(m, 'run_control_session', None)), 'No current control session'
    session = DeterministicMockSession()
    state = session.reset(seed=17, scenario='normal').state
    calls = []
    inside_first_send=threading.Event()
    expiry_delivered=threading.Event()
    real_tick=BoundedControlLoop.tick
    def expire_during_native_call(loop,*args):
        # Inject a normal advice-boundary event while the real actuator is in
        # its native RPC. Do not replace coordinator/transaction/safety logic.
        if inside_first_send.is_set() and not expiry_delivered.is_set():
            loop.coordinator.revoke('ACTION_EXPIRED')
            expiry_delivered.set()
        return real_tick(loop,*args)
    monkeypatch.setattr(BoundedControlLoop,'tick',expire_during_native_call)
    def telemetry():
        end = time.perf_counter()
        return FlightTelemetry(end-.001, end, int(end*1e9), 1, (0.,0.,0.), (0.,0.,0.), False, 0)
    def capture():
        end = time.perf_counter()
        state_now = replace(state, target=replace(state.target, timestamp_s=end))
        window = CaptureWindow(end-.002, end, end-.002, end-.001, end,
                               end-.002, end, int(end*1000)-1, int(end*1000))
        return window, state_now, MeasuredSafety(end, True, None, 10., False), telemetry(), {
            'wall_s': end, 'visible': True, 'uav_world_m': list(state.uav.position_m),
            'target_measurement_world_m': list(state.target.position_m),
            'center_error': .1, 'collision_new': False}
    class Port:
        def send(self, command):
            calls.append(command)
            if len(calls)==1:
                inside_first_send.set()
                expiry_delivered.wait(.08)
                inside_first_send.clear()
            else:
                time.sleep(.05)
            return True
        def cancel(self): calls.append('cancel')
    result = {}
    rollout = None
    if rollout_kind == 'current_causal':
        directory = Path(__file__).resolve().parents[1]/'artifacts/d5_causal_calibration_20261030'
        if not directory.exists(): pytest.skip('Local trained weights are not in Git')
        rollout, _ = m.load_current_rollout(directory)
    arm_time=time.perf_counter()-.5
    m.run_control_session(capture, telemetry, lambda: Port(),
        JevTrackPipeline(MockJevClient(), primitives=BOUNDED_PRIMITIVES, rollout_model=rollout),
        CarlaAirAlignment((0.,0.,0.)), result, duration_s=1.3, energy_start_s=arm_time)
    assert len(result['frames']) >= 3
    assert result['prefixes']
    assert result['stop_verdict'] == 'STOP_VERIFIED'
    assert any(command == 'cancel' for command in calls)
    assert all(command == 'cancel' or command.duration_s <= .05 for command in calls)
    assert result['selection_counts']['accepted'] >= 1
    assert any(row['reason_code']=='ACTION_EXPIRED' for row in result['ticks'])
    assert result['selection_counts']['requests']>=2
    assert expiry_delivered.is_set()
    assert result['metrics']['motion_observed'] is False
    assert result['energy_start_s']==arm_time
    assert result['ticks'][0]['energy']['battery_percent']<89.99


def test_session_rejects_grounded_before_transport_creation():
    import time
    from jev_uav_track.actuation import FlightTelemetry
    from jev_uav_track.async_control import MeasuredSafety
    from jev_uav_track.frames import CarlaAirAlignment
    from jev_uav_track.jev_client import MockJevClient
    from jev_uav_track.live_observation import CaptureWindow
    from jev_uav_track.mock_simulator import DeterministicMockSession, BOUNDED_PRIMITIVES
    from jev_uav_track.pipeline import JevTrackPipeline
    m = module()
    assert callable(getattr(m, 'run_control_session', None)), 'No current control session'
    state = DeterministicMockSession().reset(seed=17, scenario='normal').state
    def capture():
        end = time.perf_counter()
        return (CaptureWindow(end-.002,end,end-.002,end-.001,end,end-.002,end,1,2), state,
                MeasuredSafety(end,True,None,10.,False),
                FlightTelemetry(end-.001,end,int(end*1e9),0,(0.,0.,0.),(0.,0.,0.),False,0),
                {'wall_s':end,'visible':True,'uav_world_m':[0.,0.,18.],
                 'target_measurement_world_m':[12.,0.,2.],'center_error':.1,'collision_new':False})
    def forbidden(): raise AssertionError('Grounded input must not connect a movement client')
    result = {}
    with pytest.raises(ValueError, match='GROUNDED'):
        m.run_control_session(capture, forbidden, forbidden,
            JevTrackPipeline(MockJevClient(), primitives=BOUNDED_PRIMITIVES),
            CarlaAirAlignment((0.,0.,0.)), result, duration_s=.1)
    assert result['prefixes'] == []
    assert result['frames']
