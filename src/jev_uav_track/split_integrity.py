"""Strict content/path/seed identity checks for frozen research manifests."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Sequence


def validate_split_integrity(entries: Sequence[Mapping]) -> None:
    if not entries:
        raise ValueError("Split must contain episodes")
    paths, seeds, digests = set(), set(), set()
    for entry in entries:
        if entry.get("validation_status") != "PASS":
            raise ValueError("Every episode must be PASS")
        raw_path = entry.get("path")
        seed = entry.get("seed")
        digest = str(entry.get("sha256", "")).lower()
        if not raw_path or not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ValueError("Explicit path and nonnegative integer seed required")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Full SHA-256 required")
        path = str(Path(raw_path).resolve()).replace("\\", "/").casefold()
        if path in paths or seed in seeds or digest in digests:
            raise ValueError("Duplicate path, seed, or content in frozen split")
        paths.add(path)
        seeds.add(seed)
        digests.add(digest)
