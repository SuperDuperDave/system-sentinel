"""The catalog: importing this package registers every reading in :data:`sentinel.reading.REGISTRY`."""

from . import diagnostics, events, health, system, whea  # noqa: F401

__all__ = ["diagnostics", "events", "health", "system", "whea"]
