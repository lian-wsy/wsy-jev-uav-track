"""Frozen offline learning curves and calibration-only correction shrinkage.

No simulator, service calls, stress/test reads or automatic model promotion.
"""
import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
import numpy as np

from scripts.run_d4_rollout import load_verified
from scripts.run_d5_calibration import partition_entries, sampled_inputs, strata_masks
from jev_uav_track.training import RolloutTrainingConfig, build_training_dataset, OUTPUT_NAMES, feature_vector
from jev_uav_track.models import MissionConstraints
from jev_uav_track.primitives import DEFAULT_PRIMITIVES
from jev_uav_track.residual_intervals import episode_max_radius, correction_mask
from jev_uav_track.residual_study import fit_predictor, calibrate_shrinkage, StudyPredictor


def metrics(truth, prediction):
    return dict(zip(OUTPUT_NAMES, np.sqrt(np.mean((prediction-truth)**2, axis=0)).tolist()))


def run(split, output, sizes=(6, 12, 18), calibration_count=10, alpha=.1, samples=96, rate=16.):
    if type(samples) is not int or samples < 1 or not math.isfinite(rate) or rate <= 0:
        raise ValueError('Positive samples and finite hash rate required')
    raw = split.read_bytes()
    partition = partition_entries(json.loads(raw)['episodes'], calibration_count)
    if (not sizes or any(type(n) is not int or n < 1 or n > len(partition['fit']) for n in sizes)
            or tuple(sorted(set(sizes))) != tuple(sizes)):
        raise ValueError('Increasing unique fit sizes within frozen fit pool required')
    episode_max_radius([np.zeros((1, 4)) for _ in range(calibration_count)], alpha)
    output.mkdir(parents=True, exist_ok=False)
    cfg = RolloutTrainingConfig(max_samples=samples, target_mode='baseline_residual',
                               rollout_horizon_s=1., input_mode='causal_visible')
    paths = sorted((ROOT/'src/jev_uav_track').rglob('*.py')) + [Path(__file__),
        ROOT/'scripts/run_d5_calibration.py', ROOT/'scripts/run_d4_rollout.py',
        ROOT/'scripts/extend_development_split.py']
    protocol = {'schema': 'jev_uav_track.rollout_optimization.v1',
        'split_sha256': hashlib.sha256(raw).hexdigest(), 'input_mode': cfg.input_mode,
        'partition': {k: [e['filename'] for e in v] for k, v in partition.items()},
        'fit_sizes': list(sizes), 'subset_rule': 'nested ascending frozen SHA; no seed search',
        'models': ['linear', 'rff64'], 'ridge_alpha': 1., 'random_feature_seed': 20261031,
        'rff_dimensions': 64, 'rff_scale': '1/sqrt(input_dimension), standardization fit only',
        'shrinkage_grid': [0., .25, .5, .75, 1.],
        'shrinkage_selection': 'episode-equal calibration MSE, per-output; ties choose smaller',
        'validation_used_for_selection': False, 'test_or_stress_opened': 0,
        'samples_per_episode': samples, 'horizon_s': 1., 'stride': cfg.stride,
        'legacy_interval_alpha': alpha, 'hash_mib_per_s': rate,
        'code_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    (output/'protocol.json').write_text(json.dumps(protocol, indent=2), encoding='utf-8')
    groups = {k: [] for k in partition}
    provenance = []
    constraints = MissionConstraints(desired_range_m=cfg.desired_range_m)
    with (output/'verified_reads.jsonl').open('x', encoding='utf-8') as ledger:
        for role, entries in partition.items():
            for entry in entries:
                episode, proof = load_verified(entry, rate)
                data = build_training_dataset(episode, cfg)
                absolute = build_training_dataset(episode, replace(cfg, target_mode='absolute'))
                np.testing.assert_array_equal(data.features, absolute.features)
                indices, states = sampled_inputs(episode, cfg, data.features, constraints)
                strata = strata_masks(episode, indices, True)  # causal builder already requires all sources
                candidate_x = np.array([feature_vector(state, primitive.command_mps, primitive.yaw_rate_dps)
                    for state in states for primitive in DEFAULT_PRIMITIVES]) if role == 'validation' else None
                groups[role].append({'filename': entry['filename'], 'x': data.features,
                    'y': data.labels, 'absolute': absolute.labels,
                    'baseline': absolute.labels-data.labels, 'candidate_x': candidate_x,
                    'strata': {k: int(v.sum()) for k, v in strata.items()}})
                proof['role'] = role
                provenance.append(proof)
                ledger.write(json.dumps(proof)+'\n'); ledger.flush()
                print(f'Verified {len(provenance)}/{sum(map(len,partition.values()))}: {role} {entry["filename"]}', flush=True)
    experiments = []
    for size in sizes:
        fit = groups['fit'][:size]
        x, y = (np.concatenate([e[k] for e in fit]) for k in ('x', 'y'))
        for kind in protocol['models']:
            model = fit_predictor(x, y, kind)
            model_path = output/f'{kind}_{size}.npz'
            model.save(model_path)
            restored = StudyPredictor.load(model_path)
            np.testing.assert_array_equal(model.predict(x), restored.predict(x))
            model = restored
            blend, calibration = calibrate_shrinkage(model, [(e['x'], e['y']) for e in groups['calibration']])
            radius = episode_max_radius([e['y']-model.predict(e['x']) for e in groups['calibration']], alpha)
            records = []
            totals = {k: [] for k in ('truth', 'baseline', 'raw', 'old_gate', 'shrinkage')}
            candidate = {'count': 0, 'outside_fit_box': 0, 'old_gate_any_correction': 0,
                         'shrinkage_any_correction': 0, 'executed_actions': 0,
                         'counterfactual_benefit': 'NOT_EVALUATED'}
            for e in groups['validation']:
                pred = model.predict(e['x'])
                old_mask, _ = correction_mask(x, e['x'], pred, radius)
                correction = pred * blend * model.supported(e['x'])[:, None]
                predictions = {'baseline': e['baseline'], 'raw': e['baseline']+pred,
                    'old_gate': e['baseline']+np.where(old_mask, pred, 0.),
                    'shrinkage': e['baseline']+correction}
                totals['truth'].append(e['absolute'])
                for k in predictions:
                    totals[k].append(predictions[k])
                records.append({'filename': e['filename'], 'samples': len(e['x']), 'strata': e['strata'],
                    'rmse': {k: metrics(e['absolute'], v) for k, v in predictions.items()},
                    'supported_fraction': float(model.supported(e['x']).mean()),
                    'shrinkage_nonzero_fraction': float(np.any(np.abs(correction)>1e-12, axis=1).mean())})
                cx = e['candidate_x']; cp = model.predict(cx)
                cm, _ = correction_mask(x, cx, cp, radius)
                candidate['count'] += len(cx)
                candidate['outside_fit_box'] += int((~model.supported(cx)).sum())
                candidate['old_gate_any_correction'] += int(cm.any(axis=1).sum())
                candidate['shrinkage_any_correction'] += int((np.abs(cp*blend*model.supported(cx)[:,None])>1e-12).any(axis=1).sum())
            arrays = {k: np.concatenate(v) for k, v in totals.items()}
            experiments.append({'fit_count': size, 'model': kind,
                'fit_episodes': [e['filename'] for e in fit], 'fit_rmse': metrics(y, model.predict(x)),
                'model_sha256': hashlib.sha256(model_path.read_bytes()).hexdigest(),
                'blend': dict(zip(OUTPUT_NAMES, blend.tolist())), 'calibration': calibration,
                'old_interval_radius': dict(zip(OUTPUT_NAMES, radius.tolist())),
                'aggregate_rmse': {k: metrics(arrays['truth'], v) for k,v in arrays.items() if k!='truth'},
                'episode_equal_rmse': {k: {name: float(np.mean([e['rmse'][k][name] for e in records]))
                    for name in OUTPUT_NAMES} for k in predictions},
                'validation': records, 'candidate_diagnostics': candidate})
            print(f'Completed {kind} fit={size} blend={blend.tolist()}', flush=True)
    result = {'status': 'COMPLETED', 'protocol': protocol, 'provenance': provenance,
        'experiments': experiments, 'automatically_selected_deployment_model': None,
        'limitations': ['Recorded velocity is an action proxy, not randomized action outcomes.',
            'Visible-oracle inputs and rule-derived labels, not RGB perception.',
            'Single nested episode ordering; size effects confounded with added episode composition.',
            'Calibration-selected shrinkage is a heuristic, not guaranteed coverage or safety.',
            'No actions executed. Candidate correction acceptance does not prove benefit.',
            'Six development validation episodes already used historically; not an independent final test.',
            'All declared comparisons reported; no threshold/model selection using validation.']}
    (output/'report.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--hash-mib-per-s', type=float, default=16.)
    args = parser.parse_args()
    run(args.split, args.output, rate=args.hash_mib_per_s)
