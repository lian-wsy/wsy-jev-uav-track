"""Compare timestamp-aligned models on recorded actions, not counterfactuals."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from jev_uav_track.data import HDF5Episode
from jev_uav_track.training import (
    OUTPUT_NAMES, RolloutTrainingConfig, build_training_dataset_from_episodes,
    fit_outcome_model_cross_episode, LinearOutcomeModel,
)
from jev_uav_track.learned_rollout import LearnedOutcomeRolloutModel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples-per-episode", type=int, default=24)
    args = parser.parse_args()
    manifest = json.loads(args.split.read_text(encoding="utf-8"))
    groups = {}
    for group in ("train", "validation"):
        entries = [e for e in manifest["episodes"] if e["split"] == group]
        if not entries or any(e["validation_status"] != "PASS" for e in entries):
            raise ValueError("Nonempty PASS train/validation groups required")
        groups[group] = [HDF5Episode.open(e["path"], load_images=False) for e in entries]
    args.output.mkdir(parents=True, exist_ok=True)
    results = {}
    for mode in ("absolute", "baseline_residual"):
        config = RolloutTrainingConfig(
            max_samples=args.samples_per_episode, target_mode=mode, rollout_horizon_s=1.0,
        )
        model, metrics = fit_outcome_model_cross_episode(groups["train"], groups["validation"], config)
        path = args.output / f"{mode}.npz"
        model.save(path)
        reloaded = LinearOutcomeModel.load(path)
        inference = LearnedOutcomeRolloutModel(reloaded)
        dataset = build_training_dataset_from_episodes(groups["validation"], config)
        results[mode] = {
            "metrics": metrics,
            "loaded_inference_horizon_s": inference.baseline.config.horizon_s,
        }
        if mode == "baseline_residual":
            # Residual labels equal truth minus baseline. Zero residual is the
            # transparent model; its RMSE is therefore the label RMS.
            results["transparent"] = {
                "validation_rmse": dict(zip(
                    OUTPUT_NAMES, np.sqrt(np.mean(dataset.labels ** 2, axis=0)).tolist()
                )),
            }
    report = {
        "protocol": "recorded_action_calibration_only",
        "state_source": "oracle_state_with_backward_only_velocity_v2",
        "split": str(args.split),
        "samples_per_episode": args.samples_per_episode,
        "results": results,
        "limitations": [
            "Ground-truth HDF5 states; not a perception or closed-loop benchmark.",
            "Recorded camera velocity is an action proxy; no counterfactual action labels.",
            "No final test, live simulator or real Jev calls.",
        ],
    }
    (args.output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        name: value.get("validation_rmse", value.get("metrics", {}).get("validation_rmse"))
        for name, value in results.items()
    }, indent=2))


if __name__ == "__main__":
    main()
