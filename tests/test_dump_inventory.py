"""Portable contracts for dump observations and location-specific collection gaps."""

from __future__ import annotations

import ntpath
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

import pytest

from sentinel.bridge import BridgeResult
from sentinel.readings.dumps import ERROR_LIMIT, inventory, missing_file, take_dumps
from tests.conftest import FakeBridge

ROOTS = {
    "minidump": r"C:\Windows\Minidump",
    "memory": r"C:\Windows\MEMORY.DMP",
    "live_kernel": r"C:\Windows\LiveKernelReports",
}
APPLICATION_ROOT = r"C:\Users\example-user\AppData\Local\CrashDumps"


def dump_inventory(files: Iterable[dict[str, Any]] = (), *, application: bool = False) -> dict[str, Any]:
    """A complete synthetic inventory, shared by crash and bounded-header fixtures.

    Only the four public file fields are copied. Empty directories exist; MEMORY.DMP is
    absent unless supplied. Paths outside these synthetic inventory locations are refused.
    Application coverage is included only when explicitly requested.
    """
    roots = {**ROOTS, "application": APPLICATION_ROOT} if application else ROOTS
    locations = [{
        "id": identity, "path": path, "recursive": identity == "live_kernel",
        "present": identity != "memory", "outcome": "empty", "returned": 0,
        "error_count": 0, "errors": [], "files": [],
    } for identity, path in roots.items()]
    for file in files:
        selected = ntpath.normcase(ntpath.normpath(file["path"]))
        for source in locations:
            root = ntpath.normcase(source["path"])
            belongs = (
                selected == root if source["id"] == "memory"
                else selected.startswith(root + "\\") if source["recursive"]
                else ntpath.dirname(selected) == root
            )
            if belongs:
                source["files"].append({key: file[key] for key in ("name", "path", "bytes", "modified")})
                source.update(present=True, outcome="ok", returned=len(source["files"]))
                break
        else:
            raise ValueError("synthetic dump file is outside the fixture inventory")
    return {"locations": locations}


def _file(path: str, *, modified: str = "2025-01-02T03:04:05Z") -> dict[str, Any]:
    return {"name": ntpath.basename(path), "path": path, "bytes": 4096, "modified": modified}


def _source(payload: dict[str, Any], identity: str) -> dict[str, Any]:
    return next(source for source in payload["locations"] if source["id"] == identity)


def _fail(payload: dict[str, Any], identity: str, outcome: str = "denied", *, present: bool | None = None):
    source = _source(payload, identity)
    source.update(
        present=True if source["files"] else present, outcome=outcome, error_count=1,
        errors=[{"kind": outcome, "detail": f"synthetic {identity} {outcome}"}],
    )
    return source


def _take(payload: dict[str, Any]):
    # These cases vary kernel-source results. The all-source taker also receives an
    # explicitly observed empty application directory; its gaps have separate tests.
    payload = deepcopy(payload)
    payload["locations"].append(_source(dump_inventory(application=True), "application"))
    return take_dumps(FakeBridge(BridgeResult("ok", items=[payload], took_ms=7)), {})


def test_complete_inventory_keeps_public_file_fields_and_orders_locations_together():
    older = _file(ROOTS["memory"], modified="2025-01-01T00:00:00Z")
    newer = _file(ROOTS["minidump"] + r"\example.dmp")
    payload = dump_inventory([older, newer])
    original = deepcopy(payload)
    files, collection, warnings = inventory(payload)
    assert files == [{**newer, "source": "minidump"}, {**older, "source": "memory"}]
    assert collection["complete"] is True and warnings == []
    assert [source["id"] for source in collection["locations"]] == list(ROOTS)
    assert all("files" not in source for source in collection["locations"])
    assert payload == original


def test_partial_location_keeps_completed_files_and_its_failure_evidence():
    file = _file(ROOTS["live_kernel"] + r"\WATCHDOG\retained.dmp")
    payload = dump_inventory([file])
    _fail(payload, "live_kernel")
    reading = _take(payload)
    assert reading.outcome == "ok" and reading.observed and reading.count == 1
    assert reading.section("files").data == [{**file, "source": "live_kernel"}]
    collection = reading.section("collection").data
    assert collection["complete"] is False
    source = _source(collection, "live_kernel")
    assert source["present"] is True and source["outcome"] == "denied" and source["returned"] == 1
    assert source["errors"] == [{"kind": "denied", "detail": "synthetic live_kernel denied"}]
    assert any("live_kernel" in warning and "denied" in warning for warning in reading.warnings)


@pytest.mark.parametrize(("failures", "outcome", "count"), [
    ({}, "empty", 0),
    ({"live_kernel": "denied"}, "denied", None),
    ({"minidump": "failed", "live_kernel": "denied"}, "failed", None),
])
def test_no_files_are_empty_only_when_every_location_answered(failures, outcome, count):
    payload = dump_inventory()
    for identity, failure in failures.items():
        _fail(payload, identity, failure)
    reading = _take(payload)
    assert reading.outcome == outcome and reading.count == count
    assert reading.observed == (outcome == "empty")
    assert reading.section("files").data == []
    assert reading.section("collection").data["complete"] is (not failures)
    assert bool(reading.warnings) == bool(failures)
    if failures:
        assert reading.error and reading.error["kind"] == outcome


@pytest.mark.parametrize("changes", [
    {"returned": 1},
    {"returned": False},
    {"files": None},
    {"errors": None},
    {"errors": [None], "error_count": 1},
    {"error_count": False},
    {"error_count": 1},
    {"returned": 1, "files": [_file(ROOTS["minidump"] + r"\contradiction.dmp")]},
    {"present": None},
    {"present": 1},
    {"recursive": True},
    {"outcome": "ok"},
    {"outcome": "denied", "present": None},
    {"path": ""},
])
def test_inconsistent_empty_metadata_is_an_unobserved_failure(changes):
    payload = dump_inventory()
    _source(payload, "minidump").update(changes)
    reading = _take(payload)
    assert reading.outcome == "failed" and not reading.observed and reading.count is None
    assert reading.section("files").data == []
    collection = reading.section("collection").data
    source = _source(collection, "minidump")
    assert collection["complete"] is False and source["present"] is None
    assert source["outcome"] == "failed" and source["returned"] == 0 and source["errors"]


@pytest.mark.parametrize("payload", [None, [], {}, {"locations": None}, {"locations": {}}])
def test_missing_location_answers_cannot_establish_empty_inventory(payload):
    files, collection, warnings = inventory(payload)
    assert files == [] and collection["complete"] is False
    assert len(collection["locations"]) == 3 and len(warnings) == 3
    assert all(source["outcome"] == "failed" and source["present"] is None for source in collection["locations"])


@pytest.mark.parametrize("invalid", ["duplicate", "missing", "invalid_file"])
def test_one_invalid_location_does_not_erase_another_locations_files(invalid):
    file = _file(ROOTS["memory"])
    payload = dump_inventory([file])
    source = _source(payload, "minidump")
    if invalid == "duplicate":
        payload["locations"].append(deepcopy(source))
    elif invalid == "missing":
        payload["locations"].remove(source)
    else:
        source.update(outcome="ok", returned=1, files=[_file(ROOTS["minidump"] + r"\bad.dmp")])
        source["files"][0]["bytes"] = True
    reading = _take(payload)
    assert reading.outcome == "ok" and reading.count == 1 and reading.warnings
    assert reading.section("files").data == [{**file, "source": "memory"}]
    collection = reading.section("collection").data
    assert collection["complete"] is False
    assert _source(collection, "minidump")["outcome"] == "failed"
    assert _source(collection, "memory")["outcome"] == "ok"


def test_empty_bridge_output_is_missing_metadata_not_an_empty_inventory():
    reading = take_dumps(FakeBridge(BridgeResult("empty", items=[])), {})
    assert reading.outcome == "failed" and not reading.observed and reading.count is None
    assert reading.sections == [] and "exactly one object" in reading.error["detail"]


@pytest.mark.parametrize("outcome", ["denied", "failed"])
def test_truncated_error_details_keep_total_count_and_source_outcome(outcome):
    payload = dump_inventory()
    source = _fail(payload, "live_kernel", outcome, present=True)
    # A general failure can occur after the first ERROR_LIMIT denied children.
    source.update(error_count=ERROR_LIMIT + 1, errors=[
        {"kind": "denied", "detail": f"synthetic child {index} denied"} for index in range(ERROR_LIMIT)
    ])
    files, collection, warnings = inventory(payload)
    retained = _source(collection, "live_kernel")
    assert files == [] and collection["complete"] is False
    assert retained["outcome"] == outcome and retained["present"] is True
    assert retained["error_count"] == ERROR_LIMIT + 1 and retained["errors"] == source["errors"]
    assert any(f"{ERROR_LIMIT + 1} errors occurred; only the first {ERROR_LIMIT}" in warning for warning in warnings)


@pytest.mark.parametrize(("total", "details", "outcome"), [
    (ERROR_LIMIT + 1, ERROR_LIMIT - 1, "denied"),
    (ERROR_LIMIT + 1, ERROR_LIMIT + 1, "denied"),
    (1, 2, "denied"),
    (ERROR_LIMIT, ERROR_LIMIT, "failed"),
])
def test_error_truncation_cannot_explain_inconsistent_counts_or_known_outcomes(total, details, outcome):
    payload = dump_inventory()
    source = _fail(payload, "live_kernel", outcome, present=True)
    source.update(error_count=total, errors=[{"kind": "denied", "detail": "synthetic denial"}] * details)
    _, collection, warnings = inventory(payload)
    retained = _source(collection, "live_kernel")
    assert retained["outcome"] == "failed" and retained["present"] is None
    assert retained["errors"] != source["errors"] and warnings


@pytest.mark.parametrize(("path", "outcome"), [
    (r"C:\Windows\Minidump\absent.dmp", "empty"),
    (r"C:\Windows\MEMORY.DMP", "empty"),
    (r"C:\Windows\LiveKernelReports\WATCHDOG\absent.dmp", "denied"),
    (r"c:/WINDOWS/LiveKernelReports/WATCHDOG/../WATCHDOG/absent.dmp", "denied"),
    (r"C:\Windows\LiveKernelReports-other\absent.dmp", "empty"),
])
def test_missing_file_uses_only_the_requested_locations_collection_gap(path, outcome):
    payload = dump_inventory()
    _fail(payload, "live_kernel", present=True)
    _, collection, _ = inventory(payload)
    actual, detail = missing_file(path, collection)
    assert actual == outcome
    assert ("unknown" in detail) == (outcome == "denied")


@pytest.mark.parametrize(("path", "outcome"), [
    (r"c:/WINDOWS/minidump/absent.dmp", "failed"),
    (r"C:\Windows\Minidump\nested\absent.dmp", "empty"),
    (r"c:/windows/memory.dmp", "denied"),
    (r"C:\Windows\MEMORY.DMP.bak", "empty"),
])
def test_missing_file_respects_nonrecursive_directory_and_single_file_boundaries(path, outcome):
    payload = dump_inventory()
    _fail(payload, "minidump", "failed")
    _fail(payload, "memory", "denied")
    _, collection, _ = inventory(payload)
    assert missing_file(path, collection)[0] == outcome


def test_unknown_location_root_does_not_hide_a_known_locations_observed_miss():
    payload = dump_inventory()
    payload["locations"].remove(_source(payload, "live_kernel"))
    _, collection, _ = inventory(payload)
    outcome, detail = missing_file(ROOTS["minidump"] + r"\absent.dmp", collection)
    assert outcome == "empty" and "No exact match" in detail
    assert missing_file(r"C:\unestablished-root\absent.dmp", collection)[0] == "failed"


def test_normalization_classifies_location_without_minting_an_exact_inventory_match():
    file = _file(ROOTS["minidump"] + r"\example.dmp")
    files, collection, _ = inventory(dump_inventory([file]))
    original = deepcopy(collection)
    # Header opening still requires its own exact inventory match. This helper only
    # classifies a miss, even when normalized spellings identify the same location.
    alias = r"c:/windows/minidump/nested/../example.dmp"
    outcome, detail = missing_file(alias, collection)
    assert outcome == "empty" and "No exact match" in detail
    assert files == [{**file, "source": "minidump"}] and collection == original
