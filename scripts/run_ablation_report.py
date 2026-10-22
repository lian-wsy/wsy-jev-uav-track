"""Run cross-episode ablations and aggregate offline tracking metrics."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from jev_uav_track.data import HDF5Episode
from jev_uav_track.evaluation import OfflineEvaluationConfig, evaluate_episode
from jev_uav_track.training import (
    ABLATION_DISABLED_FEATURES,
    LinearOutcomeModel,
    RolloutTrainingConfig,
    feature_mask_for_ablation,
    fit_outcome_model_cross_episode,
)


def _mean(records: Sequence[Mapping[str, Any]], key: str) -> float | None:
    if not records:
        return None
    return sum(float(record[key]) for record in records) / len(records)


def _aggregate_episode_reports(reports: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    records = [record for report in reports for record in report.get("records", [])]
    metrics = [report.get("metrics", {}) for report in reports]
    choice_counts: Counter[str] = Counter()
    for report in reports:
        choice_counts.update(report.get("choice_counts", {}))
    if not records:
        return {
            "evaluated_frames": 0,
            "skipped_frames": sum(int(item.get("skipped_frames", 0)) for item in metrics),
            "fallback_rate": None,
            "safe_action_rate": 0.0,
            "choice_counts": dict(choice_counts),
        }
    skipped = sum(int(item.get("skipped_frames", 0)) for item in metrics)
    return {
        "evaluated_frames": len(records),
        "skipped_frames": skipped,
        "fallback_rate": sum(bool(record["fallback"]) for record in records) / len(records),
        "safe_action_rate": len(records) / (len(records) + skipped),
        "mean_selected_utility": _mean(records, "selected_utility"),
        "mean_oracle_regret": _mean(records, "utility_regret"),
        "mean_predicted_visibility": _mean(records, "predicted_visibility"),
        "mean_actual_visibility": sum(bool(record["target_visible"]) for record in records)
        / len(records),
        "mean_actual_range_m": _mean(records, "target_range_m"),
        "mean_predicted_range_error_m": _mean(records, "predicted_range_error_m"),
        "choice_counts": dict(choice_counts),
    }


def _environment_name(episode: HDF5Episode) -> str:
    stem = episode.path.stem
    return stem.rsplit("_seed", 1)[0]


def _run_ablation(
    name: str,
    train_episodes: Sequence[HDF5Episode],
    validation_episodes: Sequence[HDF5Episode],
    training_config: RolloutTrainingConfig,
    max_samples_per_episode: int,
    evaluation_config: OfflineEvaluationConfig,
    model_dir: Path,
) -> Dict[str, Any]:
    model: LinearOutcomeModel | None
    fit_metrics: Dict[str, Any] | None
    model_path: Path | None = None
    if name == "kinematic_baseline":
        model = None
        fit_metrics = None
    else:
        model, fit_metrics = fit_outcome_model_cross_episode(
            train_episodes,
            validation_episodes,
            config=training_config,
            max_samples_per_episode=max_samples_per_episode,
            feature_mask=feature_mask_for_ablation(name),
        )
        model_path = model_dir / f"{name}.npz"
        model.save(model_path)

    raw_episode_reports = []
    episode_reports = []
    for episode in validation_episodes:
        report = evaluate_episode(
            episode,
            config=evaluation_config,
            outcome_model=model,
        )
        raw_episode_reports.append(report)
        episode_reports.append(
            {
                "episode": str(episode.path),
                "environment": _environment_name(episode),
                "metrics": report["metrics"],
                "choice_counts": report["choice_counts"],
            }
        )

    result: Dict[str, Any] = {
        "name": name,
        "feature_mask": (
            None
            if name == "kinematic_baseline"
            else list(feature_mask_for_ablation(name))
        ),
        "fit_metrics": fit_metrics,
        "model_path": str(model_path) if model_path else None,
        "aggregate_metrics": _aggregate_episode_reports(raw_episode_reports),
        "per_environment": episode_reports,
    }
    return result


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
    parser.add_argument(
        "--ablation",
        action="append",
        choices=[*sorted(ABLATION_DISABLED_FEATURES), "kinematic_baseline"],
        help="Repeat to select ablations; defaults to all ablations plus baseline",
    )
    args = parser.parse_args()

    train_episodes = [HDF5Episode.open(path, load_images=False) for path in args.train_episode]
    validation_episodes = [
        HDF5Episode.open(path, load_images=False) for path in args.validation_episode
    ]
    names = args.ablation or [
        "kinematic_baseline",
        "full",
        "no_motion",
        "no_action",
        "no_visibility",
    ]
    training_config = RolloutTrainingConfig(
        horizon_frames=args.horizon_frames,
        stride=args.stride,
        max_samples=args.max_samples_per_episode,
    )
    evaluation_config = OfflineEvaluationConfig(
        max_frames=args.eval_max_frames,
        stride=args.eval_stride,
    )
    model_dir = args.output.parent / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    results = [
        _run_ablation(
            name,
            train_episodes,
            validation_episodes,
            training_config,
            args.max_samples_per_episode,
            evaluation_config,
            model_dir,
        )
        for name in names
    ]
    report = {
        "train_episodes": [str(episode.path) for episode in train_episodes],
        "validation_episodes": [str(episode.path) for episode in validation_episodes],
        "validation_statuses": {
            str(episode.path): episode.metadata.get("validation_status", "unreported")
            for episode in train_episodes + validation_episodes
        },
        "training_config": training_config.__dict__,
        "evaluation_config": evaluation_config.__dict__,
        "ablation_results": results,
        "note": (
            "All metrics are offline Mock Jev evaluations. The outcome model uses observed "
            "camera velocity as an action feature and does not provide counterfactual dynamics."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
