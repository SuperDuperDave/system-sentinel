"""Process facts from explicitly supported Windows application-fault layouts.

The raw record remains authoritative. Provider identity, event/version and the complete property
layout must agree before positions gain process semantics. Integer arithmetic retains every
FILETIME digit; encoding resolution says nothing about clock accuracy or unique process identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

APPLICATION_ERROR = "Application Error"
APPLICATION_HANG = "Application Hang"

APPLICATION_ERROR_1000 = (
    "AppName",
    "AppVersion",
    "AppTimeStamp",
    "ModuleName",
    "ModuleVersion",
    "ModuleTimeStamp",
    "ExceptionCode",
    "FaultingOffset",
    "ProcessId",
    "ProcessCreationTime",
    "AppPath",
    "ModulePath",
    "IntegratorReportId",
    "PackageFullName",
    "PackageRelativeAppId",
)

APPLICATION_HANG_1002 = (
    "AppName",
    "AppVersion",
    "ProcessId",
    "StartTime",
    "TerminationTime",
    "ExeFileName",
    "ReportId",
    "PackageFullName",
    "PackageRelativeAppId",
    "HangType",
)


@dataclass(frozen=True)
class _Template:
    provider_id: str
    event_id: int
    version: int
    names: tuple[str, ...]
    creation_field: str
    creation_basis: str


_TEMPLATES = {
    APPLICATION_ERROR: _Template(
        "a0e9b465-b939-57d7-b27d-95d8e925ff57", 1000, 0, APPLICATION_ERROR_1000, "ProcessCreationTime",
        "ProcessCreationTime is interpreted as Windows process creation FILETIME; the supported provider message names it as the application's start time.",
    ),
    APPLICATION_HANG: _Template(
        "c631c3dc-c676-59e4-2db3-5c0af00f9675", 1002, 0, APPLICATION_HANG_1002, "StartTime",
        "StartTime is interpreted as Windows process creation FILETIME from the supported layout; this provider's message does not describe the field, so its meaning has weaker support.",
    ),
}
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


def process_identity(record: dict[str, Any]) -> dict[str, Any] | None:
    """Interpret each fact independently, or retain why its value could not be established.

    The allowlist trusts the publisher's GUID/version to identify the layout. It does not detect
    same-length reordering by a writer that violates that identity, or authenticate a record.
    """
    template = _TEMPLATES.get(str(record.get("ProviderName") or ""))
    if template is None:
        return None
    source = {"provider_id": record.get("ProviderId"), "event_id": record.get("Id"), "version": record.get("Version")}
    result: dict[str, Any] = {
        "id": None, "id_status": "unsupported", "id_reason": None,
        "created_at": None, "creation_filetime": None, "creation_resolution_ns": None,
        "creation_status": "unsupported", "creation_reason": None, "source": source, "warnings": [],
    }
    properties = record.get("Properties")
    provider_id = record.get("ProviderId")
    reason = None
    if not isinstance(provider_id, str) or provider_id.lower() != template.provider_id:
        reason = "The record does not carry the supported provider GUID."
    elif type(record.get("Id")) is not int or record["Id"] != template.event_id or type(record.get("Version")) is not int or record["Version"] != template.version:
        reason = "The record does not carry the supported event ID and version."
    elif not isinstance(properties, list) or len(properties) != len(template.names):
        reason = f"The supported layout requires exactly {len(template.names)} properties."
    if reason:
        result["id_reason"] = result["creation_reason"] = reason
        return result

    id_index = template.names.index("ProcessId")
    creation_index = template.names.index(template.creation_field)
    source.update({
        "id_field": "ProcessId", "id_property_index": id_index,
        "creation_field": template.creation_field, "creation_property_index": creation_index,
        "creation_encoding": "FILETIME", "creation_basis": template.creation_basis,
    })
    pid, result["id_status"], result["id_reason"] = _unsigned(properties[id_index], 32)
    if pid == 0:
        result["id_status"], result["id_reason"] = "unsupported", "Zero does not identify an application process."
    else:
        result["id"] = pid

    ticks, result["creation_status"], result["creation_reason"] = _unsigned(properties[creation_index], 64)
    if ticks is None:
        return result
    result["creation_filetime"] = str(ticks)
    result["creation_resolution_ns"] = 100
    if ticks == 0:
        result["creation_status"], result["creation_reason"] = "unsupported", "A zero start value does not establish a process creation time."
        return result
    seconds, fraction = divmod(ticks, 10_000_000)
    try:
        whole = _FILETIME_EPOCH + timedelta(seconds=seconds)
    except OverflowError:
        result["creation_status"], result["creation_reason"] = "unsupported", "The exact FILETIME is outside the supported UTC calendar range (1601–9999)."
    else:
        result["created_at"] = whole.strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction:07d}Z"
        event_ticks = _event_filetime(record.get("TimeCreated"))
        if event_ticks is not None and ticks > event_ticks:
            result["warnings"].append("The interpreted process start is later than the event timestamp; clock changes or the recorded values may need investigation.")
    return result


def _event_filetime(value: Any) -> int | None:
    """Compare known UTC/offset event timestamps without rounding their seventh digit."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.([0-9]{1,7}))?(Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value)
    if match is None:
        return None
    try:
        whole = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
        elapsed = whole - _FILETIME_EPOCH
    except ValueError:
        return None
    return (elapsed.days * 86400 + elapsed.seconds) * 10_000_000 + int((match[2] or "").ljust(7, "0"))


def _unsigned(value: Any, bits: int) -> tuple[int | None, str, str | None]:
    """Accept exact carriers, never float truncation or a guess about unprefixed hexadecimal."""
    if value is None or value == "":
        return None, "absent", "The record did not provide this field."
    if type(value) is int:
        number = value
    elif isinstance(value, str) and len(value) <= 20 and re.fullmatch(r"0|[1-9][0-9]*", value):
        number = int(value, 10)
    elif isinstance(value, str) and re.fullmatch(r"0[xX][0-9a-fA-F]{1,16}", value):
        number = int(value, 16)
    else:
        return None, "malformed", "Expected an exact unsigned integer, canonical decimal text or explicit 0x hexadecimal text."
    if not 0 <= number < 1 << bits:
        return None, "malformed", f"The value is outside the unsigned {bits}-bit range."
    return number, "ok", None
