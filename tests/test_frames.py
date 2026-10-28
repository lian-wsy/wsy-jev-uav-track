import pytest

from jev_uav_track.frames import CoordinateFrame, convert_vector


def test_enu_ned_conversion_is_explicit_and_invertible():
    enu = (3.0, 4.0, 5.0)
    ned = convert_vector(enu, CoordinateFrame.ENU, CoordinateFrame.NED)
    assert ned == pytest.approx((4.0, 3.0, -5.0))
    assert convert_vector(ned, "ned", "enu") == pytest.approx(enu)


def test_body_flu_conversion_uses_declared_yaw():
    assert convert_vector((1.0, 0.0, 0.0), "body_flu", "enu", yaw_deg=90.0) == pytest.approx(
        (0.0, 1.0, 0.0)
    )
    assert convert_vector((0.0, 1.0, 0.0), "enu", "body_flu", yaw_deg=90.0) == pytest.approx(
        (1.0, 0.0, 0.0)
    )


def test_body_world_conversion_requires_yaw():
    with pytest.raises(ValueError, match="yaw_deg"):
        convert_vector((1.0, 0.0, 0.0), "body_flu", "enu")
