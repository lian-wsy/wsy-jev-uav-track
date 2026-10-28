"""Method-independent reference windows and failure-inclusive recovery metrics.

This evaluates sampled tracking observations, not unobserved inter-frame truth.
Event starts must be fixed from scenario/reference data before method comparison.
"""
from __future__ import annotations

import math
from statistics import mean
from typing import Sequence


def _times(values: Sequence[float]) -> list[float]:
    times = [float(v) for v in values]
    if any(not math.isfinite(t) for t in times) or any(
        b <= a for a, b in zip(times, times[1:])
    ):
        raise ValueError("Timestamps must be finite and strictly increasing")
    return times


def reference_loss_events(timestamps: Sequence[float], visible: Sequence[bool],
                          *, window_s: float = 10.0) -> list[float]:
    """Select reference loss onsets chronologically, with nonoverlapping windows."""
    times = _times(timestamps)
    if len(times) != len(visible) or not math.isfinite(window_s) or window_s <= 0:
        raise ValueError("Matching observations and positive finite window required")
    events = []
    for i, t in enumerate(times):
        if not visible[i] and (i == 0 or visible[i - 1]):
            if not events or t >= events[-1] + window_s:
                events.append(t)
    return events


def score_reacquisition(timestamps: Sequence[float], correctly_tracked: Sequence[bool],
                        *, event_starts: Sequence[float], reference_end_s: float,
                        window_s: float = 10.0, stable_s: float = 0.5,
                        max_gap_s: float = 0.25) -> dict:
    """Time to confirmation; nonrecoveries/early terminations contribute window_s.

    Eligibility depends only on the reference duration, never method survival.
    Continuous recovery requires a sampled correct run spanning stable_s, with
    no gap above max_gap_s. A late first observation cannot backdate recovery.
    """
    times = _times(timestamps)
    starts = _times(event_starts)
    if len(times) != len(correctly_tracked):
        raise ValueError("Tracking observations must match timestamps")
    if not all(math.isfinite(v) and v > 0 for v in (window_s, stable_s, max_gap_s)):
        raise ValueError("Recovery durations must be positive and finite")
    if stable_s > window_s or not math.isfinite(reference_end_s):
        raise ValueError("Invalid reference end or stable duration")
    rows = []
    for start in starts:
        if start + window_s > reference_end_s + 1e-6:
            rows.append({"start_s": start, "status": "INELIGIBLE_REFERENCE_WINDOW"})
            continue
        if not times or times[-1] < start + window_s - 1e-6:
            rows.append({"start_s": start, "status": "FAILED", "restricted_time_s": window_s,
                         "confirmation_s": None, "reason": "method_terminated_before_window_end"})
            continue
        streak_start = previous = None
        confirmation = None
        for t, correct in zip(times, correctly_tracked):
            if t < start or t > start + window_s + 1e-6:
                continue
            if not correct:
                streak_start = previous = None
                continue
            if previous is None or t - previous > max_gap_s + 1e-6:
                streak_start = t
            previous = t
            if t - streak_start >= stable_s - 1e-6:
                confirmation = t
                break
        elapsed = min(window_s, confirmation - start) if confirmation is not None else window_s
        rows.append({"start_s": start, "status": "RECOVERED" if confirmation is not None else "FAILED",
                     "restricted_time_s": elapsed, "confirmation_s": confirmation})
    eligible = [r for r in rows if r["status"] != "INELIGIBLE_REFERENCE_WINDOW"]
    successes = [r for r in eligible if r["status"] == "RECOVERED"]
    return {
        "protocol": "reference_window_confirmation_v1",
        "window_s": window_s, "stable_s": stable_s, "max_gap_s": max_gap_s,
        "eligible_events": len(eligible), "ineligible_events": len(rows) - len(eligible),
        "recovery_rate": len(successes) / len(eligible) if eligible else None,
        "restricted_mean_s": mean(r["restricted_time_s"] for r in eligible) if eligible else None,
        "success_mean_s": mean(r["restricted_time_s"] for r in successes) if successes else None,
        "events": rows,
    }
