"""Call TypeSafe Jev with environment-only credentials and a reconciled ledger.

The script prints only model metadata and the selected action; it never prints
the API key. Model discovery also consumes one preparation attempt.
"""

from __future__ import annotations

from jev_uav_track import JevTrackPipeline, TypeSafeJevClient
from jev_uav_track.demo import make_demo_state


def main() -> None:
    client = TypeSafeJevClient.from_environment()
    models = client.list_models()
    print("available_models=", [model.get("name") for model in models])

    state = make_demo_state()
    result = JevTrackPipeline(client).step(state)
    print("choice=", result.action.id)
    print("confidence=", result.decision.confidence)
    print("probabilities=", dict(result.decision.probabilities))
    print("model=", result.decision.model_version)
    print("reason=", result.reason)
    print("fallback=", result.used_fallback)
    print("api_coverage=", client.coverage())


if __name__ == "__main__":
    main()
