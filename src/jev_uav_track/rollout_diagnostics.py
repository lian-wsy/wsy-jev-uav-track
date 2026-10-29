"""Descriptive train-only support checks, never a calibrated deployment gate."""
import numpy as np

from .training import FEATURE_NAMES, OUTPUT_NAMES


def _matrix(values, columns):
    data = np.asarray(values, dtype=float)
    if data.ndim != 2 or data.shape[1] != columns or not len(data) or not np.isfinite(data).all():
        raise ValueError('Nonempty finite matrix with expected columns required')
    return data


def support_report(train, queries):
    train = _matrix(train, len(FEATURE_NAMES))
    queries = _matrix(queries, len(FEATURE_NAMES))
    lower, upper = train.min(axis=0), train.max(axis=0)
    outside = (queries < lower - 1e-8) | (queries > upper + 1e-8)
    state = [i for i, name in enumerate(FEATURE_NAMES) if not name.startswith('command_')]
    action = [i for i, name in enumerate(FEATURE_NAMES) if name.startswith('command_')]
    # A constant axis is scaled by one native unit; outside-box still flags
    # any meaningful departure. This distance is descriptive, not calibrated.
    scale = train.std(axis=0)
    scale = np.where(scale < 1e-8, 1., scale)
    distances = []
    for chunk in np.array_split(queries, max(1, (len(queries) + 31) // 32)):
        delta = (chunk[:, None, :] - train[None, :, :]) / scale
        distances.extend(np.sqrt(np.sum(delta ** 2, axis=2)).min(axis=1).tolist())
    return {
        'count': len(queries),
        'state_outside_count': int(outside[:, state].any(axis=1).sum()),
        'action_outside_count': int(outside[:, action].any(axis=1).sum()),
        'joint_box_inside_count': int((~outside.any(axis=1)).sum()),
        'outside_count_by_feature': dict(zip(FEATURE_NAMES, outside.sum(axis=0).tolist())),
        'train_min': dict(zip(FEATURE_NAMES, lower.tolist())),
        'train_max': dict(zip(FEATURE_NAMES, upper.tolist())),
        'nearest_train_standardized_l2': {
            'min': float(np.min(distances)), 'median': float(np.median(distances)),
            'p95': float(np.quantile(distances, .95)), 'max': float(np.max(distances)),
        },
        'interpretation': 'Inside marginal box is necessary only, not joint/action causal support. No threshold is tuned or used for inference.',
    }


def compare_predictions(truth, baseline, absolute, residual):
    arrays = [_matrix(v, len(OUTPUT_NAMES)) for v in (truth, baseline, absolute, residual)]
    if len({v.shape for v in arrays}) != 1:
        raise ValueError('All predictions must align on the same samples')
    truth, baseline, absolute, residual = arrays
    return {name: dict(zip(OUTPUT_NAMES, np.sqrt(np.mean((prediction - truth) ** 2, axis=0)).tolist()))
            for name, prediction in [('transparent', baseline), ('absolute', absolute),
                                      ('residual', baseline + residual)]}
