"""Read-only record integrity inventory, never a PASS/training admission audit.

CHECKED means the listed file/link/time/command invariants were checked. It
does not establish API provenance, sensor truth, safety, pairing, or effect.
Concurrent writers may yield INCOMPLETE/INVALID; audit completed runs only.
"""
import hashlib
import json
import math
from pathlib import Path
import re

from .holdout import require_development_source
from .decision_dataset import _check_source
from .models import CandidateAction, CandidateOutcome, TargetBelief, UAVState, MissionConstraints, TrackingState
from .jev_client import build_choice_request


def _load(path):
    if path.is_symlink():
        raise ValueError('linked_file')
    raw = path.read_bytes()
    def invalid(_):
        raise ValueError('nonfinite_json')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def finite_float(text):
        return _finite(float(text))
    data = json.loads(raw, parse_constant=invalid, parse_float=finite_float,
                      parse_int=lambda text: _finite(int(text)), object_pairs_hook=unique)
    if not isinstance(data, dict):
        raise ValueError('object_required')
    return data, hashlib.sha256(raw).hexdigest()


def _finite(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('nonfinite_number')
    return value


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _same_number(actual, expected):
    return math.isclose(_finite(actual), expected, rel_tol=0, abs_tol=1e-6)


def _input_checks(data, protocol, protocol_sha, identifier, episodes):
    _require(data['schema'] == 'jev.decision_input.v1', 'schema')
    _require(data['decision_id'] == identifier, 'decision_id')
    _require(data['protocol_sha256'] == protocol_sha, 'protocol_fingerprint')
    _require(data['kind'] == protocol['kind'], 'kind_mismatch')
    _require(data['training_admission'] == 'NOT_AUDITED', 'unexpected_admission')
    episode = episodes[data['episode_id']]
    require_development_source(identifier, episode)
    require_development_source(identifier, data)
    _check_source(data['state'].get('environment', {}), episode)
    observation, decision = map(_finite, (data['observation_time_s'], data['decision_time_s']))
    history = [_finite(t) for t in data['history_times_s']]
    belief_time = _finite(data['state']['target']['timestamp_s'])
    _require(belief_time <= observation <= decision and history == sorted(history)
             and all(t <= observation for t in history), 'future_or_unordered_input')
    _require(isinstance(data['clock_domain'], str) and bool(data['clock_domain']), 'clock_domain')
    candidates = {a['id']: a for a in data['candidates']}
    _require(bool(candidates) and len(candidates) == len(data['candidates']), 'candidate_ids')
    for candidate in candidates.values():
        _require(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', candidate['id']))
                 and _finite(candidate['duration_s']) > 0, 'candidate_contract')
        _check_source(candidate.get('metadata', {}), episode)
        velocity = candidate['command_mps']
        _require(isinstance(velocity, list) and len(velocity) == 3, 'command_vector')
        for value in velocity:
            _finite(value)
        _finite(candidate['yaw_rate_dps'])
    safe, rejected = data['safe_candidate_ids'], data['rejected_candidate_ids']
    _require(len(safe) == len(set(safe)) and len(rejected) == len(set(rejected))
             and not set(safe).intersection(rejected) and set(safe + rejected) == set(candidates),
             'candidate_partition')
    _require(set(data['safety_sources']) == {'clearance', 'battery'}, 'safety_sources')
    allowed_sources = {'simulator_sensor', 'scene_geometry', 'simulator_model', 'unobserved'}
    if protocol['kind'] == 'SYNTHETIC':
        allowed_sources.add('synthetic_fixture')
    _require(set(data['safety_sources'].values()) <= allowed_sources, 'safety_sources')
    if 'unobserved' in data['safety_sources'].values():
        _require(not safe, 'unobserved_safety_admitted')
    if protocol['kind'] == 'LIVE_SIMULATION':
        _require('synthetic_fixture' not in data['safety_sources'].values(), 'synthetic_safety_in_live')
    request_state = data['request_template']['state']
    _require(request_state['candidate_outcomes'] == [candidates[a] for a in safe], 'request_candidates')
    _require(request_state['belief'] == data['state']['target']
             and request_state['uav'] == data['state']['uav']
             and request_state['constraints'] == data['state']['constraints'], 'request_state')
    _require(all(request_state[field] == data['state'][field] for field in
                 ('mission_goal', 'history_summary', 'obstacle_context')), 'request_context')
    state_data = data['state']
    state = TrackingState(**{**state_data, 'target': TargetBelief(**state_data['target']),
        'uav': UAVState(**state_data['uav']), 'constraints': MissionConstraints(**state_data['constraints'])})
    typed_actions = [CandidateAction(**{**candidates[a],
        'outcome': CandidateOutcome(**candidates[a]['outcome'])}) for a in safe]
    model = data['request_template']['model']
    _require(isinstance(model, str) and bool(model.strip()), 'request_model')
    # Compare JSON wire values: model vectors are tuples in memory, lists on disk.
    expected = json.loads(json.dumps(build_choice_request(state, typed_actions, model), allow_nan=False))
    _require(data['request_template'] == expected, 'request_contract')
    return candidates


def _linked(record, identifier, input_sha):
    _require(record['decision_id'] == identifier, 'decision_id')
    _require(record['input_sha256'] == input_sha, 'input_fingerprint')
    _require(record['training_admission'] == 'NOT_AUDITED', 'unexpected_admission')


def _advice_checks(advice, selector, safe):
    _require(advice['selector'] == selector, 'selector')
    _require(advice['label_kind'] == 'ADVICE_NOT_GROUND_TRUTH', 'advice_as_truth')
    _require(_finite(advice['latency_ms']) >= 0, 'latency')
    if advice['status'] == 'FAILED':
        _require(advice['error_code'] in {'timeout', 'budget', 'protocol', 'transport', 'missing_key'}, 'error_code')
        return
    _require(advice['status'] == 'RESPONSE' and advice['choice'] in safe, 'invalid_advice_choice')
    probabilities = advice['probabilities']
    source = advice['probability_source']
    if probabilities is None:
        _require(selector == 'real_jev' and source == 'NOT_PROVIDED', 'probability_provenance')
    else:
        _require(source == ('API_RESPONSE' if selector == 'real_jev' else 'LOCAL_SELECTOR'), 'probability_provenance')
        _require(isinstance(probabilities, dict) and set(probabilities) <= set(safe)
                 and all(0 <= _finite(v) <= 1 for v in probabilities.values())
                 and _same_number(sum(probabilities.values()), 1.), 'probabilities')


def _outcome_checks(outcome, data, candidates):
    _require(outcome['paired_counterfactual'] is False, 'unverified_pairing')
    if outcome['status'] == 'NOT_EXECUTED':
        _require(outcome['metrics'] is None, 'unexecuted_metrics')
        _require(outcome['reason'] in {'safety_rejection', 'expired', 'advisory_only', 'no_dispatch'}, 'reason')
        return
    _require(outcome['status'] == 'EXECUTION_REPORTED', 'outcome_status')
    _require(outcome['kind'] == data['kind'], 'outcome_kind')
    _require(outcome['execution_frame'] == 'WORLD_Z_UP', 'execution_frame')
    action_id = outcome['executed_action_id']
    _require(action_id in data['safe_candidate_ids'], 'unsafe_execution')
    action = candidates[action_id]
    trace = outcome['execution_trace']
    _require(isinstance(trace, list) and bool(trace), 'execution_trace')
    end, duration = data['decision_time_s'], 0.
    for command in trace:
        start, seconds = map(_finite, (command['timestamp_s'], command['duration_s']))
        _require(command['acknowledged'] is True and start >= end - 1e-6 and seconds > 0,
                 'unconfirmed_or_unordered_execution')
        _require(command['command_mps'] == action['command_mps']
                 and command['yaw_rate_dps'] == action['yaw_rate_dps'], 'command_mismatch')
        end, duration = start + seconds, duration + seconds
    elapsed = end - trace[0]['timestamp_s']
    _require(_finite(outcome['label_end_s']) >= end and duration <= action['duration_s'] + 1e-6,
             'label_or_execution_horizon')
    _require(_same_number(outcome['executed_duration_s'], duration)
             and _same_number(outcome['execution_elapsed_s'], elapsed), 'execution_duration')
    full = _same_number(duration, action['duration_s']) and _same_number(elapsed, duration)
    _require(outcome['full_candidate_duration_executed'] is full, 'full_horizon_claim')
    metrics = outcome['metrics']
    _require(isinstance(metrics, dict) and set(metrics) <= {
        'visibility_fraction', 'collision', 'minimum_clearance_m', 'range_error_m', 'reacquisition_time_s'},
        'metrics')
    for key, value in metrics.items():
        if value is None:
            continue
        if key == 'collision':
            _require(type(value) is bool, 'collision_type')
        else:
            _require(_finite(value) >= 0 and (key != 'visibility_fraction' or value <= 1), 'metric_range')


def audit_dataset(root):
    """Inventory bounded local records without modifying or admitting any file."""
    root = Path(root)
    counts = dict(decisions=0, recorded_episodes=0, not_executed=0, execution_reported=0,
                  live_execution_records=0, real_jev_response_records=0,
                  real_jev_failure_records=0, admitted_training_samples=0,
                  actual_live_jev_responses_verified=0)
    report = {'schema': 'jev.dataset_integrity_inventory.v1', 'integrity_status': 'INVALID',
              'training_admission': 'NOT_AUDITED', 'counts': counts, 'issues': [],
              'real_jev_attempts': 'NOT_VERIFIED_BY_DATASET',
              'observed_strata': 'NOT_VERIFIED_BY_DATASET',
              'limitations': ['No API transport authentication or budget reconciliation.',
                             'No sensor, safety, reset, clock synchronization or producer causality audit.',
                             'No source media, actual command execution or metric recomputation.',
                             'Hash consistency is not authenticity; no training sample is admitted.']}
    issues, incomplete, invalid, episode_ids = report['issues'], False, False, set()
    try:
        protocol, protocol_sha = _load(root / 'protocol.json')
        _require(protocol['kind'] in {'SYNTHETIC', 'LIVE_SIMULATION'}, 'kind')
        _require(type(protocol['max_decisions']) is int and 1 <= protocol['max_decisions'] <= 8, 'decision_limit')
        episodes = {e['id']: e for e in protocol['episodes']}
        _require(1 <= len(episodes) <= 6 and len(episodes) == len(protocol['episodes']), 'episodes')
        require_development_source(root, protocol)
        for field, length in [('code_sha', 40), ('model_sha256', 64), ('recorder_source_sha256', 64)]:
            _require(bool(re.fullmatch('[0-9a-f]{' + str(length) + '}', protocol[field])), 'provenance_sha')
        seeds = set()
        for episode in episodes.values():
            _require(episode['split'] in {'development_train', 'development_validation'}, 'episode_split')
            _require(episode['stratum'] in {'visible', 'occluded', 'out_of_frame'}, 'intended_stratum')
            _require(bool(re.fullmatch(r'Town(?:02|05|06|07|10HD)(?:_Opt)?', episode['map'])), 'development_map')
            _require(type(episode['seed']) is int and episode['seed'] >= 0, 'episode_seed')
            _require((episode['map'], episode['seed']) not in seeds, 'duplicate_seed')
            seeds.add((episode['map'], episode['seed']))
        report['kind'], report['protocol_sha256'] = protocol['kind'], protocol_sha
        directories = sorted(p for p in root.iterdir() if p.is_dir())
        _require(len(directories) <= protocol['max_decisions'], 'decision_limit')
        if not directories:
            incomplete = True
            issues.append('no_decisions')
        for directory in directories:
            identifier = directory.name
            counts['decisions'] += 1
            try:
                _require(not directory.is_symlink() and directory.resolve().parent == root.resolve(), 'external_record_path')
                allowed_files = {'input.json', 'outcome.json', 'advice_real_jev.json',
                                 'advice_mock_jev.json', 'advice_utility.json'}
                _require(all(p.name in allowed_files and p.is_file() for p in directory.iterdir()),
                         'unrecognized_record_file')
                data, input_sha = _load(directory / 'input.json')
                candidates = _input_checks(data, protocol, protocol_sha, identifier, episodes)
                episode_ids.add(data['episode_id'])
                for selector in ('real_jev', 'mock_jev', 'utility'):
                    path = directory / f'advice_{selector}.json'
                    if path.exists():
                        advice, _ = _load(path)
                        _linked(advice, identifier, input_sha)
                        _advice_checks(advice, selector, data['safe_candidate_ids'])
                        if selector == 'real_jev':
                            key = 'real_jev_response_records' if advice['status'] == 'RESPONSE' else 'real_jev_failure_records'
                            counts[key] += 1
                if not (directory / 'outcome.json').exists():
                    incomplete = True
                    issues.append(f'{identifier}:missing_outcome')
                    continue
                outcome, _ = _load(directory / 'outcome.json')
                _linked(outcome, identifier, input_sha)
                _outcome_checks(outcome, data, candidates)
                if outcome['status'] == 'NOT_EXECUTED':
                    counts['not_executed'] += 1
                else:
                    counts['execution_reported'] += 1
                    counts['live_execution_records'] += int(protocol['kind'] == 'LIVE_SIMULATION')
            except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
                invalid = True
                # Never echo external content or raw exception text.
                code = str(exc) if type(exc) is ValueError and str(exc).replace('_', '').isalnum() else type(exc).__name__
                issues.append(f'{identifier}:{code}')
        counts['recorded_episodes'] = len(episode_ids)
        report['integrity_status'] = 'INVALID' if invalid else ('INCOMPLETE' if incomplete else 'CHECKED')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        issues.append('protocol_or_root:' + type(exc).__name__)
    return report
