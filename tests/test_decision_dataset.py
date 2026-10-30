from dataclasses import replace
import json

import pytest

from jev_uav_track.models import JevDecision
from jev_uav_track.primitives import DEFAULT_PRIMITIVES
from jev_uav_track.rollout import KinematicRolloutModel
from test_core import make_state


def protocol(**overrides):
    value = {'kind': 'SYNTHETIC', 'code_sha': 'a' * 40, 'model_sha256': 'b' * 64,
             'max_decisions': 8, 'episodes': [
                 {'id': 'ep01', 'map': 'Town02', 'seed': 101, 'stratum': 'visible',
                  'split': 'development_train'}]}
    value.update(overrides)
    return value


def writer(tmp_path, **overrides):
    from jev_uav_track import decision_dataset
    return decision_dataset.DecisionDatasetWriter(tmp_path / 'run', protocol(**overrides))


def begin(w, *, identifier='d01', **overrides):
    state = replace(make_state(), target=replace(make_state().target, timestamp_s=10.))
    actions = KinematicRolloutModel().generate(DEFAULT_PRIMITIVES, state)
    values = {'observation_time_s': 10., 'history_times_s': [9., 9.5, 10.],
              'decision_time_s': 10.02, 'clock_domain': 'sim_monotonic',
              'safety_sources': {'clearance': 'synthetic_fixture', 'battery': 'synthetic_fixture'}}
    values.update(overrides)
    w.begin_decision(identifier, 'ep01', state, actions, **values)
    return state, actions


def test_future_labels_cannot_rewrite_the_frozen_selector_input(tmp_path):
    w = writer(tmp_path)
    _, actions = begin(w)
    before = (w.root / 'd01' / 'input.json').read_bytes()
    action = actions[0]
    w.record_outcome('d01', action.id, execution_trace=[{
        'timestamp_s': 10.03, 'duration_s': .1, 'command_mps': list(action.command_mps),
        'yaw_rate_dps': action.yaw_rate_dps, 'acknowledged': True}],
        label_end_s=11.5, metrics={'visibility_fraction': 0., 'collision': True,
                                 'minimum_clearance_m': 0.})
    assert (w.root / 'd01' / 'input.json').read_bytes() == before
    label = json.loads((w.root / 'd01' / 'outcome.json').read_text())
    assert label['training_admission'] == 'NOT_AUDITED'
    assert label['executed_duration_s'] == pytest.approx(.1)
    assert label['full_candidate_duration_executed'] is False
    assert label['paired_counterfactual'] is False
    with pytest.raises(FileExistsError):
        w.record_no_execution('d01', reason='safety_rejection')


@pytest.mark.parametrize('kwargs', [
    {'observation_time_s': 10.1}, {'history_times_s': [9., 11.]},
    {'history_times_s': [10., 9.]}, {'decision_time_s': float('nan')},
    {'observation_time_s': 9.9},  # belief is later than the claimed observation
])
def test_future_or_unordered_input_times_are_rejected_before_write(tmp_path, kwargs):
    w = writer(tmp_path)
    with pytest.raises(ValueError):
        begin(w, **kwargs)
    assert not (w.root / 'd01').exists()


def test_missing_api_probabilities_are_not_recorded_as_one_hot_teacher_confidence(tmp_path):
    w = writer(tmp_path)
    _, actions = begin(w)
    chosen = actions[0].id
    decision = JevDecision(chosen, {chosen: 1.}, model_version='test-model',
                           raw={'answers': {'next_behavior': {'choice': chosen}}})
    w.record_advice('d01', 'real_jev', decision, latency_ms=20.)
    saved = json.loads((w.root / 'd01' / 'advice_real_jev.json').read_text())
    assert saved['probabilities'] is None
    assert saved['probability_source'] == 'NOT_PROVIDED'
    assert saved['label_kind'] == 'ADVICE_NOT_GROUND_TRUTH'


def test_actual_api_probabilities_and_local_advice_are_distinct(tmp_path):
    w = writer(tmp_path)
    _, actions = begin(w)
    chosen, alternative = actions[0].id, actions[1].id
    d = JevDecision(chosen, {chosen: .7, alternative: .3}, raw={
        'answers': {'next_behavior': {'choice': chosen, 'probabilities': {chosen: .7, alternative: .3}}}})
    w.record_advice('d01', 'real_jev', d, latency_ms=20.)
    w.record_advice('d01', 'mock_jev', d, latency_ms=0.)
    a = json.loads((w.root / 'd01' / 'advice_real_jev.json').read_text())
    b = json.loads((w.root / 'd01' / 'advice_mock_jev.json').read_text())
    assert a['probability_source'] == 'API_RESPONSE'
    assert a['probabilities'][chosen] == .7
    assert b['probability_source'] == 'LOCAL_SELECTOR'


def test_unsafe_candidates_cannot_be_advised_or_executed(tmp_path):
    w = writer(tmp_path)
    state = make_state()
    state = replace(state, uav=replace(state.uav, minimum_obstacle_margin_m=0))
    actions = KinematicRolloutModel().generate(DEFAULT_PRIMITIVES, state)
    w.begin_decision('d01', 'ep01', state, actions, observation_time_s=1,
                     history_times_s=[], decision_time_s=1, clock_domain='sim',
                     safety_sources={'clearance': 'unobserved', 'battery': 'unobserved'})
    record = json.loads((w.root / 'd01' / 'input.json').read_text())
    assert record['safe_candidate_ids'] == []
    with pytest.raises(ValueError):
        w.record_advice('d01', 'real_jev', JevDecision(actions[0].id, {}), latency_ms=1)
    w.record_no_execution('d01', reason='safety_rejection')
    saved = json.loads((w.root / 'd01' / 'outcome.json').read_text())
    assert saved['status'] == 'NOT_EXECUTED'
    assert saved['metrics'] is None


@pytest.mark.parametrize('map_name', ['Town03', 'Town3', 'Town03_Opt'])
def test_heldout_map_is_rejected_before_output_creation(tmp_path, map_name):
    p = protocol()
    p['episodes'][0]['map'] = map_name
    with pytest.raises(ValueError):
        writer(tmp_path, **p)
    assert not (tmp_path / 'run').exists()


def test_output_and_ids_cannot_overwrite_or_escape(tmp_path):
    w = writer(tmp_path)
    begin(w)
    with pytest.raises(FileExistsError):
        begin(w)
    with pytest.raises(ValueError):
        begin(w, identifier='../escape')
    with pytest.raises(FileExistsError):
        writer(tmp_path)


def test_writer_refuses_modified_input_before_attaching_any_label(tmp_path):
    w = writer(tmp_path)
    begin(w)
    (w.root / 'd01' / 'input.json').write_text('{}')
    with pytest.raises(ValueError, match='fingerprint'):
        w.record_no_execution('d01', reason='safety_rejection')


def test_eight_decision_cap_cannot_be_exceeded(tmp_path):
    w = writer(tmp_path)
    for i in range(8):
        begin(w, identifier=f'd{i}')
    with pytest.raises(ValueError):
        begin(w, identifier='ninth')


@pytest.mark.parametrize('mutation', ['command', 'time', 'ack', 'duration'])
def test_mismatched_or_unconfirmed_execution_is_not_an_outcome_label(tmp_path, mutation):
    w = writer(tmp_path)
    _, actions = begin(w)
    trace = {'timestamp_s': 10.03, 'duration_s': .1,
             'command_mps': list(actions[0].command_mps),
             'yaw_rate_dps': actions[0].yaw_rate_dps, 'acknowledged': True}
    if mutation == 'command': trace['command_mps'] = [99., 0., 0.]
    if mutation == 'time': trace['timestamp_s'] = 9.
    if mutation == 'ack': trace['acknowledged'] = False
    if mutation == 'duration': trace['duration_s'] = float('nan')
    with pytest.raises(ValueError):
        w.record_outcome('d01', actions[0].id, execution_trace=[trace], label_end_s=12,
                         metrics={'collision': False})
    assert not (w.root / 'd01' / 'outcome.json').exists()


def test_unobserved_safety_provenance_overrides_apparently_safe_defaults(tmp_path):
    w = writer(tmp_path)
    begin(w, safety_sources={'clearance': 'scene_geometry', 'battery': 'unobserved'})
    saved = json.loads((w.root / 'd01' / 'input.json').read_text())
    assert not saved['safe_candidate_ids']
    assert saved['request_template']['state']['candidate_outcomes'] == []


def test_failure_records_do_not_accept_arbitrary_exception_text(tmp_path):
    w = writer(tmp_path)
    begin(w)
    with pytest.raises(ValueError):
        w.record_advice_failure('d01', 'real_jev', error_code='Bearer fake-secret', latency_ms=20)
    w.record_advice_failure('d01', 'real_jev', error_code='timeout', latency_ms=8000)
    saved = json.loads((w.root / 'd01' / 'advice_real_jev.json').read_text())
    assert saved['status'] == 'FAILED'
    assert 'probabilities' not in saved


def test_live_protocol_cannot_mislabel_synthetic_safety_inputs(tmp_path):
    w = writer(tmp_path, kind='LIVE_SIMULATION')
    with pytest.raises(ValueError):
        begin(w)
    assert not (w.root / 'd01').exists()


def test_secret_or_nonfinite_metadata_is_rejected_before_output(tmp_path):
    for extra in [{'api_key': 'test-only'}, {'note': 'Bearer fake-secret'}, {'value': float('nan')}]:
        with pytest.raises(ValueError):
            writer(tmp_path, **extra)
        assert not (tmp_path / 'run').exists()


def test_freezing_inputs_makes_caller_mutation_irrelevant(tmp_path):
    w = writer(tmp_path)
    times = [9., 10.]
    _, actions = begin(w, history_times_s=times)
    original = (w.root / 'd01' / 'input.json').read_bytes()
    times.append(100.)
    actions.clear()
    w.record_no_execution('d01', reason='advisory_only')
    assert (w.root / 'd01' / 'input.json').read_bytes() == original


def test_label_outcomes_with_no_observed_metrics_remain_unknown(tmp_path):
    w = writer(tmp_path)
    _, actions = begin(w)
    action = actions[0]
    w.record_outcome('d01', action.id, execution_trace=[{'timestamp_s': 10.03,
        'duration_s': .1, 'command_mps': action.command_mps,
        'yaw_rate_dps': action.yaw_rate_dps, 'acknowledged': True}],
        label_end_s=11.5, metrics={'collision': None, 'visibility_fraction': None})
    saved = json.loads((w.root / 'd01' / 'outcome.json').read_text())
    assert saved['metrics']['collision'] is None
    assert saved['metrics']['visibility_fraction'] is None


def test_gapped_prefixes_do_not_claim_a_continuous_full_horizon(tmp_path):
    w = writer(tmp_path)
    _, actions = begin(w)
    action = actions[0]
    trace = [{'timestamp_s': 10.03 + i * 2, 'duration_s': action.duration_s / 2,
              'command_mps': action.command_mps, 'yaw_rate_dps': action.yaw_rate_dps,
              'acknowledged': True} for i in range(2)]
    w.record_outcome('d01', action.id, execution_trace=trace, label_end_s=15, metrics={})
    saved = json.loads((w.root / 'd01' / 'outcome.json').read_text())
    assert saved['full_candidate_duration_executed'] is False


def test_synthetic_cli_smoke_creates_only_synthetic_records_and_refuses_overwrite(tmp_path):
    import subprocess
    import sys
    output = tmp_path / 'smoke'
    command = [sys.executable, 'scripts/smoke_decision_dataset.py', '--output', str(output)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary['kind'] == 'SYNTHETIC'
    assert summary['real_api_requests'] == summary['simulator_actions'] == 0
    assert summary['training_admission'] == 'NOT_AUDITED'
    assert (output / 'd01' / 'outcome.json').is_file()
    before = (output / 'd01' / 'input.json').read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert (output / 'd01' / 'input.json').read_bytes() == before


@pytest.mark.parametrize('conflict', ['candidate_holdout', 'state_map', 'state_seed', 'state_split', 'json_metadata'])
def test_conflicting_or_heldout_provenance_is_rejected_before_snapshot(tmp_path, conflict):
    w = writer(tmp_path)
    state = make_state()
    actions = KinematicRolloutModel().generate(DEFAULT_PRIMITIVES, state)
    if conflict == 'candidate_holdout':
        actions[0] = replace(actions[0], metadata={'source_path': 'D:/heldout/Town03/episode.hdf5'})
    elif conflict == 'state_map':
        state = replace(state, environment={'map': 'Town07'})
    elif conflict == 'state_seed':
        state = replace(state, environment={'nested': {'seed': 999}})
    elif conflict == 'state_split':
        state = replace(state, environment={'split': 'final_test'})
    else:
        state = replace(state, environment={'episode_metadata_json': '{"map": "Town07"}'})
    with pytest.raises(ValueError):
        w.begin_decision('d01', 'ep01', state, actions, observation_time_s=10,
            history_times_s=[], decision_time_s=10, clock_domain='sim',
            safety_sources={'clearance': 'synthetic_fixture', 'battery': 'synthetic_fixture'})
    assert not (w.root / 'd01').exists()


def test_changed_protocol_cannot_be_used_to_attach_labels(tmp_path):
    w = writer(tmp_path)
    begin(w)
    (w.root / 'protocol.json').write_text('{}')
    with pytest.raises(ValueError, match='fingerprint'):
        w.record_no_execution('d01', reason='safety_rejection')
