import pytest

from jev_uav_track.frames import AirSimOriginBridge, CarlaAirAlignment
from jev_uav_track.live_observation import CaptureWindow
from jev_uav_track.jev_client import MockJevClient, UtilityRankingClient
from jev_uav_track.pipeline import JevTrackPipeline


def packet(t=10):
    return CaptureWindow(t, t + .03, t + 100, t + 100.01, t + 100.02,
                         t + 2, t + 2.02, int(t * 100), int(t * 100) + 1)


def adapter():
    from jev_uav_track.live_belief import LiveBeliefAdapter
    return LiveBeliefAdapter(AirSimOriginBridge(CarlaAirAlignment((-100, -200, 4)), (0, 0, 3)))


def update(instance, t=10, measurement=(120, 200, 2), **kwargs):
    return instance.update(packet(t), now_s=t + .03, vehicle_local_ned=(0, 0, 5),
        velocity_local_ned=(1, 2, -3), yaw_deg=0, measurement_world=measurement,
        target_actor_id=7 if measurement is not None else None,
        image_center_error=.1 if measurement is not None else None, **kwargs)


def test_live_belief_uses_vehicle_origin_and_past_only_velocity_and_history():
    instance = adapter()
    first = update(instance)
    assert first.uav.position_m == (100, 200, 2)
    assert first.uav.velocity_mps == (1, 2, 3)
    assert first.target.range_m == 20
    assert first.target.velocity_mps == (0, 0, 0)
    second = update(instance, 10.2, (120.4, 200, 2))
    assert second.target.velocity_mps == pytest.approx((2, 0, 0))
    hidden = update(instance, 10.4, None)
    assert hidden.target.position_m == pytest.approx((120.8, 200, 2))
    assert hidden.environment['target_actor_id'] is None
    assert hidden.target.identity_confidence == 0
    assert hidden.environment['belief_history']['length'] == 3
    assert hidden.environment['measurement_source'] == 'simulator_visible_oracle'


def test_unknown_live_safety_measurements_do_not_inherit_safe_defaults():
    state = update(adapter())
    assert state.uav.minimum_obstacle_margin_m == 0
    assert state.uav.battery_percent == 0
    for client in (MockJevClient(), UtilityRankingClient()):
        with pytest.raises(RuntimeError, match='hard safety'):
            JevTrackPipeline(client).step(state)


def test_real_pipeline_can_consume_valid_adapter_fixture_without_bypassing_safety():
    state = update(adapter(), minimum_clearance_m=10, battery_percent=100)
    for client in (MockJevClient(), UtilityRankingClient()):
        result = JevTrackPipeline(client).step(state)
        assert result.action.id == 'hold_center'
        assert result.action.outcome.minimum_obstacle_margin_m >= 3


def test_repeated_or_expired_packets_do_not_enter_belief_history():
    instance = adapter()
    update(instance)
    with pytest.raises(ValueError, match='Repeated'):
        update(instance)
    with pytest.raises(ValueError, match='expired'):
        instance.update(packet(11), now_s=11.3, vehicle_local_ned=(0,0,5),
            velocity_local_ned=(0,0,0), yaw_deg=0, measurement_world=None,
            target_actor_id=None, image_center_error=None)
    assert update(instance, 11.4).environment['belief_history']['length'] == 2


def test_invisible_target_does_not_trigger_position_rpc():
    from jev_uav_track.live_belief import read_visible_oracle_position
    def forbidden():
        pytest.fail('Hidden target position was queried')
    assert read_visible_oracle_position(False, forbidden) is None
    assert read_visible_oracle_position(True, lambda: (1, 2, 3)) == (1, 2, 3)


@pytest.mark.parametrize('kwargs', [dict(minimum_clearance_m=float('nan')),
                                   dict(battery_percent=101)])
def test_invalid_measurements_do_not_pollute_history(kwargs):
    instance = adapter()
    with pytest.raises(ValueError):
        update(instance, **kwargs)
    assert update(instance, 10.2).environment['belief_history']['length'] == 1


def test_owned_tesla_inventory_distinguishes_spawned_pawn_from_its_controller():
    from jev_uav_track.live_belief import identify_owned_tesla
    assert identify_owned_tesla(['BP_TeslaM3_C_123', 'WheeledVehicleAIController_456']) == 'BP_TeslaM3_C_123'
    for names in ([], ['WheeledVehicleAIController_456'], ['BP_TeslaM3_C_1', 'BP_TeslaM3_C_2'],
                  ['BP_TeslaM3_C_1', 'UnknownActor_2']):
        with pytest.raises(ValueError):
            identify_owned_tesla(names)
