"""Application dump coverage is explicit and does not change kernel inventory scope."""

from copy import deepcopy

import pytest

from sentinel.bridge import BridgeResult
from sentinel.readings.dumps import ALL_DUMPS_SCRIPT, ALL_LOCATION_IDS, LOCATION_IDS, inventory, missing_file, take_dumps
from tests.conftest import FakeBridge
from tests.test_dump_inventory import APPLICATION_ROOT, ROOTS, _fail, _file, _source, dump_inventory


def test_all_source_reading_returns_application_provenance_without_changing_payload():
    file = _file(APPLICATION_ROOT + r"\example.exe.1234.dmp")
    payload = dump_inventory([file], application=True)
    original = deepcopy(payload)
    bridge = FakeBridge(BridgeResult("ok", items=[payload]))
    reading = take_dumps(bridge, {})
    assert bridge.scripts == [ALL_DUMPS_SCRIPT]
    assert reading.outcome == "ok" and reading.count == 1 and not reading.warnings
    assert reading.section("files").data == [{**file, "source": "application"}]
    collection = reading.section("collection").data
    assert collection["complete"] is True
    assert tuple(source["id"] for source in collection["locations"]) == ALL_LOCATION_IDS
    assert _source(collection, "application")["recursive"] is False
    assert payload == original
    reading.section("files").data[0]["name"] = "a changed client copy"
    assert payload == original


@pytest.mark.parametrize("application_failure", [None, "denied", "failed"])
def test_default_inventory_keeps_kernel_files_and_coverage_separate(application_failure):
    kernel = _file(ROOTS["minidump"] + r"\kernel.dmp")
    application = _file(APPLICATION_ROOT + r"\program.dmp")
    payload = dump_inventory([kernel, application], application=True)
    if application_failure:
        _fail(payload, "application", application_failure)
    files, collection, warnings = inventory(payload)
    assert files == [{**kernel, "source": "minidump"}]
    assert tuple(source["id"] for source in collection["locations"]) == LOCATION_IDS
    assert collection["complete"] is True and warnings == []


def test_all_source_empty_requires_an_application_answer():
    payload = dump_inventory()
    reading = take_dumps(FakeBridge(BridgeResult("ok", items=[payload])), {})
    assert reading.outcome == "failed" and not reading.observed and reading.count is None
    assert reading.section("files").data == []
    missing = _source(reading.section("collection").data, "application")
    assert missing["outcome"] == "failed" and missing["present"] is None
    assert missing["errors"] and reading.warnings

    complete = take_dumps(FakeBridge(BridgeResult("ok", items=[dump_inventory(application=True)])), {})
    assert complete.outcome == "empty" and complete.count == 0 and complete.observed
    assert complete.section("collection").data["complete"] is True


@pytest.mark.parametrize("outcome", ["denied", "failed"])
def test_application_partial_result_preserves_files_and_collection_gap(outcome):
    file = _file(APPLICATION_ROOT + r"\retained.dmp")
    payload = dump_inventory([file], application=True)
    _fail(payload, "application", outcome)
    reading = take_dumps(FakeBridge(BridgeResult("ok", items=[payload])), {})
    assert reading.outcome == "ok" and reading.count == 1 and reading.warnings
    assert reading.section("files").data == [{**file, "source": "application"}]
    collection = reading.section("collection").data
    source = _source(collection, "application")
    assert collection["complete"] is False
    assert source["outcome"] == outcome and source["present"] is True and source["returned"] == 1
    assert missing_file(APPLICATION_ROOT + r"\unseen.dmp", collection)[0] == outcome
    assert missing_file(ROOTS["minidump"] + r"\unseen.dmp", collection)[0] == "empty"


@pytest.mark.parametrize("outcome", ["denied", "failed"])
def test_unavailable_application_root_keeps_its_actual_error_and_unknown_presence(outcome):
    payload = dump_inventory(application=True)
    source = _fail(payload, "application", outcome)
    source["path"] = None
    reading = take_dumps(FakeBridge(BridgeResult("ok", items=[payload])), {})
    assert reading.outcome == outcome and reading.count is None and not reading.observed
    retained = _source(reading.section("collection").data, "application")
    assert retained["path"] is None and retained["present"] is None
    assert retained["errors"] == source["errors"]
    collection = reading.section("collection").data
    assert missing_file(ROOTS["minidump"] + r"\absent.dmp", collection)[0] == "empty"
    assert missing_file(APPLICATION_ROOT + r"\absent.dmp", collection)[0] == outcome


@pytest.mark.parametrize("change", [
    {"path": None},
    {"recursive": True},
    {"outcome": "empty", "returned": 0},
])
def test_inconsistent_application_claim_cannot_supply_an_inspection_target(change):
    file = _file(APPLICATION_ROOT + r"\example.dmp")
    payload = dump_inventory([file], application=True)
    _source(payload, "application").update(change)
    reading = take_dumps(FakeBridge(BridgeResult("ok", items=[payload])), {})
    assert reading.outcome == "failed" and reading.count is None
    assert reading.section("files").data == []
    assert _source(reading.section("collection").data, "application")["outcome"] == "failed"


def test_source_identity_is_assigned_from_the_validated_location():
    payload = dump_inventory([_file(APPLICATION_ROOT + r"\example.dmp")], application=True)
    row = _source(payload, "application")["files"][0]
    row["source"] = "minidump"
    files, _, _ = inventory(payload, location_ids=ALL_LOCATION_IDS)
    assert files[0]["source"] == "application"
    assert row["source"] == "minidump"
