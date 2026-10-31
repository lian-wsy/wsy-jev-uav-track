"""Nonblocking D6 advice and short control prefixes; no simulator or network I/O.

A timed-out Python call cannot be killed. Its single daemon worker remains
occupied until it returns, including across reset/close. NO_COMMAND means no
new command was issued; it is not evidence of physical braking or safe hover.
"""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, fields, is_dataclass, replace
import math
import threading
import time
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

from .frames import CarlaAirAlignment
from .live_observation import CaptureWindow, TemporalObservationGate
from .models import CandidateAction, CandidateOutcome, JevDecision, SelectionResult, TrackingState
from .pipeline import JevTrackPipeline
from .safety import safety_reasons
from .sim_adapter import AirSimWorldVelocityCommand, compile_carlaair_candidate


def _freeze(value):
    if is_dataclass(value):
        return replace(value, **{field.name: _freeze(getattr(value, field.name)) for field in fields(value)})
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("INVALID_SNAPSHOT")


def _finite(value):
    if is_dataclass(value):
        return all(_finite(getattr(value, field.name)) for field in fields(value))
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _finite(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_finite(item) for item in value)
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return value is None or isinstance(value, (str, bool))


def _candidate_code(action):
    if not isinstance(action, CandidateAction) or not _finite(action):
        return "INVALID_CANDIDATE"
    if (not isinstance(action.id, str) or not action.id
            or not isinstance(action.command_mps, (tuple, list)) or len(action.command_mps) != 3
            or any(type(value) not in (int, float) for value in (*action.command_mps, action.yaw_rate_dps, action.duration_s))
            or not isinstance(action.outcome, CandidateOutcome) or type(action.outcome.feasible) is not bool
            or any(type(getattr(action.outcome, field.name)) not in (int, float)
                   for field in fields(action.outcome) if field.name != "feasible")
            or not isinstance(action.metadata, Mapping) or action.duration_s != 1.
            or action.metadata.get("command_frame") != "world_z_up"):
        return "INVALID_CANDIDATE"
    if math.hypot(*action.command_mps) > .3 or abs(action.yaw_rate_dps) > 15:
        return "COMMAND_LIMIT"
    return None


class _DaemonWorker:
    """One persistent daemon, one occupied slot, no pending-call queue."""
    def __init__(self):
        self._condition = threading.Condition()
        self._job = None
        self._busy = False
        self._closed = False
        self.thread = threading.Thread(target=self._run, name="d6-selector", daemon=True)
        self.thread.start()

    def submit(self, fn):
        with self._condition:
            if self._closed or self._busy:
                raise RuntimeError("WORKER_UNAVAILABLE")
            future = Future()
            self._busy = True
            self._job = future, fn
            self._condition.notify()
            return future

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._job is not None or self._closed)
                if self._job is None:
                    return
                future, fn = self._job
                self._job = None
            try:
                result, error = fn(), None
            except BaseException as exc:
                result, error = None, exc
            with self._condition:
                self._busy = False
            if error is None:
                future.set_result(result)
            else:
                future.set_exception(error)

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify()


@dataclass(frozen=True)
class RequestSnapshot:
    request_id: int
    generation: int
    submitted_s: float
    capture_start_s: float
    expires_s: float
    state: TrackingState
    candidates: tuple[CandidateAction, ...]


@dataclass(frozen=True)
class AcceptedAdvice:
    request_id: int
    generation: int
    action: CandidateAction
    valid_until_s: float
    used_fallback: bool


@dataclass(frozen=True)
class _Completed:
    finished_s: float
    result: SelectionResult | None
    reason_code: str


@dataclass
class _Pending:
    snapshot: RequestSnapshot
    future: Future
    timed_out: bool = False


class AsyncDecisionCoordinator:
    """Own one selector call and reject late, changed or old-generation advice.

    Public methods belong to a single control thread. An injected executor must
    return Future-compatible results without blocking submit; it is caller-owned.
    The default worker neither retries nor replaces a hung call.
    """
    high_level_period_s = 1.
    reply_ttl_s = .2

    def __init__(self, selector: Callable[[RequestSnapshot], SelectionResult], *,
                 clock=time.perf_counter, executor=None):
        self.selector, self.clock = selector, clock
        self._worker = _DaemonWorker() if executor is None else None
        self._executor = self._worker or executor
        self._pending = None
        self._active = None
        self._last_submit_s = None
        self._closed = False
        self.generation = 0
        self.last_code = "NO_ADVICE"
        self.counters = dict.fromkeys(("requests", "accepted", "stale", "timeouts", "fallbacks", "rejected"), 0)

    @property
    def in_flight(self):
        return self._pending is not None

    @property
    def request_snapshot(self):
        """Immutable audit view; reading it does not consume or cancel a call."""
        return self._pending.snapshot if self._pending is not None else None

    @property
    def worker_thread(self):
        return self._worker.thread if self._worker else None

    def revoke(self, code):
        self._active = None
        self.last_code = code

    def submit(self, state: TrackingState, candidates: Sequence[CandidateAction], *, capture_start_s: float):
        now = self.clock()
        if self._closed or self._pending is not None:
            return False
        if self._last_submit_s is not None and now < self._last_submit_s + self.high_level_period_s - 1e-9:
            return False
        if (not math.isfinite(now) or not math.isfinite(capture_start_s)
                or capture_start_s > now or now >= capture_start_s + self.reply_ttl_s
                or not _finite(state) or not candidates):
            self.revoke("INVALID_REQUEST")
            return False
        code = next((_candidate_code(action) for action in candidates if _candidate_code(action)), None)
        if code or len({action.id for action in candidates}) != len(candidates):
            self.revoke(code or "INVALID_CANDIDATE")
            return False
        try:
            snapshot = RequestSnapshot(self.counters["requests"] + 1, self.generation, now,
                                       capture_start_s, min(now + .2, capture_start_s + .2),
                                       _freeze(state), tuple(_freeze(action) for action in candidates))
            # Independent copy: even a badly behaved selector cannot alter the
            # trusted parameters against which its returned action is checked.
            worker_snapshot = _freeze(snapshot)
        except (TypeError, ValueError):
            self.revoke("INVALID_REQUEST")
            return False
        selector = self.selector

        def choose():
            try:
                result = _freeze(selector(worker_snapshot))
                return _Completed(self.clock(), result, "REPLY")
            except Exception:
                return _Completed(self.clock(), None, "SELECTOR_ERROR")

        try:
            future = self._executor.submit(choose)
        except Exception:
            self.revoke("WORKER_UNAVAILABLE")
            return False
        self._pending = _Pending(snapshot, future)
        self._last_submit_s = now
        self.counters["requests"] += 1
        self.last_code = "PENDING"
        return True

    def _timeout(self, pending):
        if not pending.timed_out:
            pending.timed_out = True
            self.counters["timeouts"] += 1
        self.revoke("SELECTOR_TIMEOUT")

    def poll(self):
        now = self.clock()
        if self._closed:
            self.revoke("CLOSED")
            return None
        if not math.isfinite(now):
            self.revoke("INVALID_CLOCK")
            return None
        pending = self._pending
        if pending is not None:
            request = pending.snapshot
            if pending.future.done():
                self._pending = None
                try:
                    completed = pending.future.result()
                except BaseException:
                    completed = _Completed(now, None, "SELECTOR_ERROR")
                if request.generation != self.generation:
                    self.counters["stale"] += 1
                    self.revoke("OLD_GENERATION")
                elif (pending.timed_out or not math.isfinite(completed.finished_s)
                      or completed.finished_s < request.submitted_s
                      or completed.finished_s >= request.expires_s or now >= request.expires_s):
                    self._timeout(pending)
                    self.counters["stale"] += 1
                    self.revoke("LATE_REPLY")
                else:
                    self._accept(request, completed)
            elif now >= request.expires_s:
                self._timeout(pending)
        if self._active is not None and now >= self._active.valid_until_s:
            self.revoke("ACTION_EXPIRED")
        return self._active

    def _accept(self, request, completed):
        result = completed.result
        code = None
        if completed.reason_code != "REPLY" or not isinstance(result, SelectionResult):
            code = "SELECTOR_ERROR"
        elif (not _finite(result) or _candidate_code(result.action)
              or not isinstance(result.decision, JevDecision) or not isinstance(result.decision.choice, str)
              or not isinstance(result.decision.probabilities, Mapping)
              or any(type(value) not in (int, float) for value in result.decision.probabilities.values())
              or not isinstance(result.reason, str) or type(result.used_fallback) is not bool):
            code = "INVALID_CHOICE"
        elif (result.decision.choice not in {action.id for action in request.candidates}
              or result.action.id not in {action.id for action in request.candidates}):
            code = "INVALID_CHOICE"
        elif result.reason.startswith("jev_error:"):
            code = "SELECTOR_ERROR"
        elif safety_reasons(result.action, request.state.uav, request.state.constraints):
            code = "UNSAFE_CHOICE"
        elif result.action != next(action for action in request.candidates if action.id == result.action.id):
            code = "CANDIDATE_MISMATCH"
        if code:
            self.counters["rejected"] += 1
            self.revoke(code)
            return
        self._active = AcceptedAdvice(request.request_id, request.generation, result.action,
                                      request.submitted_s + result.action.duration_s, result.used_fallback)
        self.counters["accepted"] += 1
        self.counters["fallbacks"] += int(result.used_fallback)
        self.last_code = "ACCEPTED_FALLBACK" if result.used_fallback else "ACCEPTED"

    def reset(self):
        self.generation += 1
        self._last_submit_s = None
        self.revoke("RESET")

    def close(self):
        self._closed = True
        self.revoke("CLOSED")
        if self._worker:
            self._worker.close()


@dataclass(frozen=True)
class MeasuredSafety:
    timestamp_s: float
    valid: bool
    battery_percent: float | None
    clearance_m: float | None
    collision: bool | None


@dataclass(frozen=True)
class ControlTick:
    timestamp_s: float
    generation: int
    disposition: str
    reason_code: str
    command: AirSimWorldVelocityCommand | None = None
    request_id: int | None = None
    action_id: str | None = None


class BoundedControlLoop:
    """Recheck fresh measured safety and compile only <=50 ms prefixes.

    The returned command is a dispatch request. No physical execute/hover/stop
    claim is made by this simulator-neutral module.
    """
    low_level_period_s = .05
    high_level_period_s = 1.
    candidate_horizon_s = 1.

    def __init__(self, pipeline: JevTrackPipeline, coordinator: AsyncDecisionCoordinator, *,
                 alignment: CarlaAirAlignment, clock=time.perf_counter):
        self.pipeline, self.coordinator = pipeline, coordinator
        self.alignment, self.clock = alignment, clock
        self._gate = TemporalObservationGate()
        self._last_tick_s = None
        self.command_count = 0

    def _no_command(self, now, code, *, revoke=True):
        if revoke:
            self.coordinator.revoke(code)
        return ControlTick(now, self.coordinator.generation, "NO_COMMAND", code)

    def tick(self, state: TrackingState, capture: CaptureWindow, safety: MeasuredSafety):
        now = self.clock()
        if not math.isfinite(now) or (self._last_tick_s is not None and now < self._last_tick_s):
            return self._no_command(now, "INVALID_CLOCK")
        too_soon = self._last_tick_s is not None and now < self._last_tick_s + .05 - 1e-9
        if not too_soon:
            self._last_tick_s = now
        advice = self.coordinator.poll()
        try:
            self._gate.accept(capture, now_s=now)
        except (TypeError, ValueError, AttributeError):
            return self._no_command(now, "OBSERVATION_INVALID")
        if not isinstance(safety, MeasuredSafety) or safety.valid is not True or any(
                value is None for value in (safety.timestamp_s, safety.battery_percent, safety.clearance_m, safety.collision)):
            return self._no_command(now, "SAFETY_UNMEASURED")
        if type(safety.collision) is not bool or any(type(value) not in (int, float) for value in (
                safety.timestamp_s, safety.battery_percent, safety.clearance_m)):
            return self._no_command(now, "SAFETY_INVALID")
        if not _finite(safety):
            return self._no_command(now, "SAFETY_NONFINITE")
        if (safety.timestamp_s < capture.wall_start_s
                or safety.timestamp_s > now or now >= safety.timestamp_s + .2):
            return self._no_command(now, "SAFETY_EXPIRED")
        if safety.collision:
            return self._no_command(now, "COLLISION")
        if not _finite(state):
            return self._no_command(now, "STATE_NONFINITE")
        if not 0 <= safety.battery_percent <= 100 or safety.battery_percent <= state.constraints.reserve_battery_percent:
            return self._no_command(now, "BATTERY_RESERVE")
        if safety.clearance_m < state.constraints.minimum_obstacle_margin_m:
            return self._no_command(now, "CLEARANCE")
        if too_soon:
            return self._no_command(now, "TICK_TOO_SOON", revoke=False)
        current = replace(state, uav=replace(state.uav, battery_percent=safety.battery_percent,
                                            minimum_obstacle_margin_m=safety.clearance_m))
        try:
            candidates = self.pipeline.generate_candidates(current)
        except Exception:
            return self._no_command(now, "INVALID_CANDIDATE")
        for candidate in candidates:
            if code := _candidate_code(candidate):
                return self._no_command(now, code)
        if advice is None:
            code = self.coordinator.last_code
            self.coordinator.submit(current, candidates, capture_start_s=capture.wall_start_s)
            return self._no_command(now, code, revoke=False)
        selected = next((action for action in candidates if action.id == advice.action.id), None)
        if (selected is None or selected.command_mps != advice.action.command_mps
                or selected.yaw_rate_dps != advice.action.yaw_rate_dps
                or selected.duration_s != advice.action.duration_s
                or selected.metadata.get("command_frame") != advice.action.metadata.get("command_frame")):
            return self._no_command(now, "CANDIDATE_MISMATCH")
        if safety_reasons(selected, current.uav, current.constraints):
            return self._no_command(now, "CURRENT_ACTION_UNSAFE")
        try:
            compiled = compile_carlaair_candidate(advice.action, self.alignment)
            prefix = replace(compiled, duration_s=min(.05, advice.valid_until_s - now))
            emitted = self._gate.dispatch(capture, prefix, lambda command: command, clock=self.clock)
        except (TypeError, ValueError):
            return self._no_command(now, "DISPATCH_REJECTED")
        self.command_count += 1
        return ControlTick(now, self.coordinator.generation, "COMMAND_PREFIX", "BOUNDED_PREFIX",
                           emitted, advice.request_id, advice.action.id)

    def reset(self):
        self.coordinator.reset()
        self._gate = TemporalObservationGate()
        self._last_tick_s = None
