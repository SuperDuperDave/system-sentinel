"""The catalog: importing this package registers every reading in :data:`sentinel.reading.REGISTRY`."""

from .. import performance  # noqa: F401
from . import changes, crash, diagnostics, dump_header, dumps, events, health, processes, reliability, space, system, whea, whea_reports  # noqa: F401

__all__ = ["changes", "crash", "diagnostics", "dump_header", "dumps", "events", "health", "processes", "reliability", "space", "system", "whea", "whea_reports"]
