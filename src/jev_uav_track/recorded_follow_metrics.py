"""Retrospective telemetry metrics, not a flight controller or PASS admission.

Reports observed sample performance only. The archived video controller is not
the current D6 controller; no causal method comparison is inferred from it.
"""
from __future__ import annotations

import math
import statistics


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Invalid numeric telemetry')
    return value


def vector(value, size=3):
    if not isinstance(value, (tuple, list)) or len(value) != size:
        raise ValueError('Invalid telemetry vector')
    return [number(x) for x in value]


def distribution(values):
    if not values:
        return None
    ordered = sorted(number(x) for x in values)
    # Nearest-rank p95, explicitly not an interpolated quantile.
    return {'min': ordered[0], 'median': statistics.median(ordered),
            'p95_nearest_rank': ordered[math.ceil(.95 * len(ordered)) - 1],
            'max': ordered[-1]}


def summarize_follow(data):
    try:
        return _summarize(data)
    except (KeyError, TypeError, IndexError, OverflowError, AttributeError) as exc:
        raise ValueError('Incomplete or malformed follow telemetry') from exc


def _summarize(data):
    if data['kind'] != 'CONTINUOUS_NATIVE_HD_D5_REAL_JEV':
        raise ValueError('Unsupported tracking recorder schema')
    frames = data['frames']
    if type(frames) is not list or len(frames) < 2:
        raise ValueError('Insufficient observations')
    width, height = vector(data['native_size'], 2)
    planned = number(data['requested_duration_s'])
    if min(width, height, planned) <= 0:
        raise ValueError('Invalid recording dimensions or duration')
    by_index, times, positions, ranges, centers, clearances, captures = {}, [], [], [], [], [], []
    horizontal_offsets, vertical_offsets = [], []
    collisions, visible, rejected = set(), [], 0
    for frame in frames:
        index = frame['index']
        if type(index) is not int or index < 0 or index in by_index:
            raise ValueError('Invalid frame identity')
        by_index[index] = frame
        start, end = [number(frame['window'][k]) for k in ('wall_start_s', 'wall_end_s')]
        if not 0 <= start <= end or (times and end <= times[-1]):
            raise ValueError('Nonmonotonic observation clock')
        times.append(end); captures.append(1000. * (end - start))
        positions.append(vector(frame['vehicle_position_world_m']))
        is_visible, accepted = frame['target_visible'], frame['observation_accepted']
        if type(is_visible) is not bool or type(accepted) is not bool:
            raise ValueError('Missing observation validity')
        visible.append(is_visible); rejected += not accepted
        if is_visible:
            target = vector(frame['target_measurement_world'])
            x1, y1, x2, y2 = vector(frame['bbox_xyxy'], 4)
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                raise ValueError('Invalid detection box')
            center = min(1., math.hypot((x1 + x2) / width - 1., (y1 + y2) / height - 1.) / math.sqrt(2.))
            if abs(number(frame['image_center_error']) - center) > 1e-6:
                raise ValueError('Center metric disagrees with raw box')
            centers.append(center); ranges.append(math.dist(positions[-1], target))
            horizontal_offsets.append((x1 + x2 - width) / 2.)
            vertical_offsets.append((y1 + y2 - height) / 2.)
        elif frame['target_measurement_world'] is not None:
            raise ValueError('Hidden target truth present in observation input')
        clearance = number(frame['safety']['clearance_m'])
        if clearance < 0:
            raise ValueError('Invalid recorded clearance')
        clearances.append(clearance)
        event = frame['collision']
        if type(event['new_since_arm']) is not bool or type(event['timestamp']) is not int or event['timestamp'] < 0:
            raise ValueError('Unknown collision cannot mean zero')
        if event['new_since_arm']:
            collisions.add(event['timestamp'])

    losses, pending = [], None
    for i, seen in enumerate(visible):
        if not seen and pending is None:
            pending = {'first_missing_s': times[i], 'previous_visible_s': times[i-1] if i else None}
        if seen and pending is not None:
            losses.append(dict(pending, first_return_s=times[i], censored=False)); pending = None
    if pending is not None:
        losses.append(dict(pending, first_return_s=None, censored=True))
    observed_loss = [x['first_return_s'] - x['first_missing_s'] for x in losses if not x['censored']]
    censored_loss = [times[-1] - x['first_missing_s'] for x in losses if x['censored']]
    brackets = [x['first_return_s'] - x['previous_visible_s'] for x in losses
                if not x['censored'] and x['previous_visible_s'] is not None]

    decisions = data['decisions']
    advice = {x['id']: x for x in decisions}
    if len(advice) != len(decisions):
        raise ValueError('Duplicate advice identity')
    latencies = []
    for d in decisions:
        if d['status'] == 'RESPONSE':
            latency = 1000. * (number(d['received_wall_s']) - number(d['request_wall_s']))
            if latency < 0 or abs(latency - number(d['latency_ms'])) > .01:
                raise ValueError('Unverifiable advice latency')
            latencies.append(latency)
    commands, intervals, speeds, ages, durations, used_jev = [], [], [], [], [], 0
    prefix_ids = set()
    for prefix in data['prefixes']:
        if prefix['id'] in prefix_ids or type(prefix['acknowledged']) is not bool:
            raise ValueError('Invalid command identity or acknowledgement')
        prefix_ids.add(prefix['id'])
        frame = by_index[prefix['frame_index']]
        if prefix['window'] != frame['window']:
            raise ValueError('Command not linked to its actual observation')
        if not prefix['acknowledged']:
            continue
        if not frame['target_visible'] or not frame['observation_accepted']:
            raise ValueError('Following command used an invalid target observation')
        sent, duration, completed = [number(prefix[k]) for k in ('sent_wall_s', 'duration_s', 'completed_wall_s')]
        if duration <= 0 or sent < frame['window']['wall_end_s'] or completed < sent:
            raise ValueError('Invalid command timing')
        source = prefix.get('source_jev_id')
        if source is not None:
            decision = advice[source]
            if (decision['status'] != 'RESPONSE' or decision['confidence_accepted'] is not True
                    or number(decision['received_wall_s']) > sent):
                raise ValueError('Jev attribution does not match an available reply')
            used_jev += 1
        commands.append(prefix)
        intervals.append((sent, sent + duration))
        speeds.append(math.hypot(*vector(prefix['velocity_ned_mps'])))
        ages.append(1000. * (sent - frame['window']['wall_start_s']))
        durations.append(duration)
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    span = times[-1] - times[0]
    gaps = [b-a for a, b in zip(times, times[1:])]
    result = {
        'scenario': data.get('scenario', 'straight'), 'seed': data.get('scenario_seed'),
        'model_sha256': data['model_sha256'], 'controller': data['controller'],
        'evidence_class': 'RETROSPECTIVE_RECORDED_LIVE_VISIBLE_ORACLE',
        'run_completed': data['phase'] == 'completed' and span >= .95 * planned and not data['cleanup_errors'],
        'planned_duration_s': planned, 'camera_wall_span_s': span,
        'observation_coverage_of_planned_duration': min(1., span / planned),
        'frames': len(frames), 'visible_frames': sum(visible), 'visible_fraction_sampled': sum(visible) / len(frames),
        'rejected_observations': rejected, 'observation_gaps_over_250ms': sum(x > .25 for x in gaps),
        'maximum_observation_gap_s': max(gaps),
        'visible_center_error': distribution(centers), 'visible_range_m': distribution(ranges),
        'visible_horizontal_center_offset_px': distribution(horizontal_offsets),
        'visible_vertical_center_offset_px': distribution(vertical_offsets),
        'visible_range_error_from_20m_rmse': math.sqrt(statistics.mean((x-20.)**2 for x in ranges)) if ranges else None,
        'uav_net_displacement_m': math.dist(positions[0], positions[-1]),
        'uav_horizontal_path_m': sum(math.dist(a[:2], b[:2]) for a, b in zip(positions, positions[1:])),
        'loss_events': len(losses), 'loss_events_censored': sum(x['censored'] for x in losses),
        'loss_events_returned_to_first_visible': sum(not x['censored'] for x in losses),
        'longest_observed_loss_to_first_visible_s': max(observed_loss, default=None),
        'longest_censored_loss_lower_bound_s': max(censored_loss, default=None),
        'longest_bracketed_loss_interval_s': max(brackets, default=None),
        'new_collision_events': len(collisions), 'minimum_recorded_clearance_m': min(clearances),
        'acknowledged_prefixes': len(commands), 'recorded_prefixes': len(data['prefixes']),
        'real_jev_prefixes': used_jev, 'real_jev_prefix_fraction': used_jev / len(commands) if commands else None,
        'real_jev_requests_reported': data['real_jev_requests'], 'response_records': len(latencies),
        'jev_latency_ms': distribution(latencies), 'capture_latency_ms': distribution(captures),
        'dispatch_input_age_ms': distribution(ages), 'command_speed_mps': distribution(speeds),
        'command_requested_union_s': sum(end-start for start, end in merged),
        'prefixes_over_current_d6_50ms': sum(x > .05 + 1e-9 for x in durations),
        'prefixes_over_current_d6_03mps': sum(x > .3 + 1e-9 for x in speeds),
        'fixed_window_reacquisition': 'NOT_EVALUATED_NO_INDEPENDENT_EVENT_REFERENCE',
        'identity_crossing': 'NOT_AUDITED', 'training_admission': 'NOT_AUDITED',
        'fair_comparison_eligible': False, 'learned_or_jev_gain_established': False,
        'limitations': ['Sample-visible fraction is not duration-normalized success, especially after early termination.',
            'First-visible return is not the frozen 10s/0.5s stable reacquisition metric.',
            'Acknowledged requested intervals are not proof of continuous physical execution.',
            'Recorded geometry and collision events are not a new safety certification.',
            'Archived controller/model/limits differ from current D6 and cannot be silently deployed.'],
    }
    # Also catch overflow in derived distances or aggregated intervals.
    def check(obj):
        if isinstance(obj, dict):
            for v in obj.values(): check(v)
        elif isinstance(obj, list):
            for v in obj: check(v)
        elif type(obj) is float: number(obj)
    check(result)
    return result
