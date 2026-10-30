"""Append-only pilot records. No simulator control, network or PASS admission.

The sole runner supplies observations and normalized world-z-up execution
receipts. Timestamps must share the declared monotonic clock; this recorder
checks ordering, not clock synchronization or sensor authenticity. Its safety
filter is an inventory check, never permission to dispatch a command.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

from .holdout import require_development_source
from .jev_client import build_choice_request
from .safety import filter_safe_actions


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('Invalid record identifier')
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Expected finite numeric value')
    return value


def _encoded(value):
    # A guard against accidental credential inclusion, not a general DLP tool.
    def check(item):
        if isinstance(item, dict):
            for key, content in item.items():
                if str(key).casefold() in {'authorization', 'api_key', 'apikey', 'password', 'secret', 'access_token'}:
                    raise ValueError('Credential fields are forbidden')
                check(content)
        elif isinstance(item, (tuple, list)):
            for content in item:
                check(content)
        elif isinstance(item, str) and re.search(r'apikey_|\bBearer\s|\bsk-[A-Za-z0-9]{12}', item):
            raise ValueError('Credential-like content is forbidden')
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2).encode('utf-8')


def _write(path, raw):
    with path.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _check_source(metadata, episode):
    require_development_source(episode['id'], metadata)
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            name = str(key).casefold()
            if name == 'episode_metadata_json' and isinstance(value, str):
                _check_source(json.loads(value), episode)
            if name in {'map', 'town', 'map_name', 'town_name', 'carla_map'}:
                # Support an API map path and the optimized map suffix.
                canonical = str(value).replace('\\', '/').split('/')[-1].casefold().removesuffix('_opt')
                expected = episode['map'].casefold().removesuffix('_opt')
                if canonical != expected:
                    raise ValueError('Source map conflicts with frozen episode')
            elif name in {'seed', 'episode_seed', 'scenario_seed'}:
                if type(value) is not int or value != episode['seed']:
                    raise ValueError('Source seed conflicts with frozen episode')
            elif name in {'split', 'source_split'} and value != episode['split']:
                raise ValueError('Source split conflicts with frozen episode')
            if isinstance(value, (dict, list, tuple)):
                _check_source(value, episode)
    elif isinstance(metadata, (list, tuple)):
        for item in metadata:
            _check_source(item, episode)


class DecisionDatasetWriter:
    """Single-writer, exclusive new run directory; restart never overwrites it.

    Input is durable before advice/outcomes can be appended. A crashed partial
    file/run is retained for audit and is never automatically resumed/admitted.
    Protocol and observation provenance remain caller assertions.
    """
    def __init__(self, root, protocol):
        protocol = {**protocol, 'recorder_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        raw = _encoded(protocol)
        protocol = json.loads(raw)
        if protocol.get('kind') not in {'SYNTHETIC', 'LIVE_SIMULATION'}:
            raise ValueError('Explicit dataset kind required')
        if type(protocol.get('max_decisions')) is not int or not 1 <= protocol['max_decisions'] <= 8:
            raise ValueError('Pilot requires at most eight decision points')
        for field, length in [('code_sha', 40), ('model_sha256', 64)]:
            if not re.fullmatch('[0-9a-f]{' + str(length) + '}', protocol.get(field, '')):
                raise ValueError('Code/model fingerprint required')
        episodes = protocol.get('episodes')
        if not isinstance(episodes, list) or not 1 <= len(episodes) <= 6:
            raise ValueError('Predeclare one to six pilot episodes')
        ids, seeds = set(), set()
        for episode in episodes:
            identifier = _identifier(episode['id'])
            require_development_source(identifier, episode)
            if not re.fullmatch(r'Town(?:02|05|06|07|10HD)(?:_Opt)?', episode.get('map', '')):
                raise ValueError('Only previously exposed development maps are supported')
            if episode.get('split') not in {'development_train', 'development_validation'}:
                raise ValueError('No final-test or stress-to-training admission')
            if episode.get('stratum') not in {'visible', 'occluded', 'out_of_frame'}:
                raise ValueError('Predeclare the intended scenario stratum')
            if type(episode.get('seed')) is not int or episode['seed'] < 0:
                raise ValueError('A nonnegative integer seed is required')
            if identifier in ids or (episode['map'], episode['seed']) in seeds:
                raise ValueError('Duplicate episode or map/seed')
            ids.add(identifier)
            seeds.add((episode['map'], episode['seed']))
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=False)
        _write(self.root / 'protocol.json', raw)
        self._protocol = protocol
        self._protocol_sha256 = hashlib.sha256(raw).hexdigest()
        self._episodes = {episode['id']: episode for episode in episodes}
        self._inputs = {}

    def _check_protocol(self):
        if hashlib.sha256((self.root / 'protocol.json').read_bytes()).hexdigest() != self._protocol_sha256:
            raise ValueError('Protocol fingerprint changed')

    def begin_decision(self, decision_id, episode_id, state, actions, *, observation_time_s,
                       history_times_s, decision_time_s, clock_domain, safety_sources):
        identifier = _identifier(decision_id)
        self._check_protocol()
        if identifier in self._inputs or (self.root / identifier).exists():
            raise FileExistsError('Decision already recorded')
        if episode_id not in self._episodes or len(self._inputs) >= self._protocol['max_decisions']:
            raise ValueError('Unknown episode or decision budget exhausted')
        _identifier(clock_domain)
        observation_time_s, decision_time_s = map(_number, (observation_time_s, decision_time_s))
        times = [_number(t) for t in history_times_s]
        if (observation_time_s > decision_time_s
                or _number(state.target.timestamp_s) > observation_time_s
                or any(t > observation_time_s for t in times)
                or times != sorted(times)):
            raise ValueError('Future or unordered input timestamps')
        _check_source(state.environment, self._episodes[episode_id])
        if not actions or len({a.id for a in actions}) != len(actions):
            raise ValueError('Nonempty unique candidates required')
        for action in actions:
            _identifier(action.id)
            _check_source(action.metadata, self._episodes[episode_id])
            if _number(action.duration_s) <= 0:
                raise ValueError('Candidate duration must be positive')
        if set(safety_sources) != {'clearance', 'battery'}:
            raise ValueError('Explicit clearance and battery provenance required')
        allowed = {'simulator_sensor', 'scene_geometry', 'simulator_model', 'unobserved'}
        if self._protocol['kind'] == 'SYNTHETIC':
            allowed.add('synthetic_fixture')
        if any(v not in allowed for v in safety_sources.values()):
            raise ValueError('Invalid safety input source')
        safe, rejected = filter_safe_actions(actions, state.uav, state.constraints)
        if 'unobserved' in safety_sources.values():
            safe, rejected = [], tuple(a.id for a in actions)
        payload = {'schema': 'jev.decision_input.v1', 'episode_id': episode_id,
                   'protocol_sha256': self._protocol_sha256,
                   'decision_id': identifier, 'clock_domain': clock_domain,
                   'decision_time_s': decision_time_s, 'observation_time_s': observation_time_s,
                   'history_times_s': times, 'safety_sources': safety_sources,
                   'state': state.to_dict(), 'candidates': [a.to_dict() for a in actions],
                   'safe_candidate_ids': [a.id for a in safe], 'rejected_candidate_ids': list(rejected),
                   'request_template': build_choice_request(state, safe),
                   'kind': self._protocol['kind'], 'training_admission': 'NOT_AUDITED'}
        raw = _encoded(payload)  # Validate the entire snapshot before creating output.
        folder = self.root / identifier
        folder.mkdir(exist_ok=False)
        _write(folder / 'input.json', raw)
        self._inputs[identifier] = hashlib.sha256(raw).hexdigest()
        return self._inputs[identifier]

    def _input(self, decision_id):
        self._check_protocol()
        _identifier(decision_id)
        if decision_id not in self._inputs:
            raise ValueError('Decision has not been frozen by this writer')
        raw = (self.root / decision_id / 'input.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != self._inputs[decision_id]:
            raise ValueError('Decision input fingerprint changed')
        return json.loads(raw)

    def _append(self, decision_id, name, payload):
        self._input(decision_id)
        payload = {**payload, 'input_sha256': self._inputs[decision_id],
                   'decision_id': decision_id, 'training_admission': 'NOT_AUDITED'}
        _write(self.root / decision_id / name, _encoded(payload))

    def record_advice(self, decision_id, selector, decision, *, latency_ms):
        original = self._input(decision_id)
        if selector not in {'real_jev', 'mock_jev', 'utility'} or _number(latency_ms) < 0:
            raise ValueError('Invalid selector or latency')
        if decision.choice not in original['safe_candidate_ids']:
            raise ValueError('Selector chose an unavailable or unsafe candidate')
        probabilities, source = None, 'NOT_PROVIDED'
        if selector != 'real_jev':
            probabilities, source = dict(decision.probabilities), 'LOCAL_SELECTOR'
        else:
            raw = decision.raw
            answer = raw.get('answers', {}).get('next_behavior') if isinstance(raw.get('answers'), dict) else None
            if not isinstance(answer, dict):
                answer = raw.get('next_behavior', raw)
            if isinstance(answer, dict):
                provided = answer.get('probabilities') or answer.get('probs')
                if isinstance(provided, dict) and provided:
                    probabilities, source = dict(provided), 'API_RESPONSE'
        if probabilities is not None:
            if (set(probabilities) - set(original['safe_candidate_ids'])
                    or any(not 0 <= _number(v) <= 1 for v in probabilities.values())
                    or not math.isclose(sum(probabilities.values()), 1., abs_tol=1e-6)):
                raise ValueError('Invalid provided probability distribution')
        self._append(decision_id, f'advice_{selector}.json', {
            'selector': selector, 'status': 'RESPONSE', 'choice': decision.choice,
            'probabilities': probabilities, 'probability_source': source,
            'confidence': decision.confidence, 'model_version': decision.model_version,
            'latency_ms': latency_ms, 'label_kind': 'ADVICE_NOT_GROUND_TRUTH'})

    def record_advice_failure(self, decision_id, selector, *, error_code, latency_ms):
        if (selector not in {'real_jev', 'mock_jev', 'utility'}
                or error_code not in {'timeout', 'budget', 'protocol', 'transport', 'missing_key'}
                or _number(latency_ms) < 0):
            raise ValueError('Only bounded failure categories may be recorded')
        self._append(decision_id, f'advice_{selector}.json', {
            'selector': selector, 'status': 'FAILED', 'error_code': error_code,
            'latency_ms': latency_ms, 'label_kind': 'ADVICE_NOT_GROUND_TRUTH'})

    def record_no_execution(self, decision_id, *, reason):
        if reason not in {'safety_rejection', 'expired', 'advisory_only', 'no_dispatch'}:
            raise ValueError('Unknown non-execution reason')
        self._append(decision_id, 'outcome.json', {
            'status': 'NOT_EXECUTED', 'reason': reason, 'metrics': None,
            'paired_counterfactual': False})

    def record_outcome(self, decision_id, executed_action_id, *, execution_trace, label_end_s, metrics):
        original = self._input(decision_id)
        if executed_action_id not in original['safe_candidate_ids']:
            raise ValueError('Executed action is not an admitted candidate')
        action = next(a for a in original['candidates'] if a['id'] == executed_action_id)
        if not execution_trace:
            raise ValueError('No execution receipts; use record_no_execution')
        end = original['decision_time_s']
        duration = 0.
        for command in execution_trace:
            start, seconds = map(_number, (command['timestamp_s'], command['duration_s']))
            if (command.get('acknowledged') is not True or start < end - 1e-6 or seconds <= 0
                    or list(command['command_mps']) != action['command_mps']
                    or command['yaw_rate_dps'] != action['yaw_rate_dps']):
                raise ValueError('Mismatched, unordered or unconfirmed execution')
            end, duration = start + seconds, duration + seconds
        if _number(label_end_s) < end or duration > action['duration_s'] + 1e-6:
            raise ValueError('Labels precede execution or execution exceeds candidate duration')
        allowed_metrics = {'visibility_fraction', 'collision', 'minimum_clearance_m',
                           'range_error_m', 'reacquisition_time_s'}
        if not isinstance(metrics, dict) or set(metrics) - allowed_metrics:
            raise ValueError('Unknown observed metric')
        for key, value in metrics.items():
            if value is None:
                continue
            if key == 'collision':
                if type(value) is not bool:
                    raise ValueError('Collision must be observed boolean or unknown')
            elif _number(value) < 0 or (key == 'visibility_fraction' and value > 1):
                raise ValueError('Observed metric out of range')
        self._append(decision_id, 'outcome.json', {
            'status': 'EXECUTION_REPORTED', 'executed_action_id': executed_action_id,
            'execution_frame': 'WORLD_Z_UP', 'execution_trace': execution_trace,
            'executed_duration_s': duration,
            'execution_elapsed_s': end - execution_trace[0]['timestamp_s'],
            'full_candidate_duration_executed': (
                math.isclose(duration, action['duration_s'], rel_tol=0, abs_tol=1e-6)
                and math.isclose(end - execution_trace[0]['timestamp_s'], duration,
                                 rel_tol=0, abs_tol=1e-6)),
            'label_end_s': label_end_s, 'metrics': metrics,
            'paired_counterfactual': False, 'kind': original['kind']})
