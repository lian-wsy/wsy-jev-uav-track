"""Visibility-gated state estimate; no hidden positions or future velocities."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .models import TargetBelief, UAVState, Vec3


@dataclass
class CausalTargetEstimator:
    last_position: Vec3 | None = None
    last_time_s: float | None = None
    last_actor_id: int | None = None
    velocity: Vec3 = (0.0, 0.0, 0.0)
    prior_position: Vec3 | None = None
    last_update_s: float | None = None

    def update(self, *, timestamp_s: float, measurement: Vec3 | None,
               actor_id: int | None, uav: UAVState, desired_range_m: float,
               fov_half_angle_deg: float, identity_confidence: float) -> TargetBelief:
        if not math.isfinite(timestamp_s) or (
            self.last_update_s is not None and timestamp_s <= self.last_update_s
        ):
            raise ValueError("Belief updates require finite increasing timestamps")
        self.last_update_s = timestamp_s
        visible = measurement is not None
        if visible:
            if len(measurement) != 3 or not all(math.isfinite(v) for v in measurement):
                raise ValueError("Measurement must be a finite 3-vector")
            if self.last_position is not None and actor_id == self.last_actor_id:
                dt = timestamp_s - self.last_time_s
                self.velocity = tuple((measurement[i] - self.last_position[i]) / dt for i in range(3))
            else:
                self.velocity = (0.0, 0.0, 0.0)
            self.last_position = tuple(measurement)
            self.last_time_s = timestamp_s
            self.last_actor_id = actor_id
        if self.last_position is None:
            # Fixed search prior anchored to own pose, never to hidden truth.
            if self.prior_position is None:
                angle = math.radians(uav.yaw_deg)
                self.prior_position = (
                    uav.position_m[0] + desired_range_m * math.cos(angle),
                    uav.position_m[1] + desired_range_m * math.sin(angle),
                    uav.position_m[2],
                )
            position = self.prior_position
            age = 0.0
            covariance = 10.0
        else:
            age = timestamp_s - self.last_time_s
            position = tuple(self.last_position[i] + self.velocity[i] * age for i in range(3))
            covariance = 0.05 + age * age
        relative = tuple(position[i] - uav.position_m[i] for i in range(3))
        bearing = (math.degrees(math.atan2(relative[1], relative[0])) - uav.yaw_deg + 180) % 360 - 180
        return TargetBelief(
            position_m=position, velocity_mps=self.velocity,
            range_m=math.sqrt(sum(v * v for v in relative)), bearing_deg=bearing,
            image_center_error=min(1.0, abs(bearing) / fov_half_angle_deg),
            covariance_trace=covariance, visibility_probability=1.0 if visible else 0.05,
            occlusion_risk=0.0 if visible else 0.6,
            identity_confidence=identity_confidence if self.last_position is not None else 0.0,
            timestamp_s=timestamp_s,
        )
