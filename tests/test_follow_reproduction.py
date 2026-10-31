"""Include later failed runs without relabeling a completed control window."""
import json
from pathlib import Path
import importlib.util

import pytest


def test_later_attempt_summary_retains_failure_and_quantifies_direction_and_duty(tmp_path):
    path=Path(__file__).resolve().parents[1]/'docs/experiments/reproduce_current_follow_20261031.py'
    spec=importlib.util.spec_from_file_location('reproduce_follow',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for index in range(1,10):
        (tmp_path/f'town02_current_follow{index:02d}_20261031').mkdir()
    run={'status':'FOLLOW_NOT_ACCEPTED','phase':'bounded_follow',
         'requested_duration_s':10.,'stop_verdict':'STOP_TIMEOUT',
         'scenario':{'direction_xy':[1.,0.]},
         'frames':[{'wall_s':10.+i,'visible':True,'uav_world_m':[.02*i,0.,16.-.06*i],
                    'target_measurement_world_m':[12.+.15*i,0.,0.],
                    'center_error':.1,'collision_new':False} for i in range(11)],
         'prefixes':[{'prefix_acknowledged':True,'neutral_acknowledged':True,'cancel_returned':True,
                      'owner_termination_required':False,'neutral_completed_s':20.,
                      'command':{'duration_s':.05,'velocity_mps':[.3,0.,0.]}}],
         'ticks':[{'transport_submission':'SUBMITTED'}, {'transport_submission':'SUBMITTED'}],
         'selections':[{'action_id':'orbit_left'}],
         'stop_trace':[{'now_s':20.5,'verdict':'STOP_TIMEOUT',
                        'telemetry':{'linear_velocity_mps':[.06,0.,0.]}}]}
    # A failed prefix still has a receipt and a sent command; its duration
    # must not be mislabeled as acknowledged physical execution.
    run['prefixes'].append({**run['prefixes'][0], 'prefix_acknowledged':False,
        'owner_termination_required':True,'prefix_failure_kind':'EXCEPTION','prefix_error_type':'TimeoutError'})
    (tmp_path/'town02_current_follow09_20261031/current_follow.json').write_text(json.dumps(run))
    assert len(module.summarize(tmp_path)['attempts'])==7
    result=module.summarize(tmp_path,last_attempt=9)['attempts'][-1]
    assert result['status']=='FOLLOW_NOT_ACCEPTED'
    assert result['stop_verdict']=='STOP_TIMEOUT'
    assert result['motion_diagnostics']['uav_progress_m']==pytest.approx(.2)
    assert result['motion_diagnostics']['target_progress_m']==pytest.approx(1.5)
    assert result['motion_diagnostics']['altitude_change_m']==pytest.approx(-.6)
    assert result['motion_diagnostics']['issued_prefix_seconds']==.1
    assert result['motion_diagnostics']['forward_command_integral_m']==pytest.approx(.03)
    assert result['acknowledged_prefixes']==1
    assert result['unconfirmed_submissions']==1
    assert result['motion_diagnostics']['command_integral_scope']==(
        'ALL_RECEIPTED_COMMANDS_INCLUDING_UNCERTAIN_EXCLUDES_UNRECEIPTED_SUBMISSIONS_NOT_ACTUAL_MOTION_OR_BOUND')
    assert result['motion_diagnostics']['last_stop_speed_mps']==.06
    assert result['motion_diagnostics']['choices']=={'orbit_left':1}
