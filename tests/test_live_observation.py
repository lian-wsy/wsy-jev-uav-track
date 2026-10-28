from dataclasses import replace

import pytest

from jev_uav_track.sim_adapter import AirSimWorldVelocityCommand


def window(**updates):
    from jev_uav_track.live_observation import CaptureWindow
    value = CaptureWindow(wall_start_s=10.0, wall_end_s=10.08,
                          air_before_s=100.0, image_s=100.04, air_after_s=100.07,
                          carla_before_s=2.0, carla_after_s=2.08,
                          frame_before=40, frame_after=44)
    return replace(value, **updates)


def test_live_gate_keeps_clock_domains_separate_and_bounds_command_lifetime():
    from jev_uav_track.live_observation import TemporalObservationGate
    gate = TemporalObservationGate()
    sample = window()
    gate.accept(sample, now_s=10.08)
    executed = []
    command = AirSimWorldVelocityCommand((0.1, 0, 0), 0, 0.1)
    gate.dispatch(sample, command, executed.append, clock=lambda: 10.15)
    assert len(executed) == 1
    assert 0 < executed[0].duration_s <= 0.05 + 1e-9
    with pytest.raises(ValueError, match='already'):
        gate.dispatch(sample, command, executed.append, clock=lambda: 10.16)
    assert len(executed) == 1


@pytest.mark.parametrize('updates,now', [
    ({'image_s': 99.0}, 10.08),  # Cached image outside the AirSim bracket.
    ({'image_s': 100.5}, 10.08),  # Image apparently from the future.
    ({'air_after_s': 99.5}, 10.08),
    ({'carla_after_s': 1.0}, 10.08),
    ({'wall_end_s': 10.5}, 10.5),
    ({}, 10.4),  # Old delivery cannot become fresh by calling accept again.
    ({'image_s': float('nan')}, 10.08),
    ({'frame_after': 39}, 10.08),
])
def test_invalid_or_stale_capture_is_rejected(updates, now):
    from jev_uav_track.live_observation import TemporalObservationGate
    with pytest.raises(ValueError):
        TemporalObservationGate().accept(window(**updates), now_s=now)


def test_failed_capture_does_not_poison_state_and_clock_drift_is_rejected():
    from jev_uav_track.live_observation import TemporalObservationGate
    gate = TemporalObservationGate()
    gate.accept(window(), now_s=10.08)
    with pytest.raises(ValueError):
        gate.accept(window(), now_s=10.09)  # Repeated frame.
    good = window(wall_start_s=10.2, wall_end_s=10.28,
                  air_before_s=100.2, image_s=100.24, air_after_s=100.27,
                  carla_before_s=2.2, carla_after_s=2.28, frame_before=50, frame_after=54)
    with pytest.raises(ValueError, match='clock'):
        gate.accept(replace(good, carla_before_s=3.0, carla_after_s=3.08), now_s=10.28)
    gate.accept(good, now_s=10.28)


def test_expired_or_unaccepted_packet_never_reaches_command_callback():
    from jev_uav_track.live_observation import TemporalObservationGate
    gate = TemporalObservationGate()
    sample = window()
    command = AirSimWorldVelocityCommand((0.1, 0, 0), 0, 0.05)
    executed = []
    with pytest.raises(ValueError):
        gate.dispatch(sample, command, executed.append, clock=lambda: 10.08)
    gate.accept(sample, now_s=10.08)
    with pytest.raises(ValueError, match='expired'):
        gate.dispatch(sample, command, executed.append, clock=lambda: 10.21)
    assert executed == []


@pytest.mark.parametrize('command', [
    AirSimWorldVelocityCommand((float('nan'), 0, 0), 0, 0.05),
    AirSimWorldVelocityCommand((0.4, 0, 0), 0, 0.05),
    AirSimWorldVelocityCommand((0, 0, 0), 16, 0.05),
    AirSimWorldVelocityCommand((0, 0, 0), 0, -1),
])
def test_unsafe_development_command_is_rejected(command):
    from jev_uav_track.live_observation import TemporalObservationGate
    gate = TemporalObservationGate()
    sample = window()
    gate.accept(sample, now_s=10.08)
    with pytest.raises(ValueError):
        gate.dispatch(sample, command, lambda _: pytest.fail('unsafe command dispatched'),
                      clock=lambda: 10.08)


def test_rejected_new_read_revokes_old_dispatch_authority():
    from jev_uav_track.live_observation import TemporalObservationGate
    gate = TemporalObservationGate()
    sample = window()
    gate.accept(sample, now_s=10.08)
    with pytest.raises(ValueError):
        gate.accept(window(image_s=float('nan')), now_s=10.09)
    executed = []
    with pytest.raises(ValueError):
        gate.dispatch(sample, AirSimWorldVelocityCommand((0.1, 0, 0), 0, 0.05),
                      executed.append, clock=lambda: 10.1)
    assert executed == []


def test_uncertain_rpc_cannot_be_replayed_from_the_same_observation():
    from jev_uav_track.live_observation import TemporalObservationGate
    gate = TemporalObservationGate()
    sample = window()
    gate.accept(sample, now_s=10.08)
    command = AirSimWorldVelocityCommand((0.1, 0, 0), 0, 0.05)

    def uncertain(_):
        raise TimeoutError('transport lost after possible dispatch')

    with pytest.raises(TimeoutError):
        gate.dispatch(sample, command, uncertain, clock=lambda: 10.1)
    with pytest.raises(ValueError, match='already'):
        gate.dispatch(sample, command, lambda _: pytest.fail('uncertain action replayed'),
                      clock=lambda: 10.11)
