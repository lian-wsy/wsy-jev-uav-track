"""Fixed-step synthetic kinematics for D6; never CARLA, AirSim or PASS data."""
from __future__ import annotations

from dataclasses import dataclass
import math
import random

from .async_control import MeasuredSafety
from .live_observation import CaptureWindow
from .models import TargetBelief, TrackingState, UAVState
from .primitives import BehaviorPrimitive
from .sim_adapter import AirSimWorldVelocityCommand


# Separate development vocabulary; production DEFAULT_PRIMITIVES is untouched.
BOUNDED_PRIMITIVES = (
    BehaviorPrimitive("hold_center", "hold", "Zero velocity prefix", (0., 0., 0.)),
    BehaviorPrimitive("follow_predictive", "follow", "Bounded world x follow", (.3, 0., 0.)),
    BehaviorPrimitive("orbit_left", "left", "Bounded world negative y", (0., -.3, 0.), 15.),
    BehaviorPrimitive("orbit_right", "right", "Bounded world positive y", (0., .3, 0.), -15.),
    BehaviorPrimitive("climb", "climb", "Bounded world z up", (0., 0., .3)),
    BehaviorPrimitive("scan_reacquire", "scan", "Bounded yaw scan", (0., 0., 0.), 15., .65),
)


@dataclass(frozen=True)
class SyntheticObservation:
    evidence_class: str
    seed: int
    scenario: str
    tick_index: int
    state: TrackingState
    capture: CaptureWindow
    safety: MeasuredSafety


class DeterministicMockSession:
    """Seeded synthetic world z-up; incoming commands use CarlaAir's NED axes."""
    evidence_class = "SYNTHETIC_NOT_CARLA"
    dt_s = .05

    def reset(self, *, seed: int, scenario: str):
        if type(seed) is not int or scenario not in {"normal", "delayed", "timeout"}:
            raise ValueError("INVALID_RESET")
        self.seed, self.scenario, self.index = seed, scenario, 0
        rng = random.Random(seed)
        self._position = (rng.uniform(-1, 1), rng.uniform(-1, 1), 3.)
        self._target = (20. + rng.uniform(-2, 2), rng.uniform(-2, 2), 3.)
        self._velocity = (.1 + rng.uniform(0, .1), rng.uniform(-.05, .05), 0.)
        self._yaw = 0.
        self._last_velocity = (0., 0., 0.)
        return self.observe()

    def observe(self):
        now = 1. + self.index * self.dt_s
        distance = math.dist(self._position, self._target)
        center = min(1., abs(self._target[1] - self._position[1]) / max(distance, .01))
        state = TrackingState(
            TargetBelief(position_m=self._target, velocity_mps=self._velocity, range_m=distance,
                         image_center_error=center, visibility_probability=.9, timestamp_s=now),
            UAVState(position_m=self._position, velocity_mps=self._last_velocity, yaw_deg=self._yaw,
                     battery_percent=0., minimum_obstacle_margin_m=10.),
            environment={"evidence_class": self.evidence_class})
        capture = CaptureWindow(now - .01, now, now - .01, now - .005, now,
                                now - .01, now, self.index * 2, self.index * 2 + 1)
        return SyntheticObservation(self.evidence_class, self.seed, self.scenario, self.index,
                                    state, capture, MeasuredSafety(now, True, None, 10., False))

    def step(self, command: AirSimWorldVelocityCommand | None):
        if command is not None:
            if (len(command.velocity_mps) != 3
                    or not all(math.isfinite(value) for value in (*command.velocity_mps, command.yaw_rate_dps, command.duration_s))
                    or math.hypot(*command.velocity_mps) > .3 or abs(command.yaw_rate_dps) > 15
                    or not 0 < command.duration_s <= .05):
                raise ValueError("COMMAND_LIMIT")
            # CarlaAirAlignment maps x,y,-z; this is its inverse, not ENU->NED.
            vx, vy, down = command.velocity_mps
            velocity = (vx, vy, -down)
            self._position = tuple(p + v * command.duration_s for p, v in zip(self._position, velocity))
            self._yaw += command.yaw_rate_dps * command.duration_s
            self._last_velocity = velocity
        else:
            # Synthetic zero-velocity hold between bounded prefixes is a model
            # assumption, not an assertion about a real vehicle's stopping.
            self._last_velocity = (0., 0., 0.)
        self._target = tuple(p + v * self.dt_s for p, v in zip(self._target, self._velocity))
        self.index += 1
        return self.observe()
