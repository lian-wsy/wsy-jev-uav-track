"""Exercise the shipped offline command path, not a hand-built tick."""
import json
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_d6_actuation_smoke.py"


def test_cli_runs_real_pipeline_and_transport_guards_and_keeps_failures(tmp_path):
    output = tmp_path / "fresh"
    result = subprocess.run([sys.executable, str(SCRIPT), "--output-dir", str(output)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["evidence_class"] == "SYNTHETIC_NOT_CARLA"
    assert report["live_motion"] == "NOT_EVALUATED"
    assert report["case_count"] == 14
    for method in ("utility", "mock_jev"):
        runs = {r["scenario"]: r for r in report["runs"] if r["method"] == method}
        good = runs["airborne"]
        assert good["controller_tick"]["disposition"] == "COMMAND_PREFIX"
        assert good["selection_counts"]["accepted"] == 1
        assert good["receipt"]["prefix_acknowledged"] is True
        assert good["receipt"]["stop_status"] == "NOT_VERIFIED"
        assert good["stop_verdict"] == "TELEMETRY_SETTLED"
        assert len(good["rpc_calls"]) == 3
        assert runs["grounded"]["submit_code"] == "GROUNDED_NOT_READY"
        assert runs["grounded"]["rpc_calls"] == []
        assert runs["expired_before_send"]["receipt"]["status"] == "EXPIRED_BEFORE_SEND"
        assert runs["expired_before_send"]["rpc_calls"] == []
        assert runs["rpc_rejected"]["receipt"]["owner_termination_required"] is True
        assert runs["rpc_rejected"]["receipt"]["neutral_acknowledged"] is True
        assert runs["rpc_overrun"]["receipt"]["owner_termination_required"] is True
        assert runs["watchdog_before_send"]["rpc_calls"] == []
        assert runs["watchdog_before_send"]["receipt"]["owner_termination_required"] is True
        assert runs["unsettled_after_neutral"]["stop_verdict"] == "STILL_MOVING"
        assert all(r["candidate_horizon_s"] == 1. for r in runs.values())
    assert report["runs"][0]["controller_tick"]["command"] == report["runs"][7]["controller_tick"]["command"]
    before = (output / "report.json").read_bytes()
    repeated = subprocess.run([sys.executable, str(SCRIPT), "--output-dir", str(output)],
                              capture_output=True, text=True, timeout=20)
    assert repeated.returncode == 2
    assert (output / "report.json").read_bytes() == before


def test_same_seed_produces_byte_identical_offline_evidence(tmp_path):
    outputs = []
    for name in ("first", "second"):
        directory = tmp_path / name
        result = subprocess.run([sys.executable, str(SCRIPT), "--output-dir", str(directory), "--seed", "17"],
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        outputs.append((directory / "report.json").read_bytes())
    assert outputs[0] == outputs[1]
