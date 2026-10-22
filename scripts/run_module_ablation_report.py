"""Run module-level Jev/utility ablations on verified CarlaAir episodes.

The runner keeps the learned rollout model fixed and changes one selector
module at a time.  It only opens HDF5 files in read mode and writes the report
and fitted model under the requested output directory.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Sequence

from jev_uav_track.data import HDF5Episode
from jev_uav_track.evaluation import OfflineEvaluationConfig, evaluate_episode
from jev_uav_track.jev_client import MockJevClient, UtilityRankingClient
from jev_uav_track.training import (
    RolloutTrainingConfig,
    feature_mask_for_ablation,
    fit_outcome_model_cross_episode,
)


def _mean(records: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [
        float(record[key])
        for record in records
        if record.get(key) is not None and math.isfinite(float(record[key]))
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _aggregate(reports: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    records = [record for report in reports for record in report.get("records", [])]
    choice_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for report in reports:
        choice_counts.update(report.get("choice_counts", {}))
        reason_counts.update(record.get("reason", "unknown") for record in report.get("records", []))
    skipped = sum(int(report.get("metrics", {}).get("skipped_frames", 0)) for report in reports)
    if not records:
        return {
            "evaluated_frames": 0,
            "skipped_frames": skipped,
            "fallback_rate": None,
            "safe_action_rate": 0.0,
            "unsafe_selected_rate": None,
            "choice_counts": dict(choice_counts),
            "reason_counts": dict(reason_counts),
        }
    return {
        "evaluated_frames": len(records),
        "skipped_frames": skipped,
        "fallback_rate": sum(bool(record["fallback"]) for record in records) / len(records),
        "safe_action_rate": len(records) / (len(records) + skipped),
        "unsafe_selected_rate": sum(bool(record["selected_unsafe"]) for record in records)
        / len(records),
        "mean_selected_utility": _mean(records, "selected_utility"),
        "mean_oracle_regret": _mean(records, "utility_regret"),
        "mean_predicted_visibility": _mean(records, "predicted_visibility"),
        "mean_actual_visibility": sum(bool(record["target_visible"]) for record in records)
        / len(records),
        "mean_actual_range_m": _mean(records, "target_range_m"),
        "mean_predicted_range_error_m": _mean(records, "predicted_range_error_m"),
        "mean_identity_confidence": _mean(records, "identity_confidence"),
        "mean_selected_collision_probability": _mean(
            records, "selected_collision_probability"
        ),
        "mean_selected_obstacle_margin_m": _mean(records, "selected_obstacle_margin_m"),
        "choice_counts": dict(choice_counts),
        "reason_counts": dict(reason_counts),
    }


def _environment_name(episode: HDF5Episode) -> str:
    return episode.path.stem.rsplit("_seed", 1)[0]


def _variant_spec(
    name: str,
    temperature: float,
) -> tuple[Callable[[], Any], Dict[str, bool]]:
    if name == "jev":
        return (
            lambda: MockJevClient(temperature=temperature, identity_weight=0.8),
            {
                "enable_safety_gate": True,
                "use_short_history": True,
                "use_identity_confidence": True,
            },
        )
    if name == "utility":
        return (
            UtilityRankingClient,
            {
                "enable_safety_gate": True,
                "use_short_history": True,
                "use_identity_confidence": True,
            },
        )
    if name == "no_safety_gate":
        return (
            lambda: MockJevClient(temperature=temperature, identity_weight=0.8),
            {
                "enable_safety_gate": False,
                "use_short_history": True,
                "use_identity_confidence": True,
            },
        )
    if name == "no_short_history":
        return (
            lambda: MockJevClient(temperature=temperature, identity_weight=0.8),
            {
                "enable_safety_gate": True,
                "use_short_history": False,
                "use_identity_confidence": True,
            },
        )
    if name == "no_identity_confidence":
        return (
            lambda: MockJevClient(temperature=temperature, identity_weight=0.8),
            {
                "enable_safety_gate": True,
                "use_short_history": True,
                "use_identity_confidence": False,
            },
        )
    raise ValueError(f"Unknown module ablation: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-episode", action="append", required=True)
    parser.add_argument("--validation-episode", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--horizon-frames", type=int, default=5)
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--max-samples-per-episode", type=int, default=48)
    parser.add_argument("--eval-max-frames", type=int, default=12)
    parser.add_argument("--eval-stride", type=int, default=25)
    parser.add_argument("--desired-range-m", type=float, default=20.0)
    parser.add_argument(
        "--minimum-obstacle-margin-m",
        type=float,
        default=0.0,
        help="Use 7.8 for the optional gate-stress run; 0.0 is the nominal config.",
    )
    parser.add_argument("--maximum-collision-probability", type=float, default=0.05)
    parser.add_argument("--mock-temperature", type=float, default=0.35)
    parser.add_argument(
        "--variant",
        action="append",
        choices=["jev", "utility", "no_safety_gate", "no_short_history", "no_identity_confidence"],
        help="Repeat to select variants; defaults to all module variants",
    )
    args = parser.parse_args()

    train_episodes = [HDF5Episode.open(path, load_images=False) for path in args.train_episode]
    validation_episodes = [
        HDF5Episode.open(path, load_images=False) for path in args.validation_episode
    ]
    names = args.variant or [
        "jev",
        "utility",
        "no_safety_gate",
        "no_short_history",
        "no_identity_confidence",
    ]
    training_config = RolloutTrainingConfig(
        horizon_frames=args.horizon_frames,
        stride=args.stride,
        max_samples=args.max_samples_per_episode,
    )
    model, fit_metrics = fit_outcome_model_cross_episode(
        train_episodes,
        validation_episodes,
        config=training_config,
        max_samples_per_episode=args.max_samples_per_episode,
        feature_mask=feature_mask_for_ablation("full"),
    )
    model_path = args.output.parent / "models" / "module_full.npz"
    model.save(model_path)

    variant_results = []
    for name in names:
        client_factory, flags = _variant_spec(name, args.mock_temperature)
        evaluation_config = OfflineEvaluationConfig(
            max_frames=args.eval_max_frames,
            stride=args.eval_stride,
            desired_range_m=args.desired_range_m,
            minimum_obstacle_margin_m=args.minimum_obstacle_margin_m,
            maximum_collision_probability=args.maximum_collision_probability,
            mock_temperature=args.mock_temperature,
            **flags,
        )
        raw_reports = [
            evaluate_episode(
                episode,
                config=evaluation_config,
                client=client_factory(),
                outcome_model=model,
            )
            for episode in validation_episodes
        ]
        variant_results.append(
            {
                "name": name,
                "backend": raw_reports[0].get("backend") if raw_reports else None,
                "module_flags": flags,
                "evaluation_config": evaluation_config.__dict__,
                "aggregate_metrics": _aggregate(raw_reports),
                "per_environment": [
                    {
                        "episode": str(episode.path),
                        "environment": _environment_name(episode),
                        "metrics": report["metrics"],
                        "choice_counts": report["choice_counts"],
                    }
                    for episode, report in zip(validation_episodes, raw_reports)
                ],
            }
        )

    report = {
        "train_episodes": [str(episode.path) for episode in train_episodes],
        "validation_episodes": [str(episode.path) for episode in validation_episodes],
        "validation_statuses": {
            str(episode.path): episode.metadata.get("validation_status", "unreported")
            for episode in train_episodes + validation_episodes
        },
        "training_config": training_config.__dict__,
        "fit_metrics": fit_metrics,
        "model_path": str(model_path),
        "variants": variant_results,
        "note": (
            "The Jev row is an offline identity-aware MockJev proxy; the utility row is a "
            "deterministic utility ranking client. All rows use the same fixed cross-episode "
            "outcome model and verified validation episodes. No network request or source HDF5 "
            "write is performed. Run again with minimum_obstacle_margin_m=7.8 for gate stress."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
