import hashlib
import json

import pytest

from test_decision_dataset import writer, begin


def audit(root):
    from jev_uav_track import decision_dataset_audit
    return decision_dataset_audit.audit_dataset(root)


def replace_json(path, mutate):
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def test_safety_rejection_is_not_counted_as_observed_collision_free_training(tmp_path):
    w = writer(tmp_path)
    begin(w, safety_sources={'clearance': 'scene_geometry', 'battery': 'unobserved'})
    w.record_no_execution('d01', reason='safety_rejection')
    report = audit(w.root)
    assert report['integrity_status'] == 'CHECKED'
    assert report['counts']['not_executed'] == 1
    assert report['counts']['execution_reported'] == 0
    assert report['counts']['admitted_training_samples'] == 0
    assert report['real_jev_attempts'] == 'NOT_VERIFIED_BY_DATASET'
    assert report['training_admission'] == 'NOT_AUDITED'


def test_partial_decision_is_incomplete_and_retained(tmp_path):
    w = writer(tmp_path)
    begin(w)
    before = {p: p.read_bytes() for p in w.root.rglob('*.json')}
    report = audit(w.root)
    assert report['integrity_status'] == 'INCOMPLETE'
    assert 'd01:missing_outcome' in report['issues']
    assert before == {p: p.read_bytes() for p in w.root.rglob('*.json')}


@pytest.mark.parametrize('target', ['protocol', 'input', 'outcome_link', 'partial_json'])
def test_tampered_or_truncated_records_are_rejected(tmp_path, target):
    w = writer(tmp_path)
    begin(w)
    w.record_no_execution('d01', reason='advisory_only')
    if target == 'protocol':
        replace_json(w.root / 'protocol.json', lambda x: x.update(model_sha256='c' * 64))
    elif target == 'input':
        replace_json(w.root / 'd01/input.json', lambda x: x.update(decision_time_s=200))
    elif target == 'outcome_link':
        replace_json(w.root / 'd01/outcome.json', lambda x: x.update(input_sha256='c' * 64))
    else:
        (w.root / 'd01/outcome.json').write_text('{')
    report = audit(w.root)
    assert report['integrity_status'] == 'INVALID'
    assert report['counts']['admitted_training_samples'] == 0


def test_zero_decision_run_does_not_pass_integrity_as_complete(tmp_path):
    w = writer(tmp_path)
    report = audit(w.root)
    assert report['integrity_status'] == 'INCOMPLETE'
    assert report['counts']['recorded_episodes'] == 0


def test_synthetic_execution_does_not_become_live_data(tmp_path):
    w = writer(tmp_path)
    _, actions = begin(w)
    a = actions[0]
    w.record_outcome('d01', a.id, execution_trace=[{'timestamp_s': 10.03,
        'duration_s': .1, 'command_mps': a.command_mps,
        'yaw_rate_dps': a.yaw_rate_dps, 'acknowledged': True}], label_end_s=11,
        metrics={'visibility_fraction': 1., 'collision': False})
    report = audit(w.root)
    assert report['counts']['execution_reported'] == 1
    assert report['counts']['live_execution_records'] == 0
    assert report['counts']['admitted_training_samples'] == 0
    assert report['observed_strata'] == 'NOT_VERIFIED_BY_DATASET'


def test_api_response_count_is_not_misreported_as_network_attempts(tmp_path):
    from jev_uav_track.models import JevDecision
    w = writer(tmp_path)
    _, actions = begin(w)
    w.record_advice('d01', 'real_jev', JevDecision(actions[0].id, {}, raw={}), latency_ms=20)
    w.record_no_execution('d01', reason='advisory_only')
    report = audit(w.root)
    assert report['counts']['real_jev_response_records'] == 1
    assert report['real_jev_attempts'] == 'NOT_VERIFIED_BY_DATASET'
    assert report['counts']['actual_live_jev_responses_verified'] == 0


def test_hash_consistent_but_future_input_still_fails_semantic_audit(tmp_path):
    w = writer(tmp_path)
    begin(w)
    w.record_no_execution('d01', reason='advisory_only')
    path = w.root / 'd01/input.json'
    replace_json(path, lambda x: x.update(history_times_s=[20.]))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    replace_json(w.root / 'd01/outcome.json', lambda x: x.update(input_sha256=digest))
    report = audit(w.root)
    assert report['integrity_status'] == 'INVALID'
    assert any('future_or_unordered_input' in item for item in report['issues'])


def test_numerically_populated_nonexecution_metrics_are_rejected(tmp_path):
    w = writer(tmp_path)
    begin(w)
    w.record_no_execution('d01', reason='advisory_only')
    replace_json(w.root / 'd01/outcome.json', lambda x: x.update(metrics={'collision': False}))
    assert audit(w.root)['integrity_status'] == 'INVALID'


def test_readonly_cli_reports_incomplete_with_nonzero_exit(tmp_path):
    import subprocess
    import sys
    w = writer(tmp_path)
    result = subprocess.run([sys.executable, 'scripts/audit_decision_dataset.py', str(w.root)],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)['integrity_status'] == 'INCOMPLETE'


@pytest.mark.parametrize('mutation', ['state_seed', 'negative_duration', 'safety_source',
                                      'request_history', 'nonfinite_position'])
def test_consistent_hash_does_not_bypass_snapshot_contract(tmp_path, mutation):
    w = writer(tmp_path)
    begin(w)
    w.record_no_execution('d01', reason='advisory_only')
    path = w.root / 'd01/input.json'
    def mutate(data):
        if mutation == 'state_seed': data['state']['environment'] = {'seed': 999}
        if mutation == 'negative_duration': data['candidates'][0]['duration_s'] = -1
        if mutation == 'safety_source': data['safety_sources']['battery'] = 'invented'
        if mutation == 'request_history': data['request_template']['state']['history_summary'] = 'later future observation'
        if mutation == 'nonfinite_position': data['state']['target']['position_m'][0] = float('inf')
    replace_json(path, mutate)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    replace_json(w.root / 'd01/outcome.json', lambda x: x.update(input_sha256=digest))
    assert audit(w.root)['integrity_status'] == 'INVALID'


def test_unrecognized_advice_file_is_not_silently_ignored(tmp_path):
    w = writer(tmp_path)
    begin(w)
    w.record_no_execution('d01', reason='advisory_only')
    (w.root / 'd01/advice_real_jev_retry.json').write_text('{}')
    assert audit(w.root)['integrity_status'] == 'INVALID'


def test_protocol_claiming_test_split_is_invalid_even_with_consistent_hashes(tmp_path):
    w = writer(tmp_path)
    begin(w)
    w.record_no_execution('d01', reason='advisory_only')
    p = w.root / 'protocol.json'
    replace_json(p, lambda x: x['episodes'][0].update(split='final_test'))
    path = w.root / 'd01/input.json'
    replace_json(path, lambda x: x.update(protocol_sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    replace_json(w.root / 'd01/outcome.json', lambda x: x.update(input_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    assert audit(w.root)['integrity_status'] == 'INVALID'


@pytest.mark.parametrize('mutation', ['missing_questions', 'bad_command', 'huge_integer',
                                      'false_criteria', 'bad_yaw'])
def test_reviewed_malformed_contracts_fail_closed(tmp_path, mutation):
    w = writer(tmp_path)
    begin(w)
    w.record_no_execution('d01', reason='advisory_only')
    path = w.root / 'd01/input.json'
    def mutate(data):
        if mutation == 'missing_questions': del data['request_template']['questions']
        elif mutation == 'huge_integer': data['decision_time_s'] = 10 ** 400
        elif mutation == 'false_criteria':
            question = data['request_template']['questions']['next_behavior']
            question['criteria'][data['safe_candidate_ids'][0]] = 'wrong predicted action'
        else:
            key, value = ('command_mps', ['not-a-velocity']) if mutation == 'bad_command' else ('yaw_rate_dps', 'bad-yaw')
            data['candidates'][0][key] = value
            data['request_template']['state']['candidate_outcomes'][0][key] = value
    replace_json(path, mutate)
    replace_json(w.root / 'd01/outcome.json', lambda x: x.update(input_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    assert audit(w.root)['integrity_status'] == 'INVALID'
