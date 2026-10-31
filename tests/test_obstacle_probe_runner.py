"""Exercise lifecycle against a strict SDK boundary double, not a live world."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from test_obstacle_probe import module


class Clock:
    def __init__(self): self.value = 20.; self.world = None
    def __call__(self): return self.value
    def sleep(self, seconds):
        self.value += seconds
        self.world.frame += 1
        for actor in list(self.world.actors):
            if actor.callback:
                witness = next(a for a in self.world.actors if a.type_id == 'static.prop.box01' and a.pose.location.x == 12.)
                blockers = [a for a in self.world.actors if a.type_id == 'static.prop.box01' and
                            a.pose.location.x == 6. and a.pose.location.y < 3.]
                hit = blockers[0] if blockers else witness
                if self.world.failure == 'silence': continue
                event = NS(actor=actor, other_actor=hit, frame=self.world.frame,
                    timestamp=self.value - 10., distance=3. if blockers else 9., transform=actor.pose)
                if self.world.failure == 'wrong_hit': event.other_actor = NS(id=999)
                actor.callback(event)


class Blueprint:
    def __init__(self, kind): self.id = kind; self.attrs = {}
    def set_attribute(self, name, value): self.attrs[name] = value


class Actor:
    def __init__(self, world, bp, pose):
        self.world, self.type_id, self.pose = world, bp.id, pose
        self.id = world.next_id; world.next_id += 1
        self.callback = None
        self.attributes = deepcopy(bp.attrs)
        if self.type_id == 'static.prop.box01':
            self.bounding_box = NS(get_world_vertices=lambda t: [NS(x=t.location.x + dx,
                y=t.location.y + dy, z=t.location.z + dz) for dx in (-.25, .25)
                for dy in (-.25, .25) for dz in (-.25, .25)])
    def set_simulate_physics(self, enabled):
        assert enabled is False and self.type_id == 'static.prop.box01'
        self.world.physics_disabled.append(self.id)
    def get_transform(self):
        if self.world.failure == 'pose_error': raise RuntimeError('PRIVATE_EXCEPTION_NOT_FOR_REPORT')
        return self.pose
    def listen(self, callback):
        assert self.type_id == 'sensor.other.obstacle'
        assert self.attributes == {'distance': '16.0', 'hit_radius': '3.0',
            'only_dynamics': 'false', 'debug_linetrace': 'false', 'sensor_tick': '0.05'}
        self.callback = callback
    def stop(self): self.callback = None; self.world.stopped.append(self.id)
    def destroy(self):
        self.world.destroyed.append(self.id)
        self.world.actors.remove(self)
        return self.world.failure != 'cleanup_error'


class World:
    def __init__(self, failure=None):
        self.failure = failure; self.next_id = 11; self.frame = 100
        self.actors = []; self.spawned = []; self.destroyed = []; self.stopped = []; self.physics_disabled = []
        self.clock = Clock(); self.clock.world = self
    def get_map(self): return NS(name='Carla/Maps/Town02')
    def get_blueprint_library(self): return NS(find=lambda kind: Blueprint(kind))
    def try_spawn_actor(self, bp, transform):
        actor = Actor(self, bp, transform)
        self.actors.append(actor); self.spawned.append(actor.id)
        return actor
    def wait_for_tick(self, timeout):
        assert timeout <= 2.
        self.clock.sleep(.05)
        return self.get_snapshot()
    def get_snapshot(self):
        return NS(frame=self.frame, timestamp=NS(elapsed_seconds=self.clock() - 10.))


def sdk():
    return NS(Location=lambda x=0., y=0., z=0.: NS(x=x, y=y, z=z),
              Rotation=lambda pitch=0., yaw=0., roll=0.: NS(pitch=pitch, yaw=yaw, roll=roll),
              Transform=lambda location, rotation: NS(location=location, rotation=rotation))


def run(world):
    probe = module()
    assert hasattr(probe, 'probe_obstacle_sweep'), 'Owned fixture runner missing'
    return probe.probe_obstacle_sweep(world, sdk(), clock=world.clock, sleep=world.clock.sleep)


def test_runner_executes_four_predeclared_cases_and_cleans_only_returned_actors():
    world = World()
    result = run(world)
    assert result['status'] == 'FIXTURE_RESPONSE_VERIFIED'
    assert [row['name'] for row in result['cases']] == ['baseline', 'center', 'off_axis', 'outside']
    assert len(world.spawned) == 11
    assert sorted(world.destroyed) == world.spawned
    assert len(world.stopped) == 4 and len(world.physics_disabled) == 7
    assert not world.actors
    module().require_sweep_receipt(result)


@pytest.mark.parametrize('failure', ['silence', 'wrong_hit', 'pose_error', 'cleanup_error'])
def test_failure_preserves_negative_first_case_and_stops_without_new_fixture(failure):
    world = World(failure)
    result = run(world)
    assert result['status'] == 'NOT_VERIFIED'
    assert len(result['cases']) == 1
    assert result['cases'][0]['name'] == 'baseline'
    assert sorted(world.destroyed) == world.spawned and not world.actors
    assert world.clock() < 25.
    assert result['control_ready'] is False and result['clearance_m'] is None
    assert 'PRIVATE_EXCEPTION' not in str(result)


def test_callbacks_arriving_only_inside_stop_cannot_rescue_timed_out_fixture(monkeypatch):
    world = World('silence')
    original = Actor.stop
    def delayed_stop(actor):
        if actor.callback:
            world.failure = None
            for _ in range(3): world.clock.sleep(.05)
            world.failure = 'silence'
        original(actor)
    monkeypatch.setattr(Actor, 'stop', delayed_stop)
    result = run(world)
    assert result['status'] == 'NOT_VERIFIED'
    assert len(result['cases']) == 1
    assert result['cases'][0]['events'] == []
    assert sorted(world.destroyed) == world.spawned


def test_direct_runner_refuses_reserved_map_before_blueprint_or_spawn():
    world = World()
    world.get_map = lambda: NS(name='Carla/Maps/Town03')
    with pytest.raises(ValueError, match='map'): run(world)
    assert not world.spawned


def test_sweep_profile_rejects_town03_before_files_or_connections(tmp_path, monkeypatch):
    from pathlib import Path
    import sys
    from scripts.probe_isolated_carla import smoke
    from test_map_probe import load_client_probe
    import json
    settings = {'ApiServerPort': 42451, 'Vehicles': {'JevDrone': {}}}
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(settings))
    client, counts = load_client_probe(monkeypatch, 'Town03', settings)
    with pytest.raises(ValueError, match='API-only'):
        client.main(tmp_path / 'result.json', 'sweep', path, 'takeoff', 'Town03')
    assert counts['connections'] == 0
    with pytest.raises(ValueError, match='API-only'):
        smoke(tmp_path / 'source', tmp_path / 'owned', Path(sys.executable), 'no',
              'sweep', 'takeoff', 'Town03')
    assert not (tmp_path / 'owned').exists()


def test_client_records_sweep_result_and_does_not_enter_flight_contract(tmp_path, monkeypatch):
    import json
    from test_map_probe import load_client_probe
    settings = {'ApiServerPort': 42451, 'Vehicles': {'JevDrone': {}}}
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(settings))
    client, counts = load_client_probe(monkeypatch, 'Town02', settings)
    world = World()
    real = module().probe_obstacle_sweep if hasattr(module(), 'probe_obstacle_sweep') else None
    assert real, 'Owned fixture runner missing'
    monkeypatch.setattr(module(), 'probe_obstacle_sweep', lambda *a, **kw:
        real(world, sdk(), clock=world.clock, sleep=world.clock.sleep))
    output = tmp_path / 'result.json'
    client.main(output, 'sweep', path, 'takeoff', 'Town02')
    result = json.loads(output.read_text())
    assert result['kind'] == 'LIVE_OWNED_OBSTACLE_SWEEP_FIXTURE'
    assert result['sweep_probe']['status'] == 'FIXTURE_RESPONSE_VERIFIED'
    assert 'contract' not in result
    assert counts['observations'] == 0
