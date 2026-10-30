"""Train a small state-conditioned rollout outcome calibrator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jev_uav_track.training import (
    TARGET_MODES,
    INPUT_MODES,
    RolloutTrainingConfig,
    build_training_dataset,
    fit_outcome_model,
    load_development_episode,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Output .npz model path")
    parser.add_argument("--horizon-frames", type=int, default=5)
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--max-samples", type=int, default=128)
    parser.add_argument("--desired-range-m", type=float, default=20.0)
    parser.add_argument("--target-mode", choices=TARGET_MODES, default="absolute")
    parser.add_argument("--input-mode", choices=INPUT_MODES, default="oracle")
    args = parser.parse_args()

    episode = load_development_episode(args.episode)
    config = RolloutTrainingConfig(
        horizon_frames=args.horizon_frames,
        stride=args.stride,
        max_samples=args.max_samples,
        desired_range_m=args.desired_range_m,
        target_mode=args.target_mode,
        input_mode=args.input_mode,
    )
    dataset = build_training_dataset(episode, config)
    model, metrics = fit_outcome_model(dataset, config)
    model.save(args.output)

    report = {
        "episode": str(episode.path),
        "schema": episode.metadata.get("schema", "unknown"),
        "validation_status": episode.metadata.get("validation_status", "unreported"),
        "frame_count": episode.frame_count,
        "model_path": str(args.output.resolve()),
        "metrics": metrics,
        "note": "Calibration model uses observed camera velocity as action feature; it is not counterfactual dynamics coverage.",
    }
    report_path = args.output.with_suffix(".json")
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
