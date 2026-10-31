"""Ten-second current causal-model smoke, only under the isolated owner.

This is a deliberately limited engineering trial: visible simulator annotations
and registered obstacle boxes, not RGB perception or a certified collision hull.
An unarmed scenario placement is not takeoff or successful following. Unknown
landed/clearance/collision inputs, stale packets and transport errors abort.
"""
from dataclasses import asdict, replace
import hashlib
import json
import math
from pathlib import Path
import random
import time

from jev_uav_track.current_follow import (RegisteredGeometry, load_current_rollout,
    validate_follow_endpoint, run_control_session, require_follow_receipt, route_primitives,
    ActorGeometryCache, GeometricCenterFallback)

ROOT = Path(__file__).resolve().parents[1]
SEED = 2026103101


def owned_movement_port(airsim_sdk, ports):
    """Create on the actuator worker; never borrow a shared/default endpoint.

    This SDK checks Future timeouts on a global ONE-SECOND timer: .2 can
    expire a newly created request at its very first tick. Use the minimum
    supported integral backstop. The real transaction deadline remains .2 s
    in BoundedActuator; expiration still requires owned-process termination.
    This SDK parameter is not a control lease or a physical-stop guarantee.
    """
    from jev_uav_track.actuation import AirSimMovementPort
    validate_follow_endpoint(ports)
    client=airsim_sdk.MultirotorClient(ip='127.0.0.1',port=ports.airsim_rpc,timeout_value=1)
    return AirSimMovementPort(client,airsim_sdk,vehicle_name='JevDrone')


def save(path, data):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2, allow_nan=False)


def probe_current_follow(world, air, output, result, *, ports, carla_sdk, airsim_sdk):
    validate_follow_endpoint(ports)
    result.update(kind='CURRENT_CAUSAL_BOUNDED_FOLLOW', status='FOLLOW_ABORTED',
        phase='frozen_model', method='mock_jev', real_jev_calls=0,
        rollout_variant='geometry_center_fallback_v1',
        baseline_intervention='CENTER_ONLY_WHEN_ALL_INTERVAL_OUTPUTS_REJECTED',
        scenario_seed=SEED, frames=[], prefixes=[], cleanup_errors=[], target_destroyed=False,
        initialization_kind='UNARMED_SIMULATOR_PLACEMENT_NOT_TAKEOFF',
        perception='CURRENT_VISIBLE_SIMULATOR_ANNOTATION_NOT_RGB_DETECTOR',
        geometry_scope='REGISTERED_SCENE_POINT_DISTANCE_NOT_CERTIFIED_BODY_CLEARANCE',
        limits={'speed_mps':.3, 'yaw_rate_dps':15., 'prefix_s':.05, 'reply_ttl_s':.2,
                'capture_max_s':.15, 'point_distance_reserve_m':2., 'minimum_remaining_margin_m':3.5,
                'sdk_coarse_timeout_s':1, 'transaction_watchdog_s':.2},
        training_admission='NOT_AUDITED_NOT_TRAINING_DATA', body_clearance_certified=False,
        full_d6_acceptance=False, learned_gain_claimed=False)
    actor = None
    armed = api_enabled = False
    preparation_paused = False
    images = []
    def triple(value): return [float(value.x), float(value.y), float(value.z)]
    try:
        model, result['model'] = load_current_rollout(ROOT/'artifacts/d5_causal_calibration_20261030')
        # Import native helpers only after model and own-endpoint checks.
        from probe_carla_apis import coordinate_probe
        from probe_observation_contract import origin_sample, image_response
        from jev_uav_track.actuation import FlightTelemetry
        from jev_uav_track.async_control import MeasuredSafety
        from jev_uav_track.frames import CarlaAirAlignment, AirSimOriginBridge
        from jev_uav_track.isolated_runtime import require_loaded_map
        from jev_uav_track.jev_client import MockJevClient
        from jev_uav_track.live_belief import LiveBeliefAdapter, identify_owned_tesla, read_visible_oracle_position
        from jev_uav_track.live_observation import CaptureWindow
        from jev_uav_track.pipeline import JevTrackPipeline
        from jev_uav_track.neutral_startup import initialize_neutral

        require_loaded_map(world.get_map().name, 'Town02')
        result['phase'] = 'scene_initialization'
        source_dir = output/'source'
        for path in [*sorted((ROOT/'src/jev_uav_track').glob('*.py')),
                     *sorted((ROOT/'scripts').glob('*.py'))]:
            target = source_dir/path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        result['source_sha256'] = {str(p.relative_to(source_dir)):hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in source_dir.rglob('*.py')}
        mapping = coordinate_probe(world, air)
        result['coordinate_mapping'] = mapping
        if not mapping['mapping_pass']:
            raise ValueError('CARLA_GLOBAL_MAPPING_FAILED')
        alignment = CarlaAirAlignment(tuple(mapping['offset_first_m']))
        first = origin_sample(air)
        result['origin_first'] = first
        provisional = AirSimOriginBridge(alignment, tuple(a-b for a,b in
            zip(first['vehicle_local_ned_m'], first['global_ned_m'])))

        raw = list(world.get_level_bbs(carla_sdk.CityObjectLabel.Any))
        sky = list(world.get_level_bbs(carla_sdk.CityObjectLabel.Sky))
        def key(bb):
            return tuple(triple(bb.location)+triple(bb.extent)+
                         [bb.rotation.pitch,bb.rotation.yaw,bb.rotation.roll])
        sky_keys = {key(bb) for bb in sky}
        excluded = [i for i,bb in enumerate(raw) if key(bb) in sky_keys]
        geometry = RegisteredGeometry([bb for i,bb in enumerate(raw) if i not in excluded])
        save(output/'registered_static_geometry.json', {
            'source':'CARLA_get_level_bbs_Any', 'boxes':geometry.raw, 'raw_count':len(raw),
            'excluded_explicit_sky_indices':excluded,
            'point_reserve_m':2., 'body_clearance_certified':False})
        points = sorted(world.get_map().get_spawn_points(),
                        key=lambda tf: (tf.location.x,tf.location.y,tf.rotation.yaw))
        random.Random(SEED).shuffle(points)
        result['placement_checks'] = []
        chosen = None
        for tf in points:
            yaw = math.radians(tf.rotation.yaw)
            direction = (math.cos(yaw), math.sin(yaw))
            placement = (tf.location.x-12*direction[0], tf.location.y-12*direction[1], tf.location.z+16.8)
            # Method-independent fixture choice: geometry only, no images,
            # model outcomes, validation results or selector winner involved.
            corridor = [tuple(placement[i] + (direction[i]*s if i<2 else 0.) for i in range(3))
                        for s in (-1.,0.,1.,2.,3.,4.)]
            margin = min(geometry.distance(p) for p in corridor)-2.
            result['placement_checks'].append({'position_m':placement, 'remaining_point_margin_m':margin})
            if margin >= 5.5:
                chosen = tf, direction, placement, yaw
                break
        if chosen is None:
            raise ValueError('NO_GEOMETRY_ADMISSIBLE_INITIAL_SCENARIO')
        tf, direction, placement, yaw = chosen
        target_start = (tf.location.x,tf.location.y,tf.location.z+.8)
        transform = carla_sdk.Transform(carla_sdk.Location(*target_start), carla_sdk.Rotation(yaw=tf.rotation.yaw))
        before_names = set(air.simListSceneObjects())
        actor = world.try_spawn_actor(world.get_blueprint_library().find('vehicle.tesla.model3'), transform)
        if actor is None:
            raise ValueError('TARGET_SPAWN_REJECTED')
        actor.set_simulate_physics(False)
        time.sleep(.1)
        target_name = identify_owned_tesla(sorted(set(air.simListSceneObjects())-before_names))
        actor_geometry = ActorGeometryCache(world.get_actors(), required_actor_id=actor.id)
        result['actor_geometry_cache'] = {'types_by_id':actor_geometry.types,
            'local_shapes_read_once_before_placement':True,
            'world_transforms_and_actor_inventory_refreshed_each_packet':True,
            'scenario_has_no_shape_scaling_or_blueprint_changes':True}
        air.simPause(True)
        try:
            # Initialization only; the fictitious teleport path is not flight.
            air.simSetVehiclePose(airsim_sdk.Pose(airsim_sdk.Vector3r(*provisional.carla_to_vehicle(placement)),
                airsim_sdk.to_quaternion(0.,0.,yaw)), True, vehicle_name='JevDrone')
        finally:
            air.simPause(False)
        result['placement_readback'] = []
        deadline = time.perf_counter()+1.
        while True:
            try:
                second = origin_sample(air)
                result['placement_readback'].append({'accepted':True, 'sample':second})
                break
            except ValueError as exc:
                result['placement_readback'].append({'accepted':False, 'error':str(exc)})
                if str(exc) != 'Object/vehicle/state poses disagree beyond origin translation' or time.perf_counter() >= deadline:
                    raise
                time.sleep(.03)
        # The unarmed placement is not flight. Freeze this owned fixture during
        # camera/prearm preparation instead of letting it free-fall while idle.
        air.simPause(True)
        preparation_paused = True
        if air.simIsPause() is not True:
            raise ValueError('PREPARATION_PAUSE_NOT_CONFIRMED')
        bridge = AirSimOriginBridge.calibrate(alignment,
            [first['global_ned_m'],second['global_ned_m']],
            [first['vehicle_local_ned_m'],second['vehicle_local_ned_m']])
        if math.dist(bridge.vehicle_to_carla(second['vehicle_local_ned_m']),placement)>.1:
            raise ValueError('SCENARIO_PLACEMENT_MISMATCH')
        result['origin_translation'] = list(bridge.local_minus_global_m)
        result['scenario'] = {'target_start_m':target_start,'initial_uav_m':placement,
            'direction_xy':direction,'target_speed_mps':.15,'target_motion':'SCRIPTED_NO_PHYSICS',
            'placement_is_not_flight':True, 'camera_pitch_deg':-math.degrees(math.atan2(16.,12.))}
        air.simSetCameraPose('front_center', airsim_sdk.Pose(airsim_sdk.Vector3r(.75,0.,-.2),
            airsim_sdk.to_quaternion(-math.atan2(16.,12.),0.,0.)), vehicle_name='JevDrone')
        air.simAddDetectionFilterMeshName('front_center', airsim_sdk.ImageType.Scene,target_name,vehicle_name='JevDrone')
        air.simSetDetectionFilterRadius('front_center', airsim_sdk.ImageType.Scene,6000,vehicle_name='JevDrone')
        image_response(air)  # Warm rendering before the measured run.
        adapter = LiveBeliefAdapter(bridge)
        # Fix the existing bounded world commands to the initial straight
        # route heading, ONCE. This is not a new feedback controller.
        pipeline = JevTrackPipeline(MockJevClient(),rollout_model=GeometricCenterFallback(model),
                                    primitives=route_primitives(math.degrees(yaw)))
        result['candidate_vocabulary'] = [asdict(p) for p in pipeline.primitives]
        initial_collision = air.simGetCollisionInfo(vehicle_name='JevDrone')
        collision_baseline = int(initial_collision.time_stamp)
        result['initial_collision'] = {'has_collided':bool(initial_collision.has_collided),
                                       'timestamp':collision_baseline}

        def dynamic_distance(snapshot, point):
            return actor_geometry.distance(snapshot, point, world.get_actors())

        result['phase']='prearm'
        prearm_start=time.perf_counter()
        prearm_state=air.getMultirotorState(vehicle_name='JevDrone')
        pos=bridge.vehicle_to_carla(list(prearm_state.kinematics_estimated.position))
        dynamic, boxes=dynamic_distance(world.get_snapshot(),pos)
        margin=min(dynamic,geometry.distance(pos))-2.
        contact=air.simGetCollisionInfo(vehicle_name='JevDrone')
        result['prearm']={'point_margin_m':margin,'position_m':pos,'dynamic_boxes':boxes,
            'has_collided':bool(contact.has_collided),'event_ns':int(contact.time_stamp),
            'wall_start_s':prearm_start,'wall_end_s':time.perf_counter()}
        if margin<3.5 or contact.has_collided or contact.time_stamp>collision_baseline or time.perf_counter()-prearm_start>.15:
            raise ValueError('PREARM_MEASURED_INPUT_REJECTED')
        air.enableApiControl(True,vehicle_name='JevDrone')
        api_enabled=True
        if time.perf_counter()-prearm_start>=.15:
            raise ValueError('PREARM_EXPIRED')
        energy_start = time.perf_counter()
        result['armed_resource_start_s'] = energy_start
        if air.armDisarm(True,vehicle_name='JevDrone') is not True:
            raise ValueError('ARM_NOT_ACKNOWLEDGED')
        armed=True

        def flight_sample(start,end,state,collision):
            return FlightTelemetry(start,end,int(state.timestamp),int(state.landed_state),
                tuple(state.kinematics_estimated.linear_velocity),tuple(state.kinematics_estimated.angular_velocity),
                bool(collision.has_collided),int(collision.time_stamp))

        def read_telemetry():
            start=time.perf_counter()
            state=air.getMultirotorState(vehicle_name='JevDrone')
            collision=air.simGetCollisionInfo(vehicle_name='JevDrone')
            return flight_sample(start,time.perf_counter(),state,collision)

        def read_startup_inputs():
            start=time.perf_counter()
            state=air.getMultirotorState(vehicle_name='JevDrone')
            position=bridge.vehicle_to_carla(list(state.kinematics_estimated.position))
            dynamic, boxes=dynamic_distance(world.get_snapshot(),position)
            margin=min(dynamic,geometry.distance(position))-2.
            collision=air.simGetCollisionInfo(vehicle_name='JevDrone')
            end=time.perf_counter()
            result['neutral_initialization']['geometry_trace'].append({
                'wall_start_s':start,'wall_end_s':end,'position_m':position,
                'remaining_point_margin_m':margin,'dynamic_boxes':boxes})
            return flight_sample(start,end,state,collision), margin

        def capture():
            start=time.perf_counter()
            before=world.get_snapshot()
            elapsed=before.timestamp.elapsed_seconds-source_start
            if not 0<=elapsed<=15.:
                raise ValueError('SCENARIO_SOURCE_CLOCK_INVALID')
            target_pose=carla_sdk.Transform(carla_sdk.Location(
                target_start[0]+direction[0]*.15*elapsed,target_start[1]+direction[1]*.15*elapsed,target_start[2]),
                carla_sdk.Rotation(yaw=math.degrees(yaw)))
            actor.set_transform(target_pose)
            state_before=air.getMultirotorState(vehicle_name='JevDrone')
            image=image_response(air)
            found=[d for d in air.simGetDetections('front_center',airsim_sdk.ImageType.Scene,vehicle_name='JevDrone')
                   if d.name==target_name]
            if len(found)>1:
                raise ValueError('AMBIGUOUS_TARGET_DETECTIONS')
            measurement=read_visible_oracle_position(bool(found),lambda:triple(actor.get_location()))
            bbox=center=None
            if found:
                bb=found[0].box2D
                bbox=[float(bb.min.x_val),float(bb.min.y_val),float(bb.max.x_val),float(bb.max.y_val)]
                x1,y1,x2,y2=bbox
                if not 0<=x1<x2<=image.width or not 0<=y1<y2<=image.height:
                    raise ValueError('INVALID_TARGET_BBOX')
                center=min(1.,math.hypot((x1+x2)/image.width-1.,(y1+y2)/image.height-1.)/math.sqrt(2.))
            state=air.getMultirotorState(vehicle_name='JevDrone')
            collision=air.simGetCollisionInfo(vehicle_name='JevDrone')
            after=world.get_snapshot()
            position=bridge.vehicle_to_carla(list(state.kinematics_estimated.position))
            dynamic,dynamic_boxes=dynamic_distance(after,position)
            point_distance=min(dynamic,geometry.distance(position))
            margin=max(0.,point_distance-2.)
            end=time.perf_counter()
            window=CaptureWindow(start,end,state_before.timestamp*1e-9,image.time_stamp*1e-9,state.timestamp*1e-9,
                before.timestamp.elapsed_seconds,after.timestamp.elapsed_seconds,before.frame,after.frame)
            tracking=adapter.update(window,now_s=end,vehicle_local_ned=list(state.kinematics_estimated.position),
                velocity_local_ned=list(state.kinematics_estimated.linear_velocity),
                yaw_deg=math.degrees(airsim_sdk.to_eularian_angles(state.kinematics_estimated.orientation)[2]),
                measurement_world=measurement,target_actor_id=actor.id if measurement is not None else None,
                image_center_error=center,minimum_clearance_m=margin)
            # Preserve the pre-existing stricter scene threshold in addition to
            # the controller's generic minimum. This is not certified body space.
            tracking=replace(tracking,constraints=replace(tracking.constraints,minimum_obstacle_margin_m=3.5))
            if found:
                # Measured CURRENT bbox only. This level-attitude projection
                # delta is a separately labeled transparent baseline change,
                # not a camera/dynamics calibration or residual-model update.
                tracking=replace(tracking,environment={**tracking.environment,
                    'camera_center_geometry':{'observed_xy':[(x1+x2)/image.width-1.,(y1+y2)/image.height-1.],
                        'pitch_deg':result['scenario']['camera_pitch_deg'],
                        'horizontal_fov_deg':90., 'image_size':[image.width,image.height],
                        'offset_body_forward_right_up_m':[.75,0.,.2]}})
            new_collision=int(collision.time_stamp)>collision_baseline
            record={'wall_s':end,'window':asdict(window),'visible':bool(found),'uav_world_m':list(position),
                'target_measurement_world_m':measurement,'center_error':center,'bbox_xyxy':bbox,
                'image_size':[image.width,image.height],'image_timestamp_ns':int(image.time_stamp),
                'collision_new':new_collision,'collision_timestamp_ns':int(collision.time_stamp),
                'registered_point_distance_m':point_distance,'remaining_point_margin_m':margin,
                'dynamic_boxes_audit_only':dynamic_boxes,'state':tracking.to_dict()}
            # Small 320x240 PNGs buffered until stopped; disk I/O cannot consume
            # the authority lease between the captured packet and dispatch.
            images.append(bytes(image.image_data_uint8))
            return window,tracking,MeasuredSafety(end,True,None,margin,bool(collision.has_collided) or new_collision), \
                flight_sample(start,end,state,collision),record

        def movement_port():
            # Client and event loop are created on the actuator's sole worker.
            return owned_movement_port(airsim_sdk,ports)

        result['phase']='neutral_initialization'
        result['neutral_initialization']={'geometry_trace':[]}
        air.simPause(False)
        if air.simIsPause() is not False:
            raise ValueError('PREPARATION_RESUME_NOT_CONFIRMED')
        preparation_paused=False
        initialize_neutral(read_startup_inputs,movement_port,result['neutral_initialization'],
                           collision_baseline_ns=collision_baseline)
        source_start=world.get_snapshot().timestamp.elapsed_seconds
        result['phase']='bounded_follow'
        run_control_session(capture,read_telemetry,movement_port,pipeline,alignment,result,
                            energy_start_s=energy_start)
        result['status']='FOLLOW_SMOKE_COMPLETED'
    except Exception as exc:
        result.update(status='FOLLOW_ABORTED',error_type=type(exc).__name__,error=str(exc))
    finally:
        # Only this fresh owner/actor. The external Windows job is the final
        # watchdog; an unresolved transaction never becomes a stop PASS here.
        cleanup=[]
        if armed: cleanup.append(('disarm',lambda:air.armDisarm(False,vehicle_name='JevDrone')))
        if api_enabled: cleanup.append(('release_api',lambda:air.enableApiControl(False,vehicle_name='JevDrone')))
        if preparation_paused: cleanup.append(('resume_preparation',lambda:air.simPause(False)))
        if actor is not None:
            def destroy():
                result['target_destroyed']=actor.destroy() is True
                if not result['target_destroyed']: raise RuntimeError('TARGET_CLEANUP_UNCONFIRMED')
            cleanup.append(('destroy_target',destroy))
        for name,action in cleanup:
            try:
                returned=action()
                if name=='disarm' and returned is not True:
                    raise RuntimeError('DISARM_NOT_ACKNOWLEDGED')
            except Exception as exc:
                result['cleanup_errors'].append({'step':name,'error_type':type(exc).__name__})
        if images:
            directory=output/'frames'
            directory.mkdir(exist_ok=False)
            for index,data in enumerate(images):
                with (directory/f'{index:05d}.png').open('xb') as handle: handle.write(data)
        result['recorded_png_count']=len(images)
        if result['status']=='FOLLOW_SMOKE_COMPLETED':
            try: require_follow_receipt(result)
            except ValueError as exc: result.update(status='FOLLOW_NOT_ACCEPTED',acceptance_error=str(exc))
        save(output/'current_follow.json',result)
