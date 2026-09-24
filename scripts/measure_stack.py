"""Measure synthetic saved Stack cost in a disposable directory, without host evidence.

From the checkout root: py -3 scripts\\measure_stack.py (Windows) or
PYTHONPATH=. python scripts/measure_stack.py (Linux/WSL). Results describe this
machine and filesystem, not typical use or the HTTP/MCP response time.
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel.stack import Item, Stack, index_state


def reading(number: int) -> dict:
    records = [
        {
            "RecordId": row,
            "Id": 41,
            "ProviderName": "Synthetic-Provider",
            "TimeCreated": "2026-09-20T12:00:00.0000000Z",
            "Message": "Synthetic detail. " + "x" * 512,
            "Properties": ["AB" * 512],
        }
        for row in range(1, 2001)
    ]
    return {
        "reading": "record",
        "params": {"before": "2026-09-21T00:00:00Z", "count": 2000},
        "asked_at": f"2026-09-24T00:00:{number:02d}Z",
        "outcome": "ok",
        "count": 2000,
        "sections": [{"name": "records", "kind": "raw", "data": records}],
        "method": {"kind": "synthetic", "query": "no host query"},
        "warnings": [],
        "redacted": [],
    }


def timed(action, repeats: int = 5) -> float:
    action()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        action()
        samples.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(samples), 1)


def measure_case(root: Path, n: int) -> dict:
    path = root / f"stack-{n}.json"
    stack = Stack(path)
    items = [
        Item(
            id=f"synthetic-{i}",
            added_at=f"2026-09-24T00:01:{i:02d}Z",
            kind="reading",
            title=f"Synthetic record {i}",
            reading=reading(i),
        ).to_dict()
        for i in range(n)
    ]
    stack.store.write({"items": items, "prompt_id": None, "system_prompt": True})
    initial_bytes = path.stat().st_size
    first_id = items[0]["id"]
    list_ms = timed(lambda: index_state(stack.state()))
    item_ms = timed(lambda: stack.item(first_id))
    rank = 1

    def update() -> None:
        nonlocal rank
        rank = 2 if rank == 1 else 1
        stack.update(first_id, rank=rank)

    update_ms = timed(update)
    prompt = True

    def choose() -> None:
        nonlocal prompt
        prompt = not prompt
        stack.choose(system_prompt=prompt)

    choose_ms = timed(choose)
    added = Item(
        id=f"synthetic-{n}",
        added_at="2026-09-24T00:02:00Z",
        kind="reading",
        title="Added synthetic record",
        reading=reading(n),
    )
    start = time.perf_counter()
    stack.add(added)
    add_ms = round((time.perf_counter() - start) * 1000, 1)
    if len(stack.state()["items"]) != n + 1:
        raise AssertionError("add lost an item")
    return {
        "items": n,
        "platform": platform.system(),
        "initial_file_bytes": initial_bytes,
        "index_read_median_ms": list_ms,
        "exact_item_read_median_ms": item_ms,
        "metadata_update_median_ms": update_ms,
        "prompt_choice_median_ms": choose_ms,
        "add_one_ms": add_ms,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sentinel-stack-synthetic-") as temporary:
        for n in (1, 3, 10):
            print(json.dumps(measure_case(Path(temporary), n)))


if __name__ == "__main__":
    main()
