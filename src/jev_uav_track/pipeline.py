"""Closed-loop orchestration from perception state to a selected behavior."""

from __future__ import annotations

from typing import Iterable, Sequence

from .jev_client import JevClient
from .models import CandidateAction, SelectionResult, TrackingState
from .primitives import DEFAULT_PRIMITIVES, BehaviorPrimitive
from .rollout import KinematicRolloutModel
from .selector import JevTrackSelector


class JevTrackPipeline:
    """Run local rollout, safety filtering, and Jev selection as one step."""

    def __init__(
        self,
        client: JevClient,
        rollout_model: KinematicRolloutModel | None = None,
        primitives: Iterable[BehaviorPrimitive] = DEFAULT_PRIMITIVES,
        selector: JevTrackSelector | None = None,
    ) -> None:
        self.rollout_model = rollout_model or KinematicRolloutModel()
        self.primitives = tuple(primitives)
        self.selector = selector or JevTrackSelector(client)

    def generate_candidates(
        self,
        state: TrackingState,
        primitives: Sequence[BehaviorPrimitive] | None = None,
    ) -> list[CandidateAction]:
        selected_primitives = self.primitives if primitives is None else tuple(primitives)
        return self.rollout_model.generate(selected_primitives, state)

    def step(
        self,
        state: TrackingState,
        previous_action_id: str | None = None,
        primitives: Sequence[BehaviorPrimitive] | None = None,
    ) -> SelectionResult:
        candidates = self.generate_candidates(state, primitives)
        return self.selector.select(state, candidates, previous_action_id)

