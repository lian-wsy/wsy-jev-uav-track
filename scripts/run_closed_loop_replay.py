"""Compare Mock Jev, utility ranking, and real Jev in one closed-loop replay."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict

from jev_uav_track.closed_loop import (
    ClosedLoopReplayConfig,
    compare_closed_loop_reports,
    run_closed_loop_replay,
)
from jev_uav_track.data import HDF5Episode
from jev_uav_track.jev_client import (
    JevProtocolError,
    MockJevClient,
    TypeSafeJevClient,
    UtilityRankingClient,
)
from jev_uav_track.training import LinearOutcomeModel


_ALLOWED_ENV_KEYS = {"JEV_API_KEY", "JEV_ENDPOINT", "JEV_MODEL"}


def _load_env_file(path: Path) -> None:
    """Load only Jev connection variables; never print their values."""

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
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--end-frame", type=int)
    parser.add_argument("--stride", type=int, default=8)
    parser.add_argument("--max-decisions", type=int, default=64)
    parser.add_argument("--timeout-s", type=float, default=8.0)
    parser.add_argument("--mock-temperature", type=float, default=0.35)
    parser.add_argument("--desired-range-m", type=float, default=20.0)
    parser.add_argument("--minimum-obstacle-margin-m", type=float, default=3.0)
    parser.add_argument("--fov-half-angle-deg", type=float, default=75.0)
    args = parser.parse_args()

    if args.env_file.is_file():
        _load_env_file(args.env_file)
    episode = HDF5Episode.open(args.episode, load_images=False)
    outcome_model = LinearOutcomeModel.load(args.model) if args.model else None
    config = ClosedLoopReplayConfig(
        start_frame=args.start_frame,
        end_frame=args.end_frame,
        stride=args.stride,
        max_decisions=args.max_decisions,
        desired_range_m=args.desired_range_m,
        minimum_obstacle_margin_m=args.minimum_obstacle_margin_m,
        fov_half_angle_deg=args.fov_half_angle_deg,
    )

    mock_report = run_closed_loop_replay(
        episode,
        client=MockJevClient(temperature=args.mock_temperature),
        config=config,
        outcome_model=outcome_model,
        model_name="mock-jev-v0",
    )
    utility_report = run_closed_loop_replay(
        episode,
        client=UtilityRankingClient(),
        config=config,
        outcome_model=outcome_model,
        model_name="utility-ranking-v0",
    )

    real_report: Dict[str, Any] | None = None
    real_error: str | None = None
    try:
        real_client = TypeSafeJevClient.from_environment(timeout_s=args.timeout_s)
        real_report = run_closed_loop_replay(
            episode,
            client=real_client,
            config=config,
            outcome_model=outcome_model,
            model_name=os.getenv("JEV_MODEL", "jev-latest"),
        )
    except (JevProtocolError, OSError, TimeoutError, ValueError) as exc:
        real_error = f"{type(exc).__name__}: {exc}"

    clients: Dict[str, Dict[str, Any]] = {
        "mock": mock_report,
        "utility": utility_report,
    }
    if real_report is not None:
        clients["real"] = real_report
    report: Dict[str, Any] = {
        "episode": str(episode.path),
        "schema": episode.metadata.get("schema", "unknown"),
        "validation_status": episode.metadata.get("validation_status", "unreported"),
        "config": config.__dict__,
        "clients": clients,
        # Keep convenient top-level aliases for report readers.
        "mock": mock_report,
        "utility": utility_report,
        "real": real_report,
        "real_error": real_error,
        "comparisons": compare_closed_loop_reports(clients),
        "note": (
            "All clients replay the same fixed event window. The real client receives "
            "belief state and candidate outcome JSON only; no RGB or API key is stored. "
            "The closed-loop state is a kinematic replay proxy, not a CARLA or flight "
            "dynamics result."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "real_error": real_error,
                "clients": {
                    name: {
                        "metrics": item["metrics"],
                        "identity_coverage": item["identity_coverage"],
                    }
                    for name, item in clients.items()
                },
                "comparisons": report["comparisons"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
