import json
from pathlib import Path

import pytest

from scripts.freeze_experiment_split import freeze_split


def _manifest(path: Path, items: list[dict]) -> None:
    path.write_text(
        json.dumps({"source_mode": "read_only_hdf5_adapter", "episodes": items}),
        encoding="utf-8",
    )


def _item(name: str, status: str = "PASS") -> dict:
    return {
        "path": str(Path(name).resolve()),
        "schema": "carlaair_uav_v2_candidate",
        "validation_status": status,
        "frame_count": 3,
        "target_actor_ids": [1],
    }


def test_freeze_split_is_episode_disjoint_and_marks_final_test_unavailable(tmp_path):
    baseline = tmp_path / "baseline.json"
    difficult = tmp_path / "difficult.json"
    _manifest(baseline, [_item(f"Town05_bicycle_normal_motion_close_seed{i}.hdf5") for i in range(12)])
    _manifest(difficult, [_item("Town10HD_bicycle_occlusion_seed99.hdf5")])
    result = freeze_split(baseline, difficult)
    assert result["split_counts"] == {"train": 6, "validation": 6, "development_stress": 1}
    assert result["policy"]["final_test_available"] is False
    assert len({item["path"] for item in result["episodes"]}) == 13


def test_freeze_split_rejects_non_pass(tmp_path):
    baseline = tmp_path / "baseline.json"
    difficult = tmp_path / "difficult.json"
    _manifest(baseline, [_item(f"episode_{i}.hdf5", "FAIL" if i == 0 else "PASS") for i in range(12)])
    _manifest(difficult, [_item("difficult.hdf5")])
    with pytest.raises(ValueError, match="only PASS"):
        freeze_split(baseline, difficult)
