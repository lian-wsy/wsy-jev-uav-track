"""Audit the original D1--D3 acceptance criteria from checked-in evidence.

This is deliberately a provenance audit, not a simulator launcher and not an
HDF5 validator.  It refuses to infer a final test set or a real Jev result
from a development report.  The output is suitable for a daily checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> Any:
    path = ROOT / relative
    return json.loads(path.read_text(encoding="utf-8"))


def _exists(*relative: str) -> bool:
    return all((ROOT / item).is_file() for item in relative)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _criterion(status: str, evidence: list[str], detail: str) -> dict[str, Any]:
    if status not in {"PASS", "PARTIAL", "NOT_MET", "NOT_AUDITED"}:
        raise ValueError(status)
    return {"status": status, "evidence": evidence, "detail": detail}


def build_audit() -> dict[str, Any]:
    split = _read("artifacts/acceptance_20261028/development_split_v2.json")
    episodes = split["episodes"]
    counts = Counter(entry["split"] for entry in episodes)
    statuses = {entry["validation_status"] for entry in episodes}
    sha_unique = len({entry["sha256"] for entry in episodes}) == len(episodes)
    seed_unique = len({entry["seed"] for entry in episodes}) == len(episodes)
    path_unique = len({entry["path"] for entry in episodes}) == len(episodes)
    split_ok = (
        len(episodes) == 39
        and statuses == {"PASS"}
        and sha_unique
        and seed_unique
        and path_unique
        and counts == Counter({"train": 28, "validation": 6, "development_stress": 5})
    )

    belief = _read("docs/experiments/evidence/live_belief_20261028/belief03.json")
    contract = belief["api_probe"]["contract"]
    belief_contract = contract["belief_contract"]
    frames = belief_contract["frames"]
    visible = [frame for frame in frames if frame["target_visible"]]
    missing = [frame for frame in frames if not frame["target_visible"]]
    hidden_queries = contract["belief_contract"]["target_pose_queries"] - len(visible)
    live_belief_ok = (
        len(frames) == 16
        and len(visible) == 12
        and len(missing) == 4
        and hidden_queries == 0
        and all(frame["state"]["environment"]["position_frame"] == "carla_world_z_up" for frame in frames)
        and all(candidate["metadata"]["command_frame"] == "world_z_up"
                 for frame in frames for candidate in frame["candidates"])
    )

    origin_summary = _read("docs/experiments/evidence/live_origin_20261028/summary.json")
    origin03 = next(run for run in origin_summary["runs"] if run["run"] == "origin03")
    origin_ok = origin03["status"] == "API_SMOKE_COMPLETED" and origin03["protected_unchanged"] and origin03["cleanup_confirmed"]

    isolated = _read("docs/experiments/evidence/isolated_carla_20261028/summary.json")
    contract08 = next(run for run in isolated["runs"] if run["run"] == "contract08")
    runtime_ok = (
        contract08["status"] == "API_SMOKE_COMPLETED"
        and contract08["protected_unchanged"]
        and contract08["cleanup_confirmed"]
        and contract08["command_refresh"]["acknowledged_count"] == 20
    )

    budget_text = ROOT / "docs/experiments/jev_budget_20261028.md"
    budget_source = ROOT / "src/jev_uav_track/request_budget.py"
    budget_ok = budget_text.is_file() and budget_source.is_file()

    d1 = {
        "research_hypothesis_and_sources": _criterion(
            "PASS" if _exists("docs/related_work_matrix.md") else "NOT_MET",
            ["docs/related_work_matrix.md"],
            "Jev boundary and an applicable active-tracking reference are recorded; no novelty or SOTA claim is made.",
        ),
        "development_split_and_sha": _criterion(
            "PASS" if split_ok else "NOT_MET",
            ["artifacts/acceptance_20261028/development_split_v2.json"],
            f"{len(episodes)} PASS episodes: {dict(counts)}; unique sha/seed/path={sha_unique}/{seed_unique}/{path_unique}.",
        ),
        "independent_final_town_test": _criterion(
            "NOT_MET",
            ["artifacts/acceptance_20261028/development_split_v2.json"],
            split["policy"]["final_test_reason"],
        ),
        "metrics_and_fair_comparison_protocol": _criterion(
            "PASS" if _exists("docs/metrics_protocol_v1.md") else "NOT_MET",
            ["docs/metrics_protocol_v1.md"],
            "Fixed-window, failure-inclusive, episode-level comparison and identity/safety limitations are frozen.",
        ),
    }

    d2 = {
        "owned_runtime_and_api_contract": _criterion(
            "PASS" if runtime_ok and origin_ok else "PARTIAL",
            ["docs/experiments/evidence/isolated_carla_20261028/summary.json", "docs/experiments/evidence/live_origin_20261028/summary.json"],
            "Dedicated runtime, ports, protected settings, cleanup, origin bridge and paced command refresh are evidenced; this is a bounded smoke, not a tracking run.",
        ),
        "jev_budget_and_cost_estimate": _criterion(
            "PASS" if budget_ok else "NOT_MET",
            ["docs/experiments/jev_budget_20261028.md", "src/jev_uav_track/request_budget.py"],
            "Preparation caps are implemented and the report keeps historical attempt reconciliation UNKNOWN.",
        ),
        "real_jev_smoke": _criterion(
            "PARTIAL",
            ["docs/experiments/jev_budget_20261028.md", "docs/experiments/live_belief_20261028.md"],
            "No new real Jev request was made; the bounded live probe used zero Jev calls and therefore cannot be called a real-Jev smoke result.",
        ),
    }

    d3 = {
        "coordinate_yaw_and_time_contract": _criterion(
            "PASS" if runtime_ok and origin_ok else "PARTIAL",
            ["docs/experiments/live_origin_20261028.md", "docs/experiments/live_observation_20261028.md"],
            "Global object NED and vehicle-local NED are bridged explicitly; paced and paused clocks, bounded freshness and camera extrinsics are measured.",
        ),
        "horizon_and_action_semantics": _criterion(
            "PASS" if _exists("docs/experiments/causal_belief_20261028.md") else "NOT_MET",
            ["docs/experiments/causal_belief_20261028.md", "docs/metrics_protocol_v1.md"],
            "Five-frame horizon is reported as approximately one second and action frame/expiry semantics are fixed; full high-level closed-loop consequence is later-sprint work.",
        ),
        "hidden_truth_isolation_and_live_belief": _criterion(
            "PASS" if live_belief_ok else "PARTIAL",
            ["docs/experiments/live_belief_20261028.md", "docs/experiments/evidence/live_belief_20261028/belief03.json"],
            f"16/16 windows, {len(visible)} visible and {len(missing)} missing; hidden-frame target queries={hidden_queries}. Input is simulator-visible oracle, not RGB perception.",
        ),
        "formal_tracking_action_execution": _criterion(
            "NOT_AUDITED",
            ["docs/experiments/live_belief_20261028.md"],
            "Missing measured clearance/battery caused all 32 candidate choices to be hard-rejected; no model action was executed.",
        ),
    }

    return {
        "schema": "jev_uav_track.d1_d3_acceptance.v1",
        "generated_from": "checked_in_evidence_only",
        "git_head_file_sha256": _sha256(ROOT / ".git/HEAD"),
        "criteria": {"D1": d1, "D2": d2, "D3": d3},
        "overall": "PARTIAL",
        "unresolved": [
            "D1 independent final-town PASS episodes are absent.",
            "Historical Jev request ledger is UNKNOWN; no new real Jev smoke is claimed.",
            "D3 live evidence uses simulator-visible oracle input and did not execute a selected tracking action.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
