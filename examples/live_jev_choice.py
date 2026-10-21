"""Call the real TypeSafe Jev endpoint using a local .env or environment key.

The script prints only model metadata and the selected action; it never prints
the API key. Keep .env outside Git and use .env.example as the template.
"""

from __future__ import annotations

import os
from pathlib import Path

from jev_uav_track import JevTrackPipeline, TypeSafeJevClient
from jev_uav_track.demo import make_demo_state


def load_local_env() -> None:
    """Load simple KEY=value entries without adding a dotenv dependency."""

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def main() -> None:
    load_local_env()
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


if __name__ == "__main__":
    main()
