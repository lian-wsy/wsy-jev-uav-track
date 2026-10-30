"""Offline residual learning-curve experiments, not a deployment controller.

All scaling/weights use fit episodes. Shrinkage uses calibration episodes only.
Validation is evaluated after these choices; no coverage guarantee is claimed.
"""
from dataclasses import dataclass
import numpy as np


@dataclass
class StudyPredictor:
    mean: np.ndarray
    scale: np.ndarray
    weights: np.ndarray
    projection: np.ndarray
    phase: np.ndarray
    lower: np.ndarray
    upper: np.ndarray

    def design(self, x):
        z = (np.asarray(x, dtype=float) - self.mean) / self.scale
        if self.projection.shape[1]:
            z = np.cos(z @ self.projection + self.phase) * np.sqrt(2 / len(self.phase))
        return np.column_stack([np.ones(len(z)), z])

    def predict(self, x):
        return self.design(x) @ self.weights

    def supported(self, x):
        x = np.asarray(x)
        return ((x >= self.lower - 1e-8) & (x <= self.upper + 1e-8)).all(axis=1)

    def save(self, path):
        np.savez(path, **self.__dict__)

    @classmethod
    def load(cls, path):
        with np.load(path, allow_pickle=False) as data:
            return cls(**{name: data[name] for name in cls.__dataclass_fields__})


def fit_predictor(x, residual, kind, ridge=1.):
    x, y = np.asarray(x, dtype=float), np.asarray(residual, dtype=float)
    if (x.ndim != 2 or y.ndim != 2 or not len(x) or len(x) != len(y)
            or not np.isfinite(x).all() or not np.isfinite(y).all()
            or not np.isfinite(ridge) or ridge <= 0):
        raise ValueError('Finite aligned fit arrays and positive ridge required')
    if kind not in ('linear', 'rff64'):
        raise ValueError('Unknown study model')
    scale = x.std(axis=0)
    scale = np.where(scale < 1e-8, 1., scale)
    rng = np.random.default_rng(20261031)
    width = 64 if kind == 'rff64' else 0
    projection = rng.normal(size=(x.shape[1], width)) / np.sqrt(x.shape[1])
    phase = rng.uniform(0., 2 * np.pi, size=width)
    model = StudyPredictor(x.mean(axis=0), scale, np.empty((0, 0)), projection,
                           phase, x.min(axis=0), x.max(axis=0))
    design = model.design(x)
    penalty = np.eye(design.shape[1]) * ridge
    penalty[0, 0] = 0.
    model.weights = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return model


def calibrate_shrinkage(model, episodes):
    """Minimize episode-equal MSE of the *supported* correction per output.

    Grid is predeclared, including zero. Ties favor smaller corrections.
    This is calibration-set selection, not independent coverage validation.
    Each episode is (features, true residual); final validation is never passed.
    """
    if not episodes:
        raise ValueError('Calibration episodes required')
    grid = np.array([0., .25, .5, .75, 1.])
    losses = []
    for x, truth in episodes:
        truth = np.asarray(truth, dtype=float)
        prediction = model.predict(x) * model.supported(x)[:, None]
        if not len(truth) or truth.shape != prediction.shape or not np.isfinite(truth).all():
            raise ValueError('Finite aligned calibration labels required')
        losses.append(np.mean((prediction[None] * grid[:, None, None] - truth[None])**2, axis=1))
    risk = np.mean(losses, axis=0)
    chosen = np.argmin(risk, axis=0)
    return grid[chosen], {'grid': grid.tolist(), 'episode_equal_mse': risk.tolist(),
                          'selected_grid_indices': chosen.tolist(),
                          'selection_data': 'calibration_only', 'coverage_guarantee': False}
