"""Audit verified trimmed HDF5 coverage without touching collection workers.

This command is intentionally read-only.  It scans one explicit trimmed-data
root, opens each HDF5 in read-only mode, and writes only the requested JSON
report.  It must not be pointed at a live upload staging directory.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Sequence

from jev_uav_track.data import HDF5Episode


def _fraction(values: Iterable[Any]) -> float:
    values = list(values)
    return sum(bool(value) for value in values) / len(values) if values else 0.0


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = 0.95 * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _acceleration_p95(episode: HDF5Episode) -> float:
    values: list[float] = []
    for index in range(1, episode.frame_count):
        dt = float(episode.image_timestamps[index] - episode.image_timestamps[index - 1])
        if dt <= 1e-9:
            continue
        previous = episode.target_velocities[index - 1]
        current = episode.target_velocities[index]
        value = math.sqrt(
            sum(
                (float(current[axis]) - float(previous[axis])) ** 2
                for axis in range(3)
            )
        ) / dt
        if math.isfinite(value):
            values.append(value)
    return _p95(values)


def _entry(path: Path, acceleration_threshold: float) -> Dict[str, Any]:
    episode = HDF5Episode.open(path, load_images=False)
    status = str(episode.metadata.get("validation_status", "UNREPORTED")).upper()
    actor_ids = [int(value) for value in episode.target_actor_ids]
    known_ids = sorted({value for value in actor_ids if value >= 0})
    known_sequence = [value for value in actor_ids if value >= 0]
    identity_change = len(known_ids) > 1 or any(
        left != right for left, right in zip(known_sequence, known_sequence[1:])
    )
    acceleration_p95 = _acceleration_p95(episode)
    occluded_frames = [
        index for index, value in enumerate(episode.target_occluded) if bool(value)
    ]
    entry: Dict[str, Any] = {
        "path": str(path),
        "schema": str(episode.metadata.get("schema", "unknown")),
        "validation_status": status,
        "frame_count": int(episode.frame_count),
        "visibility_observed": bool(episode.metadata.get("visibility_observed", False)),
        "target_visible_fraction": _fraction(episode.target_visible),
        "target_in_frame_fraction": _fraction(episode.target_in_frame),
        "target_occluded_fraction": _fraction(episode.target_occluded),
        "target_actor_ids": known_ids,
        "occluded_frame_indices": occluded_frames,
        "target_acceleration_p95_mps2": acceleration_p95,
        "difficult_signals": {
            "occlusion": bool(occluded_frames),
            "out_of_frame": _fraction(episode.target_in_frame) < 1.0,
            "visibility_drop": _fraction(episode.target_visible) < 1.0,
            "identity_change": identity_change,
            "high_acceleration": acceleration_p95 >= acceleration_threshold,
        },
    }
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acceleration-threshold-mps2", type=float, default=3.0)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    paths = sorted(root.rglob("*.hdf5"))
    entries: list[Dict[str, Any]] = []
    errors: list[Dict[str, str]] = []
    status_counts: Counter[str] = Counter()
    for path in paths:
        try:
            item = _entry(path, args.acceleration_threshold_mps2)
        except Exception as exc:  # Keep one corrupt file from hiding the rest.
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        entries.append(item)
        status_counts[item["validation_status"]] += 1

    pass_entries = [item for item in entries if item["validation_status"] == "PASS"]
    difficult_counts = {
        signal: sum(bool(item["difficult_signals"][signal]) for item in pass_entries)
        for signal in (
            "occlusion",
            "out_of_frame",
            "visibility_drop",
            "identity_change",
            "high_acceleration",
        )
    }
    report: Dict[str, Any] = {
        "report_version": 1,
        "source_mode": "read_only_trimmed_hdf5_root",
        "root": str(root),
        "file_count": len(paths),
        "audited_file_count": len(entries),
        "read_error_count": len(errors),
        "read_errors": errors,
        "status_counts": dict(status_counts),
        "pass_count": len(pass_entries),
        "pass_difficult_signal_counts": difficult_counts,
        "pass_occlusion_episodes": [
            item for item in pass_entries if item["difficult_signals"]["occlusion"]
        ],
        "pass_identity_crossing_episodes": [
            item for item in pass_entries if item["difficult_signals"]["identity_change"]
        ],
        "episodes": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "file_count": len(paths),
                "audited_file_count": len(entries),
                "read_error_count": len(errors),
                "status_counts": dict(status_counts),
                "pass_difficult_signal_counts": difficult_counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
