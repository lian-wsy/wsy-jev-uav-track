import pytest

from jev_uav_track.sim_adapter import (
    SimAction,
    SimObservation,
    validate_action_sequence,
    validate_observation,
)


def test_adapter_contract_accepts_bounded_action_and_observation():
    validate_action_sequence([SimAction("scan_left", duration_s=0.5)])
    validate_observation(
        SimObservation(
            timestamp_s=1.0,
            target_visible=True,
            target_in_frame=True,
            target_actor_id=4,
            range_m=12.0,
            minimum_clearance_m=8.0,
        )
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SimAction("", duration_s=0.5),
        lambda: SimAction("scan", duration_s=0.0),
        lambda: SimAction("scan", duration_s=0.5, command_frame="unknown"),
    ],
)
def test_adapter_rejects_invalid_actions(factory):
    with pytest.raises(ValueError):
        factory()


def test_adapter_rejects_visible_out_of_frame_observation():
    with pytest.raises(ValueError, match="visible target"):
        validate_observation(
            SimObservation(
                timestamp_s=0.0,
                target_visible=True,
                target_in_frame=False,
                target_actor_id=1,
            )
        )


def test_adapter_rejects_long_action_sequence():
    with pytest.raises(ValueError, match="10 s"):
        validate_action_sequence([SimAction("scan", duration_s=10.1)])
