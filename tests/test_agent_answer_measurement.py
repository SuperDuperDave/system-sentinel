"""Keep the published synthetic agent-answer measurement runnable as its source fixtures evolve."""

import asyncio
import os

from scripts.measure_agent_answers import measure


def test_the_default_agent_measurement_has_observed_inputs_and_restores_home(tmp_path):
    previous_home = os.environ.get("SYSTEM_SENTINEL_HOME")
    rows = asyncio.run(measure(tmp_path, heavy=False))

    assert [row.label for row in rows] == ["health", "crash", "events", "record", "faults", "storms", "whea", "signals"]
    assert all(row.outcome == "ok" and row.text_bytes > 0 and row.result_bytes >= row.text_bytes for row in rows)
    assert next(row for row in rows if row.label == "crash").count == 5
    assert next(row for row in rows if row.label == "signals").count == 11
    assert os.environ.get("SYSTEM_SENTINEL_HOME") == previous_home
