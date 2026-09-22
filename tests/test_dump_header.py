"""The dump reading proves its facts from a bounded header, not from a file name or a guess."""

from __future__ import annotations

import base64
import json
import struct
from copy import deepcopy

import pytest

import sentinel.bridge
from sentinel.bridge import BridgeResult
from sentinel.readings import dump_header
from sentinel.readings.dump_header import MAX_STREAMS, PREFIX_BYTES, decode_prefix, dump_header_script, take_dump_header
from sentinel.redact import Redactor
from tests.conftest import FakeBridge, real_bridge_or_skip
from tests.test_dump_inventory import dump_inventory

PATH = r"C:\Windows\Minidump\example.dmp"


def header() -> bytes:
    data = bytearray(PREFIX_BYTES)
    data[:8] = b"PAGEDU64"
    struct.pack_into("<II", data, 8, 15, 26100)
    struct.pack_into("<III", data, 48, 0x8664, 16, 0x124)
    struct.pack_into("<QQQQ", data, 64, 0, 0xFFFFCC083C6D6020, 0, 0)
    return bytes(data)


def test_kernel_prefix_yields_exact_code_and_parameters_with_a_limit():
    raw, derived = decode_prefix(header())
    assert raw["signature"] == "PAGE" and raw["valid_dump_marker"] == "DU64"
    assert raw["bugcheck_code"] == "0x00000124"
    assert raw["bugcheck_parameters"] == ["0x0000000000000000", "0xFFFFCC083C6D6020", "0x0000000000000000", "0x0000000000000000"]
    assert derived["architecture"] == "x64"
    assert derived["bugcheck"]["code"] == "0x00000124"
    assert "not checked" in derived["limit"]


def test_short_or_other_format_never_invents_a_bugcheck():
    for data in (b"PAGEDU64", b"MDMP", b"garbage" + bytes(89)):
        raw, derived = decode_prefix(data)
        assert "bugcheck_code" not in raw and "bugcheck" not in derived
        assert derived["header_status"] != "recognized"
    assert decode_prefix(b"MDMP" + bytes(92))[1]["format"] == "stream minidump"


def test_one_selected_dump_is_opened_only_after_inventory_match():
    script = dump_header_script(PATH)
    assert "$inventory = & {" in script and "Where-Object { $_.path -ieq $selected }" in script
    assert "$stream.Read($buffer, $read, $length - $read)" in script
    assert f"[Math]::Min($lengthBefore, {PREFIX_BYTES})" in script
    assert f"$count -gt {MAX_STREAMS}" in script
    assert "$directory = Read-At $rva" in script
    assert "[IO.File]::Open($file.path" in script
    assert "[IO.File]::Open($selected" not in script
    assert "Join-Path $env:SystemRoot 'Minidump'" in script
    with pytest.raises(ValueError):
        dump_header_script("")
    with pytest.raises(ValueError):
        dump_header_script("x" * 1025)
    assert "name''s" in dump_header_script("name's")


def test_denial_and_bridge_failure_carry_no_false_sections():
    denied = take_dump_header(FakeBridge(BridgeResult("ok", items=[{"status": "denied"}])), {"path": PATH})
    assert denied.outcome == "denied" and not denied.observed and [s.name for s in denied.sections] == ["collection"]
    unavailable = take_dump_header(FakeBridge(BridgeResult("unavailable", error="interop down")), {"path": PATH})
    assert unavailable.outcome == "unavailable" and unavailable.sections == []


@pytest.mark.parametrize(("failed_location", "expected"), [("minidump", "denied"), ("live_kernel", "empty")])
def test_unlisted_header_path_depends_on_its_own_location(failed_location, expected):
    coverage = dump_inventory(application=True)
    source = next(row for row in coverage["locations"] if row["id"] == failed_location)
    source.update(outcome="denied", present=None, error_count=1, errors=[{"kind": "denied", "detail": "synthetic denial"}])
    item = {"status": "not_inventoried", "inventory": coverage}
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == expected
    assert reading.count == (0 if expected == "empty" else None)
    assert reading.section("collection").data["complete"] is False
    assert reading.section("header") is None and reading.warnings
    if expected == "denied":
        assert "unknown" in reading.error["detail"]


@pytest.mark.parametrize("result", [BridgeResult("empty"), BridgeResult("ok", items=[{"status": "not_inventoried"}])])
def test_missing_inventory_answer_cannot_establish_that_a_dump_is_gone(result):
    reading = take_dump_header(FakeBridge(result), {"path": PATH})
    assert reading.outcome == "failed" and reading.count is None
    assert reading.section("header") is None


def test_read_header_survives_unrelated_inventory_denial():
    item = mdmp_item()
    source = next(row for row in item["inventory"]["locations"] if row["id"] == "live_kernel")
    source.update(outcome="denied", present=None, error_count=1, errors=[{"kind": "denied", "detail": "synthetic denial"}])
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok" and reading.count == 1 and reading.warnings
    assert reading.section("inspection").data["exception"]["name"] == "access violation"
    assert reading.section("collection").data["complete"] is False


def test_a_read_header_has_raw_provenance_and_separate_interpretation():
    item = {"status": "ok", "name": "example.dmp", "path": PATH, "bytes": 4096, "modified": "2026-09-21T00:00:00Z", "prefix": base64.b64encode(header()).decode()}
    item["inventory"] = dump_inventory([item], application=True)
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok" and reading.count == 1
    assert [(s.name, s.cls) for s in reading.sections] == [("file", "raw"), ("header", "raw"), ("inspection", "derived"), ("collection", "raw")]
    assert reading.section("inspection").data["bugcheck"]["code"] == "0x00000124"
    assert "prefix" not in reading.section("file").data
    assert reading.section("header").data["bytes_hex"] == header().hex(" ")
    assert reading.section("header").data["field_offsets"]["bugcheck_code"] == 56


def mdmp_item() -> dict:
    data = bytearray(1024)
    struct.pack_into("<4sIIIIIQ", data, 0, b"MDMP", 0xA793, 4, 128, 0, 0, 0)
    for index, (kind, length, offset) in enumerate(((6, 168, 256), (7, 32, 512), (3, 4, 600), (4, 4, 604))):
        struct.pack_into("<III", data, 128 + index * 12, kind, length, offset)
    struct.pack_into("<I", data, 256, 123)
    struct.pack_into("<II", data, 264, 0xC0000005, 0)
    struct.pack_into("<Q", data, 280, 0x00007FF612340000)
    struct.pack_into("<I", data, 288, 2)
    struct.pack_into("<QQ", data, 296, 1, 0x00000000BADF00D0)
    struct.pack_into("<HHHBBIIIIII", data, 512, 9, 6, 0, 16, 1, 10, 0, 26200, 2, 0, 0)
    struct.pack_into("<I", data, 600, 12)
    struct.pack_into("<I", data, 604, 0)
    item = {
        "status": "ok", "name": "example.dmp", "path": PATH, "bytes": len(data), "modified": "2026-09-21T00:00:00Z",
        "prefix": base64.b64encode(data[:PREFIX_BYTES]).decode(),
        "directory": base64.b64encode(data[128:176]).decode(), "directory_status": "ok",
        "samples": [
            {"index": 0, "data": base64.b64encode(data[256:424]).decode()},
            {"index": 1, "data": base64.b64encode(data[512:544]).decode()},
            {"index": 2, "data": base64.b64encode(data[600:604]).decode()},
            {"index": 3, "data": base64.b64encode(data[604:608]).decode()},
        ],
    }
    item["inventory"] = dump_inventory([item], application=True)
    return item


def test_minidump_exposes_bounded_raw_streams_and_a_useful_summary():
    item = mdmp_item()
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok" and not reading.warnings
    assert [(section.name, section.cls) for section in reading.sections] == [
        ("file", "raw"), ("header", "raw"), ("streams", "raw"), ("inspection", "derived"), ("collection", "raw"),
    ]
    raw = reading.section("streams").data
    assert raw["offset"] == 128 and len(raw["entries"]) == 4
    assert raw["entries"][0]["exception"]["code"] == "0xC0000005"
    assert raw["entries"][0]["sample"]["offset"] == 256
    assert raw["entries"][1]["system_info"]["build_number"] == 26200
    summary = reading.section("inspection").data
    assert summary["directory_status"] == "ok" and summary["streams"] == 4
    assert summary["exception"]["name"] == "access violation"
    assert summary["exception"]["access"] == {"operation": "write", "address": "0x00000000BADF00D0"}
    assert summary["system"]["architecture"] == "x64"
    assert summary["thread_count"] == 12 and summary["module_count"] == 0
    assert summary["process"] is None


def test_a_later_stream_of_the_same_type_is_intentionally_not_sampled():
    item = mdmp_item()
    prefix = bytearray(base64.b64decode(item["prefix"]))
    struct.pack_into("<I", prefix, 8, 5)
    item["prefix"] = base64.b64encode(prefix).decode()
    directory = bytearray(base64.b64decode(item["directory"]))
    directory += struct.pack("<III", 6, 168, 700)
    item["directory"] = base64.b64encode(directory).decode()

    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    later = reading.section("streams").data["entries"][4]
    assert later["name"] == "exception" and later["sample_status"] == "skipped_duplicate"
    assert "sample" not in later and not reading.warnings


def test_exception_address_is_located_in_a_recorded_module_without_a_cause_claim():
    item = mdmp_item()
    directory = bytearray(base64.b64decode(item["directory"]))
    struct.pack_into("<III", directory, 36, 4, 112, 604)
    item["directory"] = base64.b64encode(directory).decode()
    module = bytearray(112)
    struct.pack_into("<I", module, 0, 1)
    struct.pack_into("<QIIII", module, 4, 0x00007FF612340000, 0x10000, 0, 0, 800)
    struct.pack_into("<II", module, 36, (1 << 16) | 2, (3 << 16) | 4)
    name = r"C:\Users\example-user\Example\app.exe".encode("utf-16-le")
    item["samples"][3] = {
        "index": 3, "data": base64.b64encode(module).decode(),
        "names": [{"index": 0, "offset": 804, "data": base64.b64encode(name).decode()}],
    }
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok" and not reading.warnings
    raw_module = reading.section("streams").data["entries"][3]["modules"][0]
    assert raw_module["name"].endswith("app.exe") and raw_module["file_version"] == "1.2.3.4"
    assert raw_module["record_offset"] == 608
    location = reading.section("inspection").data["exception"]["module_at_address"]
    assert location["name"] == "app.exe" and "not a cause" in location["basis"]
    redacted, removed = Redactor().redact(reading.to_dict())
    assert "user" in removed
    module_name = redacted["sections"][2]["data"]["entries"][3]["modules"][0]["name"]
    assert module_name == r"C:\Users\<user>\Example\app.exe"


def test_minidump_directory_outside_file_and_excessive_count_are_explicit():
    item = mdmp_item()
    prefix = bytearray(base64.b64decode(item["prefix"]))
    struct.pack_into("<I", prefix, 12, 1000)
    item.update(prefix=base64.b64encode(prefix).decode(), directory=None, directory_status="outside_file", samples=[])
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok" and reading.section("inspection").data["directory_status"] == "outside_file"
    assert reading.section("streams").data["entries"] == [] and reading.warnings

    struct.pack_into("<I", prefix, 8, MAX_STREAMS + 1)
    item["prefix"] = base64.b64encode(prefix).decode()
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.section("inspection").data["directory_status"] == "limit"


def test_incomplete_exception_and_directory_inside_header_do_not_yield_crash_facts():
    item = mdmp_item()
    directory = bytearray(base64.b64decode(item["directory"]))
    struct.pack_into("<I", directory, 4, 40)
    item["directory"] = base64.b64encode(directory).decode()
    item["samples"][0]["data"] = base64.b64encode(base64.b64decode(item["samples"][0]["data"])[:40]).decode()
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.section("inspection").data["exception"] is None
    assert any("complete exception" in warning for warning in reading.warnings)

    prefix = bytearray(base64.b64decode(item["prefix"]))
    struct.pack_into("<I", prefix, 12, 12)
    item["prefix"] = base64.b64encode(prefix).decode()
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.section("inspection").data["directory_status"] == "invalid_offset"
    assert reading.section("streams").data["entries"] == []


def add_misc_stream(item, sample=None, *, declared=24, offset=700):
    """Append one directory entry, optionally carrying its bounded reader response."""
    prefix = bytearray(base64.b64decode(item["prefix"]))
    count = struct.unpack_from("<I", prefix, 8)[0]
    struct.pack_into("<I", prefix, 8, count + 1)
    item["prefix"] = base64.b64encode(prefix).decode()
    directory = base64.b64decode(item["directory"]) + struct.pack("<III", 15, declared, offset)
    item["directory"] = base64.b64encode(directory).decode()
    if sample is not None:
        item["samples"].append({"index": count, "data": base64.b64encode(sample).decode()})
    return count


@pytest.mark.parametrize("structure_size", [24, 44, 160])
def test_miscellaneous_base_and_extended_records_preserve_raw_prefix_and_process(structure_size):
    item = mdmp_item()
    sample = struct.pack("<6I", structure_size, 0x80000003, 4321, 1700000000, 17, 19)
    add_misc_stream(item, sample, declared=structure_size)
    original = deepcopy(item)
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok" and not reading.warnings
    entry = reading.section("streams").data["entries"][4]
    assert entry["sample"] == {"offset": 700, "bytes_hex": sample.hex(" "), "bytes_read": 24}
    assert entry["misc_info"] == {
        "size_of_info": structure_size, "flags1": 0x80000003, "process_id": 4321,
        "process_create_time": 1700000000, "process_user_time": 17, "process_kernel_time": 19,
        "field_offsets": {
            "size_of_info": 700, "flags1": 704, "process_id": 708,
            "process_create_time": 712, "process_user_time": 716, "process_kernel_time": 720,
        },
        "fields_used": {"process_id": True, "process_times": True},
    }
    assert reading.section("inspection").data["process"] == {
        "id": 4321, "created_at": "2023-11-14T22:13:20Z", "creation_precision_seconds": 1,
    }
    assert item == original


@pytest.mark.parametrize("flags", [0, 1, 2, 3, 0x80000000])
def test_miscellaneous_flags_independently_control_process_identity_and_times(flags):
    item = mdmp_item()
    add_misc_stream(item, struct.pack("<6I", 24, flags, 4321, 1700000000, 17, 19))
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok" and not reading.warnings
    raw = reading.section("streams").data["entries"][4]["misc_info"]
    assert raw["process_id"] == 4321 and raw["process_create_time"] == 1700000000
    assert raw["process_user_time"] == 17 and raw["process_kernel_time"] == 19
    assert raw["fields_used"] == {"process_id": bool(flags & 1), "process_times": bool(flags & 2)}
    assert reading.section("inspection").data["process"] == {
        "id": 4321 if flags & 1 else None,
        "created_at": "2023-11-14T22:13:20Z" if flags & 2 else None,
        "creation_precision_seconds": 1 if flags & 2 else None,
    }


@pytest.mark.parametrize(("value", "created_at"), [
    (0, "1970-01-01T00:00:00Z"),
    (0x80000000, "2038-01-19T03:14:08Z"),
    (0xFFFFFFFF, "2106-02-07T06:28:15Z"),
])
def test_miscellaneous_unsigned_fields_keep_zero_and_large_values_exactly(value, created_at):
    item = mdmp_item()
    add_misc_stream(item, struct.pack("<6I", 24, 3, value, value, value, value))
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok" and not reading.warnings
    raw = reading.section("streams").data["entries"][4]["misc_info"]
    assert all(raw[field] == value for field in ("process_id", "process_create_time", "process_user_time", "process_kernel_time"))
    assert reading.section("inspection").data["process"] == {
        "id": value, "created_at": created_at, "creation_precision_seconds": 1,
    }


@pytest.mark.parametrize(("size_of_info", "declared", "sample_bytes", "offset"), [
    (24, 23, 23, 700),  # A genuinely short record, even though its fields begin plausibly.
    (24, 24, 23, 700),  # Truncated between the directory observation and the sample.
    (24, 25, 25, 700),  # A reader response that exceeded the requested 24-byte prefix.
    (0, 24, 24, 700),
    (23, 24, 24, 700),
    (25, 24, 24, 700),
    (0xFFFFFFFF, 24, 24, 700),
    (44, 44, 24, 1000),  # Prefix fits but the declared structure extends beyond the file.
])
def test_invalid_miscellaneous_metadata_retains_the_exception_without_process_facts(size_of_info, declared, sample_bytes, offset):
    item = mdmp_item()
    prefix = struct.pack("<6I", size_of_info, 3, 4321, 1700000000, 17, 19)
    sample = (prefix + b"\xff")[:sample_bytes]
    add_misc_stream(item, sample, declared=declared, offset=offset)
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok" and reading.warnings
    summary = reading.section("inspection").data
    assert summary["process"] is None and summary["exception"]["name"] == "access violation"
    entry = reading.section("streams").data["entries"][4]
    if sample_bytes == 24 and offset == 700:
        assert entry["sample"]["bytes_hex"] == sample.hex(" ")
        assert entry["misc_info"]["size_of_info"] == size_of_info
        assert entry["misc_info"]["flags1"] == 3
        assert any("SizeOfInfo" in warning for warning in reading.warnings)


@pytest.mark.parametrize(("first_size", "supply_duplicate"), [(24, False), (24, True), (23, True)])
def test_miscellaneous_duplicate_cannot_supply_or_replace_process_identity(first_size, supply_duplicate):
    item = mdmp_item()
    # Leave room under the bridge's sample-count bound for an unexpected duplicate.
    # The actual PowerShell collector never returns its sample.
    item["samples"] = [row for row in item["samples"] if row["index"] != 3]
    add_misc_stream(item, struct.pack("<6I", first_size, 1, 4321, 0, 0, 0))
    later_sample = struct.pack("<6I", 24, 3, 8765, 1700000000, 17, 19) if supply_duplicate else None
    add_misc_stream(item, later_sample, offset=900)
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok"
    summary = reading.section("inspection").data
    assert summary["exception"]["name"] == "access violation"
    assert summary["process"] == ({"id": 4321, "created_at": None, "creation_precision_seconds": None} if first_size == 24 else None)
    later = reading.section("streams").data["entries"][5]
    assert later["sample_status"] == "skipped_duplicate" and "misc_info" not in later
    assert any("supplied sample was ignored" in warning for warning in reading.warnings) is supply_duplicate


@pytest.mark.host
@pytest.mark.parametrize("structure_size", [24, 160])
def test_windows_miscellaneous_sampling_is_bounded_and_skips_duplicate_streams(monkeypatch, structure_size):
    """Execute the real file sampler against only a disposable synthetic Windows file."""
    monkeypatch.setattr(sentinel.bridge, "POOL_SIZE", 0)
    bridge = real_bridge_or_skip()
    item = mdmp_item()
    sample = struct.pack("<6I", structure_size, 3, 4321, 1700000000, 17, 19)
    add_misc_stream(item, sample, declared=structure_size)
    add_misc_stream(item, offset=900)
    data = bytearray(item["bytes"])
    prefix = base64.b64decode(item["prefix"])
    directory = base64.b64decode(item["directory"])
    data[:len(prefix)] = prefix
    data[128:128 + len(directory)] = directory
    data[700:700 + structure_size] = b"\xa5" * structure_size
    data[900:924] = struct.pack("<6I", 24, 3, 8765, 0xFFFFFFFF, 0, 0)
    for row in item["samples"]:
        offset = struct.unpack_from("<I", directory, row["index"] * 12 + 8)[0]
        contents = base64.b64decode(row["data"])
        data[offset:offset + len(contents)] = contents

    synthetic_inventory = json.dumps(dump_inventory([item], application=True)).replace("'", "''")
    inventory_script = f"""
$syntheticInventory = '{synthetic_inventory}' | ConvertFrom-Json
$syntheticInventory.locations[0].path = [IO.Path]::GetDirectoryName($syntheticDumpPath)
$syntheticInventory.locations[0].files[0].path = $syntheticDumpPath
$syntheticInventory
"""
    monkeypatch.setattr(dump_header, "ALL_DUMPS_SCRIPT", inventory_script)
    inspected = dump_header_script(PATH).replace(f"$selected = '{PATH}'", "$selected = $syntheticDumpPath", 1)
    encoded = base64.b64encode(data).decode()
    script = f"""
& {{
    $syntheticDumpPath = [IO.Path]::GetTempFileName()
    try {{
        [IO.File]::WriteAllBytes($syntheticDumpPath, [Convert]::FromBase64String('{encoded}'))
        {inspected}
    }} finally {{ [IO.File]::Delete($syntheticDumpPath) }}
}}
"""
    result = bridge.run(script, depth=8)
    assert result.outcome == "ok" and len(result.items) == 1, result.error
    returned = result.items[0]
    assert returned["status"] == "ok"
    assert len(returned["samples"]) == 5
    sampled = next(row for row in returned["samples"] if row["index"] == 4)
    assert base64.b64decode(sampled["data"]) == sample and sampled["names"] == []
    assert all(row["index"] != 5 for row in returned["samples"])
    reading = take_dump_header(FakeBridge(result), {"path": returned["path"]})
    assert reading.outcome == "ok" and not reading.warnings
    assert reading.section("inspection").data["process"] == {
        "id": 4321, "created_at": "2023-11-14T22:13:20Z", "creation_precision_seconds": 1,
    }
    assert reading.section("inspection").data["exception"]["name"] == "access violation"
