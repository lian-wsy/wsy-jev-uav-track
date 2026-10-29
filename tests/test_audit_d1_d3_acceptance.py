from scripts.audit_d1_d3_acceptance import build_audit
import shutil

from scripts import audit_d1_d3_acceptance as audit


def test_export_without_private_artifacts_or_git_is_auditable(tmp_path, monkeypatch):
    for directory in ('docs', 'src'):
        shutil.copytree(audit.ROOT / directory, tmp_path / directory)
    monkeypatch.setattr(audit, 'ROOT', tmp_path)
    result = audit.build_audit()
    assert result['overall'] != 'PASS'
    assert not (tmp_path / 'artifacts').exists()
    assert result['source_commit'] is None


def test_missing_git_executable_does_not_prevent_evidence_audit(monkeypatch):
    def missing_git(*args, **kwargs):
        raise FileNotFoundError('git not installed')

    monkeypatch.setattr(audit.subprocess, 'run', missing_git)
    result = build_audit()
    assert result['source_commit'] is None
    assert result['overall'] == 'PARTIAL'


def test_document_presence_does_not_prove_acceptance():
    result = build_audit()
    for day, item in [('D1', 'research_hypothesis_and_sources'),
                      ('D1', 'metrics_and_fair_comparison_protocol'),
                      ('D2', 'jev_budget_and_cost_estimate'),
                      ('D3', 'horizon_and_action_semantics')]:
        assert result['criteria'][day][item]['status'] == 'REQUIRES_REVIEW'


def test_hidden_measurement_cannot_pass_by_matching_aggregate_counts(monkeypatch):
    read = audit._read

    def tampered(relative):
        data = read(relative)
        if relative.endswith('/belief03.json'):
            data['api_probe']['contract']['belief_contract']['frames'][4]['target_measurement_world'] = [1, 2, 3]
        return data

    monkeypatch.setattr(audit, '_read', tampered)
    result = build_audit()
    assert result['criteria']['D3']['hidden_truth_isolation_and_live_belief']['status'] == 'NOT_MET'


def test_acceptance_audit_preserves_unmet_final_test_and_real_jev_gates():
    result = build_audit()
    assert result["overall"] == "PARTIAL"
    assert result["criteria"]["D1"]["independent_final_town_test"]["status"] == "NOT_MET"
    assert result["criteria"]["D2"]["real_jev_smoke"]["status"] == "PARTIAL"
    assert result["criteria"]["D3"]["formal_tracking_action_execution"]["status"] == "NOT_AUDITED"


def test_acceptance_audit_counts_live_belief_without_calling_it_rgb_tracking():
    result = build_audit()
    detail = result["criteria"]["D3"]["hidden_truth_isolation_and_live_belief"]["detail"]
    assert "16/16" in detail
    assert "12 visible and 4 missing" in detail
    assert "not RGB perception" in detail
