"""Camera and timestamp probes; called only inside the owned runtime lifecycle."""
from dataclasses import asdict
import math
import time

import airsim

from jev_uav_track.isolated_runtime import require_rpc_success
from jev_uav_track.frames import camera_mount_error
from jev_uav_track.live_observation import CaptureWindow, TemporalObservationGate
from jev_uav_track.sim_adapter import AirSimWorldVelocityCommand


def image_response(air):
    responses = air.simGetImages([airsim.ImageRequest(
        'front_center', airsim.ImageType.Scene, pixels_as_float=False, compress=True)],
        vehicle_name='JevDrone')
    if len(responses) != 1:
        raise ValueError('Expected one camera response')
    response = responses[0]
    if response.width != 320 or response.height != 240 or not response.image_data_uint8:
        raise ValueError('Invalid development image')
    return response


def capture(air, world):
    """Bracket the image with same-domain AirSim reads, retaining CARLA separately."""
    start = time.perf_counter()
    carla_before = world.get_snapshot()
    air_before = air.getMultirotorState(vehicle_name='JevDrone')
    image = image_response(air)
    air_after = air.getMultirotorState(vehicle_name='JevDrone')
    carla_after = world.get_snapshot()
    end = time.perf_counter()
    window = CaptureWindow(start, end, air_before.timestamp * 1e-9,
        image.time_stamp * 1e-9, air_after.timestamp * 1e-9,
        carla_before.timestamp.elapsed_seconds, carla_after.timestamp.elapsed_seconds,
        carla_before.frame, carla_after.frame)
    return window, image


def probe_observation_contract(world, air, output_dir, result):
    result.update(kind='LIVE_OWNED_OBSERVATION_CONTRACT', phase='camera_extrinsics',
                  tracking_evaluation=False, captures=[], dispatched=[])
    gate = TemporalObservationGate()
    result['limits'] = {name: getattr(gate, name) for name in (
        'max_capture_s', 'decision_ttl_s', 'max_clock_gap_s', 'clock_roundoff_s')}
    collision_before = air.simGetCollisionInfo(vehicle_name='JevDrone').time_stamp
    result['collision_before'] = collision_before
    # Frozen scene for a pose comparison, not a dynamic pixel/pose sync claim.
    air.simPause(True)
    try:
        before = image_response(air)
        (output_dir / 'camera_before.png').write_bytes(before.image_data_uint8)
        offset = (0.75, 0.0, -0.2)  # Body FRD: forward and above the rotor plane.
        result['camera_extrinsics'] = {'relative_position_body_frd_m': offset,
                                       'before_timestamp_s': before.time_stamp * 1e-9,
                                       'scene_paused': True}
        pose = airsim.Pose(airsim.Vector3r(*offset), airsim.Quaternionr(0, 0, 0, 1))
        air.simSetCameraPose('front_center', pose, vehicle_name='JevDrone')
        state = air.getMultirotorState(vehicle_name='JevDrone')
        q = state.kinematics_estimated.orientation
        body_position = list(state.kinematics_estimated.position)
        after = image_response(air)
        (output_dir / 'camera_after.png').write_bytes(after.image_data_uint8)
        actual = list(after.camera_position)
        camera_q = after.camera_orientation
        body_quaternion = [q.x_val, q.y_val, q.z_val, q.w_val]
        camera_quaternion = [camera_q.x_val, camera_q.y_val, camera_q.z_val, camera_q.w_val]
        error = camera_mount_error(body_position, body_quaternion, offset, actual, camera_quaternion)
        result['camera_extrinsics'].update({
            'vehicle_position_ned_m': body_position,
            'vehicle_orientation_xyzw': body_quaternion, 'camera_orientation_xyzw': camera_quaternion,
            'observed_position_ned_m': actual, **error,
            'pose_pass': error['position_error_m'] < 0.02 and error['orientation_error_deg'] < 1.0,
            'after_timestamp_s': after.time_stamp * 1e-9,
            'visual_clearance': 'REQUIRES_IMAGE_REVIEW'})
        if not result['camera_extrinsics']['pose_pass']:
            raise ValueError('Camera mounting pose disagrees with vehicle transform')
    finally:
        air.simPause(False)

    result['phase'] = 'timed_capture'
    schedule = time.perf_counter()
    last = None
    for index in range(12):
        time.sleep(max(0, schedule + index * 0.2 - time.perf_counter()))
        window, image = capture(air, world)
        record = {'window': asdict(window), 'width': image.width, 'height': image.height,
                  'camera_position_ned_m': list(image.camera_position), 'accepted': False}
        result['captures'].append(record)
        try:
            gate.accept(window, now_s=time.perf_counter())
            record['accepted'] = True
            last = window
        except ValueError as exc:
            record['rejection'] = str(exc)
            last = None
    result['accepted_count'] = sum(x['accepted'] for x in result['captures'])
    if last is None:
        raise ValueError('Last live observation was not accepted; no motion dispatch')

    def execute(command):
        record = {'command': asdict(command), 'sent_wall_s': time.perf_counter()}
        result['dispatched'].append(record)
        require_rpc_success(air.moveByVelocityAsync(*command.velocity_mps, command.duration_s,
            yaw_mode=airsim.YawMode(is_rate=True, yaw_or_rate=command.yaw_rate_dps),
            vehicle_name='JevDrone'), record)
        record['completed_wall_s'] = time.perf_counter()

    result['phase'] = 'fresh_dispatch'
    command = AirSimWorldVelocityCommand((0.1, 0, 0), 0, 0.05)
    try:
        gate.dispatch(last, command, execute)
    finally:
        require_rpc_success(air.moveByVelocityAsync(0, 0, 0, 0.5, vehicle_name='JevDrone'))

    result['phase'] = 'expired_dispatch'
    old_window, _ = capture(air, world)
    result['expiry_test_window'] = asdict(old_window)
    gate.accept(old_window, now_s=time.perf_counter())
    count_before = len(result['dispatched'])
    time.sleep(0.25)
    result['expiry_checked_wall_s'] = time.perf_counter()
    try:
        gate.dispatch(old_window, command, execute)
    except ValueError as exc:
        result['expiry_rejection'] = str(exc)
    result['expired_dispatch_rejected'] = (
        len(result['dispatched']) == count_before
        and 'expired' in result.get('expiry_rejection', '').lower())
    if not result['expired_dispatch_rejected']:
        raise RuntimeError('Delayed observation reached a movement RPC')
    result['collision_after'] = air.simGetCollisionInfo(vehicle_name='JevDrone').time_stamp
    result['new_collision'] = result['collision_after'] > collision_before
    result['phase'] = 'completed'
