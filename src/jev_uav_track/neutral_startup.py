"""Zero-only initialization of an already owned, armed simulation fixture.

This is not takeoff or tracking. A native Landed -> Flying transition must be
observed, followed by a NORMAL actuator zero transaction and unchanged stop
verification using that genuinely Flying baseline. No state is manufactured.
"""
from dataclasses import asdict
import math
import time

from .actuation import BoundedActuator, StopVerifier, _telemetry_code, observe_until_stopped
from .async_control import ControlTick
from .energy import finite_number
from .sim_adapter import AirSimWorldVelocityCommand


def initialize_neutral(read_inputs, transport_factory, result, *, collision_baseline_ns,
                       clock=time.perf_counter, sleep=time.sleep):
    """Two zero transactions, no retry; geometry/telemetry must be fresh each time.

The caller proves ownership and prearm geometry and resumes its paused fixture.
read_inputs brackets native telemetry AND fresh registered point geometry in
one <=150 ms window. The point margin is not certified body clearance.
"""
    if type(collision_baseline_ns) is not int or collision_baseline_ns < 0:
        raise ValueError('INITIALIZATION_COLLISION_BASELINE_INVALID')
    result.update(status='INITIALIZING', transactions=[], input_trace=[], stop_trace=[], settling_trace=[],
                  owner_termination_required=False, initialization_is_tracking=False)

    def read():
        sample, margin = read_inputs()
        now = clock()
        result['input_trace'].append({'telemetry':asdict(sample), 'point_margin_m':margin, 'now_s':now})
        if code := _telemetry_code(sample, now):
            raise ValueError('INITIALIZATION_' + code)
        if not finite_number(margin) or margin < 3.5:
            raise ValueError('INITIALIZATION_GEOMETRY_REJECTED')
        if sample.has_collided or sample.collision_event_ns != collision_baseline_ns:
            raise ValueError('INITIALIZATION_COLLISION_REJECTED')
        return sample

    def transact(sample, *, startup):
        actuator = BoundedActuator(transport_factory, execution_domain='isolated_simulation',
                                   clock=clock, neutral_initialization=startup)
        try:
            now = clock()
            tick = ControlTick(now, 0, 'COMMAND_PREFIX', 'BOUNDED_PREFIX',
                AirSimWorldVelocityCommand((0.,0.,0.),0.,.05), 1, 'initialize_zero_velocity',
                dispatch_deadline_s=min(now+.2, sample.wall_start_s+.2))
            submitted = actuator.submit(tick, sample)
            if submitted != 'SUBMITTED':
                raise ValueError('INITIALIZATION_' + submitted)
            while True:
                receipt = actuator.poll()
                if receipt is not None:
                    result['transactions'].append({'neutral_initialization':startup,
                        'baseline_telemetry':asdict(sample), 'receipt':asdict(receipt)})
                if actuator.requires_owner_termination:
                    raise RuntimeError('INITIALIZATION_TRANSPORT_TERMINATION_REQUIRED')
                if receipt is not None:
                    if receipt.status != 'PREFIX_AND_NEUTRAL_ACKNOWLEDGED':
                        raise RuntimeError('INITIALIZATION_TRANSPORT_NOT_ACKNOWLEDGED')
                    return receipt
                sleep(.001)
        finally:
            result['owner_termination_required'] |= actuator.requires_owner_termination or actuator.in_flight
            actuator.close()

    initial = transact(read(), startup=True)
    airborne = read()
    # Startup motor/velocity transients are not an already-established hover.
    # Allow a bounded, measured warmup under the existing zero goal, with NO
    # repeated commands. Only then start the unchanged 0.5 s stop experiment.
    result['settling_deadline_s'] = initial.neutral_completed_s + 1.
    while True:
        now = clock()
        if now >= result['settling_deadline_s']:
            raise ValueError('INITIALIZATION_SETTLING_TIMEOUT')
        if airborne.landed_state != 1:
            raise ValueError('INITIALIZATION_STILL_LANDED')
        stable = (math.hypot(*airborne.linear_velocity_mps) <= .05
                  and math.hypot(*airborne.angular_velocity_rad_s) <= .05)
        result['settling_trace'].append({'telemetry':asdict(airborne),'now_s':now,
            'verdict':'INITIALIZATION_STABLE' if stable else 'INITIALIZATION_SETTLING'})
        if stable:
            break
        previous = airborne
        sleep(.05)
        airborne = read()
        if (airborne.source_timestamp_ns <= previous.source_timestamp_ns
                or airborne.wall_start_s <= previous.wall_end_s):
            raise ValueError('INITIALIZATION_REPLAYED_TELEMETRY')
    receipt = transact(airborne, startup=False)
    verifier = StopVerifier(receipt, airborne)
    verdict = observe_until_stopped(verifier, read, result['stop_trace'], clock=clock, sleep=sleep)
    if verdict != 'TELEMETRY_SETTLED':
        raise ValueError('INITIALIZATION_' + verdict)
    result['status'] = 'AIRBORNE_READY'
