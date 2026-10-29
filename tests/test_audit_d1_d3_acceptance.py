from scripts.audit_d1_d3_acceptance import build_audit


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
