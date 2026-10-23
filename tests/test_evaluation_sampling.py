from __future__ import annotations

import pytest

from jev_uav_track.evaluation import _sample_indices


def test_sample_indices_can_target_a_fixed_event_window() -> None:
    assert _sample_indices(300, max_frames=4, stride=4, start_frame=198, end_frame=211) == [
        198,
        202,
        206,
        210,
    ]


def test_sample_indices_rejects_negative_start_frame() -> None:
    with pytest.raises(ValueError):
        _sample_indices(20, max_frames=2, stride=1, start_frame=-1)
