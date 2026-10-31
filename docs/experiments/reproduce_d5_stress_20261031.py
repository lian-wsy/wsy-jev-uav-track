"""Frozen D5 stress-analysis recipe; no fitting, calibration, RPC or deployment.

Uses existing D5 numerical/evaluation functions unchanged. All five previously
exposed stress episodes are included; this is NOT a new validation/final test.
"""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
import h5py
import numpy as np

from scripts.run_d4_rollout import load_verified
from scripts.run_d5_calibration import sampled_inputs, strata_masks
from jev_uav_track.split_integrity import validate_split_integrity
from jev_uav_track.training import OUTPUT_NAMES, RolloutTrainingConfig, LinearOutcomeModel, build_training_dataset
from jev_uav_track.residual_intervals import correction_mask, interval_metrics, IntervalGatedRolloutModel
from jev_uav_track.learned_rollout import LearnedOutcomeRolloutModel
from jev_uav_track.models import MissionConstraints
from jev_uav_track.primitives import DEFAULT_PRIMITIVES
from jev_uav_track.jev_client import UtilityRankingClient, MockJevClient
from jev_uav_track.safety import filter_safe_actions

SPLIT = Path('D:/WPS Cloud/WPSDrive/1574147233/WPS云盘/wsy-jev-uav-track/artifacts/acceptance_20261028/development_split_v2.json')
ORIGINAL = ROOT / 'artifacts/d5_causal_calibration_20261030'
FROZEN = {
    SPLIT: '7326f07e67e90ed0e6397bb553abf31e23cd8d594cf647532a5b02447b9f8e87',
    ORIGINAL / 'd5_residual.npz': 'b67c0a70e50426dcebfff6a97adfaa3f286e1d26fe6a05e4f26e44a97f8ee189',
    ORIGINAL / 'calibration.json': 'fa6b59e42df90ce15e39a1ea83652ff223a7e8c4517a15d1471c5f4d44d27d00',
    ORIGINAL / 'protocol.json': 'dd8ca2293f586000ab8fa71f4d7d4725fd9696a50d881bafA5e567bcf6428130'.lower(),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def emit(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)


def run(output):
    for path, expected in FROZEN.items():
        if sha(path) != expected:
            raise ValueError('Frozen artifact SHA mismatch: ' + str(path))
    manifest = json.loads(SPLIT.read_text(encoding='utf-8'))
    validate_split_integrity(manifest['episodes'])
    entries = sorted((e for e in manifest['episodes'] if e['split'] == 'development_stress'),
                     key=lambda e: e['sha256'])
    if len(entries) != 5:
        raise ValueError('Expected all five frozen stress episodes')
    original_protocol = json.loads((ORIGINAL / 'protocol.json').read_text())
    original_cfg = RolloutTrainingConfig(**original_protocol['config'])
    if original_cfg.input_mode != 'causal_visible' or original_cfg.target_mode != 'baseline_residual':
        raise ValueError('Expected frozen causal residual input contract')
    calibration = json.loads((ORIGINAL / 'calibration.json').read_text())
    radius = np.array([calibration['radius'][key] for key in OUTPUT_NAMES])
    # correction_mask uses only marginal extrema: the saved pair is sufficient.
    fit_box = np.array([calibration['fit_min'], calibration['fit_max']])
    if (fit_box[0] > fit_box[1]).any():
        raise ValueError('Invalid frozen support box')
    model = LinearOutcomeModel.load(ORIGINAL / 'd5_residual.npz')
    model.weights.flags.writeable = False
    gated = IntervalGatedRolloutModel(model, fit_box, radius)
    raw_model = LearnedOutcomeRolloutModel(model)
    constraints = MissionConstraints(desired_range_m=original_cfg.desired_range_m)
    configs = {'original_96': original_cfg,
               'all_eligible': replace(original_cfg, stride=1, max_samples=100000)}
    output.mkdir(parents=True, exist_ok=False)
    protocol = {'kind': 'FROZEN_D5_EXPOSED_DEVELOPMENT_STRESS_ONLY',
        'frozen_artifacts': {str(p): digest for p, digest in FROZEN.items()},
        'configs': {name: asdict(cfg) for name, cfg in configs.items()},
        'episodes': entries, 'hash_mib_per_s': 16., 'retraining': False,
        'recalibration': False, 'threshold_selection': False, 'final_test_access': False,
        'recipe_sha256': sha(Path(__file__)),
        'code_sha256': {p.relative_to(ROOT).as_posix(): sha(p) for p in
            [*sorted((ROOT / 'src/jev_uav_track').rglob('*.py')),
             ROOT / 'scripts/run_d4_rollout.py', ROOT / 'scripts/run_d5_calibration.py',
             ROOT / 'scripts/extend_development_split.py']},
        'limitations': ['Historically exposed stress data, not independent validation or final test.',
            'Visible-oracle causal inputs, not RGB; future privileged truth is labels only.',
            'Recorded camera velocity action proxy; no counterfactual or closed-loop effect.',
            'Intervals describe raw residual predictions, not successful tracking probability.',
            'All eligible frames are dense diagnostics, not independent calibration samples.',
            'No true identity-crossing audit; no newly admitted episode or dataset split change.']}
    emit(output / 'protocol.json', protocol)
    buckets = {name: [] for name in configs}
    provenance = []
    candidate_rows = []
    with (output / 'verified_reads.jsonl').open('x', encoding='utf-8') as ledger, \
            (output / 'records.jsonl').open('x', encoding='utf-8') as records:
        for entry in entries:
            episode, evidence = load_verified(entry, 16.)
            with h5py.File(episode.path, 'r') as handle:
                complete = all('visibility/' + key in handle for key in
                               ('target_visible', 'target_in_frame', 'target_occluded'))
            if not complete:
                raise ValueError('Missing current-frame stratum labels')
            for policy, cfg in configs.items():
                data = build_training_dataset(episode, cfg)
                absolute = build_training_dataset(episode, replace(cfg, target_mode='absolute'))
                np.testing.assert_array_equal(data.features, absolute.features)
                indices, states = sampled_inputs(episode, cfg, data.features, constraints)
                masks = strata_masks(episode, indices, True)
                np.testing.assert_array_equal(sum(masks.values()), np.ones(len(indices)))
                residual = model.predict_array(data.features)
                base = absolute.labels - data.labels
                raw = base + residual
                accepted, inside = correction_mask(fit_box, data.features, residual, radius)
                predicted = base + np.where(accepted, residual, 0.)
                bucket = {'entry': entry, 'truth': absolute.labels, 'raw': raw, 'base': base,
                          'gated': predicted, 'accepted': accepted, 'inside': inside, 'masks': masks,
                          'horizon_s': data.resolved_rollout_horizon_s}
                buckets[policy].append(bucket)
                for row, (index, state) in enumerate(zip(indices, states)):
                    stratum = next(name for name, mask in masks.items() if mask[row])
                    record = {'policy': policy, 'filename': episode.path.name, 'frame_index': index,
                        'stratum': stratum, 'timestamp_s': state.target.timestamp_s,
                        'label_timestamp_s': float(episode.image_timestamps[index + cfg.horizon_frames]),
                        'causal_features': data.features[row].tolist(),
                        'future_label_only': absolute.labels[row].tolist(),
                        'transparent': base[row].tolist(), 'raw_residual': raw[row].tolist(),
                        'interval_gated': predicted[row].tolist(),
                        'accepted_outputs': accepted[row].tolist(), 'fit_box_inside': bool(inside[row])}
                    if policy == 'all_eligible':
                        candidates = {name: method.generate(DEFAULT_PRIMITIVES, state) for name, method in
                                      [('transparent', gated.baseline), ('residual', raw_model), ('interval_gated', gated)]}
                        ranking = {name: UtilityRankingClient().choose(state, actions).choice
                                   for name, actions in candidates.items()}
                        safe = {name: [a.id for a in filter_safe_actions(actions, state.uav, constraints)[0]]
                                for name, actions in candidates.items()}
                        diagnostic = {'filename': episode.path.name, 'frame_index': index,
                            'stratum': stratum, 'ranking': ranking, 'safe_ids': safe,
                            'mock_ranking': MockJevClient().choose(state, candidates['interval_gated']).choice,
                            'candidates': [{'id': a.id, 'fallback': a.metadata['interval_full_fallback'],
                                            'inside': a.metadata['fit_box_inside']} for a in candidates['interval_gated']]}
                        candidate_rows.append(diagnostic)
                        record['candidate_diagnostic_not_execution'] = diagnostic
                    records.write(json.dumps(record, allow_nan=False) + '\n')
            stat = episode.path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (evidence['bytes'], evidence['mtime_ns']):
                raise ValueError('Source changed during evaluation')
            evidence['visibility_annotations_complete'] = complete
            provenance.append(evidence)
            ledger.write(json.dumps(evidence) + '\n'); ledger.flush(); records.flush()
            print('Evaluated ' + str(len(provenance)) + '/5 ' + episode.path.name, flush=True)
    report = {'protocol': protocol, 'status': 'COMPLETED', 'provenance': provenance, 'sampling': {}}
    for policy, values in buckets.items():
        strata = {}
        for name in ('all', 'visible', 'occluded', 'out_of_frame', 'unknown'):
            pieces, by_episode = [], []
            for value in values:
                mask = np.ones(len(value['truth']), dtype=bool) if name == 'all' else value['masks'][name]
                if mask.any():
                    selected = {key: value[key][mask] for key in ('truth', 'raw', 'base', 'gated', 'accepted', 'inside')}
                    pieces.append(selected)
                    by_episode.append({'filename': value['entry']['filename'], 'samples': int(mask.sum()),
                        'interval': interval_metrics(selected['truth'], selected['raw'], radius),
                        'range_rmse': {method: float(np.sqrt(np.mean((selected[key][:, 2]-selected['truth'][:, 2])**2)))
                            for method, key in [('transparent', 'base'), ('raw_residual', 'raw'), ('interval_gated', 'gated')]}})
            if not pieces:
                strata[name] = {'status': 'NOT_EVALUATED', 'samples': 0, 'episodes': 0}
                continue
            combined = {key: np.concatenate([piece[key] for piece in pieces]) for key in pieces[0]}
            strata[name] = {'status': 'EVALUATED_DEVELOPMENT_STRESS', 'samples': len(combined['truth']),
                'episodes': len(pieces), 'interval': interval_metrics(combined['truth'], combined['raw'], radius),
                'recorded_full_fallback_rate': float((~combined['accepted'].any(axis=1)).mean()),
                'recorded_outside_fit_box_rate': float((~combined['inside']).mean()),
                'rmse': {method: dict(zip(OUTPUT_NAMES, np.sqrt(np.mean((combined[key]-combined['truth'])**2, axis=0)).tolist()))
                    for method, key in [('transparent', 'base'), ('raw_residual', 'raw'), ('interval_gated', 'gated')]},
                'by_episode': by_episode,
                'episode_equal_weight_range_rmse': {method: float(np.mean([r['range_rmse'][method] for r in by_episode]))
                    for method in ('transparent', 'raw_residual', 'interval_gated')}}
        report['sampling'][policy] = strata
    report['candidate_diagnostics'] = {}
    for name in ('all', 'visible', 'occluded', 'out_of_frame', 'unknown'):
        rows = [r for r in candidate_rows if name == 'all' or r['stratum'] == name]
        flat = [a for r in rows for a in r['candidates']]
        report['candidate_diagnostics'][name] = {'frames': len(rows), 'candidates': len(flat),
            'fallback_count': sum(a['fallback'] for a in flat), 'outside_fit_box_count': sum(not a['inside'] for a in flat),
            'no_safe_candidate_frames': sum(not r['safe_ids']['interval_gated'] for r in rows),
            'raw_ranking_changed': sum(r['ranking']['residual'] != r['ranking']['transparent'] for r in rows),
            'gated_ranking_changed': sum(r['ranking']['interval_gated'] != r['ranking']['transparent'] for r in rows),
            'mock_utility_disagreement': sum(r['mock_ranking'] != r['ranking']['interval_gated'] for r in rows)}
    for path, expected in FROZEN.items():
        if sha(path) != expected:
            raise ValueError('Frozen artifact changed during evaluation')
    report['frozen_artifacts_unchanged'] = True
    report['records_sha256'] = sha(output / 'records.jsonl')
    report['verified_reads_sha256'] = sha(output / 'verified_reads.jsonl')
    emit(output / 'report.json', report)
    print(json.dumps({'status': 'COMPLETED', 'strata': {name: {'samples': item['samples'], 'episodes': item['episodes']}
                     for name, item in report['sampling']['all_eligible'].items()}}, allow_nan=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
