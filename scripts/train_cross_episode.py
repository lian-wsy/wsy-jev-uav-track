"""Train and validate the rollout outcome calibrator across disjoint episodes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jev_uav_track.data import HDF5Episode
from jev_uav_track.training import (
    RolloutTrainingConfig,
    fit_outcome_model_cross_episode,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train-episode",
        action="append",
        required=True,
        help="Verified PASS HDF5 episode; repeat for multiple train episodes",
    )
    parser.add_argument(
        "--validation-episode",
        action="append",
        required=True,
        help="Verified PASS HDF5 episode; repeat for multiple validation episodes",
    )
    parser.add_argument("--output", type=Path, required=True, help="Output .npz model path")
    parser.add_argument("--horizon-frames", type=int, default=5)
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--max-samples-per-episode", type=int, default=64)
    parser.add_argument("--desired-range-m", type=float, default=20.0)
    args = parser.parse_args()

    train_episodes = [HDF5Episode.open(path, load_images=False) for path in args.train_episode]
    validation_episodes = [
        HDF5Episode.open(path, load_images=False) for path in args.validation_episode
    ]
    config = RolloutTrainingConfig(
        horizon_frames=args.horizon_frames,
        stride=args.stride,
        max_samples=args.max_samples_per_episode,
        desired_range_m=args.desired_range_m,
    )
    model, metrics = fit_outcome_model_cross_episode(
        train_episodes,
        validation_episodes,
        config=config,
        max_samples_per_episode=args.max_samples_per_episode,
    )
    model.save(args.output)
    report = {
        "model_path": str(args.output.resolve()),
        "train_episodes": [str(episode.path) for episode in train_episodes],
        "validation_episodes": [str(episode.path) for episode in validation_episodes],
        "validation_statuses": {
            str(episode.path): episode.metadata.get("validation_status", "unreported")
            for episode in train_episodes + validation_episodes
        },
        "metrics": metrics,
        "note": (
            "Cross-episode fit uses observed camera velocity as an action feature; "
            "it is an outcome calibration baseline, not counterfactual dynamics coverage."
        ),
    }
    report_path = args.output.with_suffix(".json")
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
