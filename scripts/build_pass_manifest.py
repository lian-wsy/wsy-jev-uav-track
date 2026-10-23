"""Build a read-only manifest for explicitly selected PASS HDF5 episodes.

The script deliberately accepts episode paths instead of scanning a live
collection or upload directory.  This keeps the daily reproducibility step
bounded and prevents it from accidentally admitting a pending or partial
file.  It opens each HDF5 through the repository adapter in read-only mode.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Sequence

from jev_uav_track.data import HDF5Episode


def _fraction(values: Sequence[Any]) -> float:
    if len(values) == 0:
        return 0.0
    return sum(bool(value) for value in values) / len(values)


def _episode_entry(path: str | Path, expected_schema: str | None) -> Dict[str, Any]:
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
    stat = source.stat()
    actor_ids = sorted({int(value) for value in episode.target_actor_ids})
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
        "target_actor_ids": actor_ids,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-schema")
    args = parser.parse_args()

    paths = [Path(item).expanduser().resolve() for item in args.episode]
    if len(set(paths)) != len(paths):
        raise ValueError("Episode paths must be unique")
    entries = [_episode_entry(path, args.expected_schema) for path in paths]
    schemas = sorted({entry["schema"] for entry in entries})
    report = {
        "manifest_version": 1,
        "source_mode": "read_only_hdf5_adapter",
        "episode_count": len(entries),
        "schemas": schemas,
        "all_validation_status_pass": all(
            entry["validation_status"] == "PASS" for entry in entries
        ),
        "episodes": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "episode_count": report["episode_count"],
                "schemas": schemas,
                "all_validation_status_pass": report["all_validation_status_pass"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
