import pytest


def entry(path, seed, digest, split="train"):
    return {"path": path, "seed": seed, "sha256": digest, "split": split,
            "validation_status": "PASS"}


@pytest.mark.parametrize("duplicate", [
    entry("COPY.hdf5", 2, "a" * 64, "validation"),
    entry("COPY.hdf5", 1, "b" * 64, "validation"),
    entry("original.HDF5", 2, "b" * 64, "validation"),
])
def test_copy_seed_or_windows_path_cannot_cross_splits(duplicate):
    from jev_uav_track.split_integrity import validate_split_integrity
    with pytest.raises(ValueError):
        validate_split_integrity([entry("original.hdf5", 1, "a" * 64), duplicate])


def test_unique_pass_and_content_are_accepted():
    from jev_uav_track.split_integrity import validate_split_integrity
    validate_split_integrity([entry("a.hdf5", 1, "a" * 64),
                              entry("b.hdf5", 2, "b" * 64, "validation")])


@pytest.mark.parametrize("change", [{"sha256": ""}, {"seed": None}, {"validation_status": "PENDING"}])
def test_unproven_provenance_rejected(change):
    from jev_uav_track.split_integrity import validate_split_integrity
    with pytest.raises(ValueError):
        validate_split_integrity([{**entry("a.hdf5", 1, "a" * 64), **change}])
