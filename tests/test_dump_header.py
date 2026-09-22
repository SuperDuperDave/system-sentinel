"""The dump reading proves its facts from a bounded header, not from a file name or a guess."""

from __future__ import annotations

import base64
import struct

import pytest

from sentinel.bridge import BridgeResult
from sentinel.readings.dump_header import PREFIX_BYTES, decode_prefix, dump_header_script, take_dump_header
from tests.conftest import FakeBridge

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
    for data in (b"PAGEDU64", b"MDMP" + bytes(92), b"garbage" + bytes(89)):
        raw, derived = decode_prefix(data)
        assert "bugcheck_code" not in raw and "bugcheck" not in derived
        assert derived["header_status"] != "recognized"


def test_one_selected_dump_is_opened_only_after_inventory_match():
    script = dump_header_script(PATH)
    assert "$file = & {" in script and "Where-Object { $_.path -ieq $selected }" in script
    assert "$stream.Read($buffer, 0, $buffer.Length)" in script
    assert f"byte[] {PREFIX_BYTES}" in script
    assert "[IO.File]::Open($file.path" in script
    assert "[IO.File]::Open($selected" not in script
    assert "C:\\Windows\\Minidump\\*.dmp" in script
    with pytest.raises(ValueError):
        dump_header_script("")
    with pytest.raises(ValueError):
        dump_header_script("x" * 1025)
    assert "name''s" in dump_header_script("name's")


def test_denial_and_bridge_failure_carry_no_false_sections():
    denied = take_dump_header(FakeBridge(BridgeResult("ok", items=[{"status": "denied"}])), {"path": PATH})
    assert denied.outcome == "denied" and not denied.observed and denied.sections == []
    unavailable = take_dump_header(FakeBridge(BridgeResult("unavailable", error="interop down")), {"path": PATH})
    assert unavailable.outcome == "unavailable" and unavailable.sections == []


def test_a_read_header_has_raw_provenance_and_separate_interpretation():
    item = {"status": "ok", "name": "example.dmp", "path": PATH, "bytes": 4096, "modified": "2026-09-21T00:00:00Z", "prefix": base64.b64encode(header()).decode()}
    reading = take_dump_header(FakeBridge(BridgeResult("ok", items=[item])), {"path": PATH})
    assert reading.outcome == "ok" and reading.count == 1
    assert [(s.name, s.cls) for s in reading.sections] == [("file", "raw"), ("header", "raw"), ("inspection", "derived")]
    assert reading.section("inspection").data["bugcheck"]["code"] == "0x00000124"
    assert "prefix" not in reading.section("file").data
