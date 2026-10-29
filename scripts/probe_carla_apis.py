"""One owned-instance API probe. Never connect to shared/default ports."""
import argparse
import json
import time
import threading
import math
import sys
from pathlib import Path

# The simulator's Python 3.10 client need not have this project installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jev_uav_track.isolated_runtime import (require_rpc_success, command_cadence,
    validate_probe_map, require_loaded_map, PROBE_MAPS)

import carla
import airsim


def measured_command(air, velocity, yaw_rate=0, observation=None):
    """Measure actual RPC completion and immediately issue explicit braking."""
    if observation is None:
        observation = {}
    observation.update(velocity_command_ned=velocity, yaw_rate_dps=yaw_rate, trace=[])
    ready, stop = threading.Event(), threading.Event()

    def observe():
        try:
            reader = airsim.MultirotorClient(ip='127.0.0.1', port=42451, timeout_value=5)
            ready.set()
            while not stop.is_set():
                start = time.monotonic()
                state = reader.getMultirotorState(vehicle_name='JevDrone')
                observation['trace'].append({'wall_s': time.monotonic(),
                    'airsim_s': state.timestamp * 1e-9,
                    'position': list(state.kinematics_estimated.position),
                    'velocity': list(state.kinematics_estimated.linear_velocity),
                    'yaw_rad': airsim.to_eularian_angles(state.kinematics_estimated.orientation)[2]})
                stop.wait(max(0, 0.05 - (time.monotonic() - start)))
        except Exception as exc:
            observation['sampler_error'] = type(exc).__name__
            ready.set()

    thread = threading.Thread(target=observe, daemon=True)
    thread.start()
    try:
        if not ready.wait(5):
            raise TimeoutError('Telemetry sampler did not initialize')
        observation['command_started_wall_s'] = time.monotonic()
        require_rpc_success(air.moveByVelocityAsync(*velocity, 1.0,
                drivetrain=airsim.DrivetrainType.MaxDegreeOfFreedom,
                yaw_mode=airsim.YawMode(is_rate=True, yaw_or_rate=yaw_rate),
                vehicle_name='JevDrone'))
        observation['command_completed_wall_s'] = time.monotonic()
        observation['brake_started_wall_s'] = time.monotonic()
        require_rpc_success(air.moveByVelocityAsync(0, 0, 0, 1.0, vehicle_name='JevDrone'))
        observation['brake_completed_wall_s'] = time.monotonic()
    finally:
        stop.set()
        thread.join(timeout=6)
    if thread.is_alive() or 'sampler_error' in observation:
        raise RuntimeError('Telemetry sampler failed')
    return observation


def coordinate_probe(world, air):
    """Read common static actor landmarks through two independent APIs.

AirSim caches its actor map at startup; objects spawned later by CARLA appear
in simListSceneObjects but return NaN in simGetObjectPose. Do not use them.
"""
    names = set(air.simListSceneObjects())
    samples, used = [], set()
    objects = sorted(world.get_environment_objects(carla.CityObjectLabel.Any), key=lambda x: x.name)
    for obj in objects:
        # CARLA RegisterSMComponents appends _SemanticTag_componentIndex to
        # Actor->GetName(), while storing the actor transform, not its BB center.
        name = obj.name if obj.name in names else obj.name.rsplit('_', 2)[0]
        if name not in names or name in used:
            continue
        used.add(name)
        pose = air.simGetObjectPose(name)
        actual = obj.transform.location
        values = [actual.x, actual.y, actual.z, *list(pose.position)]
        if not all(math.isfinite(x) for x in values):
            raise RuntimeError('Nonfinite landmark pose; coordinate audit invalid')
        samples.append({'carla_object_name': obj.name, 'airsim_actor_name': name,
                        'carla_xyz_m': values[:3], 'airsim_ned_m': values[3:]})
        if len(samples) == 12:
            break
    if len(samples) < 4:
        raise RuntimeError('Insufficient common static landmarks')
    offsets = [[s['airsim_ned_m'][i] - (s['carla_xyz_m'][i] if i < 2 else -s['carla_xyz_m'][i])
                for i in range(3)] for s in samples]
    spread = [max(o[i] for o in offsets) - min(o[i] for o in offsets) for i in range(3)]
    span = [max(s['carla_xyz_m'][i] for s in samples) - min(s['carla_xyz_m'][i] for s in samples)
            for i in range(3)]
    return {'target_frame': 'airsim_global_object_ned',
            'samples': samples, 'offset_first_m': offsets[0], 'offset_spread_m': spread,
            'landmark_axis_span_m': span,
            'mapping_pass': max(spread) < 0.01 and min(span) > 1.0}


def contract_probe(world, air, output, result, bootstrap, observation_contract=False, belief_contract=False):
    def sample():
        state = air.getMultirotorState(vehicle_name='JevDrone')
        snap = world.get_snapshot()
        return {'wall_s': time.monotonic(), 'carla_s': snap.timestamp.elapsed_seconds,
                'frame': snap.frame, 'airsim_s': state.timestamp * 1e-9,
                'position': list(state.kinematics_estimated.position),
                'velocity': list(state.kinematics_estimated.linear_velocity)}

    result['phase'] = 'pause'
    # AirSim pause must be measured, not assumed to pause CARLA's clock.
    air.simPause(True)
    result['pause_start'] = sample()
    time.sleep(0.4)
    result['pause_end'] = sample()
    air.simPause(False)
    time.sleep(0.2)
    result['resume'] = sample()
    result['phase'] = 'coordinate_marker'
    result['coordinate_mapping'] = coordinate_probe(world, air)
    if observation_contract:
        from probe_observation_contract import origin_sample
        result['phase'] = 'origin_before'
        result['origin_bridge'] = {'samples': []}
        result['origin_bridge']['samples'].append(origin_sample(air))
    original = world.get_settings()
    try:
        result['phase'] = 'unpaced_ticks'
        sync = world.get_settings()
        sync.synchronous_mode = True
        sync.fixed_delta_seconds = 0.05
        world.apply_settings(sync)
        result['sync_begin'] = sample()
        for _ in range(20):
            world.tick(5)
        result['sync_end'] = sample()
        result['phase'] = 'paced_ticks'
        result['paced_begin'] = sample()
        paced_start = time.monotonic()
        result['paced_trace'] = []
        for i in range(20):
            delay = paced_start + (i + 1) * 0.05 - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            world.tick(5)
            result['paced_trace'].append(sample())
        result['paced_end'] = sample()
    finally:
        world.apply_settings(original)

    air.enableApiControl(True, vehicle_name='JevDrone')
    try:
        result['phase'] = 'takeoff'
        if not air.armDisarm(True, vehicle_name='JevDrone'):
            raise RuntimeError('Task drone arm rejected')
        result['takeoff'] = {'method': bootstrap, 'before': sample(),
                             'started_wall_s': time.perf_counter()}
        collision_before = air.simGetCollisionInfo(vehicle_name='JevDrone').time_stamp
        result['takeoff']['collision_before'] = collision_before
        try:
            if bootstrap == 'takeoff':
                require_rpc_success(air.takeoffAsync(timeout_sec=10, vehicle_name='JevDrone'), result['takeoff'])
            else:
                require_rpc_success(air.moveByVelocityAsync(0, 0, -0.5, 4.0,
                    vehicle_name='JevDrone'), result['takeoff'])
                require_rpc_success(air.moveByVelocityAsync(0, 0, 0, 1.0, vehicle_name='JevDrone'))
        finally:
            result['takeoff']['completed_wall_s'] = time.perf_counter()
            result['takeoff']['after'] = sample()
            collision = air.simGetCollisionInfo(vehicle_name='JevDrone')
            result['takeoff']['collision'] = {
                'timestamp': collision.time_stamp, 'has_collided': collision.has_collided,
                'object_name': collision.object_name, 'penetration_depth': collision.penetration_depth,
                'position': list(collision.position), 'normal': list(collision.normal)}
        if bootstrap == 'velocity':
            before, after = result['takeoff']['before'], result['takeoff']['after']
            ascent = before['position'][2] - after['position'][2]
            horizontal = math.hypot(*(after['position'][i] - before['position'][i] for i in (0, 1)))
            speed = math.sqrt(sum(v * v for v in after['velocity']))
            passed = (0.8 <= ascent <= 3.0 and horizontal < 0.5 and speed < 0.3
                      and collision.time_stamp <= collision_before)
            result['takeoff'].update(ascent_m=ascent, horizontal_m=horizontal,
                                     terminal_speed_mps=speed, state_gate_pass=passed)
            if not passed:
                raise RuntimeError('Bounded vertical bootstrap state gate failed')
        result['after_takeoff'] = sample()
        result['collision_before'] = air.simGetCollisionInfo(vehicle_name='JevDrone').time_stamp
        result['phase'] = 'velocity_command'
        # One bounded world-NED x command, sampled by a separate RPC client.
        reader = airsim.MultirotorClient(ip='127.0.0.1', port=42451, timeout_value=5)
        start = time.monotonic()
        future = air.moveByVelocityAsync(0.5, 0, 0, 1.0,
                  drivetrain=airsim.DrivetrainType.MaxDegreeOfFreedom,
                  yaw_mode=airsim.YawMode(is_rate=True, yaw_or_rate=0), vehicle_name='JevDrone')
        result['command_trace'] = []
        trace = result['command_trace']
        for i in range(26):
            delay = start + i * 0.05 - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            state = reader.getMultirotorState(vehicle_name='JevDrone')
            trace.append({'wall_elapsed_s': time.monotonic() - start,
                          'airsim_s': state.timestamp * 1e-9,
                          'position': list(state.kinematics_estimated.position),
                          'velocity': list(state.kinematics_estimated.linear_velocity)})
        require_rpc_success(future)
        result['command_trace'] = trace
        result['command_wait_wall_s'] = time.monotonic() - start
        require_rpc_success(air.moveByVelocityAsync(0, 0, 0, 1.0, vehicle_name='JevDrone'))
        result['after_explicit_brake'] = sample()
        collision = air.simGetCollisionInfo(vehicle_name='JevDrone')
        result['collision_after'] = collision.time_stamp
        result['new_collision'] = collision.time_stamp > result['collision_before']
        result['phase'] = 'axis_commands'
        result['axis_commands'] = []
        for velocity, yaw_rate in [((0.3, 0, 0), 0), ((0, 0.3, 0), 0),
                                   ((0, 0, -0.3), 0), ((0, 0, 0), 15)]:
            observation = {}
            result['axis_commands'].append(observation)
            measured_command(air, velocity, yaw_rate, observation)
        result['phase'] = 'command_refresh'
        result['command_refresh'] = {'before': sample()}
        command_cadence(
            lambda duration: require_rpc_success(air.moveByVelocityAsync(
                0.2, 0, 0, duration, vehicle_name='JevDrone')),
            lambda: require_rpc_success(air.moveByVelocityAsync(
                0, 0, 0, 1.0, vehicle_name='JevDrone')),
            result['command_refresh'])
        result['command_refresh']['after'] = sample()
        result['collision_after_axes'] = air.simGetCollisionInfo(vehicle_name='JevDrone').time_stamp
        result['new_collision_axes'] = result['collision_after_axes'] > result['collision_before']
        if observation_contract:
            from jev_uav_track.frames import CarlaAirAlignment, AirSimOriginBridge
            result['phase'] = 'origin_after'
            audit = result['origin_bridge']
            audit['samples'].append(origin_sample(air))
            mapping = result['coordinate_mapping']
            if not mapping['mapping_pass']:
                raise ValueError('CARLA to global NED mapping failed')
            bridge = AirSimOriginBridge.calibrate(CarlaAirAlignment(tuple(mapping['offset_first_m'])),
                [x['global_ned_m'] for x in audit['samples']],
                [x['vehicle_local_ned_m'] for x in audit['samples']])
            audit.update(local_minus_global_m=bridge.local_minus_global_m,
                carla_to_vehicle_local_offset_m=bridge.carla_to_vehicle((0, 0, 0)),
                max_residual_m=max(math.dist(bridge.global_to_vehicle(x['global_ned_m']),
                    x['vehicle_local_ned_m']) for x in audit['samples']),
                calibration_pass=True)
        result['phase'] = 'camera'
        image = air.simGetImages([airsim.ImageRequest('front_center', airsim.ImageType.Scene,
                                                     pixels_as_float=False, compress=True)],
                                 vehicle_name='JevDrone')[0]
        result['camera'] = {'timestamp_s': image.time_stamp * 1e-9,
                            'width': image.width, 'height': image.height,
                            'camera_position': list(image.camera_position),
                            'png_bytes': len(image.image_data_uint8)}
        if image.width and image.height and image.image_data_uint8:
            (output.parent / 'camera.png').write_bytes(image.image_data_uint8)
        if observation_contract:
            from probe_observation_contract import probe_observation_contract
            result['phase'] = 'observation_contract'
            result['observation_contract'] = {}
            probe_observation_contract(world, air, output.parent, result['observation_contract'])
        if belief_contract:
            from probe_belief_contract import probe_belief_contract
            result['phase'] = 'belief_contract'
            result['belief_contract'] = {}
            probe_belief_contract(world, air, output.parent, bridge, result['belief_contract'])
    finally:
        air.cancelLastTask(vehicle_name='JevDrone')
        air.armDisarm(False, vehicle_name='JevDrone')
        air.enableApiControl(False, vehicle_name='JevDrone')
        # This instance is immediately shut down by the owner; no long-lived drone.
    result['phase'] = 'completed'
    return result


def main(output, profile, expected_settings, bootstrap, expected_map='Town02'):
    validate_probe_map(expected_map, profile)
    client = carla.Client('127.0.0.1', 3100)
    client.set_timeout(5)
    air = airsim.MultirotorClient(ip='127.0.0.1', port=42451, timeout_value=5)
    if not air.ping():
        raise RuntimeError('AirSim ping failed')
    settings = json.loads(air.getSettingsString())
    expected = json.loads(expected_settings.read_text())
    if settings != expected or expected.get('ApiServerPort') != 42451 or list(expected.get('Vehicles', {})) != ['JevDrone']:
        raise RuntimeError('Loaded AirSim settings are not task-owned settings')
    world = client.get_world()
    actual_map = world.get_map().name
    require_loaded_map(actual_map, expected_map)
    samples = []
    for _ in range(12):
        wall = time.monotonic()
        snap = world.get_snapshot()
        state = air.getMultirotorState(vehicle_name='JevDrone')
        samples.append({'wall_s': wall, 'carla_s': snap.timestamp.elapsed_seconds,
                        'carla_frame': snap.frame, 'airsim_s': state.timestamp * 1e-9,
                        'position_ned': list(state.kinematics_estimated.position)})
        time.sleep(0.1)
    result = {'kind': 'LIVE_OWNED_API_PROBE', 'carla_server': client.get_server_version(),
              'python_version': sys.version, 'rpc_success_checked': True,
              'carla_client': client.get_client_version(), 'map': actual_map,
              'requested_map': expected_map, 'map_match': True,
              'airsim_server': air.getServerVersion(), 'vehicles': air.listVehicles(),
              'settings_match': True, 'samples': samples,
              'note': 'No arm, reset, motion, RGB capture or target tracking in this probe.'}
    if profile in ('contract', 'observation', 'belief'):
        result['contract'] = {}
        result['note'] = 'Owned development physics probe; may arm/move/capture. Not a tracking comparison.'
        try:
            contract_probe(world, air, output, result['contract'], bootstrap,
                           profile in ('observation', 'belief'), profile == 'belief')
        except Exception as exc:
            result['contract_error'] = f'{type(exc).__name__}: {exc}'
            output.write_text(json.dumps(result, indent=2), encoding='utf-8')
            raise
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'samples'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=['api', 'contract', 'observation', 'belief'], default='api')
    parser.add_argument('--expected-settings', type=Path, required=True)
    parser.add_argument('--bootstrap', choices=['takeoff', 'velocity'], default='takeoff')
    parser.add_argument('--expected-map', choices=PROBE_MAPS, default='Town02')
    args = parser.parse_args()
    main(args.output, args.profile, args.expected_settings, args.bootstrap, args.expected_map)
