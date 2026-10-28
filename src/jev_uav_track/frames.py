"""Explicit vector-frame conversions for the simulator boundary.

The internal contract uses ENU world vectors (x=east, y=north, z=up), NED
world vectors (x=north, y=east, z=down), and body FLU vectors (x=forward,
y=left, z=up).  A CARLA/AirSim adapter must declare which convention it
receives; no simulator process is started by this module.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Sequence


class CoordinateFrame(str, Enum):
    ENU = "enu"
    NED = "ned"
    BODY_FLU = "body_flu"


def _vec3(value: Sequence[float]) -> tuple[float, float, float]:
    if len(value) != 3:
        raise ValueError("a vector must contain exactly three values")
    return (float(value[0]), float(value[1]), float(value[2]))


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
