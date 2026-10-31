import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

from scripts import probe_isolated_carla as launcher
from test_map_probe import load_client_probe
from test_runtime_endpoints import ports


def test_movement_client_uses_supported_coarse_timeout_without_extending_transaction_watchdog():
    from jev_uav_track.actuation import BoundedActuator
    runner=importlib.import_module('scripts.probe_current_follow')
    factory=getattr(runner,'owned_movement_port',None)
    assert callable(factory), 'Owned movement client timeout adapter missing'
    sdk=NS(MultirotorClient=lambda **kwargs:NS(options=kwargs))
    port=factory(sdk,ports())
    assert port.client.options=={'ip':'127.0.0.1','port':42461,'timeout_value':1}
    assert BoundedActuator.transaction_timeout_s==.2
    assert port.vehicle_name=='JevDrone'
    from jev_uav_track.isolated_runtime import RuntimePorts
    def forbidden(**kwargs): raise AssertionError('Protected endpoint must not construct a client')
    with pytest.raises(ValueError,match='owned endpoint'):
        factory(NS(MultirotorClient=forbidden),RuntimePorts())


def test_follow_rejects_town03_before_files_connections_or_owned_launch(tmp_path, monkeypatch):
    client, counts = load_client_probe(monkeypatch, 'Town03', {})
    with pytest.raises(ValueError, match='API-only'):
        client.main(tmp_path/'none.json', 'follow', tmp_path/'none_settings', 'takeoff', 'Town03', ports=ports())
    assert counts['connections'] == 0
    with pytest.raises(ValueError, match='API-only'):
        launcher.smoke(tmp_path/'source', tmp_path/'task', Path(sys.executable),
                       'follow', 'follow', 'takeoff', 'Town03', ports=ports())
    assert not (tmp_path/'task').exists()


def test_follow_missing_current_model_preserves_failure_before_scene_changes(tmp_path, monkeypatch):
    path = Path(launcher.__file__).with_name('probe_current_follow.py')
    assert path.is_file(), 'Native current-model follow wiring missing'
    runner = importlib.import_module('scripts.probe_current_follow')
    def missing(*args): raise ValueError('Frozen model fingerprint unavailable')
    monkeypatch.setattr(runner, 'load_current_rollout', missing)
    class Forbidden:
        def __getattr__(self, name): raise AssertionError('No scene/RPC access before model verification')
    result = {}
    runner.probe_current_follow(Forbidden(), Forbidden(), tmp_path, result,
                                ports=ports(), carla_sdk=Forbidden(), airsim_sdk=Forbidden())
    assert result['status'] == 'FOLLOW_ABORTED'
    assert result['phase'] == 'frozen_model'
    assert 'fingerprint' in result['error']
    assert result['real_jev_calls'] == 0
    assert json.loads((tmp_path/'current_follow.json').read_text())['status'] == 'FOLLOW_ABORTED'


def test_client_routes_follow_and_preserves_aborted_native_attempt(tmp_path, monkeypatch):
    settings={'ApiServerPort':42461,'Vehicles':{'JevDrone':{}}}
    path=tmp_path/'settings.json'
    path.write_text(json.dumps(settings))
    client,counts=load_client_probe(monkeypatch,'Town02',settings,carla_port=3200,airsim_port=42461)
    def attempt(world,air,output,result,**kwargs):
        assert kwargs['ports']==ports()
        result.update(status='FOLLOW_ABORTED',phase='prearm',frames=[],prefixes=[])
    monkeypatch.setitem(sys.modules,'probe_current_follow',NS(probe_current_follow=attempt))
    with pytest.raises(SystemExit) as exc:
        client.main(tmp_path/'api.json','follow',path,'takeoff','Town02',ports=ports())
    assert exc.value.code==2
    data=json.loads((tmp_path/'api.json').read_text())
    assert data['kind']=='LIVE_OWNED_CURRENT_FOLLOW'
    assert data['follow_probe']['status']=='FOLLOW_ABORTED'
    assert counts['observations']==0
