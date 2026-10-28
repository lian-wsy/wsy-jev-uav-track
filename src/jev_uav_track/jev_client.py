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
from pathlib import Path
from typing import Any, Dict, Mapping, Protocol, Sequence

from .models import CandidateAction, JevDecision, TrackingState
from .request_budget import BudgetError, RequestBudget


class JevClient(Protocol):
    def choose(self, state: TrackingState, actions: Sequence[CandidateAction]) -> JevDecision:
        ...


class JevProtocolError(RuntimeError):
    """Raised when a Jev response cannot be mapped to a candidate action."""


DEFAULT_JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_JEV_MODEL = "jev-latest"
DEFAULT_JEV_MODELS_ENDPOINT = "https://api.typesafe.ai/v1/models"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # One reservation must never silently send another request or forward
        # Authorization to a different origin. 3xx becomes a charged failure.
        return None


def _open_request(request, *, timeout):
    # Local opener only: do not change transport behavior elsewhere in the app.
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


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
        budget: RequestBudget | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout_s = timeout_s
        self.budget = budget
        self.choice_requests = 0
        self.choice_responses = 0
        self.reserved_attempts = 0
        self.budget_denials = 0

    def _reserve(self, operation: str) -> None:
        try:
            if self.budget is None:
                raise BudgetError("A reconciled preparation ledger is required")
            self.budget.reserve(operation)
        except BudgetError:
            self.budget_denials += 1
            raise
        self.reserved_attempts += 1

    def coverage(self) -> Dict[str, Any]:
        """Expose API coverage even when the selector converts errors to fallback."""
        return {
            "status": ("COMPLETE" if self.choice_requests > 0
                       and self.choice_requests == self.choice_responses else "INCOMPLETE"),
            "choice_requests": self.choice_requests,
            "valid_choice_responses": self.choice_responses,
            "reserved_attempts": self.reserved_attempts,
            "budget_denials": self.budget_denials,
        }

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
        ledger = os.getenv("JEV_BUDGET_LEDGER", "").strip()
        if not ledger:
            raise BudgetError("JEV_BUDGET_LEDGER must name the reconciled sprint ledger")
        return cls(
            api_key=api_key,
            endpoint=os.getenv(endpoint_name, DEFAULT_JEV_ENDPOINT),
            model=os.getenv(model_name, DEFAULT_JEV_MODEL),
            timeout_s=timeout_s,
            budget=RequestBudget(Path(ledger), batch_id=os.getenv("JEV_BUDGET_BATCH")),
        )

    def list_models(self) -> Sequence[Mapping[str, Any]]:
        """Return models visible to this API key for account-specific discovery."""

        request = urllib.request.Request(
            DEFAULT_JEV_MODELS_ENDPOINT,
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        self._reserve("models")
        try:
            with _open_request(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JevProtocolError(f"Jev model discovery failed: {type(exc).__name__}") from None
        models = payload.get("models") if isinstance(payload, Mapping) else None
        if not isinstance(models, list):
            raise JevProtocolError("Jev model discovery response did not contain a models list")
        return tuple(model for model in models if isinstance(model, Mapping))

    def choose(self, state: TrackingState, actions: Sequence[CandidateAction]) -> JevDecision:
        self.choice_requests += 1
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
        self._reserve("choice")
        try:
            with _open_request(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JevProtocolError(f"Jev request failed: {type(exc).__name__}") from None
        decision = _parse_decision(payload, [action.id for action in actions])
        self.choice_responses += 1
        return decision


class MockJevClient:
    """Offline deterministic substitute used by the example and unit tests.

    ``identity_weight`` is optional so the default mock remains backward
    compatible with the original utility-shaped baseline.  The module
    ablation runner enables it to exercise identity confidence and information
    seeking together without making a network request.
    """

    def __init__(
        self,
        temperature: float = 0.35,
        confidence_scale: float = 1.0,
        identity_weight: float = 0.0,
    ) -> None:
        self.temperature = max(0.05, float(temperature))
        self.confidence_scale = max(0.0, float(confidence_scale))
        self.identity_weight = max(0.0, float(identity_weight))

    def choose(self, state: TrackingState, actions: Sequence[CandidateAction]) -> JevDecision:
        identity_need = 1.0 - state.target.identity_confidence
        utilities = [
            action.outcome.utility(state.constraints)
            + self.identity_weight * identity_need * action.outcome.information_gain
            for action in actions
        ]
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
            raw={
                "utilities": dict(zip((a.id for a in actions), utilities)),
                "identity_need": identity_need,
                "identity_weight": self.identity_weight,
            },
        )


class UtilityRankingClient:
    """Deterministic utility-only selector for the Jev-vs-utility ablation."""

    def choose(self, state: TrackingState, actions: Sequence[CandidateAction]) -> JevDecision:
        if not actions:
            raise ValueError("At least one candidate action is required")
        utilities = [action.outcome.utility(state.constraints) for action in actions]
        best_index = max(range(len(actions)), key=utilities.__getitem__)
        probabilities = {
            action.id: (1.0 if index == best_index else 0.0)
            for index, action in enumerate(actions)
        }
        return JevDecision(
            choice=actions[best_index].id,
            probabilities=probabilities,
            confidence=1.0,
            model_version="utility-ranking-v0",
            raw={"utilities": dict(zip((a.id for a in actions), utilities))},
        )
