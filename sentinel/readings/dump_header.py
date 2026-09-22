"""A bounded, on-demand look inside one Windows dump file.

The inventory is cheap metadata. This reading opens only the selected file, reads at most 96
bytes through the Windows bridge, and interprets the documented x64 DUMP_HEADER64 prefix. It
does not validate the rest of a dump or run a debugger. A file that Windows denies is a denied
reading, not an empty or apparently healthy one.
"""

from __future__ import annotations

import base64
import binascii
import struct
import textwrap
from typing import Any

from ..bridge import Bridge, Outcome
from ..reading import Param, Reading, Section, Spec, register
from .crash import BUGCHECKS
from .system import DUMPS_SCRIPT

PREFIX_BYTES = 96


def dump_header_script(path: str) -> str:
    """Select an exact path from the normal inventory; never open an arbitrary caller path."""
    if not path or len(path) > 1024 or "\x00" in path:
        raise ValueError("path must name one file in the dump inventory")
    quoted = path.replace("'", "''")
    return rf"""
$selected = '{quoted}'
$file = & {{
{DUMPS_SCRIPT.strip()}
}} | Where-Object {{ $_.path -ieq $selected }} | Select-Object -First 1
if ($file) {{
    try {{
        $stream = [IO.File]::Open($file.path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try {{
            $buffer = New-Object byte[] {PREFIX_BYTES}
            $read = $stream.Read($buffer, 0, $buffer.Length)
        }} finally {{ $stream.Dispose() }}
        [pscustomobject]@{{ status = 'ok'; name = $file.name; path = $file.path; bytes = $file.bytes;
            modified = $file.modified; prefix = [Convert]::ToBase64String($buffer, 0, $read) }}
    }} catch [System.UnauthorizedAccessException] {{
        [pscustomobject]@{{ status = 'denied' }}
    }} catch {{
        [pscustomobject]@{{ status = 'failed'; reason = $_.Exception.GetType().Name }}
    }}
}}
"""


def decode_prefix(prefix: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep exact header fields apart from names and conclusions derived from them."""
    signature = prefix[:4].decode("ascii", "replace") if len(prefix) >= 4 else None
    marker = prefix[4:8].decode("ascii", "replace") if len(prefix) >= 8 else None
    raw: dict[str, Any] = {"bytes_read": len(prefix), "signature": signature, "valid_dump_marker": marker}
    if signature == "PAGE" and marker == "DU64":
        if len(prefix) < PREFIX_BYTES:
            return raw, {"format": "Windows kernel dump (x64 header)", "header_status": "truncated", "limit": "Only the file prefix was read; the dump was not validated."}
        major, minor = struct.unpack_from("<II", prefix, 8)
        machine, processors, code = struct.unpack_from("<III", prefix, 48)
        params = struct.unpack_from("<QQQQ", prefix, 64)
        raw.update({
            "major_version": major,
            "minor_version": minor,
            "machine_image_type": f"0x{machine:04X}",
            "number_processors": processors,
            "bugcheck_code": f"0x{code:08X}",
            "bugcheck_parameters": [f"0x{value:016X}" for value in params],
        })
        return raw, {
            "format": "Windows kernel dump (x64 header)",
            "header_status": "recognized",
            "architecture": {0x8664: "x64", 0xAA64: "ARM64"}.get(machine),
            "bugcheck": {"code": f"0x{code:08X}", "name": BUGCHECKS.get(code), "parameters": raw["bugcheck_parameters"]},
            "limit": "The header was read; the rest of the dump, its integrity, stack, modules and cause were not checked.",
        }
    if signature == "MDMP":
        kind = "stream minidump"
    elif signature == "PAGE":
        kind = "Windows dump with an unsupported header"
    else:
        kind = "unrecognized"
    return raw, {"format": kind, "header_status": "unsupported" if len(prefix) >= 8 else "truncated", "limit": "No crash facts were decoded from this file."}


def take_dump_header(bridge: Bridge, params: dict[str, Any]) -> Reading:
    script = dump_header_script(params["path"])
    result = bridge.run(script)
    reading = Reading(
        reading="dump_header", params=params, outcome=result.outcome,
        method={"kind": "powershell", "query": textwrap.dedent(script).strip()},
        took_ms=result.took_ms, warnings=list(result.warnings),
    )
    if result.outcome == "empty":
        reading.error = None
        return reading
    if result.outcome != "ok":
        reading.error = {"kind": result.outcome, "detail": result.error or ""}
        return reading
    if len(result.items) != 1 or not isinstance(result.items[0], dict):
        reading.outcome = "failed"
        reading.error = {"kind": "failed", "detail": "the dump query returned an unexpected shape"}
        return reading
    item = result.items[0]
    if item.get("status") != "ok":
        status: Outcome = "denied" if item.get("status") == "denied" else "failed"
        reading.outcome = status
        reading.error = {"kind": status, "detail": "Windows denied access to this dump file" if status == "denied" else f"the dump file could not be opened ({item.get('reason', 'unknown')})"}
        return reading
    try:
        prefix = base64.b64decode(item["prefix"], validate=True)
        if len(prefix) > PREFIX_BYTES:
            raise ValueError("prefix exceeded read limit")
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        reading.outcome = "failed"
        reading.error = {"kind": "failed", "detail": f"the dump header was malformed: {exc}"}
        return reading
    raw, decoded = decode_prefix(prefix)
    reading.sections = [
        Section("file", "raw", {key: item.get(key) for key in ("name", "path", "bytes", "modified")}),
        Section("header", "raw", raw),
        Section("inspection", "derived", decoded, basis="The first 96 bytes interpreted as a Windows DUMP_HEADER64 prefix only when the PAGE/DU64 markers match."),
    ]
    reading.count = 1
    return reading


register(Spec(
    name="dump_header",
    description="Read up to 96 bytes of one file in the dump inventory for its format and, for a Windows x64 kernel header, its recorded bug check and parameters. No debugger or symbol download; the rest of the file is not validated.",
    classes=("raw", "derived"),
    take=take_dump_header,
    params=(Param("path", "str", None, "Exact path returned by the dumps inventory or a crash stop."),),
    heavy=True,
))
