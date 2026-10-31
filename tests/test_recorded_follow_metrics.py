"""Hand-derived telemetry fixtures for retrospective follow performance."""
from copy import deepcopy
import importlib
import importlib.util
import math

import pytest


def evaluate(data):
    name = 'jev_uav_track.recorded_follow_metrics'
    assert importlib.util.find_spec(name), 'Raw follow performance evaluator missing'
    return importlib.import_module(name).summarize_follow(data)


def fixture():
    frames = []
    for i, visible in enumerate([True, False, False, True, True]):
        end = 1. + .1 * i
        frames.append({'index': i, 'window': {'wall_start_s': end - .02, 'wall_end_s': end},
            'target_visible': visible, 'bbox_xyxy': [40., 40., 60., 60.] if visible else None,
            'image_center_error': 0. if visible else None,
            'target_measurement_world': [20. + .1 * i, 0., 0.] if visible else None,
            'vehicle_position_world_m': [.1 * i, 0., 0.], 'observation_accepted': True,
            'safety': {'clearance_m': 5.},
            'collision': {'new_since_arm': False, 'timestamp': 1700000000000000001}})
    prefixes = [{'id': 'p0', 'frame_index': 0, 'window': deepcopy(frames[0]['window']),
        'source': 'REAL_JEV', 'source_jev_id': 'j0', 'acknowledged': True,
        'sent_wall_s': 1.01, 'duration_s': .05, 'completed_wall_s': 1.06,
        'velocity_ned_mps': [.2, 0., 0.], 'yaw_rate_dps': 0.}]
    return {'kind': 'CONTINUOUS_NATIVE_HD_D5_REAL_JEV', 'phase': 'completed',
        'frames': frames, 'prefixes': prefixes, 'native_size': [100, 100],
        'decisions': [{'id': 'j0', 'status': 'RESPONSE', 'request_wall_s': .1,
            'received_wall_s': .9, 'latency_ms': 800., 'confidence_accepted': True}],
        'requested_duration_s': .4, 'real_jev_requests': 1,
        'model_sha256': 'a'*64, 'scenario_seed': 17, 'scenario': 'straight',
        'controller': 'old-controller', 'tracking_acceptance': 'NOT_AUDITED',
        'no_training_admission': True, 'capture_rejections': [], 'cleanup_errors': []}


def test_metrics_use_actual_positions_and_do_not_claim_gain_or_event_recovery():
    r = evaluate(fixture())
    assert r['frames'] == 5 and r['visible_frames'] == 3
    assert r['visible_fraction_sampled'] == .6
    assert r['uav_horizontal_path_m'] == pytest.approx(.4)
    assert r['visible_range_m']['min'] == pytest.approx(20.)
    assert r['visible_range_error_from_20m_rmse'] == pytest.approx(0.)
    assert r['longest_observed_loss_to_first_visible_s'] == pytest.approx(.2)
    assert r['longest_bracketed_loss_interval_s'] == pytest.approx(.3)
    assert r['new_collision_events'] == 0
    assert r['real_jev_prefixes'] == 1
    assert r['command_requested_union_s'] == pytest.approx(.05)
    assert r['jev_latency_ms']['median'] == pytest.approx(800.)
    assert r['fixed_window_reacquisition'] == 'NOT_EVALUATED_NO_INDEPENDENT_EVENT_REFERENCE'
    assert r['identity_crossing'] == 'NOT_AUDITED'
    assert r['training_admission'] == 'NOT_AUDITED'
    assert r['learned_or_jev_gain_established'] is False


def test_partial_run_is_not_scored_as_complete_success():
    d = fixture(); d['phase'] = 'failed'; d['requested_duration_s'] = 1.
    r = evaluate(d)
    assert r['observation_coverage_of_planned_duration'] == pytest.approx(.4)
    assert r['run_completed'] is False
    assert r['visible_fraction_sampled'] == .6
    assert r['fair_comparison_eligible'] is False


@pytest.mark.parametrize('mutation', ['clock_backwards', 'nan_position', 'bool_visible',
    'bbox_mismatch', 'hidden_truth', 'prefix_unknown_frame', 'prefix_wrong_window',
    'unlinked_jev', 'collision_unknown'])
def test_malformed_telemetry_cannot_become_good_tracking(mutation):
    d = fixture()
    if mutation == 'clock_backwards': d['frames'][2]['window']['wall_end_s'] = .5
    if mutation == 'nan_position': d['frames'][0]['vehicle_position_world_m'][0] = math.nan
    if mutation == 'bool_visible': d['frames'][0]['target_visible'] = 1
    if mutation == 'bbox_mismatch': d['frames'][0]['image_center_error'] = .9
    if mutation == 'hidden_truth': d['frames'][1]['target_measurement_world'] = [1., 2., 3.]
    if mutation == 'prefix_unknown_frame': d['prefixes'][0]['frame_index'] = 999
    if mutation == 'prefix_wrong_window': d['prefixes'][0]['window']['wall_start_s'] = .2
    if mutation == 'unlinked_jev': d['prefixes'][0]['source_jev_id'] = 'missing'
    if mutation == 'collision_unknown': del d['frames'][0]['collision']['new_since_arm']
    with pytest.raises(ValueError): evaluate(d)


def test_request_union_does_not_double_count_overlapping_acknowledgements():
    d = fixture(); second = deepcopy(d['prefixes'][0]); second.update(id='p1', sent_wall_s=1.03, completed_wall_s=1.08)
    d['prefixes'].append(second)
    assert evaluate(d)['command_requested_union_s'] == pytest.approx(.07)


def test_source_event_timestamp_deduplicates_collision_reads():
    d = fixture()
    for row in d['frames'][2:]: row['collision']['new_since_arm'] = True
    assert evaluate(d)['new_collision_events'] == 1


def test_tail_loss_is_censored_not_reported_as_reacquired():
    d = fixture()
    for row in d['frames'][3:]:
        row.update(target_visible=False, bbox_xyxy=None, image_center_error=None, target_measurement_world=None)
    r = evaluate(d)
    assert r['loss_events'] == 1 and r['loss_events_censored'] == 1
    assert r['loss_events_returned_to_first_visible'] == 0
    assert r['longest_observed_loss_to_first_visible_s'] is None
    assert r['longest_censored_loss_lower_bound_s'] == pytest.approx(.3)


def test_sample_gaps_are_reported_not_assumed_continuous():
    d = fixture()
    d['frames'][-1]['window'] = {'wall_start_s': 1.88, 'wall_end_s': 1.9}
    r = evaluate(d)
    assert r['observation_gaps_over_250ms'] == 1


def test_signed_pixel_offsets_distinguish_aim_bias_from_target_loss():
    d = fixture()
    for frame in d['frames']:
        if frame['target_visible']:
            frame['bbox_xyxy'] = [40., 65., 60., 85.]
            frame['image_center_error'] = .5 / math.sqrt(2.)
    r = evaluate(d)
    assert r['visible_horizontal_center_offset_px']['median'] == 0.
    assert r['visible_vertical_center_offset_px']['median'] == 25.
