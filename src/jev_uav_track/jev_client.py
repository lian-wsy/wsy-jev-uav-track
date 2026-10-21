"""Jev API and local mock adapters.

The API adapter follows TypeSafe's typed-decision contract. It is kept small so
the flight stack can replace it with an on-premise gateway or a local model.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Protocol, Sequence

from .models import CandidateAction, JevDecision, TrackingState


class JevClient(Protocol):
    def choose(self, state: TrackingState, actions: Sequence[CandidateAction]) -> JevDecision:
        ...


class JevProtocolError(RuntimeError):
    """Raised when a Jev response cannot be mapped to a candidate action."""


DEFAULT_JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_JEV_MODEL = "jev-latest"
DEFAULT_JEV_MODELS_ENDPOINT = "https://api.typesafe.ai/v1/models"


def build_choice_request(
    state: TrackingState,
    actions: Sequence[CandidateAction],
    model: str = DEFAULT_JEV_MODEL,
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
    parsed_probabilities: Dict[str, float] = {}
    for key, value in probabilities.items():
        candidate_id = str(key)
        if candidate_id not in valid_ids:
            continue
        probability = float(value)
        if not math.isfinite(probability) or probability < 0.0:
            raise JevProtocolError(f"Invalid probability for candidate id: {candidate_id}")
        parsed_probabilities[candidate_id] = probability
    probabilities = parsed_probabilities
    if choice is None and probabilities:
        choice = max(probabilities, key=probabilities.get)
    if choice is None:
        raise JevProtocolError("Jev response did not contain a choice")
    choice = str(choice)
    if choice not in valid_ids:
        raise JevProtocolError(f"Jev returned unknown candidate id: {choice}")
    if not probabilities:
        probabilities = {candidate_id: (1.0 if candidate_id == choice else 0.0) for candidate_id in valid_ids}
    else:
        probabilities = {
            candidate_id: probabilities.get(candidate_id, 0.0)
            for candidate_id in valid_ids
        }
        total = sum(probabilities.values())
        if total <= 0.0:
            probabilities = {
                candidate_id: (1.0 if candidate_id == choice else 0.0)
                for candidate_id in valid_ids
            }
        else:
            probabilities = {
                candidate_id: probability / total
                for candidate_id, probability in probabilities.items()
            }
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
        endpoint: str = DEFAULT_JEV_ENDPOINT,
        model: str = DEFAULT_JEV_MODEL,
        timeout_s: float = 8.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout_s = timeout_s

    @classmethod
    def from_environment(
        cls,
        api_key_name: str = "JEV_API_KEY",
        endpoint_name: str = "JEV_ENDPOINT",
        model_name: str = "JEV_MODEL",
        timeout_s: float = 8.0,
    ) -> "TypeSafeJevClient":
        """Build a client from environment variables without hard-coding secrets."""

        api_key = os.getenv(api_key_name, "").strip()
        if not api_key:
            raise ValueError(f"Environment variable {api_key_name} is required")
        return cls(
            api_key=api_key,
            endpoint=os.getenv(endpoint_name, DEFAULT_JEV_ENDPOINT),
            model=os.getenv(model_name, DEFAULT_JEV_MODEL),
            timeout_s=timeout_s,
        )

    def list_models(self) -> Sequence[Mapping[str, Any]]:
        """Return models visible to this API key for account-specific discovery."""

        request = urllib.request.Request(
            DEFAULT_JEV_MODELS_ENDPOINT,
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JevProtocolError(f"Jev model discovery failed: {exc}") from exc
        models = payload.get("models") if isinstance(payload, Mapping) else None
        if not isinstance(models, list):
            raise JevProtocolError("Jev model discovery response did not contain a models list")
        return tuple(model for model in models if isinstance(model, Mapping))

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
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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
