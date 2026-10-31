"""D6 contracts: delayed advice never controls freshness or worker lifetime."""
from dataclasses import replace
from concurrent.futures import Future
import math
import threading
import time

import pytest

from jev_uav_track.frames import CarlaAirAlignment
from jev_uav_track.jev_client import UtilityRankingClient
from jev_uav_track.live_observation import CaptureWindow
from jev_uav_track.models import JevDecision, SelectionResult, TargetBelief, TrackingState, UAVState
from jev_uav_track.pipeline import JevTrackPipeline
from jev_uav_track.primitives import BehaviorPrimitive


class Clock:
    def __init__(self, now=1.0):
        self.now = now

    def __call__(self):
        return self.now


class ControlledExecutor:
    """Execute only on explicit completion, including the production wrapper."""
    def __init__(self):
        self.pending = None
        self.submissions = 0

    def submit(self, fn):
        assert self.pending is None
        self.submissions += 1
        future = Future()
        self.pending = (future, fn)
        return future

    def complete(self):
        future, fn = self.pending
        self.pending = None
        future.set_result(fn())


def state(**updates):
    value = TrackingState(TargetBelief(position_m=(20, 0, 3)), UAVState(position_m=(0, 0, 3)),
                          environment={"nested": {"values": [1, 2]}})
    return replace(value, **updates)


def packet(now, capture_s=.01):
    return CaptureWindow(now - capture_s, now, now - capture_s, now - capture_s / 2, now,
                         now - capture_s, now, int(round(now * 1000)) - 1, int(round(now * 1000)))


def pipeline(primitives=None):
    primitives = primitives or (BehaviorPrimitive("move", "move", "bounded", (.3, 0, 0)),)
    return JevTrackPipeline(UtilityRankingClient(), primitives=primitives)


def setup_loop():
    from jev_uav_track.async_control import AsyncDecisionCoordinator, BoundedControlLoop, MeasuredSafety
    clock, executor, pipe = Clock(), ControlledExecutor(), pipeline()
    coordinator = AsyncDecisionCoordinator(
        lambda request: pipe.selector.select(request.state, request.candidates),
        clock=clock, executor=executor)
    loop = BoundedControlLoop(pipe, coordinator, alignment=CarlaAirAlignment((4, 5, 6)), clock=clock)
    return clock, executor, coordinator, loop, MeasuredSafety(1., True, 90., 10., False)


def tick(loop, clock, measured, **updates):
    return loop.tick(state(), packet(clock.now), replace(measured, timestamp_s=clock.now, **updates))


def test_real_blocked_selector_ticks_and_close_are_nonblocking_with_one_worker():
    from jev_uav_track.async_control import AsyncDecisionCoordinator, BoundedControlLoop, MeasuredSafety
    entered, release = threading.Event(), threading.Event()
    pipe, clock = pipeline(), Clock()

    def blocked(request):
        entered.set()
        release.wait()
        return pipe.selector.select(request.state, request.candidates)

    coordinator = AsyncDecisionCoordinator(blocked, clock=clock)
    loop = BoundedControlLoop(pipe, coordinator, alignment=CarlaAirAlignment((0, 0, 0)), clock=clock)
    measured = MeasuredSafety(1., True, 90., 10., False)
    try:
        tick(loop, clock, measured)
        assert entered.wait(.5)
        start = time.perf_counter()
        for index in range(1, 101):
            clock.now = 1 + index * .05
            assert tick(loop, clock, measured).command is None
        coordinator.reset()
        coordinator.close()
        assert time.perf_counter() - start < .5
        assert coordinator.counters["requests"] == 1
        assert coordinator.counters["timeouts"] == 1
        assert coordinator.in_flight
        assert coordinator.worker_thread.daemon
        assert coordinator.worker_thread.is_alive()
    finally:
        release.set()
        coordinator.worker_thread.join(.5)


def test_late_reply_cannot_be_laundered_through_a_fresh_packet():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.21
    executor.complete()
    result = tick(loop, clock, measured)
    assert result.command is None
    assert result.reason_code == "LATE_REPLY"
    assert coordinator.counters["accepted"] == 0
    assert coordinator.counters["stale"] == 1


def test_timely_choice_gets_fresh_prefixes_without_extending_original_horizon():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    first = tick(loop, clock, measured)
    assert first.command.velocity_mps == (.3, 0, 0)
    assert first.command.duration_s == .05
    for index in range(2, 20):
        clock.now = 1 + index * .05
        assert tick(loop, clock, measured).command is not None
    clock.now = 2.
    assert tick(loop, clock, measured).command is None
    assert coordinator.counters["requests"] == 2
    assert coordinator.counters["accepted"] == 1


@pytest.mark.parametrize("updates,reason", [
    ({"valid": False}, "SAFETY_UNMEASURED"),
    ({"collision": True}, "COLLISION"),
    ({"battery_percent": None}, "SAFETY_UNMEASURED"),
    ({"clearance_m": float("nan")}, "SAFETY_NONFINITE"),
    ({"battery_percent": 19.}, "BATTERY_RESERVE"),
    ({"clearance_m": 1.}, "CLEARANCE"),
])
def test_bad_fresh_safety_revokes_prior_motion(updates, reason):
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    assert tick(loop, clock, measured).command
    clock.now = 1.1
    result = tick(loop, clock, measured, **updates)
    assert result.command is None
    assert result.disposition == "NO_COMMAND"
    assert result.reason_code == reason
    clock.now = 1.15
    assert tick(loop, clock, measured).command is None


@pytest.mark.parametrize("kind", ["expired", "reversed", "nonfinite", "slow", "unmeasured_old", "future"])
def test_invalid_observation_never_dispatches(kind):
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    assert tick(loop, clock, measured).command
    clock.now = 1.1
    sample = packet(clock.now)
    safety = replace(measured, timestamp_s=clock.now)
    if kind == "expired":
        sample = packet(.8)
    elif kind == "reversed":
        sample = packet(1.)
    elif kind == "nonfinite":
        sample = replace(sample, image_s=float("nan"))
    elif kind == "slow":
        sample = replace(sample, wall_start_s=.8)
    elif kind == "future":
        safety = replace(safety, timestamp_s=2.)
    else:
        safety = replace(safety, timestamp_s=.8)
    result = loop.tick(state(), sample, safety)
    assert result.command is None
    assert result.disposition == "NO_COMMAND"


def test_reset_keeps_running_slot_and_discards_old_generation_reply():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    loop.reset()
    clock.now = 1.05
    assert tick(loop, clock, measured).command is None
    assert executor.submissions == 1
    executor.complete()
    clock.now = 1.1
    assert tick(loop, clock, measured).command is None
    assert coordinator.counters["stale"] == 1
    assert coordinator.counters["accepted"] == 0


def test_snapshot_is_deeply_immutable_and_returned_parameters_cannot_be_changed():
    from jev_uav_track.async_control import AsyncDecisionCoordinator
    clock, executor, pipe = Clock(), ControlledExecutor(), pipeline()
    original = state()
    seen = []

    def altered(request):
        seen.append(request)
        with pytest.raises(TypeError):
            request.state.environment["nested"]["values"][0] = 99
        result = pipe.selector.select(request.state, request.candidates)
        return replace(result, action=replace(result.action, command_mps=(.2, 0, 0)))

    coordinator = AsyncDecisionCoordinator(altered, clock=clock, executor=executor)
    coordinator.submit(original, pipe.generate_candidates(original), capture_start_s=.99)
    original.environment["nested"]["values"][0] = 77
    clock.now = 1.05
    executor.complete()
    assert coordinator.poll() is None
    assert seen[0].state.environment["nested"]["values"] == (1, 2)
    assert coordinator.last_code == "CANDIDATE_MISMATCH"


@pytest.mark.parametrize("kind,reason", [("error", "SELECTOR_ERROR"), ("unknown", "INVALID_CHOICE"),
                                           ("unsafe", "UNSAFE_CHOICE"), ("overspeed", "COMMAND_LIMIT"),
                                           ("yaw", "COMMAND_LIMIT"), ("nan", "INVALID_CANDIDATE")])
def test_selector_and_command_failures_are_audit_codes_without_exception_text(kind, reason):
    clock, executor, coordinator, loop, measured = setup_loop()
    if kind in {"overspeed", "yaw", "nan"}:
        primitive = BehaviorPrimitive("bad", "bad", "bad", (.31 if kind == "overspeed" else 0, 0, 0),
                                      16 if kind == "yaw" else (math.nan if kind == "nan" else 0))
        loop.pipeline = pipeline((primitive,))
    else:
        original = coordinator.selector

        def select(request):
            if kind == "error":
                raise RuntimeError("SECRET_UNTRUSTED_TEXT")
            result = original(request)
            if kind == "unknown":
                return replace(result, decision=JevDecision("not_an_option", {}))
            return replace(result, action=replace(result.action, outcome=replace(
                result.action.outcome, collision_probability=.9)))

        coordinator.selector = select
    first = tick(loop, clock, measured)
    if executor.pending:
        clock.now = 1.05
        executor.complete()
        result = tick(loop, clock, measured)
    else:
        result = first
    assert result.command is None
    assert result.reason_code == reason
    assert "SECRET" not in repr(result)


def test_vertical_compile_uses_carlaair_mapping_and_prefix_expiry():
    clock, executor, coordinator, loop, measured = setup_loop()
    pipe = pipeline((BehaviorPrimitive("up", "up", "up", (.1, .2, .1)),))
    loop.pipeline = pipe
    coordinator.selector = lambda request: pipe.selector.select(request.state, request.candidates)
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    result = tick(loop, clock, measured)
    assert result.command.velocity_mps == (.1, .2, -.1)
    assert 0 < result.command.duration_s <= .05


def test_repeated_fast_ticks_do_not_emit_extra_prefixes():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    assert tick(loop, clock, measured).command
    clock.now = 1.06
    result = loop.tick(state(), packet(clock.now, .004), replace(measured, timestamp_s=clock.now))
    assert result.reason_code == "TICK_TOO_SOON"


@pytest.mark.parametrize("field,value", [("battery_percent", "unknown"), ("battery_percent", True),
                                         ("clearance_m", "unknown"), ("timestamp_s", None),
                                         ("collision", 0)])
def test_malformed_measured_safety_is_a_no_command_result(field, value):
    clock, executor, coordinator, loop, measured = setup_loop()
    invalid = replace(measured, **{field: value})
    result = loop.tick(state(), packet(clock.now), invalid)
    assert result.command is None
    assert result.reason_code in {"SAFETY_UNMEASURED", "SAFETY_INVALID"}


def test_current_candidate_safety_can_revoke_a_timely_choice():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    # Current clearance meets the raw 3 m minimum, but the moving rollout
    # needs a further margin; retaining only the old 10 m outcome is unsafe.
    result = tick(loop, clock, measured, clearance_m=3.01)
    assert result.command is None
    assert result.reason_code == "CURRENT_ACTION_UNSAFE"


def test_changed_current_primitive_cannot_reinterpret_an_accepted_candidate():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    loop.pipeline = pipeline((BehaviorPrimitive("move", "move", "changed", (.2, 0, 0)),))
    assert tick(loop, clock, measured).reason_code == "CANDIDATE_MISMATCH"


def test_reply_expiry_boundary_is_strict_even_with_fresh_capture():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    # Capture began at .99, so its 200 ms reply budget expires at 1.19.
    clock.now = 1.19
    executor.complete()
    assert tick(loop, clock, measured).command is None
    assert coordinator.counters["accepted"] == 0
    assert coordinator.counters["stale"] == 1


def test_prefix_is_capped_by_original_horizon():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    assert tick(loop, clock, measured).command
    clock.now = 1.999
    short = tick(loop, clock, measured)
    assert short.command.duration_s == pytest.approx(.001)
    clock.now = 2.05
    assert tick(loop, clock, measured).command is None


def test_prefix_is_capped_by_remaining_capture_budget():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    assert tick(loop, clock, measured).command
    clock.now = 1.25
    capture = replace(packet(1.2), wall_start_s=1.06)
    result = loop.tick(state(), capture, replace(measured, timestamp_s=clock.now))
    assert result.command.duration_s == pytest.approx(.01)


def test_safety_failure_on_an_early_tick_still_revokes_old_motion():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    assert tick(loop, clock, measured).command
    clock.now = 1.06
    result = loop.tick(state(), packet(clock.now, .004), replace(measured, timestamp_s=clock.now, collision=True))
    assert result.command is None
    assert result.reason_code == "COLLISION"
    clock.now = 1.10
    assert tick(loop, clock, measured).command is None


def test_pipeline_client_exception_fallback_is_not_promoted_to_motion():
    clock, executor, coordinator, loop, measured = setup_loop()

    class FailingClient:
        def choose(self, state, actions):
            raise RuntimeError("SECRET_UNTRUSTED_TEXT")

    loop.pipeline.selector.client = FailingClient()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    result = tick(loop, clock, measured)
    assert result.command is None
    assert result.reason_code == "SELECTOR_ERROR"


def test_current_request_for_audit_is_immutable_and_does_not_release_worker_slot():
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    snapshot = coordinator.request_snapshot
    assert snapshot.request_id == 1
    assert snapshot.candidates[0].command_mps == (.3, 0, 0)
    with pytest.raises(TypeError):
        snapshot.candidates[0].metadata["command_frame"] = "enu"
    assert coordinator.in_flight


@pytest.mark.parametrize("field,value", [("action", None), ("decision", None),
                                         ("reason", None), ("used_fallback", None)])
def test_malformed_selector_result_fails_closed_with_an_audit_code(field, value):
    clock, executor, coordinator, loop, measured = setup_loop()
    original = coordinator.selector
    coordinator.selector = lambda request: replace(original(request), **{field: value})
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    result = tick(loop, clock, measured)
    assert result.command is None
    assert result.reason_code == "INVALID_CHOICE"
    assert coordinator.counters["accepted"] == 0


@pytest.mark.parametrize("field,value", [("command_mps", None), ("yaw_rate_dps", None),
                                         ("metadata", None), ("outcome", None)])
def test_malformed_candidate_from_rollout_is_no_command(field, value):
    clock, executor, coordinator, loop, measured = setup_loop()
    original = loop.pipeline.generate_candidates(state())[0]
    loop.pipeline.generate_candidates = lambda state: [replace(original, **{field: value})]
    result = tick(loop, clock, measured)
    assert result.command is None
    assert result.reason_code == "INVALID_CANDIDATE"


@pytest.mark.parametrize("stage", ["revalidation", "compilation", "gate_clock", "boundary_clock"])
@pytest.mark.parametrize("start,finished,remaining", [(1.999, 2.019, None), (1.95, 1.98, .02)])
def test_action_deadline_is_checked_at_final_dispatch_boundary(monkeypatch, stage, start, finished, remaining):
    from jev_uav_track import async_control
    clock, executor, coordinator, loop, measured = setup_loop()
    tick(loop, clock, measured)
    clock.now = 1.05
    executor.complete()
    assert tick(loop, clock, measured).command
    # The accepted action belongs to the original request at 1.0, so its
    # deadline is exactly 2.0 even though this tick has a fresh capture.
    clock.now = start
    if stage == "revalidation":
        generate = loop.pipeline.generate_candidates

        def slow_generate(current):
            candidates = generate(current)
            clock.now = finished
            return candidates

        monkeypatch.setattr(loop.pipeline, "generate_candidates", slow_generate)
    elif stage == "compilation":
        compile_candidate = async_control.compile_carlaair_candidate

        def slow_compile(action, alignment):
            command = compile_candidate(action, alignment)
            clock.now = finished
            return command

        monkeypatch.setattr(async_control, "compile_carlaair_candidate", slow_compile)
    else:
        reads = 0
        cost_at_read = 2 if stage == "gate_clock" else 3

        def dispatch_clock():
            nonlocal reads
            reads += 1
            if reads >= cost_at_read:
                clock.now = finished
            return clock.now

        # Read 1: tick start; read 2: TemporalObservationGate; read 3:
        # the final command callback. The coordinator keeps its own same-domain
        # clock, so these costs target the actual dispatch boundary only.
        loop.clock = dispatch_clock
    result = loop.tick(state(), packet(start), replace(measured, timestamp_s=start))
    if remaining is None:
        assert result.command is None
        assert result.reason_code == "ACTION_EXPIRED"
        assert loop.command_count == 1
    else:
        assert result.command is not None
        assert result.command.duration_s == pytest.approx(remaining)
        assert finished + result.command.duration_s <= 2.0
