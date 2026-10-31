"""Explicit energy units; no default vehicle capacity or measured-energy claim."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def finite_number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def remaining_battery_percent(percent: float, energy_wh: float, capacity_wh: float) -> float:
    """Ideal resource SOC conversion, not a physical battery discharge model."""
    if not finite_number(capacity_wh) or capacity_wh <= 0:
        raise ValueError("ENERGY_CAPACITY_INVALID")
    if not finite_number(percent) or not 0 <= percent <= 100:
        raise ValueError("ENERGY_PERCENT_INVALID")
    if not finite_number(energy_wh) or energy_wh < 0:
        raise ValueError("ENERGY_COST_INVALID")
    return max(0., percent - 100. * (energy_wh / capacity_wh))


@dataclass(frozen=True)
class SimulatedEnergyConfig:
    """Explicit, uncalibrated resource allowance, NEVER measured battery data.

    Constant power covers ALL elapsed time, including waiting/idle. It is not
    claimed to bound physical consumption or differentiate maneuver efficiency.
    """
    capacity_wh: float
    initial_percent: float
    power_w: float
    source: str = field(default="SIMULATED_RESOURCE", init=False)
    model_id: str = field(default="constant_time_resource_v1", init=False)

    def __post_init__(self):
        remaining_battery_percent(self.initial_percent, 0., self.capacity_wh)
        if not finite_number(self.power_w) or self.power_w <= 0 or self.power_w / 3600. <= 0:
            raise ValueError("ENERGY_POWER_INVALID")


@dataclass(frozen=True)
class EnergySnapshot:
    timestamp_s: float
    source: str
    model_id: str
    capacity_wh: float
    initial_percent: float
    power_w: float
    remaining_wh: float
    battery_percent: float


class SimulatedEnergyBudget:
    """One run's monotonic allowance; no reset/refill method or simulator I/O.

    Create only after choosing an explicit simulation configuration. This class
    does not prove simulator ownership; the runtime owner/preflight gates remain
    mandatory before any live dispatch.
    """
    def __init__(self, config: SimulatedEnergyConfig, *, start_s: float):
        if not isinstance(config, SimulatedEnergyConfig) or not finite_number(start_s):
            raise ValueError("ENERGY_CONFIG_INVALID")
        self._config = config
        self._start_s = self._last_s = start_s
        self._clock_fault = False

    @property
    def config(self):
        return self._config

    def forecast_wh(self, duration_s: float) -> float:
        if not finite_number(duration_s) or duration_s <= 0:
            raise ValueError("ENERGY_DURATION_INVALID")
        cost = self.config.power_w / 3600. * duration_s
        if not finite_number(cost) or cost <= 0:
            raise ValueError("ENERGY_COST_INVALID")
        return cost

    def snapshot(self, now_s: float) -> EnergySnapshot:
        if self._clock_fault or not finite_number(now_s) or now_s < self._last_s:
            self._clock_fault = True
            raise ValueError("ENERGY_CLOCK_INVALID")
        self._last_s = now_s
        elapsed_s = now_s - self._start_s
        consumed_wh = self.forecast_wh(elapsed_s) if elapsed_s > 0 else 0.
        percent = remaining_battery_percent(self.config.initial_percent, consumed_wh, self.config.capacity_wh)
        return EnergySnapshot(now_s, self.config.source, self.config.model_id, self.config.capacity_wh,
                              self.config.initial_percent, self.config.power_w,
                              percent / 100. * self.config.capacity_wh, percent)
