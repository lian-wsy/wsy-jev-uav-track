"""Reversible sensor settings for an owned, no-motion diagnostic only.

The launcher proves process/resource ownership before apply and confirms stopped
handles before restore. This is not a controller configuration or safety proof.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .isolated_runtime import BINARY, RuntimePorts, validate_roots


DISTANCE_NAME = 'JevDistanceFront'
LIDAR_NAME = 'JevLidarDiagnostic'


def diagnostic_sensors():
    # Explicit basic sensors preserve the four upstream multirotor defaults even
    # in a fork that replaces, rather than merges, the vehicle Sensors block.
    sensors = {name: {'SensorType': kind, 'Enabled': True} for name, kind in (
        ('barometer', 1), ('imu', 2), ('gps', 3), ('magnetometer', 4))}
    pose = {'X': 0, 'Y': 0, 'Z': -0.5, 'Roll': 0, 'Pitch': 0, 'Yaw': 0,
            'DrawDebugPoints': False, 'ExternalController': False}
    sensors[DISTANCE_NAME] = dict(pose, SensorType=5, Enabled=True,
                                  MinDistance=0.2, MaxDistance=40)
    sensors[LIDAR_NAME] = dict(pose, SensorType=6, Enabled=True, NumberOfChannels=4,
        PointsPerSecond=4000, RotationsPerSecond=10, Range=40,
        VerticalFOVUpper=10, VerticalFOVLower=-30, HorizontalFOVStart=0,
        HorizontalFOVEnd=359, DataFrame='SensorLocalFrame')
    return sensors


def validate_sensor_opt_in(enabled, map_name, profile, *, capture_geometry=False):
    if type(enabled) is not bool or (enabled and (map_name != 'Town02' or profile != 'safety')):
        raise ValueError('Diagnostic sensor configuration requires Town02 safety profile')
    if type(capture_geometry) is not bool or (capture_geometry and not enabled):
        raise ValueError('Geometry capture requires explicitly configured diagnostic sensors')


def require_diagnostic_sensors(settings):
    vehicles = settings.get('Vehicles', {}) if type(settings) is dict else {}
    vehicle = vehicles.get('JevDrone', {}) if type(vehicles) is dict else {}
    if (type(vehicle) is not dict or vehicle.get('Sensors') != diagnostic_sensors()
            or 'DefaultSensors' in settings):
        raise ValueError('Loaded diagnostic sensors differ from the bounded configuration')
    return {'distance': DISTANCE_NAME, 'lidar': LIDAR_NAME}


def _strict_json(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate settings key')
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError('Nonfinite settings value')
    return json.loads(payload, object_pairs_hook=pairs, parse_constant=nonfinite)


class OwnedSensorConfiguration:
    """Three-file transaction with retained, fsynced backups and immutable receipts.

    Files are checked again after staging and before replacement. This is not an
    interprocess lock: it relies on the launcher's exclusive runtime ownership.
    Detected concurrent edits are refused, not silently overwritten.
    """

    def __init__(self, source: Path, task: Path, output: Path, *, ports: RuntimePorts,
                 map_name: str, profile: str):
        validate_sensor_opt_in(True, map_name, profile)
        validate_roots(source, task)
        self.task = Path(os.path.abspath(task))
        self.output = Path(os.path.abspath(output))
        if self.output == self.task or not self.output.is_relative_to(self.task):
            raise ValueError('Sensor evidence must stay inside the owned runtime')
        self.paths = [self.task / 'settings.json', self.task / 'package/settings.json',
                      self.task / 'package' / BINARY.parent / 'settings.json']
        self.ports = ports
        self.started = False
        self.originals = []
        self.configured = None
        self.audit = {'kind': 'OWNED_TEMPORARY_SENSOR_CONFIGURATION', 'requested_map': map_name,
            'profile': profile, 'sensor_names': {'distance': DISTANCE_NAME, 'lidar': LIDAR_NAME},
            'control_ready': False, 'restored': False, 'files': []}

    def _check_path(self, path):
        if not path.is_relative_to(self.task) or not path.resolve().is_relative_to(self.task.resolve()):
            raise ValueError('Sensor path escapes owned runtime')
        for item in (path, *path.parents):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Sensor paths cannot contain links or reparse points')
        info = path.stat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('Sensor files cannot be shared hardlinks')

    def _write_new(self, path, data):
        self._check_path(path.parent)
        with path.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self._check_path(path)
        if path.read_bytes() != data:
            raise OSError('Sensor backup/staging readback differs')

    def _require_current(self, path, expected):
        self._check_path(path)
        if path.read_bytes() != expected:
            raise RuntimeError('Sensor settings changed concurrently; refusing overwrite')

    def _replace(self, path, expected, data, phase):
        self._require_current(path, expected)
        temporary = path.with_name(path.name + '.' + self.output.name + '.' + phase + '.tmp')
        self._write_new(temporary, data)
        self._require_current(path, expected)
        os.replace(temporary, path)

    def _receipt(self, phase):
        payload = json.dumps(self.audit, indent=2, allow_nan=False).encode('utf-8')
        self._write_new(self.output / f'sensor_config_{phase}.json', payload)

    def apply(self):
        if self.started:
            raise ValueError('Sensor configuration may only be applied once')
        self.started = True
        self._check_path(self.output)
        for path in self.paths:
            self._check_path(path)
        originals = [path.read_bytes() for path in self.paths]
        if len(set(originals)) != 1:
            raise ValueError('Three owned settings copies must be byte-identical')
        settings = _strict_json(originals[0])
        vehicles = settings.get('Vehicles', {}) if type(settings) is dict else {}
        vehicle = vehicles.get('JevDrone') if type(vehicles) is dict else None
        if (type(settings) is not dict or settings.get('SimMode') != 'Multirotor'
                or type(settings.get('ApiServerPort')) is not int
                or settings['ApiServerPort'] != self.ports.airsim_rpc
                or type(vehicles) is not dict or list(vehicles) != ['JevDrone']
                or type(vehicle) is not dict or vehicle.get('VehicleType') != 'SimpleFlight'
                or 'Sensors' in vehicle or 'DefaultSensors' in settings):
            raise ValueError('Unsupported original sensor/vehicle/port settings; no overwrite')
        vehicle['Sensors'] = diagnostic_sensors()
        configured = json.dumps(settings, indent=2, allow_nan=False).encode('utf-8')
        backups = [self.output / f'sensor_settings_before_{i}.json' for i in range(3)]
        reserved = backups + [self.output / f'sensor_config_{phase}.json'
                              for phase in ('prepared', 'applied', 'restored')]
        if any(path.exists() or path.is_symlink() for path in reserved):
            raise FileExistsError('Sensor backup/receipt already exists; refusing overwrite')
        for path, payload in zip(backups, originals):
            self._write_new(path, payload)
        self.originals, self.configured = originals, configured
        self.audit['files'] = [{'path': str(path), 'backup': str(backup),
            'before_sha256': hashlib.sha256(payload).hexdigest(),
            'configured_sha256': hashlib.sha256(configured).hexdigest(),
            'applied': False, 'restored': False} for path, backup, payload in zip(self.paths, backups, originals)]
        self._receipt('prepared')
        for i, path in enumerate(self.paths):
            self._replace(path, originals[i], configured, 'apply')
            self.audit['files'][i]['applied'] = True
            self._require_current(path, configured)
        self._receipt('applied')

    def restore(self, *, owned_stopped: bool):
        if owned_stopped is not True:
            raise RuntimeError('Owned processes not confirmed stopped; sensor backups retained')
        if self.audit['restored']:
            return
        failed = False
        for i, record in enumerate(self.audit['files']):
            try:
                if record['applied']:
                    self._require_current(Path(record['backup']), self.originals[i])
                    self._replace(self.paths[i], self.configured, self.originals[i], 'restore')
                self._require_current(self.paths[i], self.originals[i])
                record.update(restored=True, restored_sha256=hashlib.sha256(self.originals[i]).hexdigest())
            except Exception as exc:
                failed = True
                # Arbitrary filesystem exception text is not part of this audit.
                record['restore_error_type'] = type(exc).__name__
        self.audit['restored'] = not failed
        if self.audit['files']:
            try:
                self._receipt('restored')
            except Exception:
                self.audit['restored'] = False
                raise
        if failed:
            raise RuntimeError('Sensor settings restoration incomplete; backups retained')
