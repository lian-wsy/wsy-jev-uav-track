"""Live visible-oracle input contract. No connections or actuation are owned here.

This is a simulator diagnostic, not a trained detector or a safety certificate.
Only caller-provided, currently visible measurements enter the causal estimator.
"""
from dataclasses import asdict, replace
import math

from .causal_belief import CausalTargetEstimator
from .frames import AirSimOriginBridge, _vec3
from .history import attach_history, build_target_belief_history
from .live_observation import CaptureWindow, TemporalObservationGate
from .models import MissionConstraints, TrackingState, UAVState


def read_visible_oracle_position(visible, read_position):
    """The hidden case must not even query a target-position RPC."""
    return _vec3(read_position()) if visible else None


def identify_owned_tesla(new_scene_names):
    """Resolve this probe's observed Tesla pawn/controller spawn inventory only.

    Called with the before/after set difference in an exclusive owned instance;
    unknown actors or multiple pawns fail closed, rather than guessing a target.
    """
    pawns = [n for n in new_scene_names if n.startswith('BP_TeslaM3_C_')]
    if len(pawns) != 1 or any(not n.startswith(('BP_TeslaM3_C_', 'WheeledVehicleAIController_'))
                              for n in new_scene_names):
        raise ValueError('New actor inventory is ambiguous; cannot establish target identity')
    return pawns[0]


class LiveBeliefAdapter:
    """Convert an accepted live packet into the existing world-z-up pipeline.

    Each instance belongs to one calibrated vehicle and session. Unknown safety
    values become conservative zeros with explicit missing-data provenance, not
    UAVState's example defaults. No action can be dispatched by this class.
    """
    def __init__(self, alignment: AirSimOriginBridge):
        self.alignment = alignment
        self.gate = TemporalObservationGate()
        self.estimator = CausalTargetEstimator()
        self.states = []
        self.constraints = MissionConstraints()

    def update(self, window: CaptureWindow, *, now_s, vehicle_local_ned,
               velocity_local_ned, yaw_deg, measurement_world, target_actor_id,
               image_center_error, minimum_clearance_m=None, battery_percent=None, battery_capacity_wh=None):
        # Rejected timing revokes the gate's old authorization immediately.
        self.gate.accept(window, now_s=now_s)
        position = self.alignment.vehicle_to_carla(vehicle_local_ned)
        vx, vy, vz = _vec3(velocity_local_ned)
        if not math.isfinite(yaw_deg):
            raise ValueError('Vehicle yaw must be finite')
        missing = []
        if minimum_clearance_m is None:
            missing.append('minimum_clearance_m')
        if battery_percent is None:
            missing.append('battery_percent')
        clearance = 0.0 if minimum_clearance_m is None else float(minimum_clearance_m)
        battery = 0.0 if battery_percent is None else float(battery_percent)
        if not math.isfinite(clearance) or clearance < 0 or not math.isfinite(battery) or not 0 <= battery <= 100:
            raise ValueError('Invalid live safety measurement')
        visible = measurement_world is not None
        if visible:
            measurement_world = _vec3(measurement_world)
            if (type(target_actor_id) is not int or target_actor_id < 0
                    or image_center_error is None or not math.isfinite(image_center_error)
                    or not 0 <= image_center_error <= 1):
                raise ValueError('Visible oracle needs an identity and finite image-center measurement')
        elif target_actor_id is not None or image_center_error is not None:
            raise ValueError('Hidden observations cannot carry current identity or image measurements')
        uav = UAVState(position_m=position, velocity_mps=(vx, vy, -vz), yaw_deg=yaw_deg,
                       minimum_obstacle_margin_m=clearance, battery_percent=battery,
                       battery_capacity_wh=battery_capacity_wh,
                       energy_source="MEASURED" if battery_percent is not None else "UNKNOWN")
        # Commit estimator/history only after the entire state passes validation.
        estimator = replace(self.estimator)
        target = estimator.update(timestamp_s=window.image_s, measurement=measurement_world,
            actor_id=target_actor_id, uav=uav, desired_range_m=self.constraints.desired_range_m,
            fov_half_angle_deg=45, identity_confidence=1.0 if visible else 0.0)
        if visible:
            target = replace(target, image_center_error=image_center_error)
        if any(not math.isfinite(x) for value in asdict(target).values()
               for x in (value if isinstance(value, tuple) else (value,))):
            raise ValueError('Derived target state is nonfinite')
        state = TrackingState(target, uav, constraints=self.constraints,
            obstacle_context='No live obstacle map; unknown clearance is zero' if minimum_clearance_m is None
                             else 'Caller-supplied current minimum clearance; no swept-volume guarantee',
            environment={'measurement_source': 'simulator_visible_oracle',
                         'target_actor_id': target_actor_id,
                         'position_frame': 'carla_world_z_up',
                         'missing_safety_measurements': missing,
                         'clock_domain': 'airsim_image', 'not_visual_detector': True})
        states = [*self.states[-7:], state]
        output = attach_history(state, build_target_belief_history(states, max_length=8))
        self.estimator, self.states = estimator, states
        return output
