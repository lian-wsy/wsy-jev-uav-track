"""Owned live visible-oracle -> causal belief -> gated shadow decision probe.

Invoked only by the isolated runtime after ownership, origin and camera checks.
No learned detector, target velocity truth, missing-target pose query or action
dispatch. A camera reversal is an out-of-view diagnostic, NOT real occlusion.
"""
from dataclasses import asdict
import math
import time

import airsim
import carla

from jev_uav_track.live_belief import LiveBeliefAdapter, read_visible_oracle_position, identify_owned_tesla
from jev_uav_track.live_observation import CaptureWindow
from jev_uav_track.jev_client import MockJevClient, UtilityRankingClient
from jev_uav_track.pipeline import JevTrackPipeline
from probe_observation_contract import image_response


def probe_belief_contract(world, air, output_dir, alignment, result):
    result.update(kind='LIVE_VISIBLE_ORACLE_SHADOW_CONTRACT', phase='spawn_owned_target',
                  frames=[], real_jev_requests=0, dispatched_actions=0, target_pose_queries=0,
                  not_tracking_evaluation=True, missing_safety_measurements=['minimum_clearance_m', 'battery_percent'])
    adapter = LiveBeliefAdapter(alignment)
    pipelines = {'mock': JevTrackPipeline(MockJevClient()), 'utility': JevTrackPipeline(UtilityRankingClient())}
    state = air.getMultirotorState(vehicle_name='JevDrone')
    xyz = alignment.vehicle_to_carla(list(state.kinematics_estimated.position))
    yaw = airsim.to_eularian_angles(state.kinematics_estimated.orientation)[2]
    location = carla.Location(x=xyz[0] + 14 * math.cos(yaw), y=xyz[1] + 14 * math.sin(yaw), z=xyz[2])
    waypoint = world.get_map().get_waypoint(location, project_to_road=True)
    if waypoint is None:
        raise ValueError('No road waypoint for owned diagnostic target')
    transform = waypoint.transform
    transform.location.z += .8
    scene_before = set(air.simListSceneObjects())
    actor = world.try_spawn_actor(world.get_blueprint_library().find('vehicle.tesla.model3'), transform)
    if actor is None:
        raise ValueError('Owned target spawn rejected; no replacement seed or existing actor borrowed')
    try:
        actor.set_simulate_physics(False)
        time.sleep(.15)
        new_names = sorted(set(air.simListSceneObjects()) - scene_before)
        result['spawn'] = {'actor_id': actor.id, 'type_id': actor.type_id, 'new_scene_names': new_names,
            'carla_position_m': [transform.location.x, transform.location.y, transform.location.z]}
        target_name = identify_owned_tesla(new_names)
        result['spawn']['target_scene_name'] = target_name
        air.simAddDetectionFilterMeshName('front_center', airsim.ImageType.Scene, target_name, vehicle_name='JevDrone')
        air.simSetDetectionFilterRadius('front_center', airsim.ImageType.Scene, 4000, vehicle_name='JevDrone')
        collision_before = air.simGetCollisionInfo(vehicle_name='JevDrone').time_stamp
        result['collision_before'] = collision_before
        result['phase'] = 'visible_oracle_shadow'
        start = time.perf_counter()
        for index in range(16):
            time.sleep(max(0, start + .2 * index - time.perf_counter()))
            # Known scenario manipulation, never described as real occlusion.
            if index in (0, 4, 8):
                reverse = index == 4
                air.simSetCameraPose('front_center', airsim.Pose(airsim.Vector3r(.75, 0, -.2),
                    airsim.to_quaternion(0, 0, math.pi if reverse else 0)), vehicle_name='JevDrone')
            began = time.perf_counter()
            carla_before = world.get_snapshot()
            air_before = air.getMultirotorState(vehicle_name='JevDrone')
            image = image_response(air)
            detections = air.simGetDetections('front_center', airsim.ImageType.Scene, vehicle_name='JevDrone')
            matches = [d for d in detections if d.name == target_name]
            if len(matches) > 1:
                raise ValueError('Duplicate target detections')
            visible = bool(matches)
            def read_position():
                result['target_pose_queries'] += 1
                p = actor.get_location()
                return p.x, p.y, p.z
            measurement = read_visible_oracle_position(visible, read_position)
            air_after = air.getMultirotorState(vehicle_name='JevDrone')
            carla_after = world.get_snapshot()
            ended = time.perf_counter()
            window = CaptureWindow(began, ended, air_before.timestamp * 1e-9, image.time_stamp * 1e-9,
                air_after.timestamp * 1e-9, carla_before.timestamp.elapsed_seconds,
                carla_after.timestamp.elapsed_seconds, carla_before.frame, carla_after.frame)
            center_error = None
            box = None
            if visible:
                b = matches[0].box2D
                box = [b.min.x_val, b.min.y_val, b.max.x_val, b.max.y_val]
                if not (all(math.isfinite(x) for x in box) and 0 <= box[0] < box[2] <= image.width
                        and 0 <= box[1] < box[3] <= image.height):
                    raise ValueError('Invalid visible detection bounding box')
                nx = (box[0] + box[2]) / image.width - 1
                ny = (box[1] + box[3]) / image.height - 1
                center_error = min(1, math.hypot(nx, ny) / math.sqrt(2))
            kinematics = air_after.kinematics_estimated
            q = image.camera_orientation
            measured_yaw = math.degrees(airsim.to_eularian_angles(kinematics.orientation)[2])
            record = {'index': index, 'requested_view': 'reversed' if 4 <= index < 8 else 'forward',
                'window': asdict(window), 'target_visible': visible, 'target_measurement_world': measurement,
                'bbox_xyxy': box, 'accepted': False, 'detection_timestamp': None,
                'synchronization': 'bounded_rpc_interval_not_exact_exposure', 'decisions': {},
                'vehicle_local_ned_m': list(kinematics.position),
                'vehicle_velocity_local_ned_mps': list(kinematics.linear_velocity),
                'uav_yaw_deg': measured_yaw, 'image_center_error': center_error,
                'camera_local_ned_m': list(image.camera_position),
                'camera_orientation_xyzw': [q.x_val, q.y_val, q.z_val, q.w_val]}
            result['frames'].append(record)
            if index in (0, 6, 10):
                (output_dir / f'belief_frame_{index:02d}.png').write_bytes(image.image_data_uint8)
            record['decision_wall_s'] = time.perf_counter()
            try:
                tracking = adapter.update(window, now_s=record['decision_wall_s'],
                    vehicle_local_ned=list(kinematics.position), velocity_local_ned=list(kinematics.linear_velocity),
                    yaw_deg=measured_yaw,
                    measurement_world=measurement, target_actor_id=actor.id if visible else None,
                    image_center_error=center_error)
            except ValueError as exc:
                record['rejection'] = str(exc)
                continue
            record.update(accepted=True, state=tracking.to_dict())
            candidates = pipelines['utility'].generate_candidates(tracking)
            record['candidates'] = [x.to_dict() for x in candidates]
            for name, pipeline in pipelines.items():
                try:
                    selected = pipeline.selector.select(tracking, candidates)
                except RuntimeError as exc:
                    if str(exc) != 'No candidate action passed the hard safety filter':
                        raise
                    record['decisions'][name] = {'status': 'SAFETY_REJECTED', 'selected': None, 'reason': str(exc)}
                else:
                    record['decisions'][name] = {'status': 'SHADOW_ONLY', 'selected': selected.action.id,
                                                'reason': selected.reason}
                    raise RuntimeError('Unknown safety inputs unexpectedly authorized a choice')
        result['accepted_count'] = sum(x['accepted'] for x in result['frames'])
        result['visible_count'] = sum(x['target_visible'] for x in result['frames'])
        result['collision_after'] = air.simGetCollisionInfo(vehicle_name='JevDrone').time_stamp
        result['new_collision'] = result['collision_after'] > collision_before
        result['phase'] = 'completed'
        if (not any(x['accepted'] and x['target_visible'] for x in result['frames'])
                or not any(x['accepted'] and not x['target_visible'] for x in result['frames'])):
            raise RuntimeError('Did not obtain both accepted visible and missing measurements')
    finally:
        result['target_destroyed'] = actor.destroy()
        air.simClearDetectionMeshNames('front_center', airsim.ImageType.Scene, vehicle_name='JevDrone')
        air.simSetCameraPose('front_center', airsim.Pose(airsim.Vector3r(.75, 0, -.2),
            airsim.Quaternionr(0, 0, 0, 1)), vehicle_name='JevDrone')
