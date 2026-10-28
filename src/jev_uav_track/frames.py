"""Explicit vector-frame conversions for the simulator boundary.

Generic conversions support ENU (x=east, y=north, z=up), NED (x=north,
y=east, z=down), and body FLU (x=forward, y=left, z=up). The actual local
CARLA producer uses its own world x/y/z-up axes, mapped separately by
CarlaAirAlignment. No simulator process is started by this module.
"""

from __future__ import annotations

import math
from enum import Enum
from dataclasses import dataclass
from typing import Sequence


class CoordinateFrame(str, Enum):
    ENU = "enu"
    NED = "ned"
    BODY_FLU = "body_flu"


def _vec3(value: Sequence[float]) -> tuple[float, float, float]:
    if len(value) != 3:
        raise ValueError("a vector must contain exactly three values")
    result = (float(value[0]), float(value[1]), float(value[2]))
    if not all(math.isfinite(v) for v in result):
        raise ValueError("vector must be finite")
    return result


def camera_mount_error(vehicle_position, vehicle_orientation_xyzw, body_offset,
                       camera_position, camera_orientation_xyzw):
    """Check a rigid forward-facing FRD camera mount in the same NED frame.

    Uses full normalized quaternions and treats q/-q as the same rotation.
    No AirSim/NumPy dependency; the requested relative orientation is identity.
    """
    def normalize(value):
        if len(value) != 4 or not all(math.isfinite(x) for x in value):
            raise ValueError('Quaternion must contain four finite values')
        norm = math.hypot(*value)
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError('Quaternion must have a finite positive norm')
        return tuple(x / norm for x in value)

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    position, offset, actual = _vec3(vehicle_position), _vec3(body_offset), _vec3(camera_position)
    q, observed = normalize(vehicle_orientation_xyzw), normalize(camera_orientation_xyzw)
    twice_cross = tuple(2 * x for x in cross(q[:3], offset))
    second_cross = cross(q[:3], twice_cross)
    expected = _vec3(tuple(position[i] + offset[i] + q[3] * twice_cross[i] + second_cross[i]
                           for i in range(3)))
    angle = 2 * math.degrees(math.acos(min(1.0, abs(sum(a * b for a, b in zip(q, observed))))))
    return {'expected_position_ned_m': expected,
            'position_error_m': math.dist(expected, actual), 'orientation_error_deg': angle}


@dataclass(frozen=True)
class CarlaAirAlignment:
    """Local producer mapping, NOT geographic ENU->NED.

    AirSim position = (CARLA.x + ox, CARLA.y + oy, -CARLA.z + oz).
    Offsets must come from a paired own-instance pose calibration.
    """
    offset_m: tuple[float, float, float]

    def __post_init__(self):
        object.__setattr__(self, "offset_m", _vec3(self.offset_m))

    def position_to_airsim(self, value: Sequence[float]) -> tuple[float, float, float]:
        x, y, z = _vec3(value)
        ox, oy, oz = self.offset_m
        return x + ox, y + oy, -z + oz

    def position_to_carla(self, value: Sequence[float]) -> tuple[float, float, float]:
        x, y, z = _vec3(value)
        ox, oy, oz = self.offset_m
        return x - ox, y - oy, oz - z

    def velocity_to_airsim(self, value: Sequence[float]) -> tuple[float, float, float]:
        x, y, z = _vec3(value)
        return x, y, -z


def _world_to_enu(value: tuple[float, float, float], frame: CoordinateFrame) -> tuple[float, float, float]:
    if frame is CoordinateFrame.ENU:
        return value
    if frame is CoordinateFrame.NED:
        north, east, down = value
        return (east, north, -down)
    raise ValueError("BODY_FLU requires yaw_deg for world conversion")


def _enu_to_world(value: tuple[float, float, float], frame: CoordinateFrame) -> tuple[float, float, float]:
    if frame is CoordinateFrame.ENU:
        return value
    if frame is CoordinateFrame.NED:
        east, north, up = value
        return (north, east, -up)
    raise ValueError("BODY_FLU requires yaw_deg for world conversion")


def body_flu_to_enu(value: Sequence[float], yaw_deg: float) -> tuple[float, float, float]:
    """Rotate a body FLU vector into ENU using yaw from east toward north."""

    forward, left, up = _vec3(value)
    angle = math.radians(float(yaw_deg))
    return (
        math.cos(angle) * forward - math.sin(angle) * left,
        math.sin(angle) * forward + math.cos(angle) * left,
        up,
    )


def enu_to_body_flu(value: Sequence[float], yaw_deg: float) -> tuple[float, float, float]:
    """Rotate an ENU vector into body FLU using the inverse yaw rotation."""

    east, north, up = _vec3(value)
    angle = math.radians(float(yaw_deg))
    return (
        math.cos(angle) * east + math.sin(angle) * north,
        -math.sin(angle) * east + math.cos(angle) * north,
        up,
    )


def convert_vector(
    value: Sequence[float],
    source: CoordinateFrame | str,
    target: CoordinateFrame | str,
    *,
    yaw_deg: float | None = None,
) -> tuple[float, float, float]:
    """Convert a vector while requiring yaw for body/world transforms."""

    source_frame = CoordinateFrame(source)
    target_frame = CoordinateFrame(target)
    vector = _vec3(value)
    if source_frame is target_frame:
        return vector
    if source_frame is CoordinateFrame.BODY_FLU:
        if yaw_deg is None:
            raise ValueError("yaw_deg is required for BODY_FLU conversion")
        enu = body_flu_to_enu(vector, yaw_deg)
    else:
        enu = _world_to_enu(vector, source_frame)
    if target_frame is CoordinateFrame.BODY_FLU:
        if yaw_deg is None:
            raise ValueError("yaw_deg is required for BODY_FLU conversion")
        return enu_to_body_flu(enu, yaw_deg)
    return _enu_to_world(enu, target_frame)
