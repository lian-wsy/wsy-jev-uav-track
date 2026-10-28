"""Apply frozen 10 s / 0.5 s recovery protocol to existing development replays.

No HDF5 reads, model selection, simulator or API requests. Reference windows
come only from recorded source annotations, independent of selector visibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean

from jev_uav_track.event_metrics import reference_loss_events, score_reacquisition


def evaluate(reference_batch, batches):
    references = {}
    annotations = {}
    def annotation(record):
        return tuple(record[k] for k in ("annotated_visible", "annotated_in_frame",
                                         "annotated_occluded", "ground_truth_actor_id"))
    for episode in reference_batch["clients"]["mock"]["episodes"]:
        records = episode["records"]
        times = [r["timestamp_s"] for r in records]
        visible = [r["annotated_visible"] and r["annotated_in_frame"] and not r["annotated_occluded"]
                   for r in records]
        references[episode["episode"]] = (reference_loss_events(times, visible), times[-1])
        annotations[episode["episode"]] = {r["timestamp_s"]: annotation(r) for r in records}
    results = {}
    for label, batch in batches.items():
        for client, data in batch["clients"].items():
            episodes = data["episodes"]
            if {e["episode"] for e in episodes} != set(references) or len(episodes) != len(references):
                raise ValueError("Methods must cover the same unique reference episodes")
            rows = []
            for e in episodes:
                starts, end = references[e["episode"]]
                records = e["records"]
                source = annotations[e["episode"]]
                if any(source.get(r["timestamp_s"]) != annotation(r) for r in records):
                    raise ValueError("Method source annotation/time differs from reference")
                # Known visible oracle ID is for evaluation, not selector input.
                correct = [r["proxy_visible"] and r["ground_truth_actor_id"] >= 0
                           and r["tracked_actor_id"] == r["ground_truth_actor_id"] for r in records]
                score = score_reacquisition([r["timestamp_s"] for r in records], correct,
                                            event_starts=starts, reference_end_s=end)
                rows.append({"episode": Path(e["episode"]).name, **score})
            eligible = [r for r in rows if r["eligible_events"]]
            results[f"{label}+{client}"] = {
                "episodes": rows,
                "eligible_events": sum(r["eligible_events"] for r in rows),
                "ineligible_events": sum(r["ineligible_events"] for r in rows),
                "episode_equal_recovery_rate": mean(r["recovery_rate"] for r in eligible) if eligible else None,
                "episode_equal_restricted_mean_s": mean(r["restricted_mean_s"] for r in eligible) if eligible else None,
            }
    return {"protocol": "reference_window_confirmation_v1", "window_s": 10, "stable_s": 0.5,
            "max_gap_s": 0.25, "mode": "development_kinematic_proxy_only",
            "reference_rule": "source annotation loss onset; chronological nonoverlapping 10s windows",
            "methods": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transparent", type=Path, required=True)
    parser.add_argument("--residual", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    batches = {name: json.loads(path.read_text(encoding="utf-8"))
               for name, path in (("transparent", args.transparent), ("residual", args.residual))}
    report = evaluate(batches["transparent"], batches)
    report["input_sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (args.transparent, args.residual)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({name: {k: v for k, v in result.items() if k != "episodes"}
                      for name, result in report["methods"].items()}, indent=2))


if __name__ == "__main__":
    main()
