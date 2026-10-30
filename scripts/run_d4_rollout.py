"""Frozen development cross-episode training and train-only support diagnostics.

Serial SHA-verified PASS reads; never reads stress/test HDF or contacts services.
Outputs raw recorded-action calibration, not deployed or closed-loop performance.
"""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
import math
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
import numpy as np
import h5py

from scripts.extend_development_split import throttled_sha
from jev_uav_track.data import HDF5Episode
from jev_uav_track.holdout import require_development_source
from jev_uav_track.primitives import DEFAULT_PRIMITIVES
from jev_uav_track.split_integrity import validate_split_integrity
from jev_uav_track.rollout_diagnostics import support_report, compare_predictions
from jev_uav_track.training import (INPUT_MODES, RolloutTrainingConfig, build_training_dataset,
    build_training_dataset_from_episodes, fit_outcome_model_cross_episode, LinearOutcomeModel)
from jev_uav_track.learned_rollout import LearnedOutcomeRolloutModel


def load_verified(entry, rate):
    path = Path(entry['path'])
    require_development_source(path, entry)
    before = path.stat()
    if before.st_size != entry['bytes'] or throttled_sha(path, rate) != entry['sha256']:
        raise ValueError(f'Frozen SHA/size mismatch: {path.name}')
    episode = HDF5Episode.open(path, load_images=False)
    require_development_source(episode.path, episode.metadata)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f'Source changed while reading: {path.name}')
    if episode.metadata.get('validation_status') != 'PASS' or episode.frame_count != entry['frame_count']:
        raise ValueError(f'PASS/frame contract mismatch: {path.name}')
    return episode, {'filename': path.name, 'split': entry['split'], 'seed': entry['seed'],
                     'sha256': entry['sha256'], 'bytes': before.st_size, 'mtime_ns': before.st_mtime_ns,
                     'validation_status': 'PASS', 'frames': episode.frame_count}


def run(split_path, output, samples=96, rate=16., input_mode='oracle'):
    if type(samples) is not int or samples < 1 or not math.isfinite(rate) or rate <= 0:
        raise ValueError('Positive sample count and finite hash rate required')
    if input_mode not in INPUT_MODES:
        raise ValueError('Unsupported input_mode')
    raw = split_path.read_bytes()
    manifest = json.loads(raw)
    validate_split_integrity(manifest['episodes'])
    entries = [e for e in manifest['episodes'] if e['split'] in ('train', 'validation')]
    if not all(any(e['split'] == g for e in entries) for g in ('train', 'validation')):
        raise ValueError('Nonempty train and validation required')
    output.mkdir(parents=True, exist_ok=False)
    cfg = RolloutTrainingConfig(max_samples=samples, rollout_horizon_s=1., target_mode='absolute',
                               input_mode=input_mode)
    protocol = {'protocol': 'recorded_action_calibration_only', 'config': asdict(cfg),
                'split_sha256': hashlib.sha256(raw).hexdigest(), 'hash_mib_per_s': rate,
                'train_count': sum(e['split'] == 'train' for e in entries),
                'validation_count': sum(e['split'] == 'validation' for e in entries),
                'test_or_stress_opened': 0, 'hyperparameter_search': False,
                'support': 'train min/max plus nearest standardized sample; diagnostic only',
                'code_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted((ROOT / 'src/jev_uav_track').rglob('*.py'))},
                'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'hash_reader_sha256': hashlib.sha256((ROOT / 'scripts/extend_development_split.py').read_bytes()).hexdigest()}
    (output / 'protocol.json').write_text(json.dumps(protocol, indent=2), encoding='utf-8')
    groups = {'train': [], 'validation': []}
    provenance = []
    with (output / 'verified_reads.jsonl').open('x', encoding='utf-8') as ledger:
        for i, entry in enumerate(entries):
            episode, evidence = load_verified(entry, rate)
            groups[entry['split']].append(episode)
            provenance.append(evidence)
            ledger.write(json.dumps(evidence) + '\n')
            ledger.flush()
            print(f'Verified {i + 1}/{len(entries)} {entry["filename"]}', flush=True)
    models = {}
    for mode in ('absolute', 'baseline_residual'):
        model, _ = fit_outcome_model_cross_episode(groups['train'], groups['validation'], replace(cfg, target_mode=mode))
        destination = output / f'{mode}.npz'
        model.save(destination)
        models[mode] = LinearOutcomeModel.load(destination)
        LearnedOutcomeRolloutModel(models[mode])  # Verify saved inference horizon.
    train = build_training_dataset_from_episodes(groups['train'], cfg)
    rows, validation_features = [], []
    truth_all, baseline_all, absolute_all, residual_all = [], [], [], []
    for episode in groups['validation']:
        data = build_training_dataset(episode, cfg)
        residual_data = build_training_dataset(episode, replace(cfg, target_mode='baseline_residual'))
        np.testing.assert_array_equal(data.features, residual_data.features)
        baseline = data.labels - residual_data.labels
        absolute = models['absolute'].predict_array(data.features)
        residual = models['baseline_residual'].predict_array(data.features)
        rows.append({'filename': episode.path.name, 'samples': data.sample_count,
                     'horizon_s': data.resolved_rollout_horizon_s,
                     'rmse': compare_predictions(data.labels, baseline, absolute, residual),
                     'recorded_support': support_report(train.features, data.features)})
        validation_features.append(data.features)
        truth_all.append(data.labels); baseline_all.append(baseline)
        absolute_all.append(absolute); residual_all.append(residual)
    features = np.concatenate(validation_features)
    candidates = {}
    for primitive in DEFAULT_PRIMITIVES:
        queries = features.copy()
        queries[:, 11:14] = primitive.command_mps
        queries[:, 14] = primitive.yaw_rate_dps
        candidates[primitive.id] = {'command_world_z_up_mps': primitive.command_mps,
                                    'yaw_rate_dps': primitive.yaw_rate_dps,
                                    **support_report(train.features, queries)}
    # Visibility strata describe labels/inputs, not scenario acceptance or identity coverage.
    strata = lambda a: {'count': len(a), 'visible_ge_0_5': int((a[:, 7] >= .5).sum()),
                        'occlusion_risk_gt_0_5': int((a[:, 8] > .5).sum())}
    report = {
        **protocol, 'status': 'COMPLETED', 'python': platform.python_version(),
        'numpy': np.__version__, 'h5py': h5py.__version__, 'provenance': provenance,
        'train_samples': train.sample_count, 'validation_samples': len(features),
        'train_horizon_s': train.resolved_rollout_horizon_s,
        'train_strata': strata(train.features), 'validation_strata': strata(features),
        'aggregate_rmse': compare_predictions(*[np.concatenate(a) for a in (truth_all, baseline_all, absolute_all, residual_all)]),
        'validation_by_episode': rows,
        'recorded_support': support_report(train.features, features),
        'candidate_support': candidates,
        'models_sha256': {mode: hashlib.sha256((output / f'{mode}.npz').read_bytes()).hexdigest() for mode in models},
        'limitations': [('Oracle HDF states with backward velocity, not visibility-gated live belief.'
                         if input_mode == 'oracle' else
                         'Visibility-gated recorded oracle observations; not an RGB detector. Future truth is labels only; image-center input is a bearing proxy.'),
                       'Camera velocity is a recorded action proxy, command yaw-rate input is fixed zero; no counterfactual labels.',
                       'Raw unbounded regression RMSE; deployed clipping/blending not evaluated.',
                       'No test/stress HDF, simulator, real Jev, or tracking-policy actions.',
                       'Support boxes and distances cannot prove causal action support or a safe OOD threshold.'],
    }
    with (output / 'report.json').open('x', encoding='utf-8') as dest:
        json.dump(report, dest, indent=2, allow_nan=False)
    print(json.dumps(report['aggregate_rmse'], indent=2), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples-per-episode', type=int, default=96)
    parser.add_argument('--hash-mib-per-s', type=float, default=16.)
    parser.add_argument('--input-mode', choices=INPUT_MODES, default='oracle')
    args = parser.parse_args()
    run(args.split, args.output, args.samples_per_episode, args.hash_mib_per_s, args.input_mode)
