"""The real actuator guards an external port; no simulator connection in tests."""
from dataclasses import replace
import threading
import time
from types import SimpleNamespace

import pytest

from jev_uav_track.async_control import ControlTick
from jev_uav_track.sim_adapter import AirSimWorldVelocityCommand
from test_async_control import Clock, ControlledExecutor


def prefix(**changes):
    return replace(ControlTick(1., 0, "COMMAND_PREFIX", "BOUNDED_PREFIX",
        AirSimWorldVelocityCommand((.3, 0., 0.), 0., .05), 1, "move",
        dispatch_deadline_s=1.19), **changes)


def telemetry(now=1., **changes):
    from jev_uav_track.actuation import FlightTelemetry
    return replace(FlightTelemetry(now - .01, now, int(round(now * 1e9)), 1,
        (0., 0., 0.), (0., 0., 0.), False, 0), **changes)


class RecordingPort:
    """External RPC double: records the production actuator's actual arguments."""
    def __init__(self, responses=(True, True), *, cancel_raises=False):
        self.responses = iter(responses)
        self.calls = []
        self.cancel_raises = cancel_raises

    def send(self, command):
        self.calls.append(("send", command))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result

    def cancel(self):
        self.calls.append(("cancel",))
        if self.cancel_raises:
            raise RuntimeError("SECRET_ERROR_MUST_NOT_ESCAPE")


def setup(port=None, factory=None):
    from jev_uav_track.actuation import BoundedActuator
    clock, executor = Clock(), ControlledExecutor()
    port = port or RecordingPort()
    actuator = BoundedActuator(factory or (lambda: port), clock=clock, executor=executor,
                              execution_domain="synthetic_simulation")
    return clock, executor, port, actuator


@pytest.mark.parametrize("change,expected", [
    ({"landed_state": 0}, "GROUNDED_NOT_READY"),
    ({"landed_state": True}, "TELEMETRY_INVALID"),
    ({"landed_state": None}, "TELEMETRY_INVALID"),
    ({"source_timestamp_ns": 1.0}, "TELEMETRY_INVALID"),
    ({"source_timestamp_ns": True}, "TELEMETRY_INVALID"),
    ({"collision_event_ns": -1}, "TELEMETRY_INVALID"),
    ({"has_collided": True}, "COLLISION"),
    ({"has_collided": None}, "TELEMETRY_INVALID"),
    ({"linear_velocity_mps": (float("nan"), 0., 0.)}, "TELEMETRY_INVALID"),
    ({"angular_velocity_rad_s": None}, "TELEMETRY_INVALID"),
    ({"wall_start_s": .8, "wall_end_s": .81}, "TELEMETRY_EXPIRED"),
    ({"wall_end_s": 1.01}, "TELEMETRY_INVALID"),
])
def test_missing_grounded_colliding_or_stale_state_never_reaches_movement_port(change, expected):
    clock, executor, port, actuator = setup()
    assert actuator.submit(prefix(), telemetry(**change)) == expected
    assert port.calls == []
    assert executor.submissions == 0


@pytest.mark.parametrize("change,expected", [
    ({"dispatch_deadline_s": None}, "LEASE_INVALID"),
    ({"dispatch_deadline_s": float("nan")}, "LEASE_INVALID"),
    ({"dispatch_deadline_s": 1.0}, "LEASE_EXPIRED"),
    ({"dispatch_deadline_s": 2.0}, "LEASE_INVALID"),
    ({"timestamp_s": True}, "LEASE_INVALID"),
    ({"generation": 1}, "OLD_GENERATION"),
    ({"generation": False}, "LEASE_INVALID"),
    ({"request_id": None}, "LEASE_INVALID"),
    ({"command": AirSimWorldVelocityCommand((.31, 0., 0.), 0., .05)}, "COMMAND_LIMIT"),
    ({"command": AirSimWorldVelocityCommand((0., 0., 0.), 16., .05)}, "COMMAND_LIMIT"),
    ({"command": AirSimWorldVelocityCommand((0., 0., 0.), 0., .051)}, "COMMAND_LIMIT"),
    ({"command": AirSimWorldVelocityCommand((True, 0., 0.), 0., .05)}, "COMMAND_INVALID"),
    ({"command": None}, "COMMAND_INVALID"),
])
def test_invalid_prefix_or_lease_is_rejected_before_external_work(change, expected):
    clock, executor, port, actuator = setup()
    assert actuator.submit(prefix(**change), telemetry()) == expected
    assert port.calls == []
    assert executor.submissions == 0


def test_actual_worker_rechecks_expiry_and_never_sends_a_delayed_prefix():
    clock, executor, port, actuator = setup()
    assert actuator.submit(prefix(), telemetry()) == "SUBMITTED"
    clock.now = 1.19
    executor.complete()
    result = actuator.poll()
    assert result.status == "EXPIRED_BEFORE_SEND"
    assert result.prefix_sent_s is None
    assert port.calls == []


def test_port_initialization_delay_cannot_bypass_final_lease_check():
    clock, executor, port, actuator = setup()

    def slow_factory():
        clock.now = 1.2
        return port

    actuator.transport_factory = slow_factory
    actuator.submit(prefix(), telemetry())
    executor.complete()
    assert actuator.poll().status == "EXPIRED_BEFORE_SEND"
    assert port.calls == []


def test_remaining_lease_shortens_actual_rpc_and_every_attempt_gets_cancel_then_neutral():
    clock, executor, port, actuator = setup()
    actuator.submit(prefix(dispatch_deadline_s=1.03), telemetry())
    clock.now = 1.02
    executor.complete()
    result = actuator.poll()
    assert len(port.calls) == 3
    assert port.calls[0][1].velocity_mps == (.3, 0., 0.)
    assert port.calls[0][1].duration_s == pytest.approx(.01)
    assert port.calls[1] == ("cancel",)
    assert port.calls[2] == ("send", AirSimWorldVelocityCommand((0., 0., 0.), 0., .05))
    assert result.status == "PREFIX_AND_NEUTRAL_ACKNOWLEDGED"
    assert result.prefix_acknowledged is True
    assert result.cancel_returned is True
    assert result.neutral_acknowledged is True
    assert result.stop_status == "NOT_VERIFIED"


@pytest.mark.parametrize("response", [False, None, 1, "true", RuntimeError("SECRET")])
def test_non_true_or_failed_prefix_still_brakes_but_requires_owner_termination(response):
    clock, executor, port, actuator = setup(RecordingPort((response, True)))
    actuator.submit(prefix(), telemetry())
    executor.complete()
    receipt = actuator.poll()
    assert receipt.prefix_acknowledged is False
    assert receipt.neutral_acknowledged is True
    assert [call[0] for call in port.calls] == ["send", "cancel", "send"]
    assert receipt.owner_termination_required is True
    assert "SECRET" not in repr(receipt)
    assert actuator.submit(prefix(timestamp_s=1.01), telemetry()) == "OWNER_TERMINATION_REQUIRED"


@pytest.mark.parametrize("cancel_raises,neutral", [(True, True), (False, False), (False, None),
                                                    (False, RuntimeError("SECRET"))])
def test_cancel_failure_does_not_skip_neutral_and_failed_cleanup_is_not_stop(cancel_raises, neutral):
    clock, executor, port, actuator = setup(RecordingPort((True, neutral), cancel_raises=cancel_raises))
    actuator.submit(prefix(), telemetry())
    executor.complete()
    receipt = actuator.poll()
    assert len(port.calls) == 3
    assert receipt.owner_termination_required
    assert receipt.stop_status == "NOT_VERIFIED"
    assert "SECRET" not in repr(receipt)


def test_one_slot_no_replay_no_new_worker_after_watchdog_and_late_ack_does_not_clear_fault():
    clock, executor, port, actuator = setup()
    actuator.submit(prefix(), telemetry())
    assert actuator.submit(prefix(), telemetry()) == "BUSY"
    assert executor.submissions == 1
    clock.now = 1.2
    assert actuator.poll() is None
    assert actuator.requires_owner_termination
    executor.complete()
    assert actuator.poll().owner_termination_required
    assert actuator.submit(prefix(timestamp_s=1.2, dispatch_deadline_s=1.39), telemetry(1.2)) == "OWNER_TERMINATION_REQUIRED"
    assert executor.submissions == 1


def test_completed_prefix_and_old_telemetry_cannot_be_replayed():
    clock, executor, port, actuator = setup()
    actuator.submit(prefix(), telemetry())
    executor.complete()
    actuator.poll()
    assert actuator.submit(prefix(), telemetry()) == "REPLAYED_INPUT"
    assert executor.submissions == 1


def test_no_command_revokes_queued_authority_before_it_can_send():
    clock, executor, port, actuator = setup()
    actuator.submit(prefix(), telemetry())
    assert actuator.submit(ControlTick(1., 0, "NO_COMMAND", "SAFETY_UNMEASURED"), telemetry()) == "HALTED"
    executor.complete()
    assert actuator.poll().status == "REVOKED_BEFORE_SEND"
    assert port.calls == []


def test_real_blocked_transport_does_not_block_close_and_neutral_is_still_attempted():
    from jev_uav_track.actuation import BoundedActuator
    entered, release, neutral = threading.Event(), threading.Event(), threading.Event()
    calls = []

    class BlockedPort:
        def send(self, command):
            calls.append(command)
            if len(calls) == 1:
                entered.set()
                release.wait(2.)
            else:
                neutral.set()
            return True

        def cancel(self):
            pass

    actuator = BoundedActuator(BlockedPort, clock=Clock(), execution_domain="synthetic_simulation")
    try:
        assert actuator.submit(prefix(), telemetry()) == "SUBMITTED"
        assert entered.wait(.5)
        started = time.perf_counter()
        actuator.close()
        assert actuator.in_flight
        assert time.perf_counter() - started < .1
        assert actuator.submit(prefix(), telemetry()) == "CLOSED"
    finally:
        release.set()
        assert neutral.wait(.5)
        actuator.worker_thread.join(.5)
    assert not actuator.worker_thread.is_alive()
    assert len(calls) == 2


def test_clock_reversal_latches_fault_and_cannot_be_cleared_by_a_future_tick():
    clock, executor, port, actuator = setup()
    actuator.poll()
    clock.now = .9
    actuator.poll()
    clock.now = 1.
    assert actuator.submit(prefix(), telemetry()) == "OWNER_TERMINATION_REQUIRED"
    assert port.calls == []


def test_new_collision_event_between_transactions_is_not_hidden_by_false_flag():
    clock, executor, port, actuator = setup()
    actuator.submit(prefix(), telemetry(collision_event_ns=50))
    executor.complete()
    actuator.poll()
    clock.now = 1.1
    result = actuator.submit(prefix(timestamp_s=1.1, dispatch_deadline_s=1.29),
                             telemetry(1.1, collision_event_ns=60))
    assert result == "NEW_COLLISION_EVENT"
    assert executor.submissions == 1


def test_future_or_rewound_collision_event_is_invalid_not_a_clean_slate():
    clock, executor, port, actuator = setup()
    assert actuator.submit(prefix(), telemetry(collision_event_ns=1000000001)) == "TELEMETRY_INVALID"
    actuator.submit(prefix(), telemetry(collision_event_ns=50))
    executor.complete()
    actuator.poll()
    clock.now = 1.1
    assert actuator.submit(prefix(timestamp_s=1.1, dispatch_deadline_s=1.29),
        telemetry(1.1, collision_event_ns=0)) == "COLLISION_HISTORY_INVALID"


def test_raising_clock_is_a_latched_fault_not_an_uncaught_exception():
    clock, executor, port, actuator = setup()

    def broken_clock():
        raise RuntimeError("SECRET_CLOCK")

    actuator.clock = broken_clock
    assert actuator.submit(prefix(), telemetry()) == "OWNER_TERMINATION_REQUIRED"
    assert port.calls == []


def test_future_completion_timestamp_cannot_be_accepted_after_local_clock_reversal():
    clock, executor, port, actuator = setup()
    actuator.submit(prefix(), telemetry())
    clock.now = 1.02
    executor.complete()
    clock.now = 1.01
    assert actuator.poll().owner_termination_required


def test_worker_clock_cannot_rewind_below_an_observed_supervisor_time_and_revive_lease():
    clock, executor, port, actuator = setup()
    actuator.submit(prefix(), telemetry())
    clock.now = 1.195  # past the lease, before the transaction watchdog
    actuator.poll()
    clock.now = 1.18
    executor.complete()
    clock.now = 1.196
    receipt = actuator.poll()
    assert receipt.owner_termination_required is True
    assert port.calls == []


def test_completion_during_poll_does_not_compare_against_an_earlier_clock(monkeypatch):
    clock, executor, port, actuator = setup()
    actuator.submit(prefix(), telemetry())
    future, fn = executor.pending
    done = future.done

    def complete_during_done_check():
        clock.now = 1.02
        executor.complete()
        return done()

    monkeypatch.setattr(future, "done", complete_during_done_check)
    clock.now = 1.01
    receipt = actuator.poll()
    assert receipt.prefix_acknowledged is True
    assert receipt.completed_s == 1.02
    assert receipt.owner_termination_required is False


def test_revoked_and_factory_failure_receipts_record_actual_completion_not_submission():
    clock, executor, port, actuator = setup()
    actuator.submit(prefix(), telemetry())
    actuator.halt()
    clock.now = 1.1
    executor.complete()
    assert actuator.poll().completed_s == 1.1

    def unavailable():
        clock.now = 1.08
        raise RuntimeError("SECRET")

    clock, executor, port, actuator = setup(factory=unavailable)
    actuator.submit(prefix(), telemetry())
    executor.complete()
    receipt = actuator.poll()
    assert receipt.status == "PORT_UNAVAILABLE"
    assert receipt.completed_s == 1.08


def successful_receipt():
    clock, executor, port, actuator = setup()
    actuator.submit(prefix(), telemetry())
    executor.complete()
    return actuator.poll()


def test_acknowledgement_alone_is_not_stopped_and_three_post_neutral_samples_are_required():
    from jev_uav_track.actuation import StopVerifier
    receipt = successful_receipt()
    verifier = StopVerifier(receipt, telemetry())
    assert receipt.stop_status == "NOT_VERIFIED"
    assert verifier.observe(telemetry(1.02), now_s=1.02) == "OBSERVING"
    assert verifier.observe(telemetry(1.07), now_s=1.07) == "OBSERVING"
    assert verifier.observe(telemetry(1.12), now_s=1.12) == "TELEMETRY_SETTLED"
    assert verifier.accepted_samples == 3


@pytest.mark.parametrize("change,want", [
    ({"linear_velocity_mps": (.051, 0., 0.)}, "STILL_MOVING"),
    ({"angular_velocity_rad_s": (0., .051, 0.)}, "STILL_MOVING"),
    ({"has_collided": True}, "COLLISION"),
    ({"collision_event_ns": 1}, "COLLISION"),
    ({"landed_state": 0}, "LANDED_STATE_CHANGED"),
    ({"source_timestamp_ns": 1000000000}, "REPLAYED_TELEMETRY"),
    ({"wall_start_s": .99}, "NOT_POST_NEUTRAL"),
])
def test_stop_evidence_does_not_hide_motion_collision_stale_or_pre_neutral_state(change, want):
    from jev_uav_track.actuation import StopVerifier
    verifier = StopVerifier(successful_receipt(), telemetry())
    assert verifier.observe(telemetry(1.02, **change), now_s=1.02) == want
    assert verifier.accepted_samples == 0


def test_fast_replies_or_paused_source_clock_are_not_a_settled_window():
    from jev_uav_track.actuation import StopVerifier
    verifier = StopVerifier(successful_receipt(), telemetry())
    for now, source in [(1.02, 1020000000), (1.07, 1021000000), (1.12, 1022000000)]:
        assert verifier.observe(telemetry(now, source_timestamp_ns=source), now_s=now) == "OBSERVING"
    assert verifier.observe(telemetry(1.51), now_s=1.51) == "STOP_TIMEOUT"


def test_failed_or_unknown_neutral_receipt_cannot_start_stop_verification():
    from jev_uav_track.actuation import StopVerifier
    receipt = successful_receipt()
    for value in (replace(receipt, neutral_acknowledged=False),
                  replace(receipt, owner_termination_required=True),
                  replace(receipt, prefix_acknowledged=False)):
        with pytest.raises(ValueError, match="NEUTRAL_NOT_CONFIRMED"):
            StopVerifier(value, telemetry())


def test_stop_verification_cannot_erase_collision_history_or_rewind_local_clock():
    from jev_uav_track.actuation import StopVerifier
    verifier = StopVerifier(successful_receipt(), telemetry(collision_event_ns=50))
    assert verifier.observe(telemetry(1.02, collision_event_ns=0), now_s=1.02) == "COLLISION_HISTORY_INVALID"
    assert verifier.observe(telemetry(1.12, collision_event_ns=50), now_s=1.12) == "COLLISION_HISTORY_INVALID"
    verifier = StopVerifier(successful_receipt(), telemetry())
    verifier.observe(telemetry(1.12), now_s=1.12)
    assert verifier.observe(telemetry(1.07), now_s=1.07) == "CLOCK_INVALID"


def test_airsim_adapter_has_no_constructor_side_effect_and_uses_get_and_explicit_ned_yaw():
    from jev_uav_track.actuation import AirSimMovementPort
    calls = []

    class Reply:
        def get(self):
            calls.append(("get",))
            return True

        def join(self):
            pytest.fail("join alone hides RPC errors")

    class Client:
        def moveByVelocityAsync(self, *args, **kwargs):
            calls.append((args, kwargs))
            return Reply()

        def cancelLastTask(self, **kwargs):
            calls.append(("cancel", kwargs))

    sdk = SimpleNamespace(DrivetrainType=SimpleNamespace(MaxDegreeOfFreedom=0),
                          YawMode=lambda **kwargs: kwargs)
    port = AirSimMovementPort(Client(), sdk, vehicle_name="JevDrone")
    assert calls == []
    assert port.send(AirSimWorldVelocityCommand((.1, -.2, .1), -15., .04)) is True
    port.cancel()
    assert calls == [((.1, -.2, .1, .04), {"drivetrain": 0,
        "yaw_mode": {"is_rate": True, "yaw_or_rate": -15.}, "vehicle_name": "JevDrone"}),
        ("get",), ("cancel", {"vehicle_name": "JevDrone"})]
