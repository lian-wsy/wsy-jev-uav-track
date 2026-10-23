"""Jev-Track: a safety-gated candidate-behavior selector for active UAV tracking."""

from .models import (
    CandidateAction,
    CandidateOutcome,
    JevDecision,
    MissionConstraints,
    TargetBelief,
    TrackingState,
    UAVState,
)
from .jev_client import (
    DEFAULT_JEV_ENDPOINT,
    DEFAULT_JEV_MODEL,
    JevClient,
    JevProtocolError,
    MockJevClient,
    TypeSafeJevClient,
    UtilityRankingClient,
    build_choice_request,
)
from .pipeline import JevTrackPipeline
from .primitives import DEFAULT_PRIMITIVES, BehaviorPrimitive
from .rollout import KinematicRolloutModel, RolloutConfig
from .selector import JevTrackSelector, SelectionConfig, SelectionResult
from .data import HDF5Episode, HDF5SchemaError
from .evaluation import OfflineEvaluationConfig, compare_jev_reports, evaluate_episode
from .history import (
    BeliefHistoryEntry,
    TargetBeliefHistory,
    attach_history,
    build_episode_belief_history,
    build_target_belief_history,
)
from .learned_rollout import LearnedOutcomeRolloutModel
from .training import (
    ABLATION_DISABLED_FEATURES,
    LinearOutcomeModel,
    RolloutTrainingConfig,
    RolloutTrainingDataset,
    build_training_dataset,
    build_training_dataset_from_episodes,
    feature_mask_for_ablation,
    feature_vector,
    fit_outcome_model,
    fit_outcome_model_cross_episode,
)

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
]
