"""Build a read-only manifest for explicitly selected difficult PASS episodes.

The command never scans or mutates a live collection directory.  Callers pass
the exact HDF5 paths that have already been audited.  In addition to the basic
PASS/schema checks, the manifest records out-of-frame, occlusion, actor-ID and
target-acceleration signals so later experiments cannot hide the coverage
limits of a selected subset.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Sequence

from jev_uav_track.data import HDF5Episode


def _fraction(values: Sequence[Any]) -> float:
    return sum(bool(value) for value in values) / len(values) if len(values) else 0.0


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _motion_stats(episode: HDF5Episode) -> Dict[str, float]:
    speeds = [
        math.sqrt(sum(float(value) ** 2 for value in row))
        for row in episode.target_velocities
    ]
    accelerations: list[float] = []
    for index in range(1, episode.frame_count):
        dt = float(episode.image_timestamps[index] - episode.image_timestamps[index - 1])
        if dt <= 1e-9:
            continue
        previous = episode.target_velocities[index - 1]
        current = episode.target_velocities[index]
        acceleration = math.sqrt(
            sum(
                (float(current[axis]) - float(previous[axis])) ** 2
                for axis in range(3)
            )
        ) / dt
        if math.isfinite(acceleration):
            accelerations.append(acceleration)
    return {
        "target_speed_p95_mps": _percentile(speeds, 95.0),
        "target_acceleration_p95_mps2": _percentile(accelerations, 95.0),
    }


def _episode_entry(
    path: str | Path,
    expected_schema: str | None,
    acceleration_threshold: float,
) -> Dict[str, Any]:
    source = Path(path).expanduser().resolve()
    episode = HDF5Episode.open(source, load_images=False)
    validation_status = str(episode.metadata.get("validation_status", "")).upper()
    if validation_status != "PASS":
        raise ValueError(
            f"Only validation_status=PASS episodes are accepted: {source} has "
            f"{validation_status or 'unreported'}"
        )
    schema = str(episode.metadata.get("schema", "unknown"))
    if expected_schema and schema != expected_schema:
        raise ValueError(
            f"Schema mismatch for {source}: expected {expected_schema}, got {schema}"
        )
    actor_ids = [int(value) for value in episode.target_actor_ids]
    known_actor_ids = sorted({value for value in actor_ids if value >= 0})
    motion = _motion_stats(episode)
    signals = {
        "visibility_drop": _fraction(episode.target_visible) < 1.0,
        "out_of_frame": _fraction(episode.target_in_frame) < 1.0,
        "occlusion": _fraction(episode.target_occluded) > 0.0,
        "identity_change": len(known_actor_ids) > 1
        or any(left != right for left, right in zip(actor_ids, actor_ids[1:])),
        "high_acceleration": motion["target_acceleration_p95_mps2"] >= acceleration_threshold,
    }
    if not any(signals.values()):
        raise ValueError(
            f"Episode has no difficult coverage signal under the configured threshold: {source}"
        )
    stat = source.stat()
    return {
        "path": str(source),
        "schema": schema,
        "validation_status": validation_status,
        "frame_count": int(episode.frame_count),
        "image_height": int(episode.image_height),
        "image_width": int(episode.image_width),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "visibility_observed": bool(episode.metadata.get("visibility_observed", False)),
        "target_visible_fraction": _fraction(episode.target_visible),
        "target_in_frame_fraction": _fraction(episode.target_in_frame),
        "target_occluded_fraction": _fraction(episode.target_occluded),
        "target_actor_ids": known_actor_ids,
        **motion,
        "difficult_signals": signals,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-schema")
    parser.add_argument("--acceleration-threshold-mps2", type=float, default=3.0)
    args = parser.parse_args()
    if args.acceleration_threshold_mps2 < 0.0:
        raise ValueError("acceleration threshold cannot be negative")

    paths = [Path(item).expanduser().resolve() for item in args.episode]
    if len(set(paths)) != len(paths):
        raise ValueError("Episode paths must be unique")
    entries = [
        _episode_entry(path, args.expected_schema, args.acceleration_threshold_mps2)
        for path in paths
    ]
    schemas = sorted({entry["schema"] for entry in entries})
    signal_counts = {
        signal: sum(bool(entry["difficult_signals"][signal]) for entry in entries)
        for signal in entries[0]["difficult_signals"]
    }
    report = {
        "manifest_version": 1,
        "source_mode": "read_only_hdf5_adapter",
        "selection_rule": {
            "explicit_episode_paths_only": True,
            "acceleration_threshold_mps2": args.acceleration_threshold_mps2,
        },
        "episode_count": len(entries),
        "schemas": schemas,
        "all_validation_status_pass": all(
            entry["validation_status"] == "PASS" for entry in entries
        ),
        "signal_counts": signal_counts,
        "episodes": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "episode_count": len(entries),
                "schemas": schemas,
                "all_validation_status_pass": report["all_validation_status_pass"],
                "signal_counts": signal_counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
