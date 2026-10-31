"""Transactional D6 movement/neutral transport; never launches or arms a vehicle.

This smoke path is NOT a sustained 20 Hz controller. Acknowledgements are
separate from stopping telemetry. An injected port belongs to an already
verified owned simulator; neither a domain string nor this class proves that
ownership. A hung RPC requires the external owned-process supervisor.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
import threading
import time

from .async_control import ControlTick, _DaemonWorker
from .energy import finite_number
from .sim_adapter import AirSimWorldVelocityCommand


@dataclass(frozen=True)
class FlightTelemetry:
    wall_start_s: float
    wall_end_s: float
    source_timestamp_ns: int
    landed_state: int
    linear_velocity_mps: tuple[float, float, float]
    angular_velocity_rad_s: tuple[float, float, float]
    has_collided: bool
    collision_event_ns: int


def _vector(value):
    return isinstance(value, tuple) and len(value) == 3 and all(finite_number(v) for v in value)


def _telemetry_code(sample, now):
    if (not isinstance(sample, FlightTelemetry) or not finite_number(now)
            or not all(finite_number(v) for v in (sample.wall_start_s, sample.wall_end_s))
            or not 0 <= sample.wall_start_s < sample.wall_end_s <= now
            or sample.wall_end_s - sample.wall_start_s > .15
            or type(sample.source_timestamp_ns) is not int or sample.source_timestamp_ns <= 0
            or type(sample.landed_state) is not int or sample.landed_state not in (0, 1)
            or type(sample.has_collided) is not bool
            or type(sample.collision_event_ns) is not int or sample.collision_event_ns < 0
            or sample.collision_event_ns > sample.source_timestamp_ns
            or not _vector(sample.linear_velocity_mps) or not _vector(sample.angular_velocity_rad_s)):
        return "TELEMETRY_INVALID"
    if now >= sample.wall_start_s + .2:
        return "TELEMETRY_EXPIRED"
    return None


def _command_code(command):
    if (not isinstance(command, AirSimWorldVelocityCommand) or not _vector(command.velocity_mps)
            or not all(finite_number(v) for v in (command.yaw_rate_dps, command.duration_s))):
        return "COMMAND_INVALID"
    if not (0 < command.duration_s <= .05) or math.hypot(*command.velocity_mps) > .3 or abs(command.yaw_rate_dps) > 15:
        return "COMMAND_LIMIT"
    return None


def _input_code(tick, sample, now, generation, *, neutral_initialization=False):
    if (not isinstance(tick, ControlTick) or tick.disposition != "COMMAND_PREFIX"
            or tick.reason_code != "BOUNDED_PREFIX"
            or type(tick.generation) is not int or tick.generation < 0
            or type(tick.request_id) is not int or tick.request_id <= 0
            or not isinstance(tick.action_id, str) or not tick.action_id
            or not finite_number(tick.timestamp_s) or tick.timestamp_s < 0
            or not finite_number(tick.dispatch_deadline_s)
            or tick.dispatch_deadline_s > tick.timestamp_s + .2
            or now < tick.timestamp_s):
        return "LEASE_INVALID"
    if tick.generation != generation:
        return "OLD_GENERATION"
    if now >= tick.dispatch_deadline_s:
        return "LEASE_EXPIRED"
    if code := _command_code(tick.command):
        return code
    if neutral_initialization and (any(tick.command.velocity_mps) or tick.command.yaw_rate_dps != 0.):
        return "INITIALIZATION_MUST_BE_NEUTRAL"
    if code := _telemetry_code(sample, now):
        return code
    if sample.has_collided:
        return "COLLISION"
    if sample.landed_state == 0 and not neutral_initialization:
        return "GROUNDED_NOT_READY"
    return None


@dataclass(frozen=True)
class ActuationReceipt:
    generation: int
    request_id: int
    submitted_s: float
    status: str
    command: AirSimWorldVelocityCommand | None = None
    prefix_sent_s: float | None = None
    prefix_acknowledged: bool = False
    cancel_returned: bool = False
    neutral_acknowledged: bool = False
    neutral_completed_s: float | None = None
    completed_s: float | None = None
    owner_termination_required: bool = False
    stop_status: str = "NOT_VERIFIED"


@dataclass
class _PendingTransaction:
    future: object
    submitted_s: float
    tick: ControlTick


class BoundedActuator:
    """One worker, no movement queue or blind retry, monotonic fault latches.

    Public methods are called by one supervisor thread. A test executor may
    replace scheduling only; the actual production transaction still runs.
    The transport factory and all port calls run on that single worker, so an
    AirSim msgpack client's event loop never crosses threads. No implicit
    connection/client, arm, takeoff, reset or process termination is performed.
    """
    transaction_timeout_s = .2

    def __init__(self, transport_factory, *, execution_domain, generation=0,
                 clock=time.perf_counter, executor=None, neutral_initialization=False):
        if (execution_domain not in ("synthetic_simulation", "isolated_simulation")
                or type(generation) is not int or generation < 0
                or not callable(transport_factory) or not callable(clock)
                or type(neutral_initialization) is not bool):
            raise ValueError("INVALID_SIMULATION_ACTUATOR")
        self.transport_factory, self.clock, self.generation = transport_factory, clock, generation
        # Opt-in only for an already-owned simulation's startup. This mode
        # NEVER permits nonzero commands, even after native state is Flying.
        # It does not arm, alter landed telemetry or prove readiness/ownership.
        self._neutral_initialization = neutral_initialization
        self._worker = _DaemonWorker() if executor is None else None
        self._executor = self._worker or executor
        self._port = None
        self._pending = None
        self._revoked = threading.Event()
        self._closed = False
        self._clock_floor = None
        self._clock_lock = threading.Lock()
        self._last_tick = self._last_source = None
        self._collision_event = None
        self.requires_owner_termination = False
        self.last_receipt = None

    @property
    def worker_thread(self):
        return self._worker.thread if self._worker else None

    @property
    def in_flight(self):
        return self._pending is not None

    def _fault(self):
        self.requires_owner_termination = True
        self._revoked.set()

    def _now(self):
        # All supervisor and worker reads share a monotonic floor. Hold only
        # around the clock/read check, NEVER while calling a transport RPC.
        with self._clock_lock:
            return self._read_clock()

    def _read_clock(self):
        try:
            now = self.clock()
        except Exception:
            self._fault()
            return None
        if (not finite_number(now) or now < 0
                or (self._clock_floor is not None and now < self._clock_floor)):
            self._fault()
            return None
        self._clock_floor = now
        return now

    def submit(self, tick, telemetry):
        self.poll()
        if self._closed:
            return "CLOSED"
        if self.requires_owner_termination:
            return "OWNER_TERMINATION_REQUIRED"
        if isinstance(tick, ControlTick) and tick.disposition == "NO_COMMAND":
            self.halt()
            return "HALTED"
        if self._revoked.is_set():
            return "HALTED"
        now = self._now()
        if now is None:
            return "OWNER_TERMINATION_REQUIRED"
        code = _input_code(tick, telemetry, now, self.generation,
                           neutral_initialization=self._neutral_initialization)
        if not code and self._collision_event is not None:
            if telemetry.collision_event_ns > self._collision_event:
                code = "NEW_COLLISION_EVENT"
            elif telemetry.collision_event_ns < self._collision_event:
                code = "COLLISION_HISTORY_INVALID"
        if code:
            # A failed new safety/lease input must not leave queued authority.
            if self.in_flight or code in ("NEW_COLLISION_EVENT", "COLLISION_HISTORY_INVALID", "COLLISION"):
                self.halt()
            return code
        if self.in_flight:
            return "BUSY"
        if (self._last_tick is not None and tick.timestamp_s <= self._last_tick
                or self._last_source is not None and telemetry.source_timestamp_ns <= self._last_source):
            return "REPLAYED_INPUT"
        self._last_tick, self._last_source = tick.timestamp_s, telemetry.source_timestamp_ns
        self._collision_event = telemetry.collision_event_ns
        try:
            future = self._executor.submit(lambda: self._execute(tick, telemetry, now))
        except Exception:
            self._fault()
            return "WORKER_UNAVAILABLE"
        self._pending = _PendingTransaction(future, now, tick)
        return "SUBMITTED"

    def _execute(self, tick, sample, submitted):
        receipt = ActuationReceipt(self.generation, tick.request_id, submitted, "REVOKED_BEFORE_SEND")
        if self._revoked.is_set():
            return replace(receipt, completed_s=self._now())
        try:
            if self._port is None:
                self._port = self.transport_factory()
        except Exception:
            return replace(receipt, status="PORT_UNAVAILABLE", completed_s=self._now())
        # Factory can be slow: expiry, state and revocation are checked AFTER it.
        now = self._now()
        if now is None:
            return replace(receipt, status="CLOCK_INVALID", owner_termination_required=True)
        if self._revoked.is_set():
            return replace(receipt, completed_s=now)
        if _input_code(tick, sample, now, self.generation,
                       neutral_initialization=self._neutral_initialization):
            return replace(receipt, status="EXPIRED_BEFORE_SEND", completed_s=now)
        remaining = min(tick.command.duration_s, tick.dispatch_deadline_s - now,
                        sample.wall_start_s + .2 - now)
        command = replace(tick.command, duration_s=remaining)
        receipt = replace(receipt, command=command, prefix_sent_s=now)
        acknowledged = canceled = neutral = False
        try:
            acknowledged = self._port.send(command) is True
        except Exception:
            pass  # Unknown outcome; never replay. Still attempt neutral.
        finally:
            try:
                self._port.cancel()
                canceled = True
            except Exception:
                pass  # A failed cancel must not skip the neutral attempt.
            try:
                neutral = self._port.send(AirSimWorldVelocityCommand((0., 0., 0.), 0., .05)) is True
            except Exception:
                pass
        completed = self._now()
        valid_clock = completed is not None
        failed = not (acknowledged and canceled and neutral and valid_clock)
        return replace(receipt,
            status="TRANSACTION_UNCERTAIN" if failed else "PREFIX_AND_NEUTRAL_ACKNOWLEDGED",
            prefix_acknowledged=acknowledged, cancel_returned=canceled,
            neutral_acknowledged=neutral,
            neutral_completed_s=completed if valid_clock and neutral else None,
            completed_s=completed if valid_clock else None, owner_termination_required=failed)

    def poll(self):
        now = self._now()
        pending = self._pending
        if pending is None:
            return None
        if not pending.future.done():
            if now is None or now >= pending.submitted_s + self.transaction_timeout_s:
                self._fault()
            return None
        self._pending = None
        try:
            receipt = pending.future.result()
        except BaseException:
            receipt = ActuationReceipt(self.generation, pending.tick.request_id,
                pending.submitted_s, "TRANSACTION_UNCERTAIN", owner_termination_required=True)
        # Completion may race the first poll clock read. Compare against a
        # read taken AFTER retrieving the already-completed Future.
        now = self._now()
        if (receipt.owner_termination_required or receipt.completed_s is None or now is None
                or receipt.completed_s > now or receipt.completed_s < pending.submitted_s
                or receipt.completed_s >= pending.submitted_s + self.transaction_timeout_s):
            self._fault()
        receipt = replace(receipt, owner_termination_required=self.requires_owner_termination)
        self.last_receipt = receipt
        return receipt

    def halt(self):
        """One-way revocation; an in-flight call still runs its neutral finally."""
        self._revoked.set()

    def close(self):
        """Nonblocking; poll/owner cleanup still required if in_flight is True."""
        self._closed = True
        self.halt()
        if self._worker:
            self._worker.close()


class AirSimMovementPort:
    """Wrap a supervisor-owned client on its own thread; no connection defaults."""
    def __init__(self, client, sdk, *, vehicle_name):
        if not isinstance(vehicle_name, str) or not vehicle_name:
            raise ValueError("VEHICLE_REQUIRED")
        self.client, self.sdk, self.vehicle_name = client, sdk, vehicle_name

    def send(self, command):
        if code := _command_code(command):
            raise ValueError(code)
        return self.client.moveByVelocityAsync(*command.velocity_mps, command.duration_s,
            drivetrain=self.sdk.DrivetrainType.MaxDegreeOfFreedom,
            yaw_mode=self.sdk.YawMode(is_rate=True, yaw_or_rate=command.yaw_rate_dps),
            vehicle_name=self.vehicle_name).get()

    def cancel(self):
        self.client.cancelLastTask(vehicle_name=self.vehicle_name)


class StopVerifier:
    """Post-neutral own-telemetry settling, not certified flight safety.

    Acquisition occurs after neutral ACK. Local and source-clock spans must
    independently advance by >=100 ms. This is not a source-clock calibration
    or proof of every unobserved instant between samples.
    """
    def __init__(self, receipt, baseline):
        if (not isinstance(receipt, ActuationReceipt)
                or receipt.status != "PREFIX_AND_NEUTRAL_ACKNOWLEDGED"
                or receipt.neutral_acknowledged is not True or receipt.cancel_returned is not True
                or receipt.prefix_acknowledged is not True
                or receipt.owner_termination_required is not False
                or not finite_number(receipt.neutral_completed_s)
                or _telemetry_code(baseline, receipt.submitted_s)
                or baseline.has_collided or baseline.landed_state != 1):
            raise ValueError("NEUTRAL_NOT_CONFIRMED")
        self.receipt, self.baseline = receipt, baseline
        self.accepted_samples = 0
        self._last_source = baseline.source_timestamp_ns
        self._last_end = baseline.wall_end_s
        self._last_now = receipt.neutral_completed_s
        self._first = None
        self._terminal = None

    def _reset(self, code, *, terminal=False):
        self.accepted_samples, self._first = 0, None
        if terminal:
            self._terminal = code
        return code

    def observe(self, sample, *, now_s):
        if self._terminal:
            return self._terminal
        if not finite_number(now_s):
            return self._reset("TELEMETRY_INVALID", terminal=True)
        if now_s < self._last_now:
            return self._reset("CLOCK_INVALID", terminal=True)
        self._last_now = now_s
        if now_s >= self.receipt.neutral_completed_s + .5:
            return self._reset("STOP_TIMEOUT", terminal=True)
        if code := _telemetry_code(sample, now_s):
            return self._reset(code)
        if sample.wall_start_s < self.receipt.neutral_completed_s:
            return self._reset("NOT_POST_NEUTRAL")
        if sample.has_collided or sample.collision_event_ns > self.baseline.collision_event_ns:
            return self._reset("COLLISION", terminal=True)
        if sample.collision_event_ns < self.baseline.collision_event_ns:
            return self._reset("COLLISION_HISTORY_INVALID", terminal=True)
        if sample.landed_state != self.baseline.landed_state:
            return self._reset("LANDED_STATE_CHANGED", terminal=True)
        if sample.source_timestamp_ns <= self._last_source or sample.wall_start_s <= self._last_end:
            return self._reset("REPLAYED_TELEMETRY")
        self._last_source, self._last_end = sample.source_timestamp_ns, sample.wall_end_s
        if math.hypot(*sample.linear_velocity_mps) > .05 or math.hypot(*sample.angular_velocity_rad_s) > .05:
            return self._reset("STILL_MOVING")
        self._first = self._first or sample
        self.accepted_samples += 1
        if (self.accepted_samples >= 3
                and sample.wall_end_s - self._first.wall_end_s >= .1 - 1e-9
                and sample.source_timestamp_ns - self._first.source_timestamp_ns >= 100000000):
            self._terminal = "TELEMETRY_SETTLED"
            return self._terminal
        return "OBSERVING"


def observe_until_stopped(verifier, read_telemetry, trace, *, clock=time.perf_counter, sleep=time.sleep):
    """Observe within the original deadline, not at a nominal control cadence.

Windows can round a 50 ms wait above 60 ms. Use short observer waits to avoid
missing the final admissible 100 ms evidence window. This sends NO commands,
does not change controller frequency and preserves every verifier threshold.
"""
    while True:
        sample = read_telemetry()
        now = clock()
        verdict = verifier.observe(sample, now_s=now)
        trace.append({'telemetry':asdict(sample), 'now_s':now, 'verdict':verdict})
        if verdict not in ('OBSERVING', 'STILL_MOVING'):
            return verdict
        sleep(.01)
