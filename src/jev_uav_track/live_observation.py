"""Bounded live observation/dispatch contract for the owned development probe.

CARLA and AirSim retain separate clocks. Receipt timing is not a claim about
exposure start or pixel/pose synchronization inside the renderer. No target
truth, simulator connections, world changes or control calls live here.
"""
from dataclasses import dataclass, fields, replace
import math
import time

from .sim_adapter import AirSimWorldVelocityCommand


@dataclass(frozen=True)
class CaptureWindow:
    wall_start_s: float
    wall_end_s: float
    air_before_s: float
    image_s: float
    air_after_s: float
    carla_before_s: float
    carla_after_s: float
    frame_before: int
    frame_after: int

    @property
    def offset_interval_s(self):
        # Conservatively retain acquisition uncertainty instead of inventing
        # a single constant offset or claiming that the two engines lockstep.
        return (self.air_before_s - self.carla_after_s,
                self.air_after_s - self.carla_before_s)


class TemporalObservationGate:
    """Short-session diagnostic policy; not a full flight safety controller.

    150 ms capture, 200 ms from request-start to decision expiry, and 100 ms
    tolerated gap between measured clock-offset intervals. These development
    limits are fixed before the live run, not tuned on tracking/test outcomes.
    """
    max_capture_s = 0.15
    decision_ttl_s = 0.2
    max_clock_gap_s = 0.1
    clock_roundoff_s = 0.005

    def __init__(self):
        self._latest = None
        self._anchor = None
        self._used = False
        self._ready = False

    def _fresh(self, sample, now_s):
        if not math.isfinite(now_s) or now_s < sample.wall_end_s:
            raise ValueError('Invalid local decision clock')
        if now_s >= sample.wall_start_s + self.decision_ttl_s:
            raise ValueError('Observation expired before dispatch')

    def accept(self, sample: CaptureWindow, *, now_s: float):
        self._ready = False  # A rejected newest read revokes old motion authority.
        if not all(math.isfinite(getattr(sample, f.name)) and getattr(sample, f.name) >= 0
                   for f in fields(sample)):
            raise ValueError('Capture times and frames must be finite and nonnegative')
        if (type(sample.frame_before) is not int or type(sample.frame_after) is not int
                or sample.frame_after < sample.frame_before):
            raise ValueError('CARLA frame order is invalid')
        if not 0 < sample.wall_end_s - sample.wall_start_s <= self.max_capture_s:
            raise ValueError('Capture interval is invalid or too slow')
        if (sample.air_after_s <= sample.air_before_s
                or sample.carla_after_s < sample.carla_before_s):
            raise ValueError('Sensor clock is paused or reversed')
        if not (sample.air_before_s - self.clock_roundoff_s <= sample.image_s
                <= sample.air_after_s + self.clock_roundoff_s):
            raise ValueError('Image lies outside its AirSim timestamp bracket')
        self._fresh(sample, now_s)
        previous = self._latest
        if previous is not None:
            if (sample.wall_start_s <= previous.wall_end_s
                    or sample.image_s <= previous.image_s
                    or sample.air_after_s <= previous.air_after_s
                    or sample.carla_after_s <= previous.carla_after_s
                    or sample.frame_after <= previous.frame_after):
                raise ValueError('Repeated or reversed live observation')
            low, high = sample.offset_interval_s
            old_low, old_high = self._anchor
            if low > old_high + self.max_clock_gap_s or high < old_low - self.max_clock_gap_s:
                raise ValueError('CARLA/AirSim clock offset intervals diverged')
        # Rejection preserves history/anchor but cannot authorize old motion.
        if self._anchor is None:
            self._anchor = sample.offset_interval_s
        self._latest = sample
        self._used = False
        self._ready = True

    def dispatch(self, sample: CaptureWindow, command: AirSimWorldVelocityCommand,
                 execute, *, clock=time.perf_counter):
        """Execute at most once using the latest accepted packet, or raise first.

        The 100 ms command cap and expiry cap bound requested duration; RPC
        transport latency/physical stopping distance are not hard bounded here.
        Caller owns explicit braking and emergency process cleanup.
        """
        if sample is not self._latest or not self._ready:
            raise ValueError('Only the latest accepted observation may dispatch')
        if self._used:
            raise ValueError('Observation already dispatched; acquire a new one')
        velocity = command.velocity_mps
        if len(velocity) != 3 or not all(math.isfinite(v) for v in (
                *velocity, command.yaw_rate_dps, command.duration_s)):
            raise ValueError('Command must be finite and three dimensional')
        if (math.sqrt(sum(v * v for v in velocity)) > 0.3
                or abs(command.yaw_rate_dps) > 15 or command.duration_s <= 0):
            raise ValueError('Command exceeds development motion limits')
        now = clock()
        self._fresh(sample, now)
        bounded = replace(command, duration_s=min(command.duration_s, 0.1,
                          sample.wall_start_s + self.decision_ttl_s - now))
        self._used = True  # A failed/uncertain RPC may not be replayed blindly.
        return execute(bounded)
