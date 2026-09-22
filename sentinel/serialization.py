"""Keep integer evidence exact when JSON reaches clients with binary64 numbers.

Collection, derivation and persistence keep Python integers. At an output boundary, integers
outside JavaScript's safe range travel as decimal strings. This does not recover precision
already lost by a caller, or change floating-point measurements into exact observations.
"""

from __future__ import annotations

from typing import Any

MAX_SAFE_INTEGER = (1 << 53) - 1


def json_safe_integers(value: Any) -> Any:
    """Return a JSON-shaped copy with oversized integers represented by their exact digits.

    Booleans are deliberately separate from integers. Other scalar values, including floats
    and existing strings, retain their original meaning. Never change the source evidence.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > MAX_SAFE_INTEGER else value
    if isinstance(value, dict):
        return {key: json_safe_integers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_integers(item) for item in value]
    return value
