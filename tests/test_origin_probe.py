"""RPC boundary tests; no simulator, SDK installation, or shared state needed."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


def load_probe(monkeypatch):
    # origin_sample itself uses only the client protocol, not SDK constructors.
    monkeypatch.setitem(sys.modules, 'airsim', SimpleNamespace())
    path = Path(__file__).resolve().parents[1] / 'scripts/probe_observation_contract.py'
    spec = importlib.util.spec_from_file_location('origin_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PausedClient:
    def __init__(self, fail=False):
        self.paused = False
        self.fail = fail

    # Installed AirSim Python API uses simIsPause, not server's simIsPaused.
    def simIsPause(self):
        return self.paused

    def simPause(self, value):
        self.paused = value

    def simListSceneObjects(self, pattern):
        return ['JevDrone']

    def pose(self, position):
        return SimpleNamespace(position=position,
            orientation=SimpleNamespace(x_val=0, y_val=0, z_val=0, w_val=1))

    def simGetObjectPose(self, name):
        assert name == 'JevDrone' and self.paused
        if self.fail:
            raise RuntimeError('RPC unavailable')
        return self.pose((1, 2, 3))

    def simGetVehiclePose(self, vehicle_name):
        assert vehicle_name == 'JevDrone' and self.paused
        return self.pose((1, 2, 6))

    def getMultirotorState(self, vehicle_name):
        assert vehicle_name == 'JevDrone' and self.paused
        return SimpleNamespace(timestamp=1000000000, kinematics_estimated=self.pose((1, 2, 6)))


def test_origin_sample_uses_installed_pause_api_and_retains_distinct_frames(monkeypatch):
    client = PausedClient()
    record = load_probe(monkeypatch).origin_sample(client)
    assert record['global_ned_m'] == [1, 2, 3]
    assert record['vehicle_local_ned_m'] == [1, 2, 6]
    assert record['local_state_error']['position_error_m'] == 0
    assert not client.paused


def test_origin_read_failure_restores_own_pause_state(monkeypatch):
    client = PausedClient(fail=True)
    with pytest.raises(RuntimeError, match='RPC unavailable'):
        load_probe(monkeypatch).origin_sample(client)
    assert not client.paused


def test_origin_probe_does_not_resume_an_already_paused_instance(monkeypatch):
    client = PausedClient()
    client.paused = True
    with pytest.raises(ValueError, match='initially running'):
        load_probe(monkeypatch).origin_sample(client)
    assert client.paused


@pytest.mark.parametrize('fault', ['missing_actor', 'pause_rejected', 'nonfinite_position',
                                    'invalid_quaternion', 'orientation_mismatch', 'state_mismatch'])
def test_origin_probe_rejects_unverified_pairs_and_leaves_no_pause(monkeypatch, fault):
    client = PausedClient()
    if fault == 'missing_actor':
        client.simListSceneObjects = lambda pattern: ['JevDrone2']
    elif fault == 'pause_rejected':
        client.simPause = lambda value: None
    elif fault == 'nonfinite_position':
        client.simGetObjectPose = lambda name: client.pose((float('nan'), 2, 3))
    elif fault in ('invalid_quaternion', 'orientation_mismatch'):
        pose = client.pose((1, 2, 3))
        pose.orientation.w_val = 0
        pose.orientation.z_val = 0 if fault == 'invalid_quaternion' else 1
        client.simGetObjectPose = lambda name: pose
    else:
        client.getMultirotorState = lambda vehicle_name: SimpleNamespace(
            timestamp=1000000000, kinematics_estimated=client.pose((1, 2, 9)))
    with pytest.raises((ValueError, RuntimeError)):
        load_probe(monkeypatch).origin_sample(client)
    assert not client.paused
