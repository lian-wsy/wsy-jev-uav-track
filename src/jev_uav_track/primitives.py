"""High-level UAV behavior primitives.

The primitives deliberately expose semantic maneuvers rather than raw motor
commands. A real vehicle adapter should translate these commands into a
trajectory and let the flight controller close the fast inner loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .models import Vec3


@dataclass(frozen=True)
class BehaviorPrimitive:
    id: str
    name: str
    description: str
    command_mps: Vec3
    yaw_rate_dps: float = 0.0
    preferred_when_visibility_below: float = 0.0


DEFAULT_PRIMITIVES: Tuple[BehaviorPrimitive, ...] = (
    BehaviorPrimitive(
        id="hold_center",
        name="保持中心",
        description="保持当前速度，稳定目标在图像中心和期望距离附近",
        command_mps=(0.0, 0.0, 0.0),
    ),
    BehaviorPrimitive(
        id="follow_predictive",
        name="预测跟随",
        description="沿目标预测运动方向前进，降低跟踪距离误差",
        command_mps=(2.5, 0.0, 0.0),
    ),
    BehaviorPrimitive(
        id="orbit_left",
        name="左侧绕飞",
        description="向左侧横移并保持目标在视场内，改善观察角度",
        command_mps=(0.8, -2.4, 0.0),
        yaw_rate_dps=12.0,
    ),
    BehaviorPrimitive(
        id="orbit_right",
        name="右侧绕飞",
        description="向右侧横移并保持目标在视场内，改善观察角度",
        command_mps=(0.8, 2.4, 0.0),
        yaw_rate_dps=-12.0,
    ),
    BehaviorPrimitive(
        id="climb",
        name="上升观察",
        description="小幅上升以减少地面遮挡并改善目标尺度",
        command_mps=(0.3, 0.0, 1.8),
        yaw_rate_dps=0.0,
    ),
    BehaviorPrimitive(
        id="descend",
        name="下降观察",
        description="小幅下降以恢复期望距离并保持安全视角",
        command_mps=(0.2, 0.0, -1.2),
        yaw_rate_dps=0.0,
    ),
    BehaviorPrimitive(
        id="scan_reacquire",
        name="扫描重捕获",
        description="减速并进行短时偏航扫描，优先恢复目标可见性",
        command_mps=(0.0, 0.0, 0.0),
        yaw_rate_dps=28.0,
        preferred_when_visibility_below=0.65,
    ),
    BehaviorPrimitive(
        id="safe_hover",
        name="安全悬停",
        description="停止横向机动并等待新的观测或人工接管",
        command_mps=(0.0, 0.0, 0.0),
        yaw_rate_dps=0.0,
    ),
)
