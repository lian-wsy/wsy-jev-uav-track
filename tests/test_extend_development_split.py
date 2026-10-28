def test_extension_selection_uses_cells_and_seeds_not_outcome_scores():
    from scripts.extend_development_split import select_additions
    rows = [
        {"path": "root/quota_grid_20261004_formal/Town02_bicycle_normal_close_seed4.hdf5", "validation_status": "PASS", "target_visible_fraction": 1},
        {"path": "root/quota_grid_20261004_formal/Town02_bicycle_normal_close_seed3.hdf5", "validation_status": "PASS", "target_visible_fraction": 0},
        {"path": "root/quota_grid_20261004_formal/Town05_walker_turn_far_seed8.hdf5", "validation_status": "PASS"},
        {"path": "root/quota_grid_20261004_formal/Town05_walker_turn_far_seed9.hdf5", "validation_status": "PASS"},
        {"path": "root/quota_grid_20261004_formal/Town07_walker_turn_far_seed10.hdf5", "validation_status": "PENDING"},
    ]
    chosen = select_additions(rows, excluded_seeds={8}, limit=2)
    assert [r["seed"] for r in chosen] == [3, 9]
    assert all(r["split"] == "train" for r in chosen)


def test_insufficient_cells_fail_instead_of_silently_shrinking_frozen_design():
    import pytest
    from scripts.extend_development_split import select_additions
    with pytest.raises(ValueError, match="Insufficient"):
        select_additions([], excluded_seeds=set(), limit=22)
