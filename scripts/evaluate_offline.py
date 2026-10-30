"""Run the offline Jev-style selector over a verified HDF5 episode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jev_uav_track.data import HDF5Episode
from jev_uav_track.evaluation import OfflineEvaluationConfig, evaluate_episode
from jev_uav_track.training import LinearOutcomeModel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument('--input-mode', choices=['auto', 'oracle', 'causal_visible'], default='auto',
                        help='auto follows model metadata; without a model defaults to legacy oracle')
    parser.add_argument("--max-frames", type=int, default=32)
    parser.add_argument("--stride", type=int, default=5)
    parser.add_argument("--desired-range-m", type=float, default=20.0)
    args = parser.parse_args()

    episode = HDF5Episode.open(args.episode, load_images=False)
    model = LinearOutcomeModel.load(args.model) if args.model else None
    report = evaluate_episode(
        episode,
        OfflineEvaluationConfig(
            input_mode=args.input_mode,
            max_frames=args.max_frames,
            stride=args.stride,
            desired_range_m=args.desired_range_m,
        ),
        outcome_model=model,
    )
    destination = args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
