import math

import pytest


def test_camera_mount_uses_full_quaternion_and_sign_equivalence():
    from jev_uav_track.frames import camera_mount_error
    s = math.sqrt(0.5)
    result = camera_mount_error((1, 2, 3), (0, 0, s, s), (0.75, 0, -0.2),
                                (1, 2.75, 2.8), (0, 0, -s, -s))
    assert result['position_error_m'] < 1e-10
    assert result['orientation_error_deg'] < 1e-5
    # A correct translation cannot conceal a wrong yaw.
    wrong = camera_mount_error((1, 2, 3), (0, 0, s, s), (0.75, 0, -0.2),
                               (1, 2.75, 2.8), (0, 0, 0, 1))
    assert wrong['orientation_error_deg'] == pytest.approx(90)


def test_camera_mount_accounts_for_roll_not_only_yaw():
    from jev_uav_track.frames import camera_mount_error
    s = math.sqrt(0.5)
    result = camera_mount_error((0, 0, 0), (s, 0, 0, s), (0.75, 0, -0.2),
                                (0.75, 0.2, 0), (s, 0, 0, s))
    assert result['position_error_m'] < 1e-10


@pytest.mark.parametrize('quaternion', [(0, 0, 0, 0), (0, float('nan'), 0, 1)])
def test_invalid_orientation_cannot_pass_camera_audit(quaternion):
    from jev_uav_track.frames import camera_mount_error
    with pytest.raises(ValueError):
        camera_mount_error((0, 0, 0), quaternion, (0.75, 0, -0.2),
                           (0.75, 0, -0.2), (0, 0, 0, 1))
