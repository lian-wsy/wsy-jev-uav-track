"""Jev-Track public API, loaded on demand.

Importing an isolated runtime/measurement module must not load training, data,
replay or Jev networking helpers. Explicit public imports retain their original
objects and modules; requesting the whole API still loads the whole API.
"""

from importlib import import_module as _import_module

_EXPORT_GROUPS = {
    '.models': ('CandidateAction', 'CandidateOutcome', 'JevDecision', 'MissionConstraints',
                'TargetBelief', 'TrackingState', 'UAVState'),
    '.jev_client': ('DEFAULT_JEV_ENDPOINT', 'DEFAULT_JEV_MODEL', 'JevClient', 'JevProtocolError',
                    'MockJevClient', 'TypeSafeJevClient', 'UtilityRankingClient', 'build_choice_request'),
    '.pipeline': ('JevTrackPipeline',),
    '.primitives': ('DEFAULT_PRIMITIVES', 'BehaviorPrimitive'),
    '.rollout': ('KinematicRolloutModel', 'RolloutConfig'),
    '.selector': ('JevTrackSelector', 'SelectionConfig', 'SelectionResult'),
    '.data': ('HDF5Episode', 'HDF5SchemaError'),
    '.evaluation': ('OfflineEvaluationConfig', 'compare_jev_reports', 'evaluate_episode'),
    '.history': ('BeliefHistoryEntry', 'TargetBeliefHistory', 'attach_history',
                 'build_episode_belief_history', 'build_target_belief_history'),
    '.learned_rollout': ('LearnedOutcomeRolloutModel',),
    '.training': ('ABLATION_DISABLED_FEATURES', 'LinearOutcomeModel', 'RolloutTrainingConfig',
        'RolloutTrainingDataset', 'build_training_dataset', 'build_training_dataset_from_episodes',
        'feature_mask_for_ablation', 'feature_vector', 'fit_outcome_model', 'fit_outcome_model_cross_episode'),
    '.closed_loop': ('ClosedLoopReplayConfig', 'aggregate_closed_loop_reports',
                     'compare_closed_loop_reports', 'run_closed_loop_replay'),
}
_EXPORT_MODULES = {name: module for module, names in _EXPORT_GROUPS.items() for name in names}


def __getattr__(name):
    try:
        module = _EXPORT_MODULES[name]
    except KeyError:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}') from None
    value = getattr(_import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "BehaviorPrimitive",
    "BeliefHistoryEntry",
    "ABLATION_DISABLED_FEATURES",
    "CandidateAction",
    "CandidateOutcome",
    "DEFAULT_PRIMITIVES",
    "DEFAULT_JEV_ENDPOINT",
    "DEFAULT_JEV_MODEL",
    "JevDecision",
    "JevClient",
    "JevProtocolError",
    "JevTrackPipeline",
    "JevTrackSelector",
    "KinematicRolloutModel",
    "HDF5Episode",
    "HDF5SchemaError",
    "LearnedOutcomeRolloutModel",
    "LinearOutcomeModel",
    "MissionConstraints",
    "MockJevClient",
    "UtilityRankingClient",
    "OfflineEvaluationConfig",
    "RolloutConfig",
    "RolloutTrainingConfig",
    "RolloutTrainingDataset",
    "SelectionConfig",
    "SelectionResult",
    "TargetBeliefHistory",
    "TargetBelief",
    "TypeSafeJevClient",
    "TrackingState",
    "UAVState",
    "build_choice_request",
    "build_episode_belief_history",
    "build_target_belief_history",
    "build_training_dataset",
    "build_training_dataset_from_episodes",
    "compare_jev_reports",
    "evaluate_episode",
    "feature_vector",
    "feature_mask_for_ablation",
    "fit_outcome_model",
    "fit_outcome_model_cross_episode",
    "attach_history",
    "ClosedLoopReplayConfig",
    "aggregate_closed_loop_reports",
    "run_closed_loop_replay",
    "compare_closed_loop_reports",
]
