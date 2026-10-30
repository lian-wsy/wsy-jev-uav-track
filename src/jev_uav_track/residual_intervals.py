"""Episode-held-out residual intervals and an OFFLINE diagnostic gate.

Nominal coverage is not a guarantee under town shift or for counterfactual
actions. Safety fields are never learned, calibrated, or relaxed here.
"""
from dataclasses import replace
import math

import numpy as np

from .learned_rollout import LearnedOutcomeRolloutModel
from .training import FEATURE_NAMES, OUTPUT_NAMES, feature_vector


def matrix(values, columns, *, allow_empty=False):
    data = np.asarray(values, dtype=float)
    if (data.ndim != 2 or data.shape[1] != columns
            or (not allow_empty and not len(data)) or not np.isfinite(data).all()):
        raise ValueError('Finite matrix with expected columns required')
    return data


def radius_vector(radius):
    values = np.asarray(radius, dtype=float)
    if values.shape != (len(OUTPUT_NAMES),) or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError('Four finite nonnegative radii required')
    return values


def episode_max_radius(errors_by_episode, alpha=.1):
    """One score/episode/output: max absolute error over that episode's samples.

    Finite-sample upper order statistic; insufficient episode count is rejected
    rather than clipping the rank and silently understating uncertainty.
    """
    if not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError('alpha must be in (0,1)')
    scores = [np.abs(matrix(e, len(OUTPUT_NAMES))).max(axis=0) for e in errors_by_episode]
    rank = math.ceil((len(scores) + 1) * (1 - alpha))
    if not scores or rank > len(scores):
        raise ValueError('Too few calibration episodes for a finite interval at this alpha')
    return np.sort(scores, axis=0)[rank - 1]


def interval_metrics(truth, prediction, radius):
    truth = matrix(truth, len(OUTPUT_NAMES), allow_empty=True)
    prediction = matrix(prediction, len(OUTPUT_NAMES), allow_empty=True)
    radius = radius_vector(radius)
    if truth.shape != prediction.shape:
        raise ValueError('Prediction and truth must align')
    if not len(truth):
        return {'status': 'NOT_EVALUATED', 'count': 0, 'coverage': None, 'width': None,
                'all_samples_covered': None}
    covered = np.abs(truth - prediction) <= radius + 1e-12
    return {'status': 'EVALUATED', 'count': len(truth),
            'coverage': dict(zip(OUTPUT_NAMES, covered.mean(axis=0).tolist())),
            'width': dict(zip(OUTPUT_NAMES, (2 * radius).tolist())),
            'all_samples_covered': dict(zip(OUTPUT_NAMES, covered.all(axis=0).tolist()))}


def correction_mask(fit_features, queries, residual, radius):
    """Accept each correction only in the fit box and if its interval excludes 0.

    Marginal support is a necessary heuristic, never causal action support.
    No validation labels or threshold tuning are involved.
    """
    fit = matrix(fit_features, len(FEATURE_NAMES))
    queries = matrix(queries, len(FEATURE_NAMES))
    residual = matrix(residual, len(OUTPUT_NAMES))
    radius = radius_vector(radius)
    if len(queries) != len(residual):
        raise ValueError('Queries and residuals must align')
    inside = ((queries >= fit.min(axis=0) - 1e-8)
              & (queries <= fit.max(axis=0) + 1e-8)).all(axis=1)
    return inside[:, None] & (np.abs(residual) > radius + 1e-12), inside


class IntervalGatedRolloutModel:
    """Diagnostic residual correction, retaining baseline trajectory/safety.

    Intervals are calibrated on recorded actions, NOT new candidate actions;
    this wrapper is not a deployment authorization or a calibrated safety gate.
    """

    def __init__(self, predictor, fit_features, radius):
        if predictor.target_mode != 'baseline_residual':
            raise ValueError('Residual predictor required')
        self.learned = LearnedOutcomeRolloutModel(predictor)
        self.baseline = self.learned.baseline
        self.fit_features = matrix(fit_features, len(FEATURE_NAMES)).copy()
        self.radius = radius_vector(radius).copy()

    def simulate(self, primitive, state):
        baseline = self.baseline.simulate(primitive, state)
        query = np.array([feature_vector(state, primitive.command_mps, primitive.yaw_rate_dps)])
        correction = self.learned.predictor.predict_array(query)
        accepted, inside = correction_mask(self.fit_features, query, correction, self.radius)
        changes = {}
        if accepted.any():
            learned = self.learned.simulate(primitive, state)
            changes = {name: getattr(learned.outcome, name)
                       for name, use in zip(OUTPUT_NAMES, accepted[0]) if use}
        return replace(baseline, outcome=replace(baseline.outcome, **changes), metadata={
            **baseline.metadata, 'model': 'offline-interval-gated-residual',
            'interval_full_fallback': not bool(accepted.any()),
            'fit_box_inside': bool(inside[0]), 'accepted_outputs': list(changes),
            'counterfactual_coverage': 'NOT_EVALUATED',
        })

    def generate(self, primitives, state):
        return [self.simulate(primitive, state) for primitive in primitives]
