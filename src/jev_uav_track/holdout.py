"""Project-reserved map guard, not an authenticity or final-test PASS audit.

Town03 was reserved by the user on 2026-10-29. Recognizable identity hints
must never enter fitting/calibration, even when another hint says Town02.
Missing or deliberately stripped provenance cannot establish map identity.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

_RESERVED = re.compile(r'(?<![a-z0-9])town0*3(?![a-z0-9])', re.IGNORECASE)
_IDENTITY_KEYS = frozenset({'town', 'town_name', 'map', 'map_name', 'carla_map',
                            'path', 'source_path', 'filename'})


def _strings(value):
    if isinstance(value, bytes):
        yield value.decode('utf-8', errors='strict')
    elif isinstance(value, (str, Path)):
        yield str(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _metadata_hints(metadata):
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            name = str(key).casefold()
            if name in _IDENTITY_KEYS:
                yield from _strings(value)
            elif name == 'episode_metadata_json':
                if isinstance(value, bytes):
                    value = value.decode('utf-8', errors='strict')
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError as exc:
                        raise ValueError('Malformed episode metadata; cannot audit reserved maps') from exc
                yield from _metadata_hints(value)
            elif isinstance(value, (Mapping, list, tuple)):
                yield from _metadata_hints(value)
    elif isinstance(metadata, (list, tuple)):
        for value in metadata:
            yield from _metadata_hints(value)


def require_development_source(path, metadata=None) -> None:
    """Reject reserved identity before development use; never opens the source.

    Check lexical and resolved paths (including filesystem link targets),
    plus structured identity metadata. No override flag is offered to training.
    The general read-only HDF adapter deliberately does not call this guard.
    """
    hints = [str(path), str(Path(path).expanduser().resolve())]
    hints.extend(_metadata_hints(metadata))
    if any(_RESERVED.search(hint) for hint in hints):
        raise ValueError('Development use of reserved map Town03 is forbidden')
