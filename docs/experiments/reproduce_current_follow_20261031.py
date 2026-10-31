"""Read-only summaries of ALL seven ordered attempts, including failures.

No simulator, training data, key, or network access. Never overwrite evidence.
Run from the repository with PYTHONPATH=src and an explicit new output path.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from jev_uav_track.current_follow import follow_metrics


def summarize(root):
    attempts=[]
    for index in range(1,8):
        path=root/f'town02_current_follow{index:02d}_20261031'
        def read(name):
            source=path/name
            return json.loads(source.read_text(encoding='utf-8')) if source.is_file() else {}
        run,owner=read('current_follow.json'),read('smoke_result.json')
        frames=run.get('frames',[])
        metrics=follow_metrics(frames) if len(frames)>=2 else None
        direction=run.get('scenario',{}).get('direction_xy')
        displacement=([frames[-1]['uav_world_m'][i]-frames[0]['uav_world_m'][i]
                       for i in range(3)] if metrics else None)
        prefixes=run.get('prefixes',[])
        ack=sum(p['prefix_acknowledged'] and p['neutral_acknowledged']
                and p['cancel_returned'] and not p['owner_termination_required'] for p in prefixes)
        submitted=sum(t.get('transport_submission')=='SUBMITTED' for t in run.get('ticks',[]))
        initialization=run.get('neutral_initialization',{})
        stop=initialization.get('stop_trace',[])
        files={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
               for p in sorted(path.iterdir()) if p.is_file()}
        attempts.append({'run':path.name,'original_directory':path.as_posix(),
            'status':run.get('status','NOT_LAUNCHED'),'phase':run.get('phase','preflight'),
            'error':run.get('error'),'owner_status':owner.get('status'),
            'model':run.get('model'),'method':run.get('method'),
            'real_jev_calls':run.get('real_jev_calls',0),
            'initialization_status':initialization.get('status','NOT_RUN'),
            'initialization_stop':stop[-1]['verdict'] if stop else None,
            'frame_count':len(frames),'prefix_receipts':len(prefixes),
            'prefix_submissions':submitted,'acknowledged_prefixes':ack,
            'unconfirmed_submissions':submitted-ack,
            'stop_verdict':run.get('stop_verdict'),
            'metrics_scope':'PARTIAL_ABORTED_TRAJECTORY_NOT_EFFECT_BENCHMARK',
            'partial_metrics':metrics,'uav_displacement_xyz_m':displacement,
            'uav_progress_along_target_direction_m':sum(displacement[i]*direction[i] for i in range(2))
                if displacement is not None and direction is not None else None,
            'selections':run.get('selections',[]),'selection_counts':run.get('selection_counts'),
            'target_destroyed':run.get('target_destroyed'),
            'protected_unchanged':owner.get('protected_unchanged'),
            'cleanup_confirmed':owner.get('cleanup_confirmed'),
            'map_config_restored':owner.get('map_config_restored'),
            'sensor_config_restored':owner.get('sensor_config_restored'),
            'files':files})
    return {'kind':'ALL_CURRENT_FOLLOW_ATTEMPTS_NOT_SUCCESS_BENCHMARK',
        'attempts':attempts,'real_jev_gain':'NOT_EVALUATED','learned_gain':'NOT_EVALUATED',
        'source_hdf_reads':0,'training_runs':0,'new_pass_episodes':0,
        'original_d1_d6_acceptance':'PARTIAL','town03_final_test_pass':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--runs',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(args.runs.resolve()):
        raise ValueError('Output must not modify original run directories')
    report=summarize(args.runs)
    with args.output.open('x',encoding='utf-8') as handle:
        json.dump(report,handle,indent=2,allow_nan=False)
    print(json.dumps({'attempts':len(report['attempts']),
        'output':str(args.output),'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}))
