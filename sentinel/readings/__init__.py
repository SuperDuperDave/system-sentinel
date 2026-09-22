"""The catalog: importing this package registers every reading in :data:`sentinel.reading.REGISTRY`."""

from .. import performance  # noqa: F401
from . import crash, diagnostics, dump_header, dumps, events, health, processes, reliability, system, whea  # noqa: F401

__all__ = ["crash", "diagnostics", "dump_header", "dumps", "events", "health", "processes", "reliability", "system", "whea"]
