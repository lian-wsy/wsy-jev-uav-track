"""Audit the original D1--D3 acceptance criteria from checked-in evidence.

This is deliberately a provenance audit, not a simulator launcher and not an
HDF5 validator.  It refuses to infer a final test set or a real Jev result
from a development report.  The output is suitable for a daily checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from scripts.check_live_belief_evidence import verify


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
    if status not in {"PASS", "PARTIAL", "NOT_MET", "NOT_AUDITED", "REQUIRES_REVIEW"}:
        raise ValueError(status)
    return {"status": status, "evidence": evidence, "detail": detail}


def build_audit() -> dict[str, Any]:
    split_path = "docs/experiments/development_split_v2_20261028.json"
    split = _read(split_path)
    episodes = split["episodes"]
    counts = Counter(entry["split"] for entry in episodes)
    sha_unique = len({entry["sha256"] for entry in episodes}) == len(episodes)
    seed_unique = len({entry["seed"] for entry in episodes}) == len(episodes)
    name_unique = len({entry["filename"] for entry in episodes}) == len(episodes)
    split_ok = (
        len(episodes) == 39
        and all(re.fullmatch(r'[0-9a-f]{64}', entry['sha256']) for entry in episodes)
        and sha_unique
        and seed_unique
        and name_unique
        and counts == Counter({"train": 28, "validation": 6, "development_stress": 5})
    )

    belief = _read("docs/experiments/evidence/live_belief_20261028/belief03.json")
    contract = belief["api_probe"]["contract"]
    belief_contract = contract["belief_contract"]
    frames = belief_contract["frames"]
    visible = [frame for frame in frames if frame["target_visible"]]
    missing = [frame for frame in frames if not frame["target_visible"]]
    # Aggregate queries minus visible frames cannot prove which frames queried.
    # Reuse the full causal replay; keep runtime-dependent prompt differences.
    try:
        live_replay = verify(belief, strict_text=False)
        live_status = 'PASS' if live_replay['status'] == 'PASS' else 'PARTIAL'
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        live_replay = {'status': 'NOT_MET', 'reason': str(exc)}
        live_status = 'NOT_MET'

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
            "REQUIRES_REVIEW" if _exists("docs/related_work_matrix.md") else "NOT_MET",
            ["docs/related_work_matrix.md"],
            "Jev boundary and an applicable active-tracking reference are recorded; no novelty or SOTA claim is made.",
        ),
        "development_split_and_sha": _criterion(
            "PARTIAL" if split_ok else "NOT_MET",
            [split_path],
            f"{len(episodes)} exported entries: {dict(counts)}; unique sha/seed/filename={sha_unique}/{seed_unique}/{name_unique}. Metadata consistency only; original HDF PASS, path uniqueness and file SHA are not revalidated here.",
        ),
        "independent_final_town_test": _criterion(
            "NOT_MET" if split['independent_town_test_available'] is False else "REQUIRES_REVIEW",
            [split_path],
            "The public manifest declares no independent final-town test. A future positive declaration requires a provenance review, not an automatic PASS.",
        ),
        "metrics_and_fair_comparison_protocol": _criterion(
            "REQUIRES_REVIEW" if _exists("docs/metrics_protocol_v1.md") else "NOT_MET",
            ["docs/metrics_protocol_v1.md"],
            "Fixed-window, failure-inclusive, episode-level comparison and identity/safety limitations are frozen.",
        ),
    }

    d2 = {
        "owned_runtime_and_api_contract": _criterion(
            "REQUIRES_REVIEW" if runtime_ok and origin_ok else "NOT_MET",
            ["docs/experiments/evidence/isolated_carla_20261028/summary.json", "docs/experiments/evidence/live_origin_20261028/summary.json"],
            "Recorded lifecycle flags and 20 acknowledged commands are consistent. This summary check does not independently validate ownership, source hashes or current runtime isolation; inspect the raw probe and report.",
        ),
        "jev_budget_and_cost_estimate": _criterion(
            "REQUIRES_REVIEW" if budget_ok else "NOT_MET",
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
            "REQUIRES_REVIEW" if runtime_ok and origin_ok else "NOT_MET",
            ["docs/experiments/live_origin_20261028.md", "docs/experiments/live_observation_20261028.md"],
            "Global object NED and vehicle-local NED are bridged explicitly; paced and paused clocks, bounded freshness and camera extrinsics are measured.",
        ),
        "horizon_and_action_semantics": _criterion(
            "REQUIRES_REVIEW" if _exists("docs/experiments/causal_belief_20261028.md") else "NOT_MET",
            ["docs/experiments/causal_belief_20261028.md", "docs/metrics_protocol_v1.md"],
            "Five-frame horizon is reported as approximately one second and action frame/expiry semantics are fixed; full high-level closed-loop consequence is later-sprint work.",
        ),
        "hidden_truth_isolation_and_live_belief": _criterion(
            live_status,
            ["docs/experiments/live_belief_20261028.md", "docs/experiments/evidence/live_belief_20261028/belief03.json"],
            f"{len(frames)}/16 recorded windows, {len(visible)} visible and {len(missing)} missing; full replay status={live_replay['status']}. Input is simulator-visible oracle, not RGB perception. Total query count is not a per-frame RPC log.",
        ),
        "formal_tracking_action_execution": _criterion(
            "NOT_AUDITED",
            ["docs/experiments/live_belief_20261028.md"],
            "Missing measured clearance/battery caused all 32 candidate choices to be hard-rejected; no model action was executed.",
        ),
    }

    criteria = {"D1": d1, "D2": d2, "D3": d3}
    evidence_paths = sorted({path for day in criteria.values() for item in day.values() for path in item['evidence']})
    source_commit = None
    # Exported trees have no repository identity; never inherit an enclosing repo.
    if (ROOT / '.git').exists():
        try:
            head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
        except OSError:
            pass  # Git metadata is optional; evidence hashes remain available.
        else:
            if head.returncode == 0:
                source_commit = head.stdout.strip()
    return {
        "schema": "jev_uav_track.d1_d3_acceptance.v2",
        "generated_from": "checked_in_evidence_only",
        "source_commit": source_commit,
        "auditor_sha256": _sha256(Path(__file__)),
        "evidence_sha256": {path: _sha256(ROOT / path) if (ROOT / path).is_file() else None for path in evidence_paths},
        "live_replay": live_replay,
        "criteria": criteria,
        "scope": "Evidence inventory plus metadata/causal replay checks; NOT a complete automatic acceptance certificate. REQUIRES_REVIEW items are document or summary references only.",
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
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
