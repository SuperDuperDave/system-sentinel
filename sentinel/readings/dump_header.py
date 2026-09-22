"""A bounded, on-demand look at the structure of one inventoried Windows dump.

The exact bytes behind every interpreted field are returned with their file offsets. Kernel
headers and user-mode minidump directories are different formats. The latter yields only a few
fixed metadata records; this query does not traverse memory, stack or module-content streams.
"""

from __future__ import annotations

import base64
import binascii
import ntpath
import struct
import textwrap
from datetime import UTC, datetime
from typing import Any

from ..bridge import Bridge, Outcome
from ..reading import Param, Reading, Section, Spec, register
from .crash import BUGCHECKS, EXCEPTIONS
from .dumps import DUMPS_SCRIPT, inventory, missing_file

PREFIX_BYTES = 96
MAX_STREAMS = 128
DIRECTORY_ENTRY_BYTES = 12
MAX_MODULES = 128
MODULE_RECORD_BYTES = 108
MAX_MODULE_NAME_BYTES = 512
STREAM_NAMES = {
    3: "threads", 4: "modules", 5: "memory", 6: "exception", 7: "system", 8: "extended threads",
    9: "memory 64", 14: "unloaded modules", 15: "miscellaneous", 16: "memory information",
    17: "thread information", 21: "system memory information", 24: "thread names",
}
SAMPLE_BYTES = {3: 4, 4: 4 + MAX_MODULES * MODULE_RECORD_BYTES, 6: 168, 7: 32}


def dump_header_script(path: str) -> str:
    """Select an exact path from the normal inventory; never open an arbitrary caller path."""
    if not path or len(path) > 1024 or "\x00" in path:
        raise ValueError("path must name one file in the dump inventory")
    quoted = path.replace("'", "''")
    return rf"""
$selected = '{quoted}'
$inventory = & {{
{DUMPS_SCRIPT.strip()}
}}
$file = $inventory.locations | ForEach-Object {{ $_.files }} | Where-Object {{ $_.path -ieq $selected }} | Select-Object -First 1
if ($file) {{
    try {{
        $stream = [IO.File]::Open($file.path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try {{
            function Read-At([long]$offset, [int]$length) {{
                if ($offset -lt 0 -or $length -lt 0 -or ([long]$offset + $length) -gt $stream.Length) {{ return $null }}
                $buffer = New-Object byte[] $length
                [void]$stream.Seek($offset, [IO.SeekOrigin]::Begin)
                $read = 0
                while ($read -lt $length) {{
                    $part = $stream.Read($buffer, $read, $length - $read)
                    if ($part -le 0) {{ return $null }}
                    $read += $part
                }}
                return [Convert]::ToBase64String($buffer)
            }}
            $lengthBefore = $stream.Length
            $prefix = Read-At 0 ([int][Math]::Min($lengthBefore, {PREFIX_BYTES}))
            $directory = $null
            $directoryStatus = 'not_applicable'
            $samples = @()
            $head = [Convert]::FromBase64String($prefix)
            if ($head.Length -ge 32 -and [Text.Encoding]::ASCII.GetString($head, 0, 4) -eq 'MDMP') {{
                $count = [BitConverter]::ToUInt32($head, 8)
                $rva = [BitConverter]::ToUInt32($head, 12)
                if ($count -gt {MAX_STREAMS}) {{ $directoryStatus = 'limit' }}
                else {{
                    $directory = Read-At $rva ([int]($count * {DIRECTORY_ENTRY_BYTES}))
                    if ($null -eq $directory) {{ $directoryStatus = 'outside_file' }}
                    else {{
                        $directoryStatus = 'ok'
                        $entries = [Convert]::FromBase64String($directory)
                        $seen = @{{}}
                        for ($i = 0; $i -lt $count; $i++) {{
                            $at = $i * {DIRECTORY_ENTRY_BYTES}
                            $kind = [BitConverter]::ToUInt32($entries, $at)
                            $bytes = [BitConverter]::ToUInt32($entries, $at + 4)
                            $where = [BitConverter]::ToUInt32($entries, $at + 8)
                            $sampleLength = switch ($kind) {{ 3 {{ 4 }} 4 {{ 4 }} 6 {{ 168 }} 7 {{ 32 }} default {{ 0 }} }}
                            if ($sampleLength -gt 0 -and $bytes -gt 0 -and -not $seen.ContainsKey($kind)) {{
                                $seen[$kind] = $true
                                $sample = Read-At $where ([int][Math]::Min($bytes, $sampleLength))
                                $names = @()
                                if ($kind -eq 4 -and $null -ne $sample -and $bytes -ge 4) {{
                                    $countBytes = [Convert]::FromBase64String($sample)
                                    $moduleCount = [BitConverter]::ToUInt32($countBytes, 0)
                                    $needed = [long]4 + ([long]$moduleCount * {MODULE_RECORD_BYTES})
                                    if ($moduleCount -le {MAX_MODULES} -and $needed -le $bytes) {{
                                        $whole = Read-At $where ([int]$needed)
                                        if ($null -ne $whole) {{
                                            $sample = $whole
                                            $moduleBytes = [Convert]::FromBase64String($whole)
                                            for ($moduleIndex = 0; $moduleIndex -lt $moduleCount; $moduleIndex++) {{
                                                $nameRva = [BitConverter]::ToUInt32($moduleBytes, 4 + $moduleIndex * {MODULE_RECORD_BYTES} + 20)
                                                $nameHeader = Read-At $nameRva 4
                                                if ($null -eq $nameHeader) {{ continue }}
                                                $nameLength = [BitConverter]::ToUInt32([Convert]::FromBase64String($nameHeader), 0)
                                                if ($nameLength -gt {MAX_MODULE_NAME_BYTES} -or ($nameLength % 2) -ne 0) {{ continue }}
                                                $nameData = Read-At ([long]$nameRva + 4) ([int]$nameLength)
                                                if ($null -ne $nameData) {{ $names += [pscustomobject]@{{ index = $moduleIndex; offset = ([long]$nameRva + 4); data = $nameData }} }}
                                            }}
                                        }}
                                    }}
                                }}
                                if ($null -ne $sample) {{ $samples += [pscustomobject]@{{ index = $i; data = $sample; names = $names }} }}
                            }}
                        }}
                    }}
                }}
            }}
            [pscustomobject]@{{ status = 'ok'; name = $file.name; path = $file.path; bytes = $lengthBefore;
                inventory_bytes = $file.bytes; modified = $file.modified; prefix = $prefix;
                directory = $directory; directory_status = $directoryStatus; samples = $samples;
                length_after = $stream.Length; inventory = $inventory }}
        }} finally {{ $stream.Dispose() }}
    }} catch [System.UnauthorizedAccessException] {{
        [pscustomobject]@{{ status = 'denied'; inventory = $inventory }}
    }} catch {{
        [pscustomobject]@{{ status = 'failed'; reason = $_.Exception.GetType().Name; inventory = $inventory }}
    }}
}} else {{
    [pscustomobject]@{{ status = 'not_inventoried'; inventory = $inventory }}
}}
"""


def decode_prefix(prefix: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep exact header fields apart from names and conclusions derived from them."""
    signature = prefix[:4].decode("ascii", "replace") if len(prefix) >= 4 else None
    marker = prefix[4:8].decode("ascii", "replace") if len(prefix) >= 8 else None
    raw: dict[str, Any] = {"offset": 0, "bytes_read": len(prefix), "bytes_hex": prefix.hex(" "), "signature": signature, "valid_dump_marker": marker}
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
            "field_offsets": {"major_version": 8, "minor_version": 12, "machine_image_type": 48, "number_processors": 52, "bugcheck_code": 56, "bugcheck_parameters": 64},
        })
        return raw, {
            "format": "Windows kernel dump (x64 header)",
            "header_status": "recognized",
            "architecture": {0x8664: "x64", 0xAA64: "ARM64"}.get(machine),
            "bugcheck": {"code": f"0x{code:08X}", "name": BUGCHECKS.get(code), "parameters": raw["bugcheck_parameters"]},
            "limit": "The header was read; the rest of the dump, its integrity, stack, modules and cause were not checked.",
        }
    if signature == "MDMP":
        if len(prefix) < 32:
            return raw, {"format": "stream minidump", "header_status": "truncated", "limit": "The 32-byte minidump header is incomplete."}
        version, count, directory_rva, checksum, timestamp, flags = struct.unpack_from("<IIIIIQ", prefix, 4)
        raw.update({
            "version": f"0x{version:08X}", "number_of_streams": count, "stream_directory_rva": directory_rva,
            "checksum": f"0x{checksum:08X}", "timestamp": timestamp, "flags": f"0x{flags:016X}",
            "field_offsets": {"version": 4, "number_of_streams": 8, "stream_directory_rva": 12, "checksum": 16, "timestamp": 20, "flags": 24},
        })
        try:
            recorded_at = datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z") if timestamp else None
        except (OverflowError, OSError, ValueError):
            recorded_at = None
        return raw, {
            "format": "stream minidump", "header_status": "recognized", "recorded_at": recorded_at,
            "limit": "The header and selected metadata can be inspected; memory payloads, stacks and whole-file integrity are not read.",
        }
    if signature == "PAGE":
        kind = "Windows dump with an unsupported header"
    else:
        kind = "unrecognized"
    return raw, {"format": kind, "header_status": "unsupported" if len(prefix) >= 8 else "truncated", "limit": "No crash facts were decoded from this file."}


def decode_directory(prefix: bytes, directory: bytes | None, status: str, file_size: int, samples: dict[int, tuple[bytes, dict[int, tuple[int, str]]]]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Expose every directory entry and the exact bounded metadata bytes behind the useful ones."""
    count, rva = struct.unpack_from("<II", prefix, 8)
    raw: dict[str, Any] = {"offset": rva, "bytes_hex": directory.hex(" ") if directory is not None else None, "entries": []}
    summary: dict[str, Any] = {"directory_status": status, "streams": count, "thread_count": None, "module_count": None, "modules_read": None, "exception": None, "system": None}
    warnings: list[str] = []
    if count > MAX_STREAMS:
        summary["directory_status"] = "limit"
        warnings.append(f"The file declares {count} streams; this read is capped at {MAX_STREAMS}.")
        return raw, summary, warnings
    expected = count * DIRECTORY_ENTRY_BYTES
    if (count and rva < 32) or rva + expected > file_size or directory is None or len(directory) != expected:
        summary["directory_status"] = "invalid_offset" if count and rva < 32 else "outside_file" if rva + expected > file_size else "incomplete"
        warnings.append("The minidump stream directory could not be read completely.")
        return raw, summary, warnings
    summary["directory_status"] = "ok"
    sampled_kinds: set[int] = set()
    for index in range(count):
        kind, size, offset = struct.unpack_from("<III", directory, index * DIRECTORY_ENTRY_BYTES)
        range_status = "empty" if size == 0 else "within_file" if offset + size <= file_size else "outside_file"
        entry: dict[str, Any] = {"index": index, "type": kind, "name": STREAM_NAMES.get(kind, f"stream {kind}"), "offset": offset, "bytes": size, "range_status": range_status}
        duplicate_sample_kind = kind in SAMPLE_BYTES and size > 0 and kind in sampled_kinds
        if kind in SAMPLE_BYTES and size > 0:
            sampled_kinds.add(kind)
        bundle = samples.get(index)
        if bundle is not None and kind in SAMPLE_BYTES and range_status == "within_file":
            sample, names = bundle
            minimum_sample = min(size, 4 if kind == 4 else SAMPLE_BYTES[kind])
            if len(sample) == minimum_sample or (kind == 4 and len(sample) <= size and len(sample) <= SAMPLE_BYTES[4]):
                entry["sample"] = {"offset": offset, "bytes_hex": sample.hex(" "), "bytes_read": len(sample)}
                entry["sample_status"] = "read"
                if kind in (3, 4):
                    if len(sample) >= 4:
                        entry["recorded_count"] = struct.unpack_from("<I", sample)[0]
                        summary["thread_count" if kind == 3 else "module_count"] = entry["recorded_count"]
                        if kind == 4:
                            module_count = entry["recorded_count"]
                            needed = 4 + module_count * MODULE_RECORD_BYTES
                            if module_count > MAX_MODULES:
                                warnings.append(f"The module list declares {module_count} entries; this read is capped at {MAX_MODULES}.")
                            elif size < needed or len(sample) != needed:
                                warnings.append(f"Stream {index} has no complete bounded module list.")
                            else:
                                entry["modules"] = _module_fields(sample, names, offset)
                                summary["modules_read"] = len(entry["modules"])
                    else:
                        warnings.append(f"Stream {index} has no complete count field.")
                elif kind == 6:
                    entry["exception"] = _exception_fields(sample)
                    if entry["exception"]:
                        summary["exception"] = _exception_summary(entry["exception"])
                    else:
                        warnings.append(f"Stream {index} has no complete exception record in its bounded prefix.")
                elif kind == 7:
                    entry["system_info"] = _system_fields(sample)
                    if entry["system_info"]:
                        summary["system"] = _system_summary(entry["system_info"])
                    else:
                        warnings.append(f"Stream {index} has no complete system record in its bounded prefix.")
            else:
                entry["sample_status"] = "incomplete"
                warnings.append(f"Stream {index} changed or was truncated while its metadata was read.")
        elif kind in SAMPLE_BYTES and range_status == "within_file" and size > 0:
            entry["sample_status"] = "skipped_duplicate" if duplicate_sample_kind else "unavailable"
            if not duplicate_sample_kind:
                warnings.append(f"Stream {index} metadata could not be read.")
        if range_status == "outside_file":
            warnings.append(f"Stream {index} points beyond the current file length.")
        raw["entries"].append(entry)
    if any(entry["range_status"] == "outside_file" for entry in raw["entries"]):
        summary["directory_status"] = "invalid_entries"
    if summary["exception"]:
        address = int(summary["exception"]["address"], 16)
        for entry in raw["entries"]:
            for module in entry.get("modules", []):
                base = int(module["base_address"], 16)
                if base <= address < base + module["bytes"]:
                    summary["exception"]["module_at_address"] = {
                        "name": ntpath.basename(module["name"]) if module["name"] else None,
                        "version": module["file_version"], "base_address": module["base_address"],
                        "basis": "The recorded exception address falls inside this module's recorded image range; this is not a cause finding.",
                    }
                    break
            if "module_at_address" in summary["exception"]:
                break
    return raw, summary, warnings


def _module_fields(sample: bytes, names: dict[int, tuple[int, str]], stream_offset: int) -> list[dict[str, Any]]:
    count = struct.unpack_from("<I", sample)[0]
    modules: list[dict[str, Any]] = []
    for index in range(count):
        record_offset = 4 + index * MODULE_RECORD_BYTES
        base, image_size, checksum, stamp, name_rva = struct.unpack_from("<QIIII", sample, record_offset)
        version_ms, version_ls = struct.unpack_from("<II", sample, record_offset + 32)
        supplied_name = names.get(index)
        name = supplied_name[1] if supplied_name and supplied_name[0] == name_rva + 4 else None
        version = (
            f"{version_ms >> 16}.{version_ms & 0xFFFF}.{version_ls >> 16}.{version_ls & 0xFFFF}"
            if version_ms or version_ls else None
        )
        modules.append({
            "index": index, "record_offset": stream_offset + record_offset,
            "base_address": f"0x{base:016X}", "bytes": image_size,
            "checksum": f"0x{checksum:08X}", "timestamp": stamp,
            "name_rva": name_rva, "name": name, "file_version": version,
        })
    return modules


def _exception_fields(sample: bytes) -> dict[str, Any] | None:
    # MINIDUMP_EXCEPTION_STREAM is a fixed 168-byte record. A shorter stream may
    # contain plausible leading fields, but it is not a complete exception record.
    if len(sample) < 168:
        return None
    thread_id, code, flags = struct.unpack_from("<IxxxxII", sample)
    linked_record, address = struct.unpack_from("<QQ", sample, 16)
    count = struct.unpack_from("<I", sample, 32)[0]
    if count > 15 or len(sample) < 40 + count * 8:
        return None
    parameters = struct.unpack_from("<" + "Q" * count, sample, 40) if count else ()
    return {
        "thread_id": thread_id, "code": f"0x{code:08X}", "flags": f"0x{flags:08X}",
        "linked_record": f"0x{linked_record:016X}", "address": f"0x{address:016X}",
        "number_of_parameters": count, "parameters": [f"0x{value:016X}" for value in parameters],
    }


def _exception_summary(raw: dict[str, Any]) -> dict[str, Any]:
    code = int(raw["code"], 16)
    summary = {"thread_id": raw["thread_id"], "code": raw["code"], "name": EXCEPTIONS.get(code), "address": raw["address"]}
    if code == 0xC0000005 and len(raw["parameters"]) >= 2:
        operation = int(raw["parameters"][0], 16)
        summary["access"] = {"operation": {0: "read", 1: "write", 8: "execute"}.get(operation), "address": raw["parameters"][1]}
    return summary


def _system_fields(sample: bytes) -> dict[str, Any] | None:
    if len(sample) < 32:
        return None
    arch, level, revision, processors, product, major, minor, build, platform, csd_rva, reserved = struct.unpack_from("<HHHBBIIIIII", sample)
    return {
        "processor_architecture": arch, "processor_level": level, "processor_revision": revision,
        "number_of_processors": processors, "product_type": product, "major_version": major,
        "minor_version": minor, "build_number": build, "os_platform": platform,
        "csd_version_rva": csd_rva, "reserved": reserved,
    }


def _system_summary(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "architecture": {0: "x86", 5: "ARM", 6: "IA-64", 9: "x64", 12: "ARM64"}.get(raw["processor_architecture"]),
        "windows_version": f"{raw['major_version']}.{raw['minor_version']}.{raw['build_number']}",
        "processors": raw["number_of_processors"],
    }


def _read_encoded(value: Any, maximum: int, name: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{name} was missing")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{name} was not base64") from exc
    if len(data) > maximum:
        raise ValueError(f"{name} exceeded its read limit")
    return data


def take_dump_header(bridge: Bridge, params: dict[str, Any]) -> Reading:
    script = dump_header_script(params["path"])
    result = bridge.run(script, depth=8)
    reading = Reading(
        reading="dump_header", params=params, outcome=result.outcome,
        method={"kind": "powershell", "query": textwrap.dedent(script).strip()},
        took_ms=result.took_ms, warnings=list(result.warnings),
    )
    if result.outcome == "empty":
        reading.outcome = "failed"
        reading.error = {"kind": "failed", "detail": "The dump query returned no inventory or inspection result."}
        return reading
    if result.outcome != "ok":
        reading.error = {"kind": result.outcome, "detail": result.error or ""}
        return reading
    if len(result.items) != 1 or not isinstance(result.items[0], dict):
        reading.outcome = "failed"
        reading.error = {"kind": "failed", "detail": "the dump query returned an unexpected shape"}
        return reading
    item = result.items[0]
    _, collection, warnings = inventory(item.get("inventory"))
    reading.sections.append(Section("collection", "raw", collection))
    reading.warnings.extend(warnings)
    if item.get("status") == "not_inventoried":
        reading.outcome, detail = missing_file(params["path"], collection)
        if reading.observed:
            reading.count = 0
        else:
            reading.error = {"kind": reading.outcome, "detail": detail}
        return reading
    if item.get("status") != "ok":
        status: Outcome = "denied" if item.get("status") == "denied" else "failed"
        reading.outcome = status
        reading.error = {"kind": status, "detail": "Windows denied access to this dump file" if status == "denied" else f"the dump file could not be opened ({item.get('reason', 'unknown')})"}
        return reading
    try:
        prefix = _read_encoded(item.get("prefix"), PREFIX_BYTES, "prefix")
        file_size = item["bytes"]
        if type(file_size) is not int or file_size < len(prefix):
            raise ValueError("file length was invalid")
        directory_value = item.get("directory")
        directory = _read_encoded(directory_value, MAX_STREAMS * DIRECTORY_ENTRY_BYTES, "directory") if directory_value is not None else None
        sample_rows = item.get("samples") or []
        if not isinstance(sample_rows, list) or len(sample_rows) > len(SAMPLE_BYTES):
            raise ValueError("the stream samples had an unexpected shape")
        samples: dict[int, tuple[bytes, dict[int, tuple[int, str]]]] = {}
        for row in sample_rows:
            if not isinstance(row, dict) or type(row.get("index")) is not int or row["index"] in samples:
                raise ValueError("a stream sample had an unexpected index")
            name_rows = row.get("names") or []
            if not isinstance(name_rows, list) or len(name_rows) > MAX_MODULES:
                raise ValueError("module names exceeded their read limit")
            names: dict[int, tuple[int, str]] = {}
            for name_row in name_rows:
                if (not isinstance(name_row, dict) or type(name_row.get("index")) is not int or type(name_row.get("offset")) is not int
                        or name_row["index"] in names or not 0 <= name_row["index"] < MAX_MODULES):
                    raise ValueError("a module name had an unexpected location")
                encoded_name = _read_encoded(name_row.get("data"), MAX_MODULE_NAME_BYTES, "module name")
                if len(encoded_name) % 2:
                    raise ValueError("a module name had an odd UTF-16 byte count")
                names[name_row["index"]] = (name_row["offset"], encoded_name.decode("utf-16-le"))
            samples[row["index"]] = (_read_encoded(row.get("data"), max(SAMPLE_BYTES.values()), "stream sample"), names)
    except (KeyError, TypeError, ValueError, UnicodeError, binascii.Error) as exc:
        reading.outcome = "failed"
        reading.error = {"kind": "failed", "detail": f"the dump header was malformed: {exc}"}
        return reading
    raw, decoded = decode_prefix(prefix)
    if file_size == 0:
        reading.warnings.append("The inventoried dump file is zero bytes; it contains no header to inspect.")
    if item.get("length_after") not in (None, file_size):
        reading.warnings.append("The dump changed length while its metadata was read; treat this inspection as provisional.")
    if item.get("inventory_bytes") not in (None, file_size):
        reading.warnings.append("The dump length differs from the inventory; the file may still be changing.")
    reading.sections = [Section("file", "raw", {key: item.get(key) for key in ("name", "path", "bytes", "modified", "inventory_bytes")}), Section("header", "raw", raw)]
    basis = "Only the selected file's bounded header bytes were interpreted; whole-file integrity and memory were not inspected."
    if raw["signature"] == "MDMP" and decoded["header_status"] == "recognized":
        streams, summary, warnings = decode_directory(prefix, directory, str(item.get("directory_status") or "incomplete"), file_size, samples)
        reading.sections.append(Section("streams", "raw", streams))
        decoded.update(summary)
        reading.warnings.extend(warnings)
        basis = "The 32-byte MINIDUMP_HEADER, bounded MINIDUMP_DIRECTORY and selected fixed metadata stream prefixes were interpreted at their recorded file offsets."
    reading.sections.append(Section("inspection", "derived", decoded, basis=basis))
    reading.sections.append(Section("collection", "raw", collection))
    reading.count = 1
    return reading


register(Spec(
    name="dump_header",
    description="Inspect one inventoried dump's bounded structural metadata: exact header bytes, a kernel bug check or a user-mode minidump's streams and exception, with offsets and raw bytes. No memory payload, debugger, symbol download or whole-file validation.",
    classes=("raw", "derived"),
    take=take_dump_header,
    params=(Param("path", "str", None, "Exact path returned by the dumps inventory or a crash stop."),),
    heavy=True,
))
