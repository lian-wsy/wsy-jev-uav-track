"""Freeze a reproducible episode split from explicit PASS manifests.

The source manifests are treated as read-only evidence.  This command never
scans a live collection directory and never copies or edits HDF5 files.  The
current repository has a small baseline manifest and a difficult-scenario
manifest; difficult episodes are kept in a development stress pool because
they have already been used for threshold exploration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from jev_uav_track.holdout import require_development_source


_SEED_RE = re.compile(r"_seed(?P<seed>\d+)(?:\.hdf5)?$")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("source_mode", "")) != "read_only_hdf5_adapter":
        raise ValueError(f"unsupported manifest source_mode: {path}")
    episodes = payload.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError(f"manifest has no episodes: {path}")
    return payload


def _filename(item: dict[str, Any]) -> str:
    return Path(str(item["path"])).name


def _seed(name: str) -> int | None:
    match = _SEED_RE.search(name)
    return int(match.group("seed")) if match else None


def _entry(item: dict[str, Any], *, split: str, source: str) -> dict[str, Any]:
    require_development_source(item['path'], item)
    status = str(item.get("validation_status", "")).upper()
    if status != "PASS":
        raise ValueError(f"only PASS episodes may enter a split: {item.get('path')}")
    schema = str(item.get("schema", ""))
    if not schema:
        raise ValueError(f"missing schema: {item.get('path')}")
    name = _filename(item)
    return {
        "path": str(Path(str(item["path"])).resolve()),
        "filename": name,
        "seed": _seed(name),
        "schema": schema,
        "validation_status": status,
        "frame_count": int(item.get("frame_count", 0)),
        "target_actor_ids": list(item.get("target_actor_ids", [])),
        "target_occluded_fraction": float(item.get("target_occluded_fraction", 0.0)),
        "target_in_frame_fraction": float(item.get("target_in_frame_fraction", 0.0)),
        "source_manifest": source,
        "split": split,
    }


def freeze_split(baseline_manifest: Path, difficult_manifest: Path) -> dict[str, Any]:
    baseline = _load(baseline_manifest)
    difficult = _load(difficult_manifest)
    baseline_items = list(baseline["episodes"])
    difficult_items = list(difficult["episodes"])
    if len(baseline_items) != 12:
        raise ValueError(f"expected the 12-episode baseline manifest, got {len(baseline_items)}")
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    # The baseline manifest is ordered as six deterministic pairs.  Keep each
    # pair's first seed in train and second seed in validation.
    for index, item in enumerate(baseline_items):
        split = "train" if index < 6 else "validation"
        entry = _entry(item, split=split, source=str(baseline_manifest))
        if entry["path"] in seen:
            raise ValueError(f"duplicate episode path: {entry['path']}")
        seen.add(entry["path"])
        entries.append(entry)
    # Difficult episodes were already used for threshold/model diagnostics.
    # Keep them available for stress development but never call them final test.
    for item in difficult_items:
        entry = _entry(item, split="development_stress", source=str(difficult_manifest))
        if entry["path"] in seen:
            raise ValueError(f"duplicate episode path: {entry['path']}")
        seen.add(entry["path"])
        entries.append(entry)
    split_counts = {name: sum(item["split"] == name for item in entries)
                    for name in ("train", "validation", "development_stress")}
    return {
        "split_version": 1,
        "policy": {
            "source_mode": "read_only_hdf5_adapter",
            "pass_only": True,
            "episode_disjoint": True,
            "final_test_available": False,
            "final_test_reason": (
                "all currently audited episodes were used for development or threshold exploration; "
                "collect an untouched PASS set before claiming final-test results"
            ),
            "target_episode_count": "30-60",
        },
        "source_manifests": [
            {"path": str(baseline_manifest.resolve()), "sha256": _sha256(baseline_manifest)},
            {"path": str(difficult_manifest.resolve()), "sha256": _sha256(difficult_manifest)},
        ],
        "split_counts": split_counts,
        "episodes": entries,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--difficult-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = freeze_split(args.baseline_manifest, args.difficult_manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "split_counts": result["split_counts"]}, indent=2))


if __name__ == "__main__":
    main()
