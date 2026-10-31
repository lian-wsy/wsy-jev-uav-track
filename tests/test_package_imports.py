"""Runtime probes must not load the research stack through the package facade."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def fresh_python(code):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / 'src'))
    result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize('entry', ['import jev_uav_track',
    'from jev_uav_track.isolated_runtime import RuntimePorts',
    'from jev_uav_track.body_probe import require_body_receipt',
    'from scripts import probe_isolated_carla'])
def test_runtime_imports_do_not_load_training_data_or_jev(entry):
    fresh_python("""
import sys
blocked = ('jev_uav_track.training', 'jev_uav_track.data', 'jev_uav_track.closed_loop',
           'jev_uav_track.evaluation', 'jev_uav_track.jev_client', 'numpy', 'h5py')
class RejectResearch:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('Runtime imported unnecessary research dependency: ' + fullname)
sys.meta_path.insert(0, RejectResearch())
""" + entry + """
import jev_uav_track
assert 'JevTrackPipeline' in dir(jev_uav_track)
assert not any(name in sys.modules for name in blocked)
""")


def test_historical_public_exports_resolve_to_original_objects_and_remain_cacheable():
    # The pre-refactor public contract, independently enumerated from callers.
    fresh_python("""
import importlib, pickle
import jev_uav_track as package
exports = {
    'models': ('CandidateAction','CandidateOutcome','JevDecision','MissionConstraints',
               'TargetBelief','TrackingState','UAVState'),
    'jev_client': ('DEFAULT_JEV_ENDPOINT','DEFAULT_JEV_MODEL','JevClient','JevProtocolError',
                   'MockJevClient','TypeSafeJevClient','UtilityRankingClient','build_choice_request'),
    'pipeline': ('JevTrackPipeline',),
    'primitives': ('DEFAULT_PRIMITIVES','BehaviorPrimitive'),
    'rollout': ('KinematicRolloutModel','RolloutConfig'),
    'selector': ('JevTrackSelector','SelectionConfig','SelectionResult'),
    'data': ('HDF5Episode','HDF5SchemaError'),
    'evaluation': ('OfflineEvaluationConfig','compare_jev_reports','evaluate_episode'),
    'history': ('BeliefHistoryEntry','TargetBeliefHistory','attach_history',
                'build_episode_belief_history','build_target_belief_history'),
    'learned_rollout': ('LearnedOutcomeRolloutModel',),
    'training': ('ABLATION_DISABLED_FEATURES','LinearOutcomeModel','RolloutTrainingConfig',
        'RolloutTrainingDataset','build_training_dataset','build_training_dataset_from_episodes',
        'feature_mask_for_ablation','feature_vector','fit_outcome_model','fit_outcome_model_cross_episode'),
    'closed_loop': ('ClosedLoopReplayConfig','aggregate_closed_loop_reports',
                    'compare_closed_loop_reports','run_closed_loop_replay')}
expected = {name for names in exports.values() for name in names}
assert set(package.__all__) == expected
for module, names in exports.items():
    for name in names:
        value = getattr(package, name)
        assert value is getattr(importlib.import_module('jev_uav_track.' + module), name)
        assert getattr(package, name) is value
namespace = {}
exec('from jev_uav_track import *', namespace)
assert expected <= namespace.keys()
assert all(namespace[name] is getattr(package, name) for name in expected)
assert pickle.loads(pickle.dumps(package.RolloutConfig)) is package.RolloutConfig
from jev_uav_track import isolated_runtime
assert isolated_runtime.RuntimePorts(carla_rpc=3200, carla_streaming=3201,
    carla_secondary=3202, airsim_rpc=42461).airsim_rpc == 42461
try:
    package.nonexistent_export
except AttributeError:
    pass
else:
    raise AssertionError('Unknown export must raise AttributeError')
""")
