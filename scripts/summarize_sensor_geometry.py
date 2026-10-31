"""Offline analysis of bounded geometry receipts; never authorizes control.

Example: python scripts/summarize_sensor_geometry.py --input api_probe.json
         --output artifacts/new_geometry_summary.json
Input is read-only; output is exclusive-create. No simulator/SDK/network imports.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from jev_uav_track.frames import camera_mount_error


def xyz(value):
    return tuple(value[key] for key in ('x_val', 'y_val', 'z_val'))


def xyzw(value):
    return tuple(value[key] for key in ('x_val', 'y_val', 'z_val', 'w_val'))


def state_summary(row):
    result = {'timestamp_ns': row['payload']['timestamp'], 'geometry_status': row['geometry_status'],
              'position_ned_m': None, 'speed_mps': None, 'landed_state': None}
    if row['geometry'] is not None:
        kin = row['geometry']['kinematics_estimated']
        result.update(position_ned_m=xyz(kin['position']), orientation_xyzw=xyzw(kin['orientation']),
                      velocity_mps=xyz(kin['linear_velocity']), speed_mps=math.hypot(*xyz(kin['linear_velocity'])),
                      angular_velocity=xyz(kin['angular_velocity']), landed_state=row['geometry']['landed_state'])
    return result


def summarize(api):
    report = api['safety_probe']
    if (api.get('kind') != 'LIVE_OWNED_SAFETY_SENSOR_DIAGNOSTIC'
            or api.get('map', '').split('/')[-1] != 'Town02' or api.get('settings_match') is not True
            or report.get('capture_geometry') is not True or report.get('geometry_schema_version') != 1
            or report.get('control_ready') is not False or report.get('clearance_m') is not None
            or report.get('status') != 'DIAGNOSTIC_COMPLETED'):
        raise ValueError('Need completed owned Town02 no-motion raw geometry receipt')
    if (type(report.get('completed_samples')) is not int or not 1 <= report['completed_samples'] <= 6
            or len(report['queries']) != report['rpc_count'] or report['rpc_count'] > 30):
        raise ValueError('Invalid diagnostic count')
    # Reject nonfinite JSON rather than introducing permissive summary values.
    json.dumps(api, allow_nan=False)
    bands = [10., 10. - 40./3, 10. - 80./3, -30.]
    result = {'evidence_class': 'OFFLINE_ANALYSIS_OF_RAW_DIAGNOSTIC',
              'frame_alignment_status': 'NOT_CALIBRATED', 'control_ready': False, 'clearance_m': None,
              'mount_hypothesis': 'SensorLocalFrame to vehicle-inertial NED, identity rotation, offset (0,0,-0.5)m',
              'timing_limit': 'Same query round is not simultaneous; no clock interpolation or calibration',
              'coverage_limit': 'Returned hits only; nearest return is not environmental clearance',
              'expected_pitch_bands_deg': bands, 'samples': [],
              'rpc_count': report['rpc_count'], 'completed_samples': report['completed_samples'],
              'geometry_status_counts': dict(Counter(row['geometry_status'] for row in report['queries']))}
    consumed = 0
    for index in range(report['completed_samples']):
        rows = [row for row in report['queries'] if row['sample_index'] == index]
        if [row['rpc'] for row in rows] != ['getMultirotorState', 'simGetCollisionInfo',
                'getDistanceSensorData', 'getLidarData', 'getMultirotorState']:
            raise ValueError('Full five-query round required for paired geometry analysis')
        consumed += len(rows)
        before, collision, distance, lidar, after = rows
        first, last = state_summary(before), state_summary(after)
        sample = {'index': index, 'state_before': first, 'state_after': last,
                  'host_round_duration_s': after['wall_end_s'] - before['wall_start_s'],
                  'collision': {'geometry_status': collision['geometry_status'],
                      'contact_geometry': deepcopy(collision['geometry']),
                      **deepcopy(collision['payload'] or {})},
                  'distance': {'payload_status': distance['payload_status'],
                               'payload': deepcopy(distance['payload'])},
                  'lidar': {'geometry_status': lidar['geometry_status']}}
        if lidar['geometry'] is not None:
            cloud, payload = lidar['geometry']['point_cloud'], lidar['payload']
            if (not isinstance(cloud, list) or len(cloud) % 3 or len(cloud) > 30000
                    or any(type(x) not in (int, float) or not math.isfinite(x) for x in cloud)):
                raise ValueError('Malformed or oversized cloud')
            digest = hashlib.sha256(json.dumps(cloud, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            if payload['point_cloud_sha256'] != digest or payload['point_count'] != len(cloud) // 3:
                raise ValueError('Archived full cloud does not match received hash/count')
            points = [cloud[i:i+3] for i in range(0, len(cloud), 3)]
            ranges = [math.hypot(*point) for point in points]
            elevations = [math.degrees(math.atan2(-z, math.hypot(x, y))) for x, y, z in points
                          if math.hypot(x, y, z) > 0]
            band_counts, errors = [0] * 4, []
            for pitch in elevations:
                nearest = min(range(4), key=lambda i: abs(pitch - bands[i]))
                error = abs(pitch - bands[nearest])
                errors.append(error)
                if error <= .05: band_counts[nearest] += 1
            data = sample['lidar']
            data.update(point_count=len(points), point_cloud_sha256=digest,
                nearest_return_m_not_clearance=min(ranges) if ranges else None,
                farthest_return_m=max(ranges) if ranges else None,
                elevation_deg_min=min(elevations) if elevations else None,
                elevation_deg_max=max(elevations) if elevations else None,
                expected_pitch_band_hit_counts=band_counts,
                points_outside_pitch_bands=len(points) - sum(band_counts),
                max_pitch_band_error_deg=max(errors) if errors else None,
                sensor_timestamp_ns=payload['time_stamp'], sensor_pose=deepcopy(payload['pose']),
                numeric_age_to_before_state_ns=first['timestamp_ns'] - payload['time_stamp'],
                numeric_age_to_after_state_ns=last['timestamp_ns'] - payload['time_stamp'])
            for label, state in [('before', first), ('after', last)]:
                data['mount_comparison_' + label] = None
                if state['position_ned_m'] is not None:
                    data['mount_comparison_' + label] = camera_mount_error(
                        state['position_ned_m'], state['orientation_xyzw'], (0., 0., -.5),
                        xyz(payload['pose']['position']), xyzw(payload['pose']['orientation']))
        result['samples'].append(sample)
    if consumed != len(report['queries']):
        raise ValueError('Unpaired query rows')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    result = summarize(json.loads(raw))
    result['input'] = {'path': str(args.input.resolve()), 'bytes': len(raw),
                       'sha256': hashlib.sha256(raw).hexdigest()}
    result['recipe_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    encoded = json.dumps(result, indent=2, allow_nan=False)
    with args.output.open('x', encoding='utf-8') as handle:
        handle.write(encoded + '\n')
    print(json.dumps({'output': str(args.output), 'samples': result['completed_samples'],
                      'input_sha256': result['input']['sha256'], 'control_ready': False}))


if __name__ == '__main__':
    main()
