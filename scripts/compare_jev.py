"""Compare Mock Jev with a real Jev client on a few recorded states."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict

from jev_uav_track.data import HDF5Episode
from jev_uav_track.evaluation import (
    OfflineEvaluationConfig,
    compare_jev_reports,
    evaluate_episode,
)
from jev_uav_track.jev_client import MockJevClient, TypeSafeJevClient
from jev_uav_track.training import LinearOutcomeModel


_ALLOWED_ENV_KEYS = {"JEV_API_KEY", "JEV_ENDPOINT", "JEV_MODEL"}


def _load_env_file(path: Path) -> None:
    """Load only Jev connection variables without printing their values."""

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name not in _ALLOWED_ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[name] = value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--max-frames", type=int, default=4)
    parser.add_argument("--stride", type=int, default=75)
    parser.add_argument("--desired-range-m", type=float, default=20.0)
    parser.add_argument("--timeout-s", type=float, default=8.0)
    parser.add_argument("--mock-temperature", type=float, default=0.35)
    args = parser.parse_args()

    if args.env_file.is_file():
        _load_env_file(args.env_file)
    episode = HDF5Episode.open(args.episode, load_images=False)
    model = LinearOutcomeModel.load(args.model) if args.model else None
    config = OfflineEvaluationConfig(
        max_frames=args.max_frames,
        stride=args.stride,
        desired_range_m=args.desired_range_m,
        mock_temperature=args.mock_temperature,
    )
    mock_report = evaluate_episode(
        episode,
        config=config,
        client=MockJevClient(temperature=args.mock_temperature),
        outcome_model=model,
    )
    real_client = TypeSafeJevClient.from_environment(timeout_s=args.timeout_s)
    real_report = evaluate_episode(
        episode,
        config=config,
        client=real_client,
        outcome_model=model,
    )
    report: Dict[str, object] = {
        "episode": str(episode.path),
        "schema": episode.metadata.get("schema", "unknown"),
        "validation_status": episode.metadata.get("validation_status", "unreported"),
        "visibility_observed": bool(episode.metadata.get("visibility_observed", False)),
        "config": config.__dict__,
        "mock": mock_report,
        "real": real_report,
        "comparison": compare_jev_reports(mock_report, real_report),
        "note": (
            "The real client receives state and candidate outcome JSON only; no RGB is sent. "
            "API key values are never included in this report."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    comparison = report["comparison"]
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "mock_metrics": mock_report["metrics"],
                "real_metrics": real_report["metrics"],
                "comparison": comparison,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
