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


def test_local_carlaair_mapping_preserves_horizontal_axes_and_compiles_climb():
    from jev_uav_track.frames import CarlaAirAlignment
    from jev_uav_track.sim_adapter import compile_carlaair_candidate
    from jev_uav_track.rollout import KinematicRolloutModel
    from jev_uav_track.models import TrackingState, TargetBelief, UAVState
    from jev_uav_track.primitives import DEFAULT_PRIMITIVES
    alignment = CarlaAirAlignment((100, -20, 3))
    assert alignment.position_to_airsim((1, 2, 5)) == (101, -18, -2)
    assert alignment.position_to_carla((101, -18, -2)) == (1, 2, 5)
    action = KinematicRolloutModel().simulate(
        next(p for p in DEFAULT_PRIMITIVES if p.id == "climb"),
        TrackingState(TargetBelief(), UAVState()),
    )
    command = compile_carlaair_candidate(action, alignment)
    assert command.velocity_mps == (0.3, 0, -1.8)
    assert command.duration_s == action.duration_s


def test_global_object_origin_is_not_vehicle_state_origin():
    from jev_uav_track.frames import CarlaAirAlignment, AirSimOriginBridge
    bridge = AirSimOriginBridge.calibrate(
        CarlaAirAlignment((-100, -200, 4)),
        [(1, 2, 3), (2, 2, 1)], [(6, -1, 6), (7, -1, 4)])
    # Hand-derived local = global + (5, -3, 3); CARLA z has opposite sign.
    assert bridge.global_to_vehicle((10, 20, -2)) == pytest.approx((15, 17, 1))
    assert bridge.carla_to_vehicle((110, 220, 6)) == pytest.approx((15, 17, 1))
    assert bridge.vehicle_to_carla((15, 17, 1)) == pytest.approx((110, 220, 6))
    assert bridge.local_minus_global_m == pytest.approx((5, -3, 3))


@pytest.mark.parametrize('global_points,local_points', [
    ([], []),
    ([(0, 0, 0)], [(0, 0, 3)]),
    ([(0, 0, 0), (0, 0, 0)], [(0, 0, 3), (0, 0, 3)]),
    ([(0, 0, 0), (1, 0, 0)], [(0, 0, 3)]),
    ([(0, 0, 0), (1, 0, 0)], [(0, 0, 3), (1, 0, 3.2)]),
    ([(0, 0, 0), (float('nan'), 0, 0)], [(0, 0, 3), (1, 0, 3)]),
])
def test_origin_bridge_rejects_unproven_or_inconsistent_pairs(global_points, local_points):
    from jev_uav_track.frames import CarlaAirAlignment, AirSimOriginBridge
    with pytest.raises(ValueError):
        AirSimOriginBridge.calibrate(CarlaAirAlignment((0, 0, 0)), global_points, local_points)
