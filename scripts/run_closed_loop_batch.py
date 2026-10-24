"""Aggregate Mock Jev and utility closed-loop replay over a PASS manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from jev_uav_track.closed_loop import (
    ClosedLoopReplayConfig,
    aggregate_closed_loop_reports,
    run_closed_loop_replay,
)
from jev_uav_track.data import HDF5Episode
from jev_uav_track.jev_client import MockJevClient, UtilityRankingClient
from jev_uav_track.training import LinearOutcomeModel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--end-frame", type=int)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--max-decisions", type=int, default=512)
    parser.add_argument("--desired-range-m", type=float, default=20.0)
    parser.add_argument("--minimum-obstacle-margin-m", type=float, default=3.0)
    parser.add_argument("--fov-half-angle-deg", type=float, default=75.0)
    parser.add_argument("--near-miss-margin-m", type=float, default=0.5)
    parser.add_argument(
        "--near-miss-collision-probability-fraction",
        type=float,
        default=0.5,
        help=(
            "Mark a near miss when predicted collision probability reaches this "
            "fraction of the hard safety threshold."
        ),
    )
    parser.add_argument(
        "--max-learned-feature-zscore",
        type=float,
        help=(
            "Optional support-domain guard for --model. Candidate actions beyond "
            "this standardized feature distance use the transparent rollout."
        ),
    )
    parser.add_argument("--max-episodes", type=int)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    entries = [
        item
        for item in manifest.get("episodes", [])
        if str(item.get("validation_status", "")).upper() == "PASS"
    ]
    if args.max_episodes is not None:
        if args.max_episodes <= 0:
            raise ValueError("max-episodes must be positive")
        entries = entries[: args.max_episodes]
    if not entries:
        raise ValueError("The manifest contains no PASS episodes")

    config = ClosedLoopReplayConfig(
        start_frame=args.start_frame,
        end_frame=args.end_frame,
        stride=args.stride,
        max_decisions=args.max_decisions,
        desired_range_m=args.desired_range_m,
        minimum_obstacle_margin_m=args.minimum_obstacle_margin_m,
        fov_half_angle_deg=args.fov_half_angle_deg,
        near_miss_margin_m=args.near_miss_margin_m,
        near_miss_collision_probability_fraction=args.near_miss_collision_probability_fraction,
        max_learned_feature_zscore=args.max_learned_feature_zscore,
    )
    outcome_model = LinearOutcomeModel.load(args.model) if args.model else None
    reports: Dict[str, list[Dict[str, Any]]] = {"mock": [], "utility": []}
    for entry in entries:
        episode = HDF5Episode.open(entry["path"], load_images=False)
        reports["mock"].append(
            run_closed_loop_replay(
                episode,
                client=MockJevClient(),
                config=config,
                outcome_model=outcome_model,
                model_name="mock-jev-v0",
            )
        )
        reports["utility"].append(
            run_closed_loop_replay(
                episode,
                client=UtilityRankingClient(),
                config=config,
                outcome_model=outcome_model,
                model_name="utility-ranking-v0",
            )
        )

    aggregates = {
        name: aggregate_closed_loop_reports(items)
        for name, items in reports.items()
    }
    report = {
        "manifest": str(args.manifest.resolve()),
        "source_mode": "read_only_hdf5_manifest",
        "evaluation_mode": "kinematic_replay_proxy",
        "config": config.__dict__,
        "episode_count": len(entries),
        "clients": {
            name: {"aggregate": aggregates[name], "episodes": items}
            for name, items in reports.items()
        },
        "note": (
            "This batch intentionally compares offline Mock Jev and utility only. "
            "The real Jev fixed-window smoke test remains in the three-way report; "
            "this script does not issue network requests."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "episode_count": len(entries),
                "clients": aggregates,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
