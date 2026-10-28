"""Replay the complete 16-frame visible-oracle development contract offline.

Checks internal consistency/reproducibility, not authenticity or tracking gains.
No simulator, HDF5, credentials, network, or action dispatch is used.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

from jev_uav_track.frames import AirSimOriginBridge, CarlaAirAlignment
from jev_uav_track.live_belief import LiveBeliefAdapter
from jev_uav_track.live_observation import CaptureWindow
from jev_uav_track.jev_client import MockJevClient, UtilityRankingClient
from jev_uav_track.pipeline import JevTrackPipeline


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def numerically_equal(actual, expected):
    """Allow cross-runtime float roundoff; IDs, booleans and strings stay exact."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if type(actual) is int or type(expected) is int:
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return (math.isfinite(actual) and math.isfinite(expected)
                and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12))
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(numerically_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(numerically_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def verify(evidence, *, strict_text=True):
    controller = evidence['controller']
    require(controller['status'] == 'API_SMOKE_COMPLETED'
            and controller['protected_unchanged'] is True and controller['cleanup_confirmed'] is True,
            'Owned lifecycle was not completed and verified')
    contract = evidence['api_probe']['contract']
    probe = contract['belief_contract']
    frames = probe['frames']
    require(probe['kind'] == 'LIVE_VISIBLE_ORACLE_SHADOW_CONTRACT'
            and probe['phase'] == 'completed' and probe['target_destroyed'] is True
            and probe['new_collision'] is False, 'Incomplete diagnostic lifecycle')
    require(len(frames) == 16 and probe['accepted_count'] == 16 and probe['visible_count'] == 12
            and probe['target_pose_queries'] == 12 and probe['dispatched_actions'] == 0
            and probe['real_jev_requests'] == 0, 'Incomplete sequence or forbidden query/dispatch count')
    pairs = contract['origin_bridge']['samples']
    mapping = contract['coordinate_mapping']
    require(mapping['mapping_pass'] is True, 'Global landmark mapping unverified')
    bridge = AirSimOriginBridge.calibrate(CarlaAirAlignment(tuple(mapping['offset_first_m'])),
        [p['global_ned_m'] for p in pairs], [p['vehicle_local_ned_m'] for p in pairs])
    adapter = LiveBeliefAdapter(bridge)
    pipelines = {'mock': JevTrackPipeline(MockJevClient()), 'utility': JevTrackPipeline(UtilityRankingClient())}
    count = 0
    text_differences = []
    for index, row in enumerate(frames):
        visible = not 4 <= index < 8
        require(row['index'] == index and row['accepted'] is True
                and row['target_visible'] is visible, 'Visible/missing/recovered sequence mismatch')
        require((row['target_measurement_world'] is not None) == visible,
                'Hidden measurement or absent visible measurement')
        state = adapter.update(CaptureWindow(**row['window']), now_s=row['decision_wall_s'],
            vehicle_local_ned=row['vehicle_local_ned_m'], velocity_local_ned=row['vehicle_velocity_local_ned_mps'],
            yaw_deg=row['uav_yaw_deg'], measurement_world=row['target_measurement_world'],
            target_actor_id=probe['spawn']['actor_id'] if visible else None,
            image_center_error=row['image_center_error'])
        actual_state, expected_state = state.to_dict(), dict(row['state'])
        actual_text, expected_text = actual_state.pop('history_summary'), expected_state.pop('history_summary')
        if actual_text != expected_text:
            text_differences.append({'frame': index, 'actual': actual_text, 'recorded': expected_text})
            require(not strict_text, 'History summary differs; use the original Python runtime for strict replay')
        require(numerically_equal(actual_state, expected_state), f'Belief/history mismatch at frame {index}')
        require(state.environment['belief_history']['length'] == min(index + 1, 8), 'History window mismatch')
        candidates = pipelines['utility'].generate_candidates(state)
        require(numerically_equal([a.to_dict() for a in candidates], row['candidates']), f'Rollout mismatch at frame {index}')
        count += len(candidates)
        for name, pipeline in pipelines.items():
            try:
                pipeline.selector.select(state, candidates)
            except RuntimeError as exc:
                require(str(exc) == 'No candidate action passed the hard safety filter', 'Unexpected selection failure')
            else:
                raise ValueError('Unknown live safety inputs permitted a selection')
            require(row['decisions'][name]['status'] == 'SAFETY_REJECTED'
                    and row['decisions'][name]['selected'] is None, 'Recorded selection mismatch')
    durations = [r['window']['wall_end_s'] - r['window']['wall_start_s'] for r in frames]
    return {'status': 'PASS' if not text_differences else 'NUMERIC_ONLY_TEXT_MISMATCH',
            'kind': 'OFFLINE_REPRODUCTION_OF_LIVE_SHADOW_INPUTS',
            'python_version': sys.version, 'strict_text': strict_text,
            'text_differences': text_differences,
            'float_abs_tolerance': 1e-12, 'float_relative_tolerance': 1e-12,
            'verified_frames': len(frames), 'visible_frames': 12, 'missing_frames': 4,
            'verified_candidate_predictions': count, 'verified_safety_rejections': 2 * len(frames),
            'mean_capture_ms': statistics.mean(durations) * 1000, 'max_capture_ms': max(durations) * 1000,
            'observed_capture_hz': 15 / (frames[-1]['window']['wall_start_s'] - frames[0]['window']['wall_start_s']),
            'not_tracking_evaluation': True, 'no_policy_actions_executed': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--allow-text-differences', action='store_true',
                        help='Diagnostic only; summary mismatches remain explicit and status is not PASS')
    args = parser.parse_args()
    raw = args.evidence.read_bytes()
    result = verify(json.loads(raw), strict_text=not args.allow_text_differences)
    result['evidence_sha256'] = hashlib.sha256(raw).hexdigest()
    result['verifier_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    payload = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        with args.output.open('x', encoding='utf-8') as handle:
            handle.write(payload)
    print(payload)
