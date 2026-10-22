"""Safety-gated Jev selection for semantic UAV behaviors."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .jev_client import JevClient
from .models import (
    CandidateAction,
    JevDecision,
    SelectionResult,
    TrackingState,
)
from .safety import filter_safe_actions


@dataclass(frozen=True)
class SelectionConfig:
    """Acceptance thresholds for a high-level Jev decision."""

    minimum_confidence: float = 0.55
    minimum_top_probability: float = 0.45
    minimum_probability_margin: float = 0.08
    hysteresis_margin: float = 0.05
    maximum_utility_drop_for_hold: float = 0.25


class JevTrackSelector:
    """Select one safe candidate and fall back deterministically when needed.

    In the normal path Jev only chooses among candidates whose consequences
    were already rolled out and passed hard safety checks. The optional gate
    switch exists for offline ablations; it is enabled by default. The
    selector remains usable when an external Jev call times out or returns low
    confidence.
    """

    def __init__(
        self,
        client: JevClient,
        config: SelectionConfig | None = None,
        enable_safety_gate: bool = True,
    ) -> None:
        self.client = client
        self.config = config or SelectionConfig()
        self.enable_safety_gate = bool(enable_safety_gate)

    @staticmethod
    def _best_action(state: TrackingState, actions: Sequence[CandidateAction]) -> CandidateAction:
        return max(actions, key=lambda action: action.outcome.utility(state.constraints))

    @staticmethod
    def _one_hot_decision(choice: CandidateAction, reason: str) -> JevDecision:
        return JevDecision(
            choice=choice.id,
            probabilities={choice.id: 1.0},
            confidence=1.0,
            model_version="deterministic-fallback-v0",
            raw={"fallback": reason},
        )

    def _fallback(
        self,
        state: TrackingState,
        safe_actions: Sequence[CandidateAction],
        rejected_ids: Sequence[str],
        reason: str,
        previous_action_id: str | None = None,
        decision: JevDecision | None = None,
    ) -> SelectionResult:
        best = self._best_action(state, safe_actions)
        previous = next(
            (action for action in safe_actions if action.id == previous_action_id),
            None,
        )
        if previous is not None:
            best_utility = best.outcome.utility(state.constraints)
            previous_utility = previous.outcome.utility(state.constraints)
            if best_utility - previous_utility <= self.config.maximum_utility_drop_for_hold:
                best = previous
                reason = f"{reason}:hold_previous"
        return SelectionResult(
            action=best,
            decision=decision or self._one_hot_decision(best, reason),
            used_fallback=True,
            reason=reason,
            rejected_action_ids=tuple(rejected_ids),
        )

    def select(
        self,
        state: TrackingState,
        actions: Sequence[CandidateAction],
        previous_action_id: str | None = None,
    ) -> SelectionResult:
        if not actions:
            raise ValueError("At least one candidate action is required")

        if self.enable_safety_gate:
            safe_actions, rejected_ids = filter_safe_actions(actions, state.uav, state.constraints)
        else:
            safe_actions, rejected_ids = list(actions), ()
        if not safe_actions:
            raise RuntimeError("No candidate action passed the hard safety filter")

        decision_state = state
        if previous_action_id:
            prior_history = state.history_summary.strip()
            previous_context = f"Previous selected behavior: {previous_action_id}."
            history = f"{prior_history}\n{previous_context}" if prior_history else previous_context
            decision_state = replace(state, history_summary=history)

        try:
            decision = self.client.choose(decision_state, safe_actions)
        except Exception as exc:  # Runtime safety path for a remote model or gateway.
            return self._fallback(
                state,
                safe_actions,
                rejected_ids,
                f"jev_error:{type(exc).__name__}",
                previous_action_id,
            )

        by_id = {action.id: action for action in safe_actions}
        selected = by_id.get(decision.choice)
        if selected is None:
            return self._fallback(
                state,
                safe_actions,
                rejected_ids,
                "jev_unknown_choice",
                previous_action_id,
                decision=decision,
            )

        confidence_ok = (
            decision.confidence is None
            or decision.confidence >= self.config.minimum_confidence
        )
        probability_ok = (
            decision.top_probability >= self.config.minimum_top_probability
            and decision.margin >= self.config.minimum_probability_margin
        )
        if not confidence_ok or not probability_ok:
            return self._fallback(
                state,
                safe_actions,
                rejected_ids,
                "confidence_gate",
                previous_action_id,
                decision=decision,
            )

        previous = by_id.get(previous_action_id or "")
        if (
            previous is not None
            and previous.id != selected.id
            and decision.margin < self.config.hysteresis_margin
            and selected.outcome.utility(state.constraints)
            - previous.outcome.utility(state.constraints)
            <= self.config.maximum_utility_drop_for_hold
        ):
            return SelectionResult(
                action=previous,
                decision=decision,
                used_fallback=True,
                reason="hysteresis_hold",
                rejected_action_ids=tuple(rejected_ids),
            )

        return SelectionResult(
            action=selected,
            decision=decision,
            used_fallback=False,
            reason="jev_choice",
            rejected_action_ids=tuple(rejected_ids),
        )

