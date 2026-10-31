"""A zero-velocity startup transaction is not permission for grounded flight."""
from dataclasses import replace
import importlib
import time
from pathlib import Path

import pytest

from jev_uav_track.actuation import BoundedActuator
from jev_uav_track.sim_adapter import AirSimWorldVelocityCommand
from test_actuation import RecordingPort, prefix, telemetry
from test_async_control import Clock, ControlledExecutor


def initialization():
    clock, executor, port = Clock(), ControlledExecutor(), RecordingPort()
    try:
        actuator = BoundedActuator(lambda: port, clock=clock, executor=executor,
            execution_domain='synthetic_simulation', neutral_initialization=True)
    except TypeError:
        pytest.fail('No strictly neutral-only initialization path')
    return clock, executor, port, actuator


def zero():
    return prefix(command=AirSimWorldVelocityCommand((0.,0.,0.),0.,.05), action_id='initialize_zero_velocity')


def test_initialization_sends_only_zero_then_cancel_zero_without_forging_landed_state():
    clock, executor, port, actuator = initialization()
    raw=telemetry(landed_state=0)
    assert actuator.submit(zero(), raw)=='SUBMITTED'
    executor.complete()
    receipt=actuator.poll()
    assert receipt.status=='PREFIX_AND_NEUTRAL_ACKNOWLEDGED'
    assert [call[0] for call in port.calls]==['send','cancel','send']
    assert all(call[0]=='cancel' or (call[1].velocity_mps==(0.,0.,0.) and
               call[1].yaw_rate_dps==0. and call[1].duration_s<=.05) for call in port.calls)
    assert raw.landed_state==0
    assert receipt.stop_status=='NOT_VERIFIED'


@pytest.mark.parametrize('landed',[0,1])
@pytest.mark.parametrize('command',[
    AirSimWorldVelocityCommand((.001,0.,0.),0.,.05),
    AirSimWorldVelocityCommand((0.,0.,0.),.001,.05),
    AirSimWorldVelocityCommand((0.,0.,-.001),0.,.05),
])
def test_initialization_can_never_dispatch_nonzero_motion_even_after_airborne(landed,command):
    clock, executor, port, actuator = initialization()
    assert actuator.submit(prefix(command=command),telemetry(landed_state=landed))=='INITIALIZATION_MUST_BE_NEUTRAL'
    assert executor.submissions==0 and port.calls==[]


@pytest.mark.parametrize('change',[
    {'landed_state':None}, {'has_collided':True}, {'collision_event_ns':-1},
    {'wall_start_s':.7,'wall_end_s':.8},
])
def test_neutral_initialization_does_not_exempt_missing_collision_or_expired_inputs(change):
    clock, executor, port, actuator = initialization()
    assert actuator.submit(zero(),telemetry(**change))!='SUBMITTED'
    assert executor.submissions==0 and port.calls==[]


def test_normal_actuator_still_rejects_grounded_even_for_zero_velocity():
    port=RecordingPort()
    actuator=BoundedActuator(lambda:port,clock=Clock(),executor=ControlledExecutor(),
                            execution_domain='synthetic_simulation')
    assert actuator.submit(zero(),telemetry(landed_state=0))=='GROUNDED_NOT_READY'
    assert port.calls==[]


def startup_runner():
    from jev_uav_track import __path__
    assert (Path(next(iter(__path__)))/'neutral_startup.py').is_file(), 'Measured neutral startup missing'
    return importlib.import_module('jev_uav_track.neutral_startup').initialize_neutral


def native_edges(*, transition=True, margin=7., collision=0, ack=True):
    # Only the native transport/sensor is doubled. The real transactions and
    # post-neutral StopVerifier run unchanged, with advancing real clocks.
    state={'landed':0}
    commands=[]
    def read():
        start=time.perf_counter()
        time.sleep(.001)  # The native RPC double must have a positive capture interval.
        now=time.perf_counter()
        return telemetry(now,wall_start_s=start,landed_state=state['landed'],collision_event_ns=collision), margin
    class Port:
        def send(self,command):
            commands.append(command)
            if transition: state['landed']=1
            return ack
        def cancel(self): commands.append('cancel')
    return read, lambda:Port(), commands


def test_startup_requires_actual_flying_before_normal_zero_and_stop_verification():
    run=startup_runner()
    read,factory,commands=native_edges()
    result={}
    run(read,factory,result,collision_baseline_ns=0)
    assert result['status']=='AIRBORNE_READY'
    assert len(result['transactions'])==2
    assert result['transactions'][0]['baseline_telemetry']['landed_state']==0
    assert result['transactions'][1]['baseline_telemetry']['landed_state']==1
    assert result['transactions'][0]['receipt']['stop_status']=='NOT_VERIFIED'
    assert result['stop_trace'][-1]['verdict']=='TELEMETRY_SETTLED'
    assert all(c=='cancel' or (c.velocity_mps==(0.,0.,0.) and c.yaw_rate_dps==0.) for c in commands)


@pytest.mark.parametrize('kwargs,error,transaction_count',[
    ({'transition':False},'STILL_LANDED',1),
    ({'margin':3.49},'GEOMETRY',0),
    ({'collision':1},'COLLISION',0),
    ({'ack':False},'TRANSPORT',1),
])
def test_startup_failure_never_authorizes_tracking_or_blindly_retries(kwargs,error,transaction_count):
    run=startup_runner()
    read,factory,commands=native_edges(**kwargs)
    result={}
    with pytest.raises((ValueError,RuntimeError),match=error):
        run(read,factory,result,collision_baseline_ns=0)
    assert result['status']!='AIRBORNE_READY'
    assert len(result['transactions'])==transaction_count
    assert all(c=='cancel' or c.velocity_mps==(0.,0.,0.) for c in commands)


def test_startup_waits_for_measured_stability_before_starting_normal_stop_transaction():
    run=startup_runner()
    read,factory,commands=native_edges()
    airborne_reads=0
    def settling():
        nonlocal airborne_reads
        sample,margin=read()
        if sample.landed_state==1:
            airborne_reads+=1
            if airborne_reads<=3:
                sample=replace(sample,linear_velocity_mps=(0.,0.,.09))
        return sample,margin
    result={}
    run(settling,factory,result,collision_baseline_ns=0)
    assert result['transactions'][1]['baseline_telemetry']['linear_velocity_mps']==(0.,0.,0.)
    assert result['settling_trace'][0]['verdict']=='INITIALIZATION_SETTLING'
    assert result['settling_trace'][-1]['verdict']=='INITIALIZATION_STABLE'
    assert len(result['transactions'])==2  # No repeated startup thrust commands.


def test_initialization_never_settles_has_bounded_deadline_and_no_second_transaction():
    run=startup_runner()
    read,factory,commands=native_edges()
    def moving():
        sample,margin=read()
        return replace(sample,linear_velocity_mps=(0.,0.,.09)),margin
    result={}
    with pytest.raises(ValueError,match='SETTLING_TIMEOUT'):
        run(moving,factory,result,collision_baseline_ns=0)
    assert len(result['transactions'])==1
    assert result['status']!='AIRBORNE_READY'
