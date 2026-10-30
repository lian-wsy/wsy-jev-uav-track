"""Synthetic end-to-end recorder smoke. Never creates a real client or simulator."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jev_uav_track.decision_dataset import DecisionDatasetWriter
from jev_uav_track.jev_client import MockJevClient, UtilityRankingClient
from jev_uav_track.models import TargetBelief, TrackingState, UAVState
from jev_uav_track.primitives import DEFAULT_PRIMITIVES
from jev_uav_track.rollout import KinematicRolloutModel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    protocol = {'kind': 'SYNTHETIC', 'code_sha': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
        'model_sha256': hashlib.sha256((repo / 'src/jev_uav_track/rollout.py').read_bytes()).hexdigest(),
        'model_kind': 'synthetic_kinematic_fixture_not_trained_model',
        'max_decisions': 1, 'episodes': [{'id': 'synthetic01', 'map': 'Town02', 'seed': 101,
        'stratum': 'visible', 'split': 'development_train'}]}
    writer = DecisionDatasetWriter(args.output, protocol)
    state = TrackingState(TargetBelief(position_m=(20, 0, 5), timestamp_s=10),
                          UAVState(position_m=(0, 0, 5)))
    actions = KinematicRolloutModel().generate(DEFAULT_PRIMITIVES, state)
    writer.begin_decision('d01', 'synthetic01', state, actions, observation_time_s=10,
        history_times_s=[9, 9.5, 10], decision_time_s=10.02, clock_domain='synthetic_clock',
        safety_sources={'clearance': 'synthetic_fixture', 'battery': 'synthetic_fixture'})
    for name, client in [('mock_jev', MockJevClient()), ('utility', UtilityRankingClient())]:
        writer.record_advice('d01', name, client.choose(state, actions), latency_ms=0)
    # Fixture receipts are not actual RPC acknowledgements. Kind remains SYNTHETIC.
    action = actions[0]
    writer.record_outcome('d01', action.id, execution_trace=[{'timestamp_s': 10.03,
        'duration_s': .1, 'command_mps': action.command_mps,
        'yaw_rate_dps': action.yaw_rate_dps, 'acknowledged': True}],
        label_end_s=11.5, metrics={'collision': None, 'visibility_fraction': None})
    print(json.dumps({'kind': 'SYNTHETIC', 'real_api_requests': 0, 'simulator_actions': 0,
                      'training_admission': 'NOT_AUDITED', 'decisions': 1}))


if __name__ == '__main__':
    main()
