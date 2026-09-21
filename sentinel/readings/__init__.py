"""The catalog: importing this package registers every reading in :data:`sentinel.reading.REGISTRY`."""

from . import crash, diagnostics, events, health, reliability, system, whea  # noqa: F401

__all__ = ["crash", "diagnostics", "events", "health", "reliability", "system", "whea"]
