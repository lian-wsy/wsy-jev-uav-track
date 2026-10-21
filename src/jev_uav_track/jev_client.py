"""Jev API and local mock adapters.

The API adapter follows TypeSafe's typed-decision contract. It is kept small so
the flight stack can replace it with an on-premise gateway or a local model.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Protocol, Sequence

from .models import CandidateAction, JevDecision, TrackingState


class JevClient(Protocol):
    def choose(self, state: TrackingState, actions: Sequence[CandidateAction]) -> JevDecision:
        ...


class JevProtocolError(RuntimeError):
    """Raised when a Jev response cannot be mapped to a candidate action."""


def build_choice_request(
    state: TrackingState,
    actions: Sequence[CandidateAction],
    model: str = "jev-1.13.0",
) -> Dict[str, Any]:
    criteria = {action.id: action.option_description() for action in actions}
    return {
        "model": model,
        "state": {
            "mission_goal": state.mission_goal,
            "belief": state.target.__dict__,
            "uav": state.uav.__dict__,
            "constraints": state.constraints.__dict__,
            "obstacle_context": state.obstacle_context,
            "history_summary": state.history_summary,
            "candidate_outcomes": [action.to_dict() for action in actions],
        },
        "questions": {
            "next_behavior": {
                "type": "choice",
                "instructions": (
                    "Choose the single feasible behavior that best maintains target identity, "
                    "visibility, useful range, and state-estimation quality. The candidate "
                    "descriptions already contain predicted consequences; evaluate them rather "
                    "than inventing a new maneuver."
                ),
                "criteria": criteria,
            }
        },
    }


def _extract_answer(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    answers = payload.get("answers")
    if isinstance(answers, Mapping):
        answer = answers.get("next_behavior")
        if isinstance(answer, Mapping):
            return answer
    answer = payload.get("next_behavior")
    if isinstance(answer, Mapping):
        return answer
    if isinstance(answer, str):
        return {"choice": answer}
    return payload


def _parse_decision(payload: Mapping[str, Any], valid_ids: Sequence[str]) -> JevDecision:
    answer = _extract_answer(payload)
    choice = answer.get("choice") or answer.get("value") or answer.get("selected")
    probabilities = answer.get("probabilities") or answer.get("probs") or {}
    if not isinstance(probabilities, Mapping):
        probabilities = {}
    probabilities = {str(k): float(v) for k, v in probabilities.items()}
    if choice is None and probabilities:
        choice = max(probabilities, key=probabilities.get)
    if choice is None:
        raise JevProtocolError("Jev response did not contain a choice")
    choice = str(choice)
    if choice not in valid_ids:
        raise JevProtocolError(f"Jev returned unknown candidate id: {choice}")
    if not probabilities:
        probabilities = {candidate_id: (1.0 if candidate_id == choice else 0.0) for candidate_id in valid_ids}
    confidence = answer.get("confidence")
    confidence = float(confidence) if confidence is not None else None
    return JevDecision(
        choice=choice,
        probabilities=probabilities,
        confidence=confidence,
        model_version=payload.get("model_version") or payload.get("model"),
        raw=payload,
    )


class TypeSafeJevClient:
    """Minimal standard-library client for the official TypeSafe endpoint."""

    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://api.typesafe.ai/v1/systemone",
        model: str = "jev-1.13.0",
        timeout_s: float = 2.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout_s = timeout_s

    def choose(self, state: TrackingState, actions: Sequence[CandidateAction]) -> JevDecision:
        request_body = json.dumps(build_choice_request(state, actions, self.model)).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=request_body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise JevProtocolError(f"Jev request failed: {exc}") from exc
        return _parse_decision(payload, [action.id for action in actions])


class MockJevClient:
    """Offline deterministic substitute used by the example and unit tests."""

    def __init__(self, temperature: float = 0.35, confidence_scale: float = 1.0) -> None:
        self.temperature = max(0.05, float(temperature))
        self.confidence_scale = max(0.0, float(confidence_scale))

    def choose(self, state: TrackingState, actions: Sequence[CandidateAction]) -> JevDecision:
        utilities = [action.outcome.utility(state.constraints) for action in actions]
        logits = [utility / self.temperature for utility in utilities]
        maximum = max(logits)
        weights = [math.exp(logit - maximum) for logit in logits]
        total = sum(weights) or 1.0
        probabilities = {action.id: weight / total for action, weight in zip(actions, weights)}
        choice = max(probabilities, key=probabilities.get)
        confidence = max(probabilities.values()) * self.confidence_scale
        return JevDecision(
            choice=choice,
            probabilities=probabilities,
            confidence=min(1.0, confidence),
            model_version="mock-jev-v0",
            raw={"utilities": dict(zip((a.id for a in actions), utilities))},
        )
