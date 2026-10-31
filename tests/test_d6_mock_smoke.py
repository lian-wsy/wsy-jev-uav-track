from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

import pytest

from jev_uav_track.sim_adapter import AirSimWorldVelocityCommand


def test_seed_reset_and_fixed_actions_reproduce_synthetic_trace():
    from jev_uav_track.mock_simulator import DeterministicMockSession
    session = DeterministicMockSession()
    command = AirSimWorldVelocityCommand((.1, .2, -.1), 10., .05)
    first = session.reset(seed=7, scenario="normal")
    trace = [session.step(command) for _ in range(4)]
    assert session.reset(seed=7, scenario="normal") == first
    assert [session.step(command) for _ in range(4)] == trace
    assert session.reset(seed=8, scenario="normal") != first
    assert first.evidence_class == "SYNTHETIC_NOT_CARLA"
    assert trace[-1].state.uav.position_m[2] == pytest.approx(first.state.uav.position_m[2] + .02)


def test_synthetic_adapter_rejects_unbounded_commands():
    from jev_uav_track.mock_simulator import DeterministicMockSession
    session = DeterministicMockSession()
    session.reset(seed=7, scenario="normal")
    with pytest.raises(ValueError, match="COMMAND_LIMIT"):
        session.step(AirSimWorldVelocityCommand((.4, 0, 0), 0, .05))


def test_smoke_cli_uses_real_selectors_finite_traces_and_exclusive_output(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_d6_mock_smoke.py"
    output = tmp_path / "smoke"
    command = [sys.executable, str(script), "--output-dir", str(output), "--seed", "17", "--ticks", "45",
               "--energy-capacity-wh", "20", "--energy-initial-percent", "90", "--energy-power-w", "120"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["evidence_class"] == "SYNTHETIC_NOT_CARLA"
    assert report["timing_model"] == "SYNTHETIC_CONTROLLED_EXECUTOR"
    assert report["live_control"] == "NOT_EVALUATED"
    assert report["d1_data"] == "NOT_EVALUATED"
    assert report["reset_reproducible"] is True
    assert report["energy_config"]["source"] == "SIMULATED_RESOURCE"
    assert len(report["runs"]) == 6
    for run in report["runs"]:
        assert run["controller"] == "BoundedControlLoop"
        assert run["candidate_horizon_s"] == 1.
        assert run["low_level_period_s"] == .05
        assert run["high_level_period_s"] == 1.
        assert run["counts"]["requests"] >= 2
        assert len(run["trace"]) == 45
        assert len(run["requests"]) == run["counts"]["requests"]
        assert run["responses"]
        # All 2.25 seconds, including NO_COMMAND/timeout, consume .075 Wh.
        assert run["final_energy"]["remaining_wh"] == pytest.approx(17.925)
        assert run["final_energy"]["battery_percent"] == pytest.approx(89.625)
        for request in run["requests"]:
            assert request["candidates"]
            assert request["expires_s"] <= request["submitted_s"] + .2
            assert request["state"]["uav"]["energy_source"] == "SIMULATED_RESOURCE"
            assert request["state"]["uav"]["battery_capacity_wh"] == 20.
            assert all(a["outcome"]["energy_cost_wh"] == pytest.approx(1. / 30.) for a in request["candidates"])
        for response in run["responses"]:
            assert response["result"]["decision"]["model_version"] in {"utility-ranking-v0", "mock-jev-v0"}
        if run["scenario"] == "timeout":
            assert run["counts"]["timeouts"] >= 1
            assert run["counts"]["commands"] == 0
        elif run["scenario"] == "delayed":
            assert run["counts"]["accepted"] >= 1
        else:
            assert run["counts"]["commands"] >= 1
        for row in run["trace"]:
            assert row["observation"]["evidence_class"] == "SYNTHETIC_NOT_CARLA"
            assert row["observation"]["safety"]["battery_percent"] is None
            assert row["tick"]["energy"]["source"] == "SIMULATED_RESOURCE"
            if row["tick"]["command"]:
                assert 0 < row["tick"]["command"]["duration_s"] <= .05
    encoded = (output / "report.json").read_text()
    assert "NaN" not in encoded and "Infinity" not in encoded
    before = encoded
    second = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert second.returncode != 0
    assert (output / "report.json").read_text() == before


def test_same_seed_smoke_is_byte_reproducible(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_d6_mock_smoke.py"
    for name in ("one", "two"):
        result = subprocess.run([sys.executable, str(script), "--output-dir", str(tmp_path / name),
                                 "--seed", "19", "--ticks", "25", "--energy-capacity-wh", "20",
                                 "--energy-initial-percent", "90", "--energy-power-w", "120"],
                                 capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
    assert (tmp_path / "one" / "report.json").read_bytes() == (tmp_path / "two" / "report.json").read_bytes()


def test_smoke_cli_requires_explicit_energy_config_before_output_creation(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_d6_mock_smoke.py"
    output = tmp_path / "no_energy"
    result = subprocess.run([sys.executable, str(script), "--output-dir", str(output)],
                             capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert not output.exists()
