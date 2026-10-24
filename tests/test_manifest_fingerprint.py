import hashlib

import pytest

from jev_uav_track.data import sha256_file


def test_sha256_file_streams_and_matches_standard_digest(tmp_path):
    source = tmp_path / "episode.hdf5"
    payload = (b"jev-uav-track\x00" * 8193) + b"tail"
    source.write_bytes(payload)

    assert sha256_file(source, chunk_size=17) == hashlib.sha256(payload).hexdigest()
    with pytest.raises(ValueError):
        sha256_file(source, chunk_size=0)
