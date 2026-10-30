"""Visible-oracle training inputs, distinct from privileged future labels.

Consumes recorded own-pose and visible position/identity observations in order.
It is not an RGB detector and does not provide counterfactual action evidence.
"""
from .causal_belief import CausalTargetEstimator
from .models import TrackingState, UAVState


def iter_causal_recorded_states(episode, constraints, stop):
    required = ('visibility_observed', 'in_frame_observed',
                'occlusion_observed', 'camera_position_observed')
    if (any(episode.metadata.get(key) is not True for key in required)
            or episode.camera_yaws_deg is None):
        raise ValueError('Causal training requires recorded visibility/in-frame/occlusion and camera pose/yaw')
    if not 0 <= stop <= episode.frame_count:
        raise ValueError('Causal input stop must be within episode')
    estimator = CausalTargetEstimator()
    for index in range(stop):
        uav = UAVState(
            position_m=tuple(float(x) for x in episode.camera_positions[index]),
            velocity_mps=tuple(float(x) for x in episode.camera_velocities[index]),
            yaw_deg=float(episode.camera_yaws_deg[index]),
            battery_percent=0., minimum_obstacle_margin_m=0.)
        visible = (bool(episode.target_visible[index])
                   and bool(episode.target_in_frame[index])
                   and not bool(episode.target_occluded[index]))
        # Never read a hidden identity or position, including at cold start.
        actor_id = int(episode.target_actor_ids[index]) if visible else None
        if actor_id is not None and actor_id < 0:
            raise ValueError('Causal training requires known identity for a visible observation')
        measurement = tuple(float(x) for x in episode.target_positions[index]) if visible else None
        target = estimator.update(timestamp_s=float(episode.image_timestamps[index]),
            measurement=measurement, actor_id=actor_id, uav=uav,
            desired_range_m=constraints.desired_range_m, fov_half_angle_deg=45.,
            identity_confidence=1. if visible else 0.)
        yield TrackingState(target=target, uav=uav, constraints=constraints,
            mission_goal='Maintain target identity and visual visibility',
            obstacle_context='Recorded input has no timestamped clearance/battery observation',
            environment={'measurement_source': 'recorded_visible_oracle',
                         'target_actor_id': actor_id,
                         'frame_index': index,
                         'position_frame': 'carla_world_z_up',
                         'clock_domain': 'recorded_image_timestamp',
                         'missing_safety_measurements': ['minimum_clearance_m', 'battery_percent'],
                         'not_visual_detector': True})
