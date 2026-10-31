"""Signed camera motion must not credit both yaw directions as centering."""
from dataclasses import replace
import math

import pytest

from jev_uav_track.models import TargetBelief, TrackingState, UAVState
from jev_uav_track.primitives import BehaviorPrimitive
from jev_uav_track.rollout import KinematicRolloutModel


def wrapper(base=None):
    from jev_uav_track import current_follow
    factory = getattr(current_follow, 'GeometricCenterFallback', None)
    assert callable(factory), 'Signed full-fallback camera projection missing'
    return factory(base or FullFallback())


class FullFallback:
    def generate(self, primitives, state):
        return [replace(a, metadata={**a.metadata, 'interval_full_fallback': True})
                for a in KinematicRolloutModel().generate(primitives, state)]


def scene(bearing=0., yaw=0.):
    angle=math.radians(bearing+yaw)
    # Horizontal camera, 90deg HFOV, centered elevation: x=tan(bearing).
    return TrackingState(TargetBelief(position_m=(20*math.cos(angle),20*math.sin(angle),0.),
        image_center_error=abs(math.tan(math.radians(bearing)))/math.sqrt(2)),
        UAVState(yaw_deg=yaw), environment={'camera_center_geometry':{
            'observed_xy': [math.tan(math.radians(bearing)),0.],
            'pitch_deg':0., 'horizontal_fov_deg':90., 'image_size':[320,240],
            'offset_body_forward_right_up_m':[0.,0.,0.]}})


def test_centered_target_is_worsened_by_either_yaw_not_improved():
    s=scene()
    actions=wrapper().generate([BehaviorPrimitive('hold','hold','',(0.,0.,0.)),
        BehaviorPrimitive('left','left','',(0.,0.,0.),15.),
        BehaviorPrimitive('right','right','',(0.,0.,0.),-15.)],s)
    assert actions[0].outcome.image_center_error == pytest.approx(0.)
    assert actions[1].outcome.image_center_error == pytest.approx(.189468690981506)
    assert actions[2].outcome.image_center_error == pytest.approx(.189468690981506)


def test_only_signed_yaw_towards_target_centers_it():
    s=scene(bearing=15.)
    actions=wrapper().generate([BehaviorPrimitive('towards','towards','',(0.,0.,0.),15.),
        BehaviorPrimitive('away','away','',(0.,0.,0.),-15.)],s)
    assert actions[0].outcome.image_center_error == pytest.approx(0.,abs=1e-12)
    assert actions[1].outcome.image_center_error == pytest.approx(.408248290463863)


def test_world_rotation_does_not_change_camera_prediction():
    actions=[]
    for yaw in (0.,90.,-150.):
        angle=math.radians(yaw)
        primitive=BehaviorPrimitive('translate','translate','',(-.3*math.sin(angle),.3*math.cos(angle),0.),0.)
        actions.append(wrapper().generate([primitive],scene(bearing=15.,yaw=yaw))[0])
    assert actions[0].outcome.image_center_error == pytest.approx(actions[1].outcome.image_center_error)
    assert actions[0].outcome.image_center_error == pytest.approx(actions[2].outcome.image_center_error)


def test_projection_keeps_measured_center_offset_and_preserves_safety_and_commands():
    s=scene()
    s=replace(s,environment={'camera_center_geometry':{
        **s.environment['camera_center_geometry'],'observed_xy':[.2,-.1]}})
    primitives=[BehaviorPrimitive('hold','hold','',(0.,0.,0.))]
    original=FullFallback().generate(primitives,s)[0]
    result=wrapper().generate(primitives,s)[0]
    assert result.outcome.image_center_error == pytest.approx(math.sqrt(.025))
    assert replace(result.outcome,image_center_error=original.outcome.image_center_error)==original.outcome
    assert result.command_mps==original.command_mps and result.trajectory==original.trajectory
    assert result.metadata['interval_full_fallback'] is True
    assert result.metadata['center_model']=='signed-level-camera-delta-v1'


@pytest.mark.parametrize('skip',['hidden','partially_learned'])
def test_hidden_or_partially_learned_outputs_are_not_silently_changed(skip):
    s=scene()
    primitives=[BehaviorPrimitive('yaw','yaw','',(0.,0.,0.),15.)]
    if skip=='hidden': s=replace(s,target=replace(s.target,visibility_probability=.05),environment={})
    class Base(FullFallback):
        def generate(self, primitives, state):
            actions=super().generate(primitives,state)
            return [replace(a,metadata={**a.metadata,'interval_full_fallback':skip=='hidden'}) for a in actions]
    base=Base()
    assert wrapper(base).generate(primitives,s)==base.generate(primitives,s)


@pytest.mark.parametrize('bad',['missing','nan','zero_fov','bad_size','behind_camera','bad_center'])
def test_invalid_visible_projection_fails_closed(bad):
    s=scene()
    geometry=dict(s.environment['camera_center_geometry'])
    if bad=='missing': s=replace(s,environment={})
    elif bad=='behind_camera': s=replace(s,target=replace(s.target,position_m=(-20.,0.,0.)))
    else:
        if bad=='nan': geometry['pitch_deg']=math.nan
        elif bad=='zero_fov': geometry['horizontal_fov_deg']=0.
        elif bad=='bad_size': geometry['image_size']=[320,0]
        else: geometry['observed_xy']=[2.,0.]
        s=replace(s,environment={'camera_center_geometry':geometry})
    with pytest.raises(ValueError):
        wrapper().generate([BehaviorPrimitive('hold','hold','',(0.,0.,0.))],s)


def test_future_behind_camera_is_maximum_center_error_not_a_false_improvement():
    action=wrapper().generate([BehaviorPrimitive('turn','turn','',(0.,0.,0.),180.)],scene())[0]
    assert action.outcome.image_center_error == 1.


def test_camera_mount_offset_moves_with_yaw_and_pitch_projection():
    s=scene()
    s=replace(s,target=replace(s.target,position_m=(11.,0.,-9.)),environment={
        'camera_center_geometry':{'observed_xy':[0.,0.], 'pitch_deg':-45.,
            'horizontal_fov_deg':90.,'image_size':[320,240],
            'offset_body_forward_right_up_m':[1.,0.,1.]}})
    action=wrapper().generate([BehaviorPrimitive('up','up','',(0.,0.,1.))],s)[0]
    # (10,0,-10) -> (10,0,-11): camera up=-1/sqrt(2), depth=21/sqrt(2).
    assert action.outcome.image_center_error == pytest.approx(1/(21*.75*math.sqrt(2)))
