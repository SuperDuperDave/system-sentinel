"""The catalog: importing this package registers every reading in :data:`sentinel.reading.REGISTRY`."""

from . import events, health  # noqa: F401

__all__ = ["events", "health"]
