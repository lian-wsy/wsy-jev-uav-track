"""D5 train-only held-out calibration; validation diagnostics, never deployment.

Uses serial SHA-verified PASS state reads. No simulator, RGB, network, stress or
test data. Original D4 models are untouched. Every run requires a fresh output.
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
import h5py
import numpy as np

from scripts.run_d4_rollout import load_verified
from jev_uav_track.split_integrity import validate_split_integrity
from jev_uav_track.training import (RolloutTrainingConfig, build_training_dataset,
    build_training_dataset_from_episodes, fit_outcome_model_cross_episode,
    LinearOutcomeModel, feature_vector, OUTPUT_NAMES)
from jev_uav_track.residual_intervals import (episode_max_radius, interval_metrics,
    correction_mask, IntervalGatedRolloutModel)
from jev_uav_track.learned_rollout import LearnedOutcomeRolloutModel
from jev_uav_track.models import MissionConstraints
from jev_uav_track.primitives import DEFAULT_PRIMITIVES
from jev_uav_track.safety import filter_safe_actions
from jev_uav_track.jev_client import MockJevClient, UtilityRankingClient
from jev_uav_track.selector import JevTrackSelector


def partition_entries(entries, calibration_count):
    validate_split_integrity(entries)
    # Fixed content ordering; no performance or validation-dependent selection.
    ordered = sorted((e for e in entries if e['split'] == 'train'), key=lambda e: e['sha256'])
    validation = sorted((e for e in entries if e['split'] == 'validation'), key=lambda e: e['sha256'])
    if (type(calibration_count) is not int or not 0 < calibration_count < len(ordered)
            or not validation):
        raise ValueError('Nonempty fit/calibration/validation episode groups required')
    return {'fit': ordered[calibration_count:], 'calibration': ordered[:calibration_count],
            'validation': validation}


def sampled_indices(episode, cfg, features):
    indices = list(range(0, episode.frame_count - cfg.horizon_frames, cfg.stride))
    if len(indices) > cfg.max_samples:
        indices = [indices[i] for i in np.linspace(0, len(indices)-1, cfg.max_samples).round().astype(int)]
    # Guard against silent drift from the training sampler/feature construction.
    actual = np.array([feature_vector(episode.tracking_state(i), episode.camera_velocities[i]) for i in indices])
    np.testing.assert_array_equal(actual, features)
    return indices


def strata_masks(episode, indices, annotations_complete):
    count = len(indices)
    if not annotations_complete:
        return {name: np.full(count, name == 'unknown', dtype=bool)
                for name in ('visible', 'occluded', 'out_of_frame', 'unknown')}
    visible = episode.target_visible[indices]
    in_frame = episode.target_in_frame[indices]
    occluded = episode.target_occluded[indices]
    # Disjoint, current-input strata. Out-of-frame has precedence over occlusion.
    return {'visible': visible & in_frame & ~occluded,
            'occluded': in_frame & occluded, 'out_of_frame': ~in_frame,
            'unknown': in_frame & ~occluded & ~visible}


def _rmse(truth, predictions):
    return {name: dict(zip(OUTPUT_NAMES, np.sqrt(np.mean((value-truth)**2, axis=0)).tolist()))
            for name, value in predictions.items()}


def run(split_path, output, samples=96, rate=16., calibration_count=10, alpha=.1):
    if type(samples) is not int or samples < 1 or not math.isfinite(rate) or rate <= 0:
        raise ValueError('Positive samples and finite hash rate required')
    # Refuse insufficient episode counts before opening source HDFs.
    if type(calibration_count) is not int or calibration_count < 1:
        raise ValueError('Positive calibration count required')
    episode_max_radius([np.zeros((1, 4)) for _ in range(calibration_count)], alpha)
    raw = split_path.read_bytes()
    partition = partition_entries(json.loads(raw)['episodes'], calibration_count)
    output.mkdir(parents=True, exist_ok=False)
    cfg = RolloutTrainingConfig(max_samples=samples, rollout_horizon_s=1., target_mode='baseline_residual')
    code_paths = sorted((ROOT / 'src/jev_uav_track').rglob('*.py')) + [
        Path(__file__), ROOT / 'scripts/run_d4_rollout.py', ROOT / 'scripts/extend_development_split.py']
    protocol = {'schema': 'jev_uav_track.d5.v1', 'kind': 'OFFLINE_RECORDED_ACTION_CALIBRATION',
        'config': asdict(cfg), 'alpha': alpha, 'nominal_coverage': 1-alpha,
        'split_sha256': hashlib.sha256(raw).hexdigest(), 'hash_mib_per_s': rate,
        'partition_rule': 'ascending frozen train SHA; first calibration_count for calibration',
        'partition': {key: [e['filename'] for e in entries] for key, entries in partition.items()},
        'interval_rule': 'ceil((n_episodes+1)*(1-alpha)) order statistic of per-episode maximum absolute errors, separately per outcome',
        'gate_rule': 'fit-feature marginal box AND residual interval excludes zero, per output',
        'test_or_stress_opened': 0, 'validation_used_for_fit_or_thresholds': False,
        'code_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths}}
    (output / 'protocol.json').write_text(json.dumps(protocol, indent=2), encoding='utf-8')
    groups = {key: [] for key in partition}
    annotations = {}
    provenance = []
    with (output / 'verified_reads.jsonl').open('x', encoding='utf-8') as ledger:
        for role, entries in partition.items():
            for entry in entries:
                episode, evidence = load_verified(entry, rate)
                with h5py.File(episode.path, 'r') as handle:
                    complete = all('visibility/' + name in handle for name in
                                   ('target_visible', 'target_occluded', 'target_in_frame'))
                stat = episode.path.stat()
                if (stat.st_size, stat.st_mtime_ns) != (evidence['bytes'], evidence['mtime_ns']):
                    raise ValueError('Source changed during annotation audit')
                annotations[str(episode.path)] = complete
                evidence.update(role=role, visibility_annotations_complete=complete)
                provenance.append(evidence)
                groups[role].append(episode)
                ledger.write(json.dumps(evidence) + '\n'); ledger.flush()
                print(f'Verified {len(provenance)}/{sum(map(len, partition.values()))}: {role} {episode.path.name}', flush=True)
    model, fit_report = fit_outcome_model_cross_episode(groups['fit'], groups['calibration'], cfg)
    model_path = output / 'd5_residual.npz'
    model.save(model_path)
    reloaded = LinearOutcomeModel.load(model_path)
    fit = build_training_dataset_from_episodes(groups['fit'], cfg)
    np.testing.assert_array_equal(model.predict_array(fit.features), reloaded.predict_array(fit.features))
    model = reloaded
    errors = []
    for episode in groups['calibration']:
        data = build_training_dataset(episode, cfg)
        errors.append(data.labels - model.predict_array(data.features))
    radius = episode_max_radius(errors, alpha)
    gated_model = IntervalGatedRolloutModel(model, fit.features, radius)
    learned_model = LearnedOutcomeRolloutModel(model)
    baseline_model = gated_model.baseline
    (output / 'calibration.json').write_text(json.dumps({
        'radius': dict(zip(OUTPUT_NAMES, radius.tolist())), 'alpha': alpha,
        'calibration_episode_max_errors': [np.abs(e).max(axis=0).tolist() for e in errors],
        'fit_min': fit.features.min(axis=0).tolist(), 'fit_max': fit.features.max(axis=0).tolist(),
    }, indent=2), encoding='utf-8')

    truth_all, raw_all, baseline_all, gated_all, accepted_all, masks_all = [], [], [], [], [], []
    rows = []
    decisions = {'frames': 0, 'ranking_only_not_executed': True, 'real_jev_requests': 0,
        'no_safe_candidate_frames': 0, 'safe_selection_frames': 0,
        'candidate_count': 0, 'candidate_full_fallback_count': 0, 'candidate_outside_fit_box_count': 0,
        'ranking_changed_vs_transparent': {'residual': 0, 'interval_gated': 0},
        'mock_vs_utility_ranking_disagreement': 0, 'safe_choices_changed_vs_transparent': 0,
        'candidate_by_id': {p.id: {'count': 0, 'full_fallback_count': 0, 'outside_fit_box_count': 0}
                            for p in DEFAULT_PRIMITIVES}}
    constraints = MissionConstraints(desired_range_m=cfg.desired_range_m)  # default hard safety, NOT relaxed
    with (output / 'records.jsonl').open('x', encoding='utf-8') as records:
        for episode in groups['validation']:
            data = build_training_dataset(episode, cfg)
            absolute = build_training_dataset(episode, replace(cfg, target_mode='absolute'))
            np.testing.assert_array_equal(data.features, absolute.features)
            prediction = model.predict_array(data.features)
            baseline = absolute.labels - data.labels
            raw_prediction = baseline + prediction
            accepted, inside = correction_mask(fit.features, data.features, prediction, radius)
            gated_prediction = baseline + np.where(accepted, prediction, 0.)
            indices = sampled_indices(episode, cfg, data.features)
            masks = strata_masks(episode, indices, annotations[str(episode.path)])
            scores = interval_metrics(absolute.labels, raw_prediction, radius)
            rows.append({'filename': episode.path.name, 'samples': data.sample_count,
                'horizon_s': data.resolved_rollout_horizon_s, 'interval': scores,
                'full_fallback_rate': float((~accepted.any(axis=1)).mean()),
                'outside_fit_box_rate': float((~inside).mean()),
                'rmse': _rmse(absolute.labels, {'transparent': baseline, 'residual': raw_prediction,
                                              'interval_gated': gated_prediction})})
            truth_all.append(absolute.labels); raw_all.append(raw_prediction); baseline_all.append(baseline)
            gated_all.append(gated_prediction); accepted_all.append(accepted); masks_all.append(masks)
            for index in indices:
                state = episode.tracking_state(index, constraints=constraints)
                methods = {'transparent': baseline_model.generate(DEFAULT_PRIMITIVES, state),
                    'residual': learned_model.generate(DEFAULT_PRIMITIVES, state),
                    'interval_gated': gated_model.generate(DEFAULT_PRIMITIVES, state)}
                ranking = {name: UtilityRankingClient().choose(state, candidates).choice
                           for name, candidates in methods.items()}
                mock_rank = MockJevClient().choose(state, methods['interval_gated']).choice
                safe = {name: filter_safe_actions(candidates, state.uav, constraints)[0]
                        for name, candidates in methods.items()}
                assert len({tuple(a.id for a in values) for values in safe.values()}) == 1
                selected = {name: (JevTrackSelector(UtilityRankingClient()).select(state, candidates).action.id
                                  if safe[name] else None) for name, candidates in methods.items()}
                decisions['frames'] += 1
                decisions['no_safe_candidate_frames'] += int(not safe['transparent'])
                decisions['safe_selection_frames'] += int(bool(safe['transparent']))
                decisions['safe_choices_changed_vs_transparent'] += int(selected['interval_gated'] != selected['transparent'])
                decisions['mock_vs_utility_ranking_disagreement'] += int(mock_rank != ranking['interval_gated'])
                for name in ('residual', 'interval_gated'):
                    decisions['ranking_changed_vs_transparent'][name] += int(ranking[name] != ranking['transparent'])
                for candidate in methods['interval_gated']:
                    fallback = candidate.metadata['interval_full_fallback']
                    outside = not candidate.metadata['fit_box_inside']
                    decisions['candidate_count'] += 1
                    decisions['candidate_full_fallback_count'] += int(fallback)
                    decisions['candidate_outside_fit_box_count'] += int(outside)
                    per_id = decisions['candidate_by_id'][candidate.id]
                    per_id['count'] += 1; per_id['full_fallback_count'] += int(fallback)
                    per_id['outside_fit_box_count'] += int(outside)
                records.write(json.dumps({'filename': episode.path.name, 'frame_index': index,
                    'timestamp_s': state.target.timestamp_s, 'ungated_diagnostic_ranking': ranking,
                    'mock_ungated_ranking': mock_rank, 'safe_utility_choice': selected,
                    'safe_candidate_ids': [a.id for a in safe['transparent']],
                    'interval_candidates': {a.id: {'full_fallback': a.metadata['interval_full_fallback'],
                        'accepted_outputs': a.metadata['accepted_outputs'], 'fit_box_inside': a.metadata['fit_box_inside']}
                        for a in methods['interval_gated']}}) + '\n')
    truth, raw_prediction, baseline, gated_prediction = map(np.concatenate,
        (truth_all, raw_all, baseline_all, gated_all))
    accepted = np.concatenate(accepted_all)
    strata = {}
    for name in ('visible', 'occluded', 'out_of_frame', 'unknown'):
        mask = np.concatenate([m[name] for m in masks_all])
        strata[name] = {**interval_metrics(truth[mask], raw_prediction[mask], radius),
            'full_fallback_rate': float((~accepted[mask].any(axis=1)).mean()) if mask.any() else None}
    report = {**protocol, 'status': 'COMPLETED', 'provenance': provenance,
        'python': platform.python_version(), 'numpy': np.__version__, 'h5py': h5py.__version__,
        'model_sha256': hashlib.sha256(model_path.read_bytes()).hexdigest(),
        'fit_samples': fit.sample_count, 'calibration_samples': sum(len(e) for e in errors),
        'validation_samples': len(truth), 'radius': dict(zip(OUTPUT_NAMES, radius.tolist())),
        'validation_interval': interval_metrics(truth, raw_prediction, radius),
        'validation_episode_all_samples_coverage': {name: sum(r['interval']['all_samples_covered'][name]
            for r in rows)/len(rows) for name in OUTPUT_NAMES},
        'validation_strata': strata, 'validation_by_episode': rows,
        'recorded_full_fallback_rate': float((~accepted.any(axis=1)).mean()),
        'recorded_output_fallback_rate': dict(zip(OUTPUT_NAMES, (~accepted).mean(axis=0).tolist())),
        'aggregate_rmse': _rmse(truth, {'transparent': baseline, 'residual': raw_prediction, 'interval_gated': gated_prediction}),
        'action_diagnostics': decisions,
        'limitations': [
            'Offline HDF oracle states and backward camera-velocity action proxy; not visibility-gated live perception.',
            'Nominal episode quantile, not guaranteed coverage: curated towns/seeds need not be exchangeable.',
            'Per-output intervals over sampled frames, not simultaneous across outputs or unseen time steps.',
            'Candidate counterfactual coverage NOT_EVALUATED; fit box cannot establish causal support.',
            'Coverage is for raw residual predictions, not the gated or clipped deployment distribution.',
            'Ranking over all candidates is diagnostic only; default hard safety separately rejects unknown clearance.',
            'Mock has zero identity weight and utility-shaped rankings; agreement is by construction, not real Jev evidence.',
            'No live action, real Jev request, RGB, stress/test access or tracking efficacy claim.',
            'Historical D4 used all 28 train; this new model uses the fit subset only and is not the same estimator.',
        ]}
    with (output / 'report.json').open('x', encoding='utf-8') as dest:
        json.dump(report, dest, indent=2, allow_nan=False)
    print(json.dumps({'status': report['status'], 'radius': report['radius'],
        'validation_coverage': report['validation_interval']['coverage'],
        'recorded_full_fallback_rate': report['recorded_full_fallback_rate']}), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples-per-episode', type=int, default=96)
    parser.add_argument('--calibration-episodes', type=int, default=10)
    parser.add_argument('--alpha', type=float, default=.1)
    parser.add_argument('--hash-mib-per-s', type=float, default=16.)
    args = parser.parse_args()
    run(args.split, args.output, args.samples_per_episode, args.hash_mib_per_s,
        args.calibration_episodes, args.alpha)
