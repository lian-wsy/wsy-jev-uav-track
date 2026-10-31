"""Small current-model follow smoke helpers; no connections or flight authority.

Registered-box distances are measurements of the supplied scene inventory,
NOT a certified UAV collision envelope. This development smoke cannot certify
body clearance, obstacle coverage, RGB tracking, identity crossing or D1-D6.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time

from .recorded_follow_metrics import distribution, number, vector


MODEL_SHA = 'b67c0a70e50426dcebfff6a97adfaa3f286e1d26fe6a05e4f26e44a97f8ee189'
CALIBRATION_SHA = 'fa6b59e42df90ce15e39a1ea83652ff223a7e8c4517a15d1471c5f4d44d27d00'
PROTOCOL_SHA = 'dd8ca2293f586000ab8fa71f4d7d4725fd9696a50d881bafa5e567bcf6428130'


def validate_follow_endpoint(ports):
    from .isolated_runtime import RuntimePorts
    if ports != RuntimePorts(3200, 3201, 3202, 42461):
        raise ValueError('Current follow requires this task owned endpoint tuple')


def load_current_rollout(directory):
    """No fitting, threshold changes, validation-best promotion or old model."""
    from .residual_intervals import IntervalGatedRolloutModel
    from .training import LinearOutcomeModel, OUTPUT_NAMES
    from .rollout import KinematicRolloutModel, RolloutConfig
    import numpy as np
    directory = Path(directory)
    hashes = {}
    for name, expected in (('d5_residual.npz', MODEL_SHA), ('calibration.json', CALIBRATION_SHA),
                           ('protocol.json', PROTOCOL_SHA)):
        path = directory / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen current model fingerprint mismatch: ' + name)
        hashes[name] = expected
    config = json.loads((directory / 'protocol.json').read_text())['config']
    if config['input_mode'] != 'causal_visible' or config['target_mode'] != 'baseline_residual':
        raise ValueError('Current causal residual contract required')
    calibration = json.loads((directory / 'calibration.json').read_text())
    predictor = LinearOutcomeModel.load(directory / 'd5_residual.npz')
    rollout = IntervalGatedRolloutModel(predictor,
        np.array([calibration['fit_min'], calibration['fit_max']]),
        np.array([calibration['radius'][name] for name in OUTPUT_NAMES]),
        baseline=KinematicRolloutModel(RolloutConfig(horizon_s=1.)))
    # LearnedOutcomeRolloutModel verifies this nominal second against the
    # resolved training timestamps with its unchanged 1e-6 s tolerance.
    dataset = predictor.training_summary.get('train_dataset', predictor.training_summary.get('dataset', {}))
    return rollout, {'model_sha256': MODEL_SHA, 'input_mode': config['input_mode'],
        'artifact_sha256': hashes, 'interval_gate': 'ORIGINAL_UNCHANGED',
        'resolved_training_horizon_s':dataset['resolved_rollout_horizon_s'],
        'execution_candidate_horizon_s':1., 'horizon_agreement_tolerance_s':1e-6}


def route_primitives(heading_deg):
    """Rotate fixed world primitives once, without exceeding strict caps by ULP."""
    from dataclasses import replace
    from .mock_simulator import BOUNDED_PRIMITIVES
    angle = math.radians(number(heading_deg))
    c, s = math.cos(angle), math.sin(angle)
    result = []
    for primitive in BOUNDED_PRIMITIVES:
        x, y, z = primitive.command_mps
        velocity = (c*x-s*y, s*x+c*y, z)
        norm = math.hypot(*velocity)
        if norm:
            scale = min(1., math.nextafter(.3, 0.) / norm)
            velocity = tuple(v*scale for v in velocity)
        result.append(replace(primitive, command_mps=velocity))
    return tuple(result)


class GeometricCenterFallback:
    """Explicit engineering variant; NEVER alter the frozen residual baseline.

    Only when ALL candidates fully fall back and the target is currently
    visible, replace the unsigned center heuristic with a signed pinhole
    projection delta anchored at the measured bounding-box center. Commands,
    trajectories, interval gates, other outcomes and safety remain untouched.
    Level body attitude and constant velocity are approximations, not learned
    dynamics or calibrated pixel-error/visibility uncertainty. No hidden truth
    or future frame is accepted here. A partial learned result is not mixed
    with this separately versioned baseline intervention.
    """
    def __init__(self, baseline):
        self.baseline = baseline

    def generate(self, primitives, state):
        from dataclasses import replace
        actions = self.baseline.generate(primitives, state)
        if (state.target.visibility_probability != 1.
                or not actions
                or not all(a.metadata.get('interval_full_fallback') is True for a in actions)):
            return actions
        try:
            geometry = state.environment['camera_center_geometry']
            x, y = map(number, geometry['observed_xy'])
            pitch = math.radians(number(geometry['pitch_deg']))
            fov = number(geometry['horizontal_fov_deg'])
            width, height = map(number, geometry['image_size'])
            offset = vector(geometry['offset_body_forward_right_up_m'])
            if not (abs(x) <= 1. and abs(y) <= 1. and -math.pi/2 < pitch < math.pi/2
                    and 0 < fov < 180 and width > 0 and height > 0):
                raise ValueError('Invalid visible camera projection')
            horizontal = number(math.tan(math.radians(fov/2)))
            vertical = number(horizontal*height/width)
            if min(horizontal,vertical) <= 0:
                raise ValueError('Degenerate camera projection')
            relative = [a-b for a,b in zip(vector(state.target.position_m), vector(state.uav.position_m))]
            target_velocity = vector(state.target.velocity_mps)
            yaw = number(state.uav.yaw_deg)
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError('Missing or malformed visible camera projection') from exc

        def project(relative, heading):
            angle = math.radians(number(heading))
            c, s, cp, sp = math.cos(angle), math.sin(angle), math.cos(pitch), math.sin(pitch)
            # Body forward/right/up mount offset follows the predicted yaw.
            rx = relative[0] - (c*offset[0]-s*offset[1])
            ry = relative[1] - (s*offset[0]+c*offset[1])
            rz = relative[2] - offset[2]
            depth = number(cp*(c*rx+s*ry)+sp*rz)
            if depth <= 1e-9:
                return None
            return (number((-s*rx+c*ry)/(depth*horizontal)),
                    number((sp*(c*rx+s*ry)-cp*rz)/(depth*vertical)))

        current = project(relative, yaw)
        if current is None:
            raise ValueError('Visible target is behind nominal camera')
        corrected = []
        for action in actions:
            duration = number(action.duration_s)
            if duration <= 0:
                raise ValueError('Projection needs positive action duration')
            command = vector(action.command_mps)
            end_relative = [relative[i]+(target_velocity[i]-command[i])*duration for i in range(3)]
            future = project(end_relative, yaw+number(action.yaw_rate_dps)*duration)
            center = (min(1., number(math.hypot(x+future[0]-current[0],y+future[1]-current[1]))/math.sqrt(2.))
                      if future is not None else 1.)
            corrected.append(replace(action, outcome=replace(action.outcome,image_center_error=center),
                metadata={**action.metadata, 'center_model':'signed-level-camera-delta-v1',
                          'legacy_center_error':action.outcome.image_center_error,
                          'learned_center_correction':False}))
        return corrected


class RegisteredGeometry:
    """Exact point-to-OBB distance over supplied CARLA registered boxes.

Signed source extents are preserved; abs merely permutes +/- box vertices.
The caller must retain all other classes, explicitly document any excluded
Sky entries, and refresh actor boxes. No missing inventory means clear space.
"""
    def __init__(self, boxes):
        import numpy as np
        if not boxes:
            raise ValueError('Missing registered geometry')
        self.raw = []
        rotations = []
        for box in boxes:
            center = vector([box.location.x, box.location.y, box.location.z])
            extent = vector([box.extent.x, box.extent.y, box.extent.z])
            rotation = vector([box.rotation.pitch, box.rotation.yaw, box.rotation.roll])
            if not any(abs(x) > 0 for x in extent):
                raise ValueError('Degenerate registered geometry')
            self.raw.append({'center_m': center, 'signed_extent_m': extent,
                             'rotation_pitch_yaw_roll_deg': rotation})
            p, y, r = map(math.radians, rotation)
            cp, sp, cy, sy, cr, sr = math.cos(p), math.sin(p), math.cos(y), math.sin(y), math.cos(r), math.sin(r)
            rotations.append(((cp*cy, cy*sp*sr-sy*cr, -cy*sp*cr-sy*sr),
                              (cp*sy, sy*sp*sr+cy*cr, -sy*sp*cr+cy*sr), (sp, -cp*sr, cp*cr)))
        self.centers = np.array([b['center_m'] for b in self.raw])
        self.extents = np.abs([b['signed_extent_m'] for b in self.raw])
        self.rotations = np.array(rotations)

    def distance(self, point):
        import numpy as np
        local = np.einsum('nji,nj->ni', self.rotations, np.asarray(vector(point)) - self.centers)
        distances = np.linalg.norm(np.maximum(0., np.abs(local) - self.extents), axis=1)
        result = float(np.min(distances))
        if not math.isfinite(result):
            raise ValueError('Nonfinite registered geometry distance')
        return result


class ActorGeometryCache:
    """Cache immutable local shapes; every query uses fresh snapshot transforms.

Only for this fixed-shape, exclusive scenario: no actor scaling/blueprint
changes are performed. Actor addition/removal/type changes abort, never reuse
an old world-space obstacle pose or silently drop unknown actors. Construct
before scenario placement, outside any short observation authority window.
"""
    @staticmethod
    def inventory(actors):
        result = {}
        for actor in actors:
            if actor.type_id.startswith(('vehicle.', 'walker.', 'static.')):
                if type(actor.id) is not int or actor.id < 0 or actor.id in result:
                    raise ValueError('Invalid actor inventory')
                result[actor.id] = actor
        return result

    def __init__(self, actors, *, required_actor_id):
        inventory = self.inventory(actors)
        if required_actor_id not in inventory:
            raise ValueError('Target missing from actor geometry inventory')
        self.types = {identity: actor.type_id for identity, actor in inventory.items()}
        # bounding_box can be a separate native RPC per actor; never read it
        # again inside a 150 ms capture. get_world_vertices is local arithmetic.
        self.shapes = {identity: actor.bounding_box for identity, actor in inventory.items()}

    def distance(self, snapshot, point, current_actors):
        point = vector(point)
        current = {identity: actor.type_id for identity, actor in self.inventory(current_actors).items()}
        if current != self.types:
            raise ValueError('Actor geometry inventory changed; new scene initialization required')
        distances, records = [], []
        for identity, shape in self.shapes.items():
            actor = snapshot.find(identity)
            if actor is None:
                raise ValueError('Actor missing from current snapshot')
            vertices = [vector([v.x, v.y, v.z]) for v in shape.get_world_vertices(actor.get_transform())]
            if len(vertices) != 8:
                raise ValueError('Invalid current actor bounding vertices')
            lo = [min(p[i] for p in vertices) for i in range(3)]
            hi = [max(p[i] for p in vertices) for i in range(3)]
            distances.append(math.hypot(*(max(lo[i]-point[i], 0., point[i]-hi[i]) for i in range(3))))
            records.append({'id': identity, 'type': self.types[identity], 'vertices_m': vertices})
        return min(distances), records


def follow_metrics(frames):
    if not isinstance(frames, list) or len(frames) < 2:
        raise ValueError('Insufficient following observations')
    times, positions, centers, ranges = [], [], [], []
    target_path = 0.
    previous_target = None
    visible_count = collisions = 0
    for row in frames:
        timestamp = number(row['wall_s'])
        if times and timestamp <= times[-1]:
            raise ValueError('Nonmonotonic following samples')
        times.append(timestamp)
        positions.append(vector(row['uav_world_m']))
        if type(row['visible']) is not bool or type(row['collision_new']) is not bool:
            raise ValueError('Unknown visibility or collision')
        collisions += row['collision_new']
        if row['visible']:
            target = vector(row['target_measurement_world_m'])
            center = number(row['center_error'])
            if not 0 <= center <= 1:
                raise ValueError('Invalid image center error')
            ranges.append(math.dist(positions[-1], target))
            centers.append(center)
            visible_count += 1
            if previous_target is not None:
                target_path += math.dist(previous_target, target)
            previous_target = target
        else:
            if row['target_measurement_world_m'] is not None or row['center_error'] is not None:
                raise ValueError('Hidden truth in observation')
            previous_target = None
    path = sum(math.dist(a, b) for a, b in zip(positions, positions[1:]))
    return {'sample_count': len(frames), 'span_s': times[-1] - times[0],
            'visible_count': visible_count, 'visible_fraction': visible_count / len(frames),
            'desired_range_m': 20.,
            'range_rmse_m': math.sqrt(sum((x - 20.)**2 for x in ranges) / len(ranges)) if ranges else None,
            'center_error': distribution(centers), 'uav_path_m': path,
            'target_visible_path_m': target_path, 'new_collision_samples': collisions,
            'motion_observed': path >= .1 and target_path >= .5,
            'learned_gain': 'NOT_EVALUATED', 'stable_reacquisition': 'NOT_EVALUATED',
            'identity_crossing': 'NOT_AUDITED'}


def require_follow_receipt(receipt):
    """A completion flag alone is never evidence of successful following."""
    from .actuation import ActuationReceipt, FlightTelemetry, StopVerifier
    from .sim_adapter import AirSimWorldVelocityCommand
    def telemetry(raw):
        return FlightTelemetry(**{**raw,
            'linear_velocity_mps':tuple(raw['linear_velocity_mps']),
            'angular_velocity_rad_s':tuple(raw['angular_velocity_rad_s'])})
    try:
        metrics = follow_metrics(receipt['frames'])
        prefixes = receipt['prefixes']
        if (receipt['kind'] != 'CURRENT_CAUSAL_BOUNDED_FOLLOW'
                or receipt['status'] != 'FOLLOW_SMOKE_COMPLETED'
                or receipt['model']['model_sha256'] != MODEL_SHA
                or receipt['method'] != 'mock_jev' or receipt['real_jev_calls'] != 0
                or receipt['metrics'] != metrics or not metrics['motion_observed']
                or metrics['span_s'] < 8. or metrics['new_collision_samples'] != 0
                or not prefixes or receipt['stop_verdict'] != 'STOP_VERIFIED'
                or receipt['cleanup_errors'] or receipt['target_destroyed'] is not True):
            raise ValueError('Current follow has incomplete effect or stop evidence')
        for prefix in prefixes:
            command = prefix['command']
            if (prefix['status'] != 'PREFIX_AND_NEUTRAL_ACKNOWLEDGED'
                    or prefix['owner_termination_required'] is not False
                    or prefix['prefix_acknowledged'] is not True
                    or prefix['neutral_acknowledged'] is not True
                    or prefix['cancel_returned'] is not True
                    or math.hypot(*vector(command['velocity_mps'])) > .3
                    or abs(number(command['yaw_rate_dps'])) > 15.
                    or not 0 < number(command['duration_s']) <= .05):
                raise ValueError('Invalid bounded transport evidence')
        last = dict(prefixes[-1])
        baseline = telemetry(last.pop('baseline_telemetry'))
        command = last['command']
        last['command'] = AirSimWorldVelocityCommand(tuple(command['velocity_mps']),
                                                     command['yaw_rate_dps'],command['duration_s'])
        verifier = StopVerifier(ActuationReceipt(**last), baseline)
        verdict = None
        for entry in receipt['stop_trace']:
            verdict = verifier.observe(telemetry(entry['telemetry']),now_s=entry['now_s'])
            if verdict != entry['verdict']:
                raise ValueError('Stored stop verdict disagrees with raw telemetry')
        if verdict != 'TELEMETRY_SETTLED':
            raise ValueError('Stopping telemetry not independently verified')
    except (KeyError, TypeError, AttributeError, OverflowError, IndexError) as exc:
        raise ValueError('Malformed current-follow receipt') from exc
    return metrics


def run_control_session(capture, read_telemetry, transport_factory, pipeline, alignment, result,
                        *, duration_s=10., clock=time.perf_counter, sleep=time.sleep, energy_start_s=None):
    """Wire existing production components; native calls are injected at edges.

No new feedback law or relaxed limits: original bounded primitives, 200 ms
advice/observation expiry, 0.3 m/s and 50 ms prefixes. Neutral is sent after
EVERY prefix. This transactional loop does not claim sustained 20 Hz control.
"""
    from dataclasses import asdict
    from .actuation import BoundedActuator, StopVerifier, observe_until_stopped
    from .async_control import AsyncDecisionCoordinator, BoundedControlLoop
    from .energy import SimulatedEnergyBudget, SimulatedEnergyConfig
    if not 0 < number(duration_s) <= 12.:
        raise ValueError('Follow smoke duration must be in (0,12] seconds')
    result.update(frames=[], ticks=[], prefixes=[], selections=[], stop_trace=[],
                  stop_verdict='NOT_EVALUATED', requested_duration_s=duration_s)
    def select(request):
        selection = pipeline.selector.select(request.state, request.candidates)
        result['selections'].append({'request_id': request.request_id,
            'action_id': selection.action.id, 'selector_fallback': selection.used_fallback,
            'candidate_ids': [a.id for a in request.candidates],
            'candidate_diagnostics': [{'id':a.id, 'center_model':a.metadata.get('center_model'),
                'image_center_error':a.outcome.image_center_error,
                'legacy_center_error':a.metadata.get('legacy_center_error'),
                'utility':a.outcome.utility(request.state.constraints)} for a in request.candidates],
            'interval_full_fallback': [a.metadata.get('interval_full_fallback') for a in request.candidates]})
        return selection
    coordinator = AsyncDecisionCoordinator(select, clock=clock)
    start = clock()
    energy_start = start if energy_start_s is None else number(energy_start_s)
    if not 0 <= energy_start <= start:
        coordinator.close()
        raise ValueError('Energy start must include prior armed initialization')
    result['energy_start_s'] = energy_start
    energy = SimulatedEnergyBudget(SimulatedEnergyConfig(20., 90., 120.), start_s=energy_start)
    loop = BoundedControlLoop(pipeline, coordinator, alignment=alignment, clock=clock,
                             simulated_energy=energy, execution_domain='isolated_simulation')
    actuator = BoundedActuator(transport_factory, execution_domain='isolated_simulation', clock=clock)
    baseline = last_baseline = last_receipt = None

    def poll():
        nonlocal last_receipt, last_baseline
        receipt = actuator.poll()
        if receipt is not None:
            result['prefixes'].append({**asdict(receipt), 'baseline_telemetry':asdict(baseline)})
            last_receipt, last_baseline = receipt, baseline
            if receipt.status != 'PREFIX_AND_NEUTRAL_ACKNOWLEDGED':
                raise RuntimeError('TRANSPORT_NOT_ACKNOWLEDGED')
        if actuator.requires_owner_termination:
            raise RuntimeError('OWNED_TRANSPORT_TERMINATION_REQUIRED')

    try:
        while clock() - start < duration_s:
            began = clock()
            poll()
            window, state, safety, telemetry, frame = capture()
            result['frames'].append(frame)
            if telemetry.landed_state != 1:
                raise ValueError('GROUNDED_NOT_READY_AFTER_SCENARIO_PLACEMENT')
            if telemetry.has_collided or frame['collision_new']:
                raise ValueError('CURRENT_COLLISION')
            tick = loop.tick(state, window, safety)
            row = asdict(tick)
            result['ticks'].append(row)
            poll()
            if tick.command is not None and not actuator.in_flight:
                baseline = telemetry
                row['transport_submission'] = actuator.submit(tick, telemetry)
                if row['transport_submission'] != 'SUBMITTED':
                    raise ValueError('CURRENT_DISPATCH_' + row['transport_submission'])
            # Initial/no advice or between-transaction idle is not a stop claim.
            # A safety rejection during an RPC revokes that one pending lease.
            # Ordinary advice expiry emits no new prefix. Let the already
            # bounded transaction finish its cancel/neutral tail; its original
            # deadline/watchdog remain in force while fresh advice is requested.
            elif tick.command is None and actuator.in_flight and tick.reason_code not in (
                    'TICK_TOO_SOON', 'NO_ADVICE', 'REQUEST_PENDING', 'ACTION_EXPIRED'):
                actuator.halt()
                raise ValueError('CURRENT_INPUT_REVOKED_DURING_RPC')
            sleep(max(.001, .05 - (clock() - began)))
        while actuator.in_flight:
            poll()
            sleep(.001)
        if last_receipt is not None:
            verifier = StopVerifier(last_receipt, last_baseline)
            verdict = observe_until_stopped(verifier, read_telemetry, result['stop_trace'], clock=clock, sleep=sleep)
            result['stop_verdict'] = 'STOP_VERIFIED' if verdict == 'TELEMETRY_SETTLED' else verdict
        if len(result['frames']) >= 2:
            result['metrics'] = follow_metrics(result['frames'])
    finally:
        result['selection_counts'] = dict(coordinator.counters)
        result['owner_termination_required'] = actuator.requires_owner_termination or actuator.in_flight
        actuator.close()
        coordinator.close()
