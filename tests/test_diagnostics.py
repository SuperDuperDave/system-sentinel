"""The deep diagnostics and Windows' own second opinion: the fabric is assembled correctly, a
transition is named by its provider as well as its id, the reliability rollup is held to its rule,
and signals says what it could not see.

The unit tests hold the derivations to fixtures, because the shapes that matter on this
machine (a root port with two endpoints under it and a disabled device) are not all present.
"""

import asyncio

import pytest

from sentinel import readings  # noqa: F401  (registers the catalog)
from sentinel.bridge import BridgeResult
from sentinel.reading import REGISTRY, take
from sentinel.readings.diagnostics import (
    CONSTRAINTS_SCRIPT,
    MEMORY_BASIS,
    MEMORY_SCRIPT,
    PCIE_SCRIPT,
    _aspm,
    constraints_derived,
    memory_derived,
    pcie_address,
    pcie_topology,
    power_derived,
    power_script,
    power_source,
    sleep_model,
    take_constraints,
    take_signals_sync,
    transition_kind,
    transitions_query,
)
from sentinel.readings.reliability import RELIABILITY_SCRIPT_TEMPLATE, reliability_days
from tests.conftest import FakeBridge, real_bridge_or_skip

ROOT_PORT = "PCI\\VEN_1022&DEV_1483\\3&A&0&19"
UPSTREAM = "PCI\\VEN_1022&DEV_43E9\\4&C&0&020A"

# A fabric with one root port carrying two endpoints, one switch below it, and a device on
# the root complex. The GPU's parent is spelled in the other case on purpose: Windows gives
# an instance id back in whichever case the interface that reported it used.
PCI_DEVICES = [
    {"Name": "PCI Express Root Port", "InstanceId": ROOT_PORT, "Class": "System", "Status": "OK", "Problem": "CM_PROB_NONE", "Service": "pci",
     "Location": "@System32\\drivers\\pci.sys,#65536;PCI bus %1, device %2, function %3;(0,1,1)", "Parent": "ACPI\\PNP0A08\\0", "ParentName": "PCI Express Root Complex"},
    {"Name": "NVIDIA GeForce RTX 3080", "InstanceId": "PCI\\VEN_10DE&DEV_2206\\4&B&0&0008", "Class": "Display", "Status": "OK", "Problem": "CM_PROB_NONE", "Service": "nvlddmkm",
     "Location": "@System32\\drivers\\pci.sys,#65536;PCI bus %1, device %2, function %3;(1,0,0)", "Parent": ROOT_PORT.lower(), "ParentName": "PCI Express Root Port"},
    {"Name": "High Definition Audio Controller", "InstanceId": "PCI\\VEN_10DE&DEV_1AEF\\4&B&0&0108", "Class": "MEDIA", "Status": "Error", "Problem": "CM_PROB_FAILED_START", "Service": "HDAudBus",
     "Location": "@System32\\drivers\\pci.sys,#65536;PCI bus %1, device %2, function %3;(1,0,1)", "Parent": ROOT_PORT, "ParentName": "PCI Express Root Port"},
    {"Name": "AMD SMBus", "InstanceId": "PCI\\VEN_1022&DEV_790B\\3&A&0&A0", "Class": "System", "Status": "OK", "Problem": "CM_PROB_NONE", "Service": "",
     "Location": "@System32\\drivers\\pci.sys,#65536;PCI bus %1, device %2, function %3;(0,20,0)", "Parent": "ACPI\\PNP0A08\\0", "ParentName": "PCI Express Root Complex"},
]
RELATIONS_OK = {"exit_code": 0, "parsed": True, "listed": 40, "mapped": 40}

POWER_PAYLOAD = {
    "sources": {key: {"outcome": "ok"} for key in ("sleep_states", "aspm", "wake_armed", "hiberboot", "boot_time", "transitions")}
    | {"batteries": {"outcome": "empty"}, "transitions": {"outcome": "ok", "limit": 120, "limit_reached": False, "returned": 5}},
    "sleep_states": [
        "The following sleep states are available on this system:", "Standby (S3)", "Hibernate", "Fast Startup",
        "The following sleep states are not available on this system:", "Standby (S0 Low Power Idle)", "The system firmware does not support this standby state.",
    ],
    "aspm": {"ac_index": "0x00000002", "dc_index": "0x00000000"},
    "wake_armed": ["NONE"],
    "hiberboot_enabled": 1,
    "batteries": [],
    "boot_time": "2020-01-01T00:00:00.0000000Z",
    "transitions": [
        {"RecordId": 10, "Id": 6005, "ProviderName": "EventLog", "TimeCreated": "2026-09-20T10:00:00.000Z"},
        {"RecordId": 9, "Id": 1, "ProviderName": "Microsoft-Windows-Power-Troubleshooter", "TimeCreated": "2026-09-19T10:00:00.000Z"},
        {"RecordId": 8, "Id": 1, "ProviderName": "Microsoft-Windows-Kernel-General", "TimeCreated": "2026-09-18T10:00:00.000Z"},
        {"RecordId": 7, "Id": 41, "ProviderName": "Microsoft-Windows-Kernel-Power", "TimeCreated": "2026-09-17T10:00:00.000Z"},
        {"RecordId": 6, "Id": 4101, "ProviderName": "Display", "TimeCreated": "2026-09-16T10:00:00.000Z"},
    ],
}

MEMORY_PAYLOAD = {
    "modules": [
        {"BankLabel": "P0 CHANNEL A", "DeviceLocator": "DIMM 1", "Manufacturer": "Corsair", "PartNumber": "CMK16 ", "serial_number": "AAAA",
         "Capacity": 8589934592, "Speed": 3600, "ConfiguredClockSpeed": 2133, "ConfiguredVoltage": 1200, "TotalWidth": 64, "DataWidth": 64},
        {"BankLabel": "P0 CHANNEL B", "DeviceLocator": "DIMM 1", "Manufacturer": "Kingston", "PartNumber": "KF432", "serial_number": "BBBB",
         "Capacity": 8589934592, "Speed": 3200, "ConfiguredClockSpeed": 3200, "ConfiguredVoltage": 1350, "TotalWidth": 72, "DataWidth": 64},
    ],
    "arrays": [{"MaxCapacity": 134217728, "MemoryDevices": 4, "MemoryErrorCorrection": 3, "Use": 3}],
    "sources": {key: {"outcome": "ok"} for key in ("modules", "arrays", "diagnostic", "log_begins")},
    "diagnostic": {
        "Id": 1201,
        "TimeCreated": "2026-07-04T02:11:09.000Z",
        "LevelDisplayName": "Information",
        "Message": "The Windows Memory Diagnostic tested the computer's memory and detected no errors.",
    },
    "log_begins": "2026-07-01T06:15:00.000Z",
}

# Three days of Windows' own index and events, including two successful updates. The largest fall
# is not on the day the index is lowest, which is the whole point of the rule that reads it.
RELIABILITY_STABILITY = [
    {"TimeGenerated": "2026-09-17T22:00:00.000Z", "SystemStabilityIndex": 9.4, "RelID": 1},
    {"TimeGenerated": "2026-09-17T23:00:00.000Z", "SystemStabilityIndex": 9.1, "RelID": 2},
    {"TimeGenerated": "2026-09-18T20:00:00.000Z", "SystemStabilityIndex": 7.9, "RelID": 3},
    {"TimeGenerated": "2026-09-18T23:00:00.000Z", "SystemStabilityIndex": 7.4, "RelID": 4},
    {"TimeGenerated": "2026-09-19T21:00:00.000Z", "SystemStabilityIndex": 6.2, "RelID": 5},
    {"TimeGenerated": "2026-09-19T23:00:00.000Z", "SystemStabilityIndex": None, "RelID": 6},
]

RELIABILITY_RECORDS = [
    {"SourceName": "Microsoft-Windows-WindowsUpdateClient", "EventIdentifier": 19, "TimeGenerated": "2026-09-18T09:30:00.000Z",
     "ProductName": "Security Update", "Message": "Installation Successful", "Logfile": "System", "RecordNumber": 4011,
     "InsertionStrings": ["Security Update"], "User": "SOMEBOX\\someone", "ComputerName": "SOMEBOX"},
    {"SourceName": "Microsoft-Windows-WindowsUpdateClient", "EventIdentifier": 19, "TimeGenerated": "2026-09-18T09:31:00.000Z",
     "ProductName": "Security Update", "Message": "Installation Successful", "Logfile": "System", "RecordNumber": 4012,
     "InsertionStrings": ["Security Update"], "User": "SOMEBOX\\someone", "ComputerName": "SOMEBOX"},
    {"SourceName": "Application Error", "EventIdentifier": 1000, "TimeGenerated": "2026-09-19T21:04:00.000Z",
     "ProductName": "example.exe", "Message": "Faulting application example.exe", "Logfile": "Application", "RecordNumber": 4013,
     "InsertionStrings": ["example.exe"], "User": "SOMEBOX\\someone", "ComputerName": "SOMEBOX"},
]

def reliability_payload(record_rows, stability_rows, **outcomes):
    collection = {}
    for name, rows in (("records", record_rows), ("stability", stability_rows)):
        outcome = outcomes.get(name, "ok" if rows else "empty")
        observed = outcome in ("ok", "empty")
        collection[name] = {"outcome": outcome, "available": len(rows) if observed else None, "returned": len(rows), "limit": 500 if name == "records" else None, "error": None if observed else "synthetic source failure"}
    return {"records": record_rows, "stability": stability_rows, "collection": collection, "window_days": 30, "warnings": []}


RELIABILITY_PAYLOAD = reliability_payload(RELIABILITY_RECORDS, RELIABILITY_STABILITY)

# Stops as crash composes them: two that share a bug check, two that wrote none, one on its own.
def _stop(started_at: str, code: str | None = None, name: str | None = None) -> dict:
    return {
        "started_at": started_at,
        "bugcheck": {"code": code, "name": name, "parameters": [], "source": "Kernel-Power 41", "bucket": None} if code else None,
        "no_bugcheck_recorded": code is None,
    }


STOPS = [
    _stop("2026-09-19T03:12:04.000Z", "0x133", "DPC_WATCHDOG_VIOLATION"),
    _stop("2026-09-14T22:41:19.000Z", "0x133", "DPC_WATCHDOG_VIOLATION"),
    _stop("2026-09-07T08:02:55.000Z"),
    _stop("2026-09-02T19:30:00.000Z"),
    _stop("2026-08-30T11:00:00.000Z", "0x1a", "MEMORY_MANAGEMENT"),
]

CONSTRAINT_DEVICES = [
    {"Name": "Realtek Audio", "InstanceId": "HDAUDIO\\A", "Class": "MEDIA", "Status": "Error", "Problem": "CM_PROB_DISABLED", "ProblemDescription": "This device is disabled. (Code 22)."},
    {"Name": "Intel Wireless Bluetooth", "InstanceId": "USB\\B", "Class": "Bluetooth", "Status": "Error", "Problem": "CM_PROB_FAILED_START", "ProblemDescription": "This device cannot start. (Code 10)."},
]


def payload_bridge() -> FakeBridge:
    """Each script is answered by its own fixture, matched on a phrase only that script has."""
    return FakeBridge(
        by_marker={
            "pnputil": BridgeResult("ok", items=[{"devices": PCI_DEVICES, "relation_source": RELATIONS_OK, "warnings": []}], took_ms=11),
            "powercfg": BridgeResult("ok", items=[dict(POWER_PAYLOAD, warnings=["powercfg /a produced no output: the supported sleep states were not observed."])], took_ms=12),
            "Win32_PhysicalMemory": BridgeResult("ok", items=[dict(MEMORY_PAYLOAD)], took_ms=13),
            "CM_PROB_NONE": BridgeResult("ok", items=[{"devices": CONSTRAINT_DEVICES, "warnings": []}], took_ms=14),
            "Win32_ReliabilityRecords": BridgeResult("ok", items=[dict(RELIABILITY_PAYLOAD)], took_ms=15),
        }
    )


# ---------------------------------------------------------------- pcie


def test_the_fabric_places_only_devices_with_observed_parent_chains():
    groups, coverage = pcie_topology(PCI_DEVICES, RELATIONS_OK)
    assert coverage == {"relations": "complete", "returned_devices": 4, "placed_devices": 4, "unplaced": []}
    assert sorted(member["name"] for group in groups for member in group["members"]) == ["AMD SMBus", "High Definition Audio Controller", "NVIDIA GeForce RTX 3080"]


def test_endpoints_under_one_root_port_are_one_group_whatever_the_case():
    groups, _ = pcie_topology(PCI_DEVICES, RELATIONS_OK)
    shared = groups[0]  # the largest group first
    assert shared["kind"] == "root_port"
    assert shared["upstream"] == {"instance_id": ROOT_PORT, "name": "PCI Express Root Port"}
    assert sorted(m["name"] for m in shared["members"]) == ["High Definition Audio Controller", "NVIDIA GeForce RTX 3080"]
    assert all(m["upstream"] == [ROOT_PORT] for m in shared["members"])


def test_a_device_on_the_root_complex_is_grouped_under_its_own_parent():
    groups, _ = pcie_topology(PCI_DEVICES, RELATIONS_OK)
    complex_group = next(g for g in groups if g["upstream"]["instance_id"] == "ACPI\\PNP0A08\\0")
    assert complex_group["kind"] == "non_pci_parent"
    assert [m["name"] for m in complex_group["members"]] == ["AMD SMBus"]
    assert complex_group["upstream"]["name"] == "PCI Express Root Complex"
    assert complex_group["members"][0]["upstream"] == []


def test_a_chain_that_points_at_itself_does_not_loop():
    looped = [{"Name": "A", "InstanceId": "PCI\\A", "Parent": "PCI\\B"}, {"Name": "B", "InstanceId": "PCI\\B", "Parent": "PCI\\A"}]
    groups, coverage = pcie_topology(looped, RELATIONS_OK)
    assert groups is None and coverage["relations"] == "none"
    assert {row["reason"] for row in coverage["unplaced"]} == {"parent relations form a loop"}


def test_the_bus_address_is_read_from_the_enumerators_location_string():
    assert pcie_address("@System32\\drivers\\pci.sys,#65536;PCI bus %1, device %2, function %3;(1,0,1)") == {"bus": 1, "device": 0, "function": 1, "address": "01:00.1"}
    assert pcie_address(None) is None
    assert pcie_address("PCI bus 1, device 0, function 0") is None


def test_pcie_keeps_raw_devices_and_counts_them_separately_from_placed_groups():
    reading = asyncio.run(take("pcie", payload_bridge(), {}))
    assert reading.outcome == "ok" and reading.count == 4
    assert [(s.name, s.cls) for s in reading.sections] == [("devices", "raw"), ("groups", "derived"), ("coverage", "derived"), ("collection", "raw")]
    assert reading.section("devices").data == PCI_DEVICES
    assert reading.section("coverage").data["relations"] == "complete"
    assert reading.section("collection").data["pnputil"] == RELATIONS_OK
    assert reading.section("groups").basis
    assert "pnputil" in reading.method["query"] and reading.method["kind"] == "powershell"


def test_pcie_says_unavailable_rather_than_empty_when_the_bridge_did_not_answer():
    reading = asyncio.run(take("pcie", FakeBridge(BridgeResult("unavailable", error="powershell.exe was not found")), {}))
    assert reading.outcome == "unavailable" and reading.sections == []
    assert reading.error == {"kind": "unavailable", "detail": "powershell.exe was not found"}


def test_pcie_inventory_query_failure_is_not_an_empty_machine():
    reading = asyncio.run(take("pcie", FakeBridge(BridgeResult("failed", error="Get-PnpDevice failed")), {}))
    assert reading.outcome == "failed" and reading.section("devices") is None


def test_a_machine_with_no_pci_device_is_empty_not_ok():
    bridge = FakeBridge(BridgeResult("ok", items=[{"devices": [], "relation_source": RELATIONS_OK, "warnings": []}]))
    reading = asyncio.run(take("pcie", bridge, {}))
    assert reading.outcome == "empty" and reading.count == 0
    assert reading.section("groups").data == []


def test_missing_relations_keep_inventory_without_inventing_a_group():
    devices = [dict(PCI_DEVICES[1], Parent=None), dict(PCI_DEVICES[2], Parent=None)]
    source = {"exit_code": 1, "parsed": False, "listed": 0, "mapped": 0}
    reading = asyncio.run(take("pcie", FakeBridge(BridgeResult("ok", items=[{"devices": devices, "relation_source": source, "warnings": []}])), {}))
    assert reading.outcome == "ok" and reading.count == 2
    assert reading.section("devices").data == devices
    assert reading.section("groups").data is None
    assert reading.section("coverage").data["relations"] == "none"
    assert len(reading.section("coverage").data["unplaced"]) == 2
    assert reading.warnings


def test_partial_relations_place_only_complete_chains():
    devices = PCI_DEVICES + [dict(PCI_DEVICES[1], InstanceId="PCI\\OTHER", Parent=None)]
    groups, coverage = pcie_topology(devices, RELATIONS_OK)
    assert coverage["relations"] == "partial" and coverage["placed_devices"] == 4
    assert coverage["unplaced"] == [{"instance_id": "PCI\\OTHER", "at": "PCI\\OTHER", "reason": "parent not reported"}]
    assert all(m["instance_id"] != "PCI\\OTHER" for g in groups for m in g["members"])


def test_missing_upstream_pci_device_is_unplaced():
    groups, coverage = pcie_topology([dict(PCI_DEVICES[1], Parent="PCI\\NOT_RETURNED")], RELATIONS_OK)
    assert groups is None and coverage["relations"] == "none"
    assert coverage["unplaced"][0]["reason"] == "upstream PCI device was not returned"
    assert coverage["unplaced"][0]["at"] == "PCI\\NOT_RETURNED"


def test_missing_parent_mid_chain_marks_descendants_unplaced_and_keeps_independent_chain():
    devices = [dict(d, Parent=None) if d["InstanceId"] == ROOT_PORT else d for d in PCI_DEVICES]
    groups, coverage = pcie_topology(devices, RELATIONS_OK)
    assert coverage["relations"] == "partial" and coverage["placed_devices"] == 1
    assert {r["instance_id"] for r in coverage["unplaced"]} == {ROOT_PORT, PCI_DEVICES[1]["InstanceId"], PCI_DEVICES[2]["InstanceId"]}
    assert {r["at"] for r in coverage["unplaced"]} == {ROOT_PORT}
    assert [[m["name"] for m in g["members"]] for g in groups] == [["AMD SMBus"]]


def test_missing_relation_source_is_unknown_even_when_devices_have_parent_fields():
    groups, coverage = pcie_topology(PCI_DEVICES, {})
    assert groups is None and coverage["relations"] == "none"
    assert {r["reason"] for r in coverage["unplaced"]} == {"the relation source did not answer"}


def test_nonzero_relation_exit_makes_even_reported_chains_partial():
    source = dict(RELATIONS_OK, exit_code=5)
    groups, coverage = pcie_topology(PCI_DEVICES, source)
    assert groups and coverage["relations"] == "partial" and coverage["unplaced"] == []
    reading = asyncio.run(take("pcie", FakeBridge(BridgeResult("ok", items=[{"devices": PCI_DEVICES, "relation_source": source, "warnings": []}])), {}))
    assert any("source did not complete cleanly" in w for w in reading.warnings)


# ---------------------------------------------------------------- power


def test_a_transition_is_named_by_its_provider_as_well_as_its_id():
    assert transition_kind({"ProviderName": "Microsoft-Windows-Power-Troubleshooter", "Id": 1}) == "wake"
    assert transition_kind({"ProviderName": "Microsoft-Windows-Kernel-General", "Id": 1}) == "unnamed transition"
    assert transition_kind({"ProviderName": "Microsoft-Windows-Kernel-Power", "Id": 41}) == "unexpected shutdown"


def test_the_logs_own_start_and_stop_are_in_the_ledger():
    assert transition_kind({"ProviderName": "EventLog", "Id": 6005}) == "log started"
    assert transition_kind({"ProviderName": "EventLog", "Id": 6006}) == "log stopped"
    assert "Provider[@Name='EventLog'] and (EventID=6005 or EventID=6006 or EventID=6008)" in transitions_query()


def test_the_ledger_query_gives_each_provider_its_own_event_ids():
    query = transitions_query()
    assert "Provider[@Name='Microsoft-Windows-Kernel-General'] and (EventID=12 or EventID=13)" in query
    assert "Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and (EventID=1)" in query
    assert query.count("<Select") == 6  # one per provider, not one per event id
    assert transitions_query() in power_script()


def test_the_sleep_model_is_read_only_from_the_states_that_are_available():
    assert sleep_model(POWER_PAYLOAD["sleep_states"]) == "legacy standby (S3)"
    assert sleep_model(["The following sleep states are available on this system:", "Standby (S0 Low Power Idle)"]) == "modern standby (S0 low power idle)"
    assert sleep_model(["Standby (S3)"]) is None  # without the heading there is no way to tell
    assert sleep_model([]) is None


def test_the_link_power_setting_is_the_documented_index():
    assert _aspm("0x00000002") == {"index": "0x00000002", "setting": "L1"}
    assert _aspm("0x00000000")["setting"] == "off"
    assert _aspm("not a number") == {"index": "not a number", "setting": None}
    assert _aspm(None) is None


def test_power_counts_the_ledger_and_reports_the_window_it_covers():
    derived = power_derived(POWER_PAYLOAD)
    assert derived["sleep_model"] == "legacy standby (S3)"
    assert derived["power_source"] == "mains (no battery reported)"
    assert derived["fast_startup"] is True
    assert derived["wake_armed"] == [] and derived["wake_armed_count"] == 0  # powercfg prints NONE for nothing armed
    assert derived["ledger"]["counts"] == {"display driver reset": 1, "log started": 1, "unexpected shutdown": 1, "unnamed transition": 1, "wake": 1}
    assert derived["ledger"]["window"] == {"first": "2026-09-16T10:00:00.000Z", "last": "2026-09-20T10:00:00.000Z"}
    assert derived["ledger"]["limit"] == 120 and derived["ledger"]["limit_reached"] is False
    assert derived["uptime_seconds"] > 0


def test_power_lifts_a_sub_query_failure_into_the_envelope():
    reading = asyncio.run(take("power", payload_bridge(), {}))
    assert reading.outcome == "ok"
    assert [(s.name, s.cls) for s in reading.sections] == [("raw", "raw"), ("derived", "derived"), ("collection", "raw")]
    assert any("powercfg /a" in w for w in reading.warnings)
    assert "warnings" not in reading.section("raw").data  # a warning is not a finding


def test_power_failed_sources_do_not_claim_mains_wake_devices_or_empty_transitions():
    payload = dict(POWER_PAYLOAD, sources={**POWER_PAYLOAD["sources"],
        "batteries": {"outcome": "failed"}, "wake_armed": {"outcome": "failed"}, "transitions": {"outcome": "failed"}},
        batteries=[], wake_armed=[], transitions=[])
    derived = power_derived(payload)
    assert derived["power_source"] is None
    assert derived["wake_armed"] is None and derived["wake_armed_count"] is None
    assert derived["ledger"]["records"] is None and derived["ledger"]["counts"] is None
    assert derived["sleep_model"] == "legacy standby (S3)"  # independent source remains visible
    assert power_derived({"batteries": []})["power_source"] is None  # missing provenance is a failure


def test_power_battery_status_only_names_a_source_when_windows_reports_one():
    assert power_source([{"BatteryStatus": 1}]) == "battery (discharging)"
    assert power_source([{"BatteryStatus": 2}]) == "mains (battery present)"
    assert power_source([{"BatteryStatus": 6}]) == "mains (battery charging)"
    assert "unknown" in power_source([{"BatteryStatus": 3}])
    assert "disagree" in power_source([{"BatteryStatus": 1}, {"BatteryStatus": 2}])
    assert power_source([{"BatteryStatus": 1}, {"BatteryStatus": 3}]) == "battery (discharging)"
    assert power_source([{"BatteryStatus": 2}, {"BatteryStatus": 3}]) == "mains (battery present)"


@pytest.mark.parametrize("source, field, derived_path", [
    ("sleep_states", "sleep_states", ("sleep_model",)),
    ("aspm", "aspm", ("link_power_management", "ac")),
    ("hiberboot", "hiberboot_enabled", ("fast_startup",)),
    ("boot_time", "boot_time", ("uptime_seconds",)),
])
def test_power_a_failed_independent_source_cannot_use_stale_raw_values(source: str, field: str, derived_path: tuple[str, ...]):
    payload = dict(POWER_PAYLOAD, sources={**POWER_PAYLOAD["sources"], source: {"outcome": "failed"}})
    assert payload[field] is not None  # stale values deliberately remain in the synthetic payload
    value = power_derived(payload)
    for key in derived_path:
        value = value[key]
    assert value is None


def test_power_with_no_answered_sources_keeps_only_failure_provenance():
    payload = dict(POWER_PAYLOAD, sources={name: {"outcome": "failed"} for name in POWER_PAYLOAD["sources"]},
        sleep_states=None, wake_armed=None, batteries=None, transitions=None)
    reading = asyncio.run(take("power", FakeBridge(BridgeResult("ok", items=[payload])), {}))
    assert reading.outcome == "failed" and reading.count is None
    assert [section.name for section in reading.sections] == ["collection"]


def test_power_mismatched_transition_metadata_cannot_certify_returned_counts():
    payload = dict(POWER_PAYLOAD, sources={**POWER_PAYLOAD["sources"], "transitions": {"outcome": "ok", "limit": 120, "limit_reached": False, "returned": 6}})
    reading = asyncio.run(take("power", FakeBridge(BridgeResult("ok", items=[payload])), {}))
    assert reading.outcome == "ok"
    assert reading.section("collection").data["transitions"]["outcome"] == "failed"
    assert reading.section("derived").data["ledger"]["records"] is None
    assert any("transitions source" in warning for warning in reading.warnings)


# ---------------------------------------------------------------- memory


def test_memory_reads_the_slots_and_the_kit():
    derived = memory_derived(MEMORY_PAYLOAD)
    assert derived["installed_gb"] == 16.0
    assert (derived["slots_used"], derived["slots_total"], derived["slots_free"]) == (2, 4, 2)
    assert derived["modules"][0]["locator"] == "P0 CHANNEL A - DIMM 1"
    assert derived["modules"][0]["error_correction"] is False and derived["modules"][1]["error_correction"] is True
    assert derived["mixed_kit"] is True and derived["kits"] == ["Corsair CMK16", "Kingston KF432"]
    assert derived["below_rated_speed"] == ["DIMM 1"]
    assert "ledger" not in derived


def test_memory_does_not_invent_empty_slots_when_the_array_count_is_unknown_or_inconsistent():
    for count in (None, 0, 1):
        payload = dict(MEMORY_PAYLOAD, arrays=[{"MemoryDevices": count}])
        derived = memory_derived(payload)
        assert derived["slots_used"] == 2
        assert derived["slots_total"] is None
        assert derived["slots_free"] is None


def test_memory_does_not_turn_missing_module_capacity_into_zero_gigabytes():
    modules = [dict(MEMORY_PAYLOAD["modules"][0], Capacity=None), MEMORY_PAYLOAD["modules"][1]]
    derived = memory_derived(dict(MEMORY_PAYLOAD, modules=modules))
    assert derived["installed_gb"] is None
    assert derived["modules"][0]["capacity_gb"] is None
    assert derived["modules"][1]["capacity_gb"] == 8.0
    empty = memory_derived(dict(MEMORY_PAYLOAD, modules=[]))
    assert empty["installed_gb"] is None
    assert empty["slots_free"] is None


def test_the_bug_check_half_of_the_ledger_belongs_to_the_crash_reading_now():
    assert "Microsoft-Windows-WHEA-Logger" not in MEMORY_SCRIPT
    assert "WER-SystemErrorReporting" not in MEMORY_SCRIPT
    assert "crash" in MEMORY_BASIS and "whea" in MEMORY_BASIS


def test_the_memory_diagnostic_carries_how_far_back_no_result_reaches():
    diagnostic = memory_derived(MEMORY_PAYLOAD)["memory_diagnostic"]
    assert diagnostic["last_result"] == {
        "Id": 1201,
        "TimeCreated": "2026-07-04T02:11:09.000Z",
        "LevelDisplayName": "Information",
        "Message": "The Windows Memory Diagnostic tested the computer's memory and detected no errors.",
    }
    assert diagnostic["log_begins"] == "2026-07-01T06:15:00.000Z"
    assert diagnostic["outcome"] == "ok"
    assert "never run" in MEMORY_BASIS  # a null result is not proof the test was never run


def test_no_diagnostic_result_is_a_null_beside_the_logs_reach_not_a_silence():
    payload = dict(MEMORY_PAYLOAD, diagnostic=None, sources={**MEMORY_PAYLOAD["sources"], "diagnostic": {"outcome": "empty"}})
    assert memory_derived(payload)["memory_diagnostic"] == {"outcome": "empty", "last_result": None, "log_begins": "2026-07-01T06:15:00.000Z"}
    assert memory_derived({})["memory_diagnostic"] == {"outcome": "failed", "last_result": None, "log_begins": None}


def test_an_unobserved_module_inventory_is_unknown_not_zero():
    payload = dict(MEMORY_PAYLOAD, modules=[], sources={**MEMORY_PAYLOAD["sources"], "modules": {"outcome": "failed"}})
    derived = memory_derived(payload)
    assert derived["slots_used"] is None and derived["modules"] is None and derived["mixed_kit"] is None
    assert derived["slots_total"] == 4 and derived["slots_free"] is None
    assert memory_derived({"modules": [], "sources": {"modules": {"outcome": "empty"}}})["slots_used"] == 0


def test_memory_missing_inventory_fields_fail_the_source_without_erasing_other_data():
    payload = dict(MEMORY_PAYLOAD)
    del payload["modules"]
    bridge = FakeBridge(BridgeResult("ok", items=[payload]))
    reading = asyncio.run(take("memory", bridge, {}))
    assert reading.outcome == "ok" and reading.count is None
    assert reading.section("collection").data["modules"]["outcome"] == "failed"
    assert reading.section("derived").data["slots_used"] is None
    assert reading.section("derived").data["slots_total"] == 4
    assert any("modules source" in warning for warning in reading.warnings)
    empty = asyncio.run(take("memory", FakeBridge(BridgeResult("ok", items=[{}])), {}))
    assert empty.outcome == "failed" and empty.count is None
    assert [section.name for section in empty.sections] == ["collection"]
    null_inventory = asyncio.run(take("memory", FakeBridge(BridgeResult("ok", items=[dict(MEMORY_PAYLOAD, modules=None)])), {}))
    assert null_inventory.section("collection").data["modules"]["outcome"] == "failed"
    assert null_inventory.section("derived").data["slots_used"] is None


def test_memory_diagnostic_failure_is_not_a_negative_test_result():
    payload = dict(MEMORY_PAYLOAD, sources={**MEMORY_PAYLOAD["sources"], "diagnostic": {"outcome": "failed"}})
    result = memory_derived(payload)["memory_diagnostic"]
    assert result["outcome"] == "failed" and result["last_result"] is None


@pytest.mark.parametrize("code, expected", [(0, None), (1, None), (2, None), (3, False), (4, False), (5, True), (6, True), (7, None)])
def test_array_error_correction_uses_the_documented_windows_codes(code: int, expected: bool | None):
    payload = dict(MEMORY_PAYLOAD, arrays=[{**MEMORY_PAYLOAD["arrays"][0], "MemoryErrorCorrection": code}])
    derived = memory_derived(payload)
    assert derived["array_error_correction"] is expected
    assert derived["array_error_correction_type"] is not None


def test_multiple_system_memory_arrays_contribute_all_slots_and_require_ecc_agreement():
    first = {"MemoryDevices": 8, "MemoryErrorCorrection": 5, "Use": 3}
    second = {"MemoryDevices": 8, "MemoryErrorCorrection": 5, "Use": 3}
    derived = memory_derived(dict(MEMORY_PAYLOAD, arrays=[first, second]))
    assert derived["slots_total"] == 16 and derived["slots_free"] == 14
    assert derived["array_error_correction"] is True and derived["array_error_correction_type"] == "single-bit ECC"
    mixed = memory_derived(dict(MEMORY_PAYLOAD, arrays=[first, {**second, "MemoryErrorCorrection": 3}]))
    assert mixed["slots_total"] == 16 and mixed["array_error_correction"] is None and mixed["array_error_correction_type"] is None
    missing = memory_derived(dict(MEMORY_PAYLOAD, arrays=[first, {**second, "MemoryDevices": None}]))
    assert missing["slots_total"] is None and missing["slots_free"] is None


def test_memory_returns_raw_and_derived_and_counts_its_modules():
    reading = asyncio.run(take("memory", payload_bridge(), {}))
    assert reading.outcome == "ok" and reading.count == 2
    assert [(s.name, s.cls) for s in reading.sections] == [("raw", "raw"), ("derived", "derived"), ("collection", "raw")]
    assert reading.section("raw").data["modules"][0]["serial_number"] == "AAAA"  # redaction happens at the boundary, not here


# ---------------------------------------------------------------- constraints


def test_a_disabled_device_and_a_device_that_will_not_start_are_kept_apart():
    derived = constraints_derived(CONSTRAINT_DEVICES)
    assert [d["name"] for d in derived["disabled"]] == ["Realtek Audio"]
    assert [d["name"] for d in derived["not_working"]] == ["Intel Wireless Bluetooth"]
    assert derived["counts"] == {"disabled": 1, "not_working": 1}
    assert derived["by_problem"] == {"CM_PROB_DISABLED": 1, "CM_PROB_FAILED_START": 1}


def test_every_device_working_is_an_empty_reading_not_an_absent_one():
    bridge = FakeBridge(BridgeResult("ok", items=[{"devices": [], "warnings": []}]))
    reading = asyncio.run(take("constraints", bridge, {}))
    assert reading.outcome == "empty" and reading.count == 0 and reading.error is None


@pytest.mark.parametrize("result", [
    BridgeResult("empty"),
    BridgeResult("ok", items=[{"devices": []}, {"devices": CONSTRAINT_DEVICES}]),
])
def test_missing_or_ambiguous_device_object_cannot_claim_every_device_is_working(result):
    reading = take_constraints(FakeBridge(result), {})
    assert reading.outcome == "failed" and not reading.observed
    assert reading.count is None and reading.sections == []
    assert "exactly one object" in reading.error["detail"]


def test_constraints_returns_raw_and_derived():
    reading = asyncio.run(take("constraints", payload_bridge(), {}))
    assert reading.outcome == "ok" and reading.count == 2
    assert [(s.name, s.cls) for s in reading.sections] == [("raw", "raw"), ("derived", "derived")]


# ---------------------------------------------------------------- reliability


def test_the_day_rollup_reads_the_index_at_each_days_end_and_its_lowest_hour():
    rollup = reliability_days(RELIABILITY_RECORDS, RELIABILITY_STABILITY)
    assert [d["day"] for d in rollup["days"]] == ["2026-09-17", "2026-09-18", "2026-09-19"]
    assert [d["index_last"] for d in rollup["days"]] == [9.1, 7.4, 6.2]  # the last hour that reported one
    assert [d["index_min"] for d in rollup["days"]] == [9.1, 7.4, 6.2]
    assert rollup["days"][1]["records"] == {"Microsoft-Windows-WindowsUpdateClient": 2}
    assert rollup["days"][1]["event_types"] == [
        {"source": "Microsoft-Windows-WindowsUpdateClient", "event_id": 19, "count": 2}
    ]
    assert rollup["days"][0]["records"] == {}  # a day Windows counted nothing on is still a day
    assert rollup["index_now"] == 6.2
    assert rollup["index_lowest"] == {"day": "2026-09-19", "index": 6.2}
    assert rollup["sources"] == {"Microsoft-Windows-WindowsUpdateClient": 2, "Application Error": 1}
    assert (rollup["from"], rollup["to"]) == ("2026-09-17T22:00:00.000Z", "2026-09-19T23:00:00.000Z")


def test_a_day_with_records_and_no_index_is_still_a_day():
    rollup = reliability_days([RELIABILITY_RECORDS[2]], [])
    assert rollup["days"] == [{
        "day": "2026-09-19", "index_last": None, "index_min": None,
        "records": {"Application Error": 1},
        "event_types": [{"source": "Application Error", "event_id": 1000, "count": 1}],
    }]
    assert rollup["index_now"] is None and rollup["index_lowest"] is None


def test_reliability_returns_both_raw_sections_and_the_rollup():
    reading = asyncio.run(take("reliability", payload_bridge(), {}))
    assert reading.outcome == "ok" and reading.count == 3
    assert [(s.name, s.cls) for s in reading.sections] == [("records", "raw"), ("stability", "raw"), ("days", "derived"), ("collection", "raw")]
    assert reading.section("days").basis
    assert reading.section("records").data[0]["ComputerName"] == "SOMEBOX"  # redaction happens at the boundary
    assert "-30" in reading.method["query"]


def test_a_machine_windows_kept_no_record_of_is_empty_not_ok():
    bridge = FakeBridge(BridgeResult("ok", items=[reliability_payload([], [])]))
    reading = asyncio.run(take("reliability", bridge, {}))
    assert reading.outcome == "empty" and reading.count == 0 and reading.error is None
    assert [s.name for s in reading.sections] == ["records", "stability", "days", "collection"]


def test_one_class_answering_and_the_other_not_is_still_an_observed_reading():
    payload = reliability_payload(RELIABILITY_RECORDS, [], stability="failed")
    reading = asyncio.run(take("reliability", FakeBridge(BridgeResult("ok", items=[payload])), {}))
    assert reading.outcome == "ok" and reading.count == 3
    assert any("stability did not answer" in w for w in reading.warnings)
    assert all(d["index_last"] is None for d in reading.section("days").data["days"])


@pytest.mark.parametrize("records,stability,expected", [
    ("failed", "failed", "failed"), ("denied", "denied", "denied"),
    ("denied", "failed", "failed"), ("empty", "failed", "failed"),
    ("failed", "empty", "failed"), ("empty", "denied", "denied"),
])
def test_no_data_from_a_partial_or_failed_collection_is_not_an_observed_absence(records, stability, expected):
    payload = reliability_payload([], [], records=records, stability=stability)
    reading = asyncio.run(take("reliability", FakeBridge(BridgeResult("ok", items=[payload])), {}))
    assert reading.outcome == expected and not reading.observed
    assert reading.count is None and reading.error["kind"] == expected
    assert reading.section("collection").data["records"]["outcome"] == records
    assert reading.section("collection").data["stability"]["outcome"] == stability


def test_stability_without_event_observation_keeps_counts_unknown_in_rollup_and_signals():
    payload = reliability_payload([], RELIABILITY_STABILITY, records="failed")
    reading = asyncio.run(take("reliability", FakeBridge(BridgeResult("ok", items=[payload])), {}))
    assert reading.outcome == "ok" and reading.count is None
    days = reading.section("days").data
    assert days["sources"] is None and days["index_now"] == 6.2
    assert all(row["records"] is None and row["event_types"] is None for row in days["days"])
    signals, _ = take_signals_sync(_inputs(reliability=reading))
    fall = next(s for s in signals if s["id"] == "transition:reliability-index-fall")
    assert fall["evidence"]["records"] is None and fall["evidence"]["event_types"] is None
    assert "could not be observed" in fall["summary"]


@pytest.mark.parametrize("collection", [None, {}, {"records": {"outcome": "empty", "available": 0, "returned": 1}}])
def test_missing_or_inconsistent_source_results_do_not_certify_an_empty_machine(collection):
    payload = {"records": [], "stability": [], "collection": collection}
    reading = asyncio.run(take("reliability", FakeBridge(BridgeResult("ok", items=[payload])), {}))
    assert reading.outcome == "failed" and not reading.observed


@pytest.mark.parametrize("source,rows,metadata", [
    ("records", [], {"outcome": "empty", "available": 5, "returned": 0, "limit": 500}),
    ("records", [{}, {}], {"outcome": "ok", "available": 2, "returned": 2, "limit": 1}),
    ("stability", [{}], {"outcome": "ok", "available": 2, "returned": 1, "limit": None}),
    ("records", [], {"outcome": "empty", "available": 0, "returned": 0, "limit": 0}),
    ("records", [], {"outcome": "empty", "available": 0, "returned": 0, "limit": True}),
    ("records", [], {"outcome": "empty", "available": 0, "returned": 0, "limit": "500"}),
    ("records", [], {"outcome": "empty", "available": 0, "returned": 0}),
])
def test_contradictory_source_coverage_cannot_certify_observation(source, rows, metadata):
    payload = reliability_payload([], [])
    payload[source] = rows
    payload["collection"][source] = metadata
    reading = asyncio.run(take("reliability", FakeBridge(BridgeResult("ok", items=[payload])), {}))
    assert reading.outcome == "failed" and not reading.observed and reading.count is None
    assert reading.section(source).data == []
    assert reading.section("collection").data[source]["outcome"] == "failed"
    other = "stability" if source == "records" else "records"
    assert reading.section("collection").data[other]["outcome"] == "empty"


def test_reliability_exposes_the_sources_returned_and_available_counts():
    payload = reliability_payload(RELIABILITY_RECORDS, RELIABILITY_STABILITY)
    payload["collection"]["records"].update(available=900, limit=3)
    reading = asyncio.run(take("reliability", FakeBridge(BridgeResult("ok", items=[payload])), {}))
    source = reading.section("collection").data["records"]
    assert (source["available"], source["returned"], source["limit"]) == (900, 3, 3)
    assert reading.count == 3


def test_rows_from_an_incomplete_source_never_enter_the_observed_rollup():
    payload = reliability_payload(RELIABILITY_RECORDS, RELIABILITY_STABILITY, records="failed")
    reading = asyncio.run(take("reliability", FakeBridge(BridgeResult("ok", items=[payload])), {}))
    assert reading.outcome == "ok" and reading.count is None
    assert reading.section("records").data == []
    assert reading.section("days").data["sources"] is None


def test_a_window_outside_the_range_is_refused():
    for days in (0, 400):
        with pytest.raises(ValueError, match="days"):
            asyncio.run(take("reliability", payload_bridge(), {"days": days}))


# ---------------------------------------------------------------- signals


def _reading(name: str, sections: list[tuple[str, str, object]], outcome: str = "ok"):
    from sentinel.reading import Reading, Section

    return Reading(reading=name, params={}, outcome=outcome, method={"kind": "powershell", "query": "q"}, sections=[Section(n, c, d) for n, c, d in sections])


def _inputs(**over):
    base = {
        "hardware": _reading("hardware", [("fingerprint", "derived", {"gpu": {"date": "2018-01-01", "driver_version": "1.0"}}), ("config", "raw", {"fast_startup": False})]),
        "pcie": _reading("pcie", [("groups", "derived", [{"kind": "root_port", "upstream": {"instance_id": "PCI\\R", "name": "Root Port"}, "members": [{"name": "GPU", "status": "OK"}, {"name": "Audio", "status": "Error"}]}]), ("coverage", "derived", {"relations": "complete", "returned_devices": 3, "placed_devices": 3, "unplaced": []})]),
        "power": _reading("power", [("derived", "derived", {"fast_startup": True, "uptime_seconds": 30 * 86400, "link_power_management": {"ac": {"index": "0x2", "setting": "L1"}}, "ledger": {"counts": {"unexpected shutdown": 2, "wake": 1, "display driver reset": 1}, "window": {"first": "a", "last": "b"}}})]),
        "constraints": _reading("constraints", [("derived", "derived", constraints_derived(CONSTRAINT_DEVICES))]),
        "events": _reading("events", [("records", "raw", [{"ProviderName": "Service Control Manager", "TimeCreated": f"2026-09-0{i % 9 + 1}T00:00:00Z"} for i in range(30)] + [{"ProviderName": "Quiet", "TimeCreated": "2026-09-01T00:00:00Z"}])]),
        "crash": _reading("crash", [("stops", "derived", STOPS), ("collection", "raw", {"system": {"outcome": "ok", "bound_reached": False}, "reports": {"outcome": "ok", "bound_reached": False}})]),
        "reliability": _reading("reliability", [("days", "derived", reliability_days(RELIABILITY_RECORDS, RELIABILITY_STABILITY))]),
    }
    base.update(over)
    return base


def test_every_class_can_fire_and_each_signal_names_the_readings_it_drew_on():
    signals, basis = take_signals_sync(_inputs())
    classes = {s["class"] for s in signals}
    assert classes == {"suppressions", "gaps", "pressure", "transitions", "mismatches"}
    assert [s["class"] for s in signals] == sorted((s["class"] for s in signals), key=["suppressions", "gaps", "pressure", "transitions", "mismatches"].index)
    assert all(s["readings"] and s["id"] and s["title"] and s["summary"] and isinstance(s["evidence"], dict) for s in signals)
    assert "Observed: hardware, pcie, power, constraints, events, crash, reliability." in basis
    assert "Not observed" not in basis


def test_a_missing_input_is_named_in_the_basis_and_the_rest_still_answer():
    signals, basis = take_signals_sync(_inputs(hardware=None, power=_reading("power", [], outcome="unavailable")), {"hardware": "not registered"})
    assert "Not observed" in basis and "hardware (not registered)" in basis and "power (unavailable)" in basis
    gap = next(s for s in signals if s["id"] == "gap:inputs")
    assert gap["evidence"]["not_observed"] == {"hardware": "not registered", "power": "unavailable"}
    assert {s["class"] for s in signals} >= {"gaps", "pressure"}
    assert not any(s["id"] == "suppression:fast-startup" for s in signals)  # neither input said so


def test_the_disabled_device_is_a_suppression_and_the_one_that_will_not_start_is_a_gap():
    signals, _ = take_signals_sync(_inputs())
    assert any(s["id"] == "suppression:disabled:HDAUDIO\\A" for s in signals)
    assert any(s["id"] == "gap:not-working:USB\\B" for s in signals)


def test_an_endpoint_in_error_beside_healthy_ones_under_a_root_port_is_a_mismatch():
    signals, _ = take_signals_sync(_inputs())
    mismatch = next(s for s in signals if s["id"] == "mismatch:root-port:PCI\\R")
    assert mismatch["evidence"]["not_ok"] == ["Audio"] and mismatch["readings"] == ["pcie"]


def test_a_problem_code_also_counts_as_a_non_ok_root_port_member():
    pcie = _reading("pcie", [("groups", "derived", [{"kind": "root_port", "upstream": {"instance_id": "PCI\\R", "name": "Root Port"}, "members": [{"name": "GPU", "status": "OK", "problem": "CM_PROB_NONE"}, {"name": "Audio", "status": "OK", "problem": "CM_PROB_FAILED_START"}]}])])
    signals, _ = take_signals_sync(_inputs(pcie=pcie))
    mismatch = next(s for s in signals if s["id"] == "mismatch:root-port:PCI\\R")
    assert mismatch["evidence"]["not_ok"] == ["Audio"]


def test_root_complex_members_do_not_imply_a_shared_link():
    pcie = _reading("pcie", [("groups", "derived", [{"kind": "non_pci_parent", "upstream": {"instance_id": "ACPI\\ROOT", "name": "Root Complex"}, "members": [{"name": "GPU", "status": "OK"}, {"name": "Audio", "status": "Error"}]}])])
    signals, _ = take_signals_sync(_inputs(pcie=pcie))
    assert not any(s["id"].startswith("mismatch:root-port:") for s in signals)


def test_partial_pcie_relations_raise_a_gap_without_dropping_reported_links():
    groups, coverage = pcie_topology(PCI_DEVICES + [dict(PCI_DEVICES[1], InstanceId="PCI\\OTHER", Parent=None)], RELATIONS_OK)
    pcie = _reading("pcie", [("groups", "derived", groups), ("coverage", "derived", coverage)])
    signals, _ = take_signals_sync(_inputs(pcie=pcie))
    gap = next(s for s in signals if s["id"] == "gap:pcie-relations")
    assert gap["evidence"] == {"relations": "partial", "returned_devices": 5, "placed_devices": 4, "unplaced_devices": 1}
    assert any(s["id"].startswith("mismatch:root-port:") for s in signals)


def test_no_pcie_relations_raise_a_gap_and_no_shared_link_lead():
    pcie = _reading("pcie", [("groups", "derived", None), ("coverage", "derived", {"relations": "none", "returned_devices": 2, "placed_devices": 0, "unplaced": [{"instance_id": "PCI\\A"}, {"instance_id": "PCI\\B"}]})])
    signals, _ = take_signals_sync(_inputs(pcie=pcie))
    gap = next(s for s in signals if s["id"] == "gap:pcie-relations")
    assert gap["title"] == "PCI parent relationships were not observed"
    assert not any(s["id"].startswith("mismatch:root-port:") for s in signals)


def test_stops_that_share_a_bug_check_and_stops_that_wrote_none_are_each_one_signal():
    signals, _ = take_signals_sync(_inputs())
    shared = next(s for s in signals if s["id"] == "transition:repeated-stop:0x133")
    assert shared["title"] == "2 stops share bug check 0x133 (DPC_WATCHDOG_VIOLATION)"
    assert shared["evidence"]["started_at"] == ["2026-09-19T03:12:04.000Z", "2026-09-14T22:41:19.000Z"]
    assert shared["evidence"]["code"] == "0x133" and shared["readings"] == ["crash"]
    silent = next(s for s in signals if s["id"] == "transition:repeated-stop:no-bugcheck")
    assert silent["title"] == "2 stops wrote no bug check" and silent["evidence"]["code"] is None
    assert not any(s["id"] == "transition:repeated-stop:0x1a" for s in signals)  # one stop is a stop


def test_the_unexpected_shutdown_signal_counts_crash_stops_once():
    with_crash = next(s for s in take_signals_sync(_inputs())[0] if s["id"] == "transition:unexpected-shutdown")
    assert with_crash["readings"] == ["crash"]
    assert with_crash["title"] == "5 unplanned stops returned"
    assert with_crash["evidence"]["returned"] == 5
    assert with_crash["evidence"]["limit"] == 20
    assert with_crash["evidence"]["limit_reached"] is False
    assert with_crash["evidence"]["sources_complete"] is True
    assert with_crash["evidence"]["stops"] == [
        {"started_at": s["started_at"], "announced_at": None, "reported_at": None, "anchor_at": s["started_at"], "code": (s["bugcheck"] or {}).get("code"), "name": (s["bugcheck"] or {}).get("name")} for s in STOPS
    ]
    without, _ = take_signals_sync(_inputs(crash=None))
    assert not any(s["id"] == "transition:unexpected-shutdown" for s in without)
    assert any(s["id"] == "gap:inputs" for s in without)


def test_two_shutdown_records_and_one_crash_stop_are_one_lead():
    power = _reading("power", [("derived", "derived", {"ledger": {"counts": {"unexpected shutdown": 6, "unexpected shutdown, logged at the next start": 6}}})])
    crash = _reading("crash", [("stops", "derived", STOPS[:1]), ("collection", "raw", {"system": {"outcome": "ok", "bound_reached": False}, "reports": {"outcome": "ok", "bound_reached": False}})])
    lead = next(s for s in take_signals_sync(_inputs(power=power, crash=crash))[0] if s["id"] == "transition:unexpected-shutdown")
    assert lead["title"] == "1 unplanned stop returned"
    assert lead["evidence"]["returned"] == 1 and lead["readings"] == ["crash"]


def test_signals_handles_an_unknown_power_transition_ledger():
    power = _reading("power", [("derived", "derived", {"ledger": {"counts": None, "window": None}})])
    signals, _ = take_signals_sync(_inputs(power=power))
    assert not any(signal["id"] == "transition:display-reset-near-wake" for signal in signals)


def test_crash_stop_lead_survives_an_empty_power_ledger_but_marks_partial_crash_source():
    power = _reading("power", [("derived", "derived", {"ledger": {"counts": {}, "window": {}}})])
    crash = _reading("crash", [("stops", "derived", STOPS[:1]), ("collection", "raw", {"system": {"outcome": "failed", "bound_reached": None}, "reports": {"outcome": "ok", "bound_reached": False}})])
    crash.warnings = ["System stop records did not answer"]
    signals, basis = take_signals_sync(_inputs(power=power, crash=crash))
    lead = next(s for s in signals if s["id"] == "transition:unexpected-shutdown")
    assert lead["evidence"]["returned"] == 1 and lead["evidence"]["sources_complete"] is False and lead["evidence"]["limit_reached"] is False
    assert "Observed with warnings: crash" in basis


def test_stop_limit_is_a_returned_cap_even_when_both_sources_answer():
    crash = _reading("crash", [("stops", "derived", STOPS[:1] * 20), ("collection", "raw", {"system": {"outcome": "ok", "bound_reached": False}, "reports": {"outcome": "ok", "bound_reached": False}})])
    lead = next(s for s in take_signals_sync(_inputs(crash=crash))[0] if s["id"] == "transition:unexpected-shutdown")
    assert lead["evidence"]["returned"] == 20 and lead["evidence"]["sources_complete"] is True and lead["evidence"]["limit_reached"] is True


def test_a_report_only_or_power_only_stop_keeps_its_own_time_and_log_anchor():
    report_only = dict(STOPS[0], started_at=None, reported_at="2026-09-20T01:00:00Z")
    power_only = dict(STOPS[1], started_at=None, announced_at="2026-09-19T01:00:00Z")
    crash = _reading("crash", [("stops", "derived", [report_only, power_only]), ("collection", "raw", {"system": {"outcome": "failed", "bound_reached": None}, "reports": {"outcome": "ok", "bound_reached": False}})])
    lead = next(s for s in take_signals_sync(_inputs(crash=crash))[0] if s["id"] == "transition:unexpected-shutdown")
    assert [s["anchor_at"] for s in lead["evidence"]["stops"]] == [report_only["reported_at"], power_only["announced_at"]]
    assert lead["evidence"]["stops"][0]["reported_at"] == report_only["reported_at"]
    assert lead["evidence"]["stops"][1]["announced_at"] == power_only["announced_at"]


def test_missing_or_malformed_crash_collection_cannot_claim_complete_sources():
    for sections in ([ ("stops", "derived", STOPS[:1]) ], [("stops", "derived", STOPS[:1]), ("collection", "raw", {"system": {"outcome": "ok", "bound_reached": "no"}, "reports": {"outcome": "ok", "bound_reached": False}})]):
        crash = _reading("crash", sections)
        lead = next(s for s in take_signals_sync(_inputs(crash=crash))[0] if s["id"] == "transition:unexpected-shutdown")
        assert lead["evidence"]["sources_complete"] is False


def test_stop_limit_comes_from_the_crash_reading_params():
    crash = _reading("crash", [("stops", "derived", STOPS + STOPS[:2]), ("collection", "raw", {"system": {"outcome": "ok", "bound_reached": False}, "reports": {"outcome": "ok", "bound_reached": False}})])
    crash.params = {"count": 7}
    lead = next(s for s in take_signals_sync(_inputs(crash=crash))[0] if s["id"] == "transition:unexpected-shutdown")
    assert lead["evidence"]["returned"] == 7 and lead["evidence"]["limit"] == 7 and lead["evidence"]["limit_reached"] is True


def test_crash_answering_without_stops_does_not_create_a_stop_lead():
    crash = _reading("crash", [("stops", "derived", []), ("collection", "raw", {"system": {"outcome": "empty", "bound_reached": False}, "reports": {"outcome": "empty", "bound_reached": False}})], outcome="empty")
    signals, _ = take_signals_sync(_inputs(crash=crash))
    assert not any(s["id"] == "transition:unexpected-shutdown" for s in signals)


def test_the_index_fall_points_at_the_day_it_fell_not_the_lowest_day():
    fall = next(s for s in take_signals_sync(_inputs())[0] if s["id"] == "transition:reliability-index-fall")
    assert fall["evidence"]["day"] == "2026-09-18"  # the furthest fall, not the lowest index
    assert fall["evidence"]["fall"] == 1.7 and fall["evidence"]["index_before"] == 9.1
    assert fall["evidence"]["records"] == {"Microsoft-Windows-WindowsUpdateClient": 2}
    assert fall["evidence"]["event_types"] == [
        {"source": "Microsoft-Windows-WindowsUpdateClient", "event_id": 19, "count": 2}
    ]
    assert fall["readings"] == ["reliability"]


def test_an_index_that_only_drifts_fires_nothing():
    steady = [{"day": "2026-09-17", "index_last": 9.1, "index_min": 9.1, "records": {}}, {"day": "2026-09-18", "index_last": 8.9, "index_min": 8.9, "records": {}}]
    signals, _ = take_signals_sync(_inputs(reliability=_reading("reliability", [("days", "derived", {"days": steady})])))
    assert not any(s["id"] == "transition:reliability-index-fall" for s in signals)


def test_an_index_fall_across_a_missing_day_is_not_placed_on_the_next_returned_day():
    spaced = [
        {"day": "2026-09-17", "index_last": 9.1, "index_min": 9.1, "records": {}},
        {"day": "2026-09-19", "index_last": 4.0, "index_min": 4.0, "records": {}},
    ]
    signals, _ = take_signals_sync(_inputs(reliability=_reading("reliability", [("days", "derived", {"days": spaced})])))
    assert not any(s["id"] == "transition:reliability-index-fall" for s in signals)


def test_signals_is_empty_rather_than_ok_when_the_inputs_are_observed_and_quiet():
    quiet = {
        "hardware": _reading("hardware", [("fingerprint", "derived", {}), ("config", "raw", {"fast_startup": False})]),
        "pcie": _reading("pcie", [("groups", "derived", [])]),
        "power": _reading("power", [("derived", "derived", {"fast_startup": False, "uptime_seconds": 60, "ledger": {"counts": {}, "window": {}}})]),
        "constraints": _reading("constraints", [("derived", "derived", constraints_derived([]))]),
        "events": _reading("events", [("records", "raw", [])]),
    }
    signals, basis = take_signals_sync(quiet)
    assert signals == [] and "Not observed" not in basis


def test_signals_takes_every_input_and_carries_their_provenance():
    reading = asyncio.run(take("signals", payload_bridge(), {}))
    assert reading.method["kind"] == "readings"
    assert [r["name"] for r in reading.method["readings"]] == ["hardware", "pcie", "power", "constraints", "events", "crash", "reliability"]
    assert all("outcome" in r and "params" in r for r in reading.method["readings"])
    power_input = next(r for r in reading.method["readings"] if r["name"] == "power")
    assert power_input["warnings_total"] == 1
    assert power_input["warnings"] == ["powercfg /a produced no output: the supported sleep states were not observed."]
    assert any("power answered with 1 warning" in warning for warning in reading.warnings)
    assert "Observed with warnings: power" in reading.section("signals").basis
    assert [(s.name, s.cls) for s in reading.sections] == [("signals", "inferred")]
    assert reading.section("signals").basis
    assert reading.outcome in ("ok", "empty")


def test_signals_bounds_but_counts_input_warnings_for_the_agent():
    bridge = payload_bridge()
    warnings = ["first " + "word " * 80] + [f"warning {index}" for index in range(1, 7)]
    bridge.by_marker["powercfg"] = BridgeResult("ok", items=[dict(POWER_PAYLOAD, warnings=warnings)])
    reading = asyncio.run(take("signals", bridge, {}))
    power_input = next(r for r in reading.method["readings"] if r["name"] == "power")
    assert power_input["warnings_total"] == 7 and len(power_input["warnings"]) == 5
    assert len(power_input["warnings"][0]) <= 300 and power_input["warnings"][0].endswith("...")
    assert any("power answered with 7 warnings" in warning for warning in reading.warnings)


def test_warning_preview_does_not_cut_through_a_machine_name():
    bridge = payload_bridge()
    warning = "read " + "x " * 145 + "DESKTOP-SYNTHETICNAME at the end"
    bridge.by_marker["powercfg"] = BridgeResult("ok", items=[dict(POWER_PAYLOAD, warnings=[warning])])
    reading = asyncio.run(take("signals", bridge, {}))
    preview = next(r for r in reading.method["readings"] if r["name"] == "power")["warnings"][0]
    assert "DESKTOP-" not in preview and preview.endswith("...")


def test_signals_is_unavailable_and_says_nothing_when_no_input_observed():
    bridge = FakeBridge(BridgeResult("unavailable", error="powershell.exe was not found"))
    reading = asyncio.run(take("signals", bridge, {}))
    assert reading.outcome == "unavailable" and reading.count == 0
    assert reading.section("signals").data == []
    assert reading.error == {"kind": "unavailable", "detail": "no reading this one is made of observed the machine"}
    assert len(reading.warnings) == len(reading.method["readings"])


def test_an_input_that_is_not_registered_is_recorded_not_raised(monkeypatch):
    monkeypatch.delitem(REGISTRY, "pcie")
    reading = asyncio.run(take("signals", payload_bridge(), {}))
    entry = next(r for r in reading.method["readings"] if r["name"] == "pcie")
    assert entry["outcome"] == "not registered" and entry["took_ms"] is None
    assert any("pcie was not observed (not registered)" in w for w in reading.warnings)


# ---------------------------------------------------------------- the catalog


def test_the_six_readings_are_registered_with_what_they_carry():
    for name in ("pcie", "power", "memory", "constraints", "signals", "reliability"):
        assert name in REGISTRY and REGISTRY[name].description
    assert REGISTRY["signals"].classes == ("inferred",)
    assert REGISTRY["memory"].private == ("modules[].serial_number",)
    assert REGISTRY["reliability"].private[:2] == ("User", "ComputerName")
    assert [REGISTRY[n].heavy for n in ("pcie", "power", "memory", "signals", "reliability")] == [True] * 5
    assert REGISTRY["constraints"].heavy is False


def test_the_scripts_ask_for_what_the_readings_claim():
    assert "Get-PnpDevice -PresentOnly" in PCIE_SCRIPT and "/enum-devices /connected /relations /format xml" in PCIE_SCRIPT
    assert "Win32_PhysicalMemoryArray" in MEMORY_SCRIPT and "Microsoft-Windows-WHEA-Logger" not in MEMORY_SCRIPT
    assert "Microsoft-Windows-MemoryDiagnostics-Results" in MEMORY_SCRIPT and "-LogName System -Oldest -MaxEvents 1" in MEMORY_SCRIPT
    assert "Win32_ReliabilityStabilityMetrics" in RELIABILITY_SCRIPT_TEMPLATE and "Win32_ReliabilityRecords" in RELIABILITY_SCRIPT_TEMPLATE
    assert "CM_PROB_NONE" in CONSTRAINTS_SCRIPT
    assert "powercfg.exe /devicequery wake_armed" in power_script()


# ---------------------------------------------------------------- on this machine


@pytest.mark.host
@pytest.mark.parametrize("name", ["pcie", "power", "memory", "constraints", "signals", "reliability"])
def test_each_reading_observes_this_machine_well_inside_the_limit(name):
    bridge = real_bridge_or_skip()
    reading = asyncio.run(take(name, bridge, {}))
    assert reading.outcome in ("ok", "empty"), (reading.outcome, reading.error, reading.warnings)
    assert reading.took_ms < 30000, reading.took_ms
    assert reading.sections, "an observed reading has its sections"


@pytest.mark.host
def test_the_fabric_on_this_machine_is_whole():
    bridge = real_bridge_or_skip()
    reading = asyncio.run(take("pcie", bridge, {}))
    if reading.outcome != "ok":
        pytest.skip("the PCI bus was not observed")
    devices = reading.section("devices").data
    coverage = reading.section("coverage").data
    assert coverage["relations"] == "complete", reading.section("collection").data
    grouped = [m for g in reading.section("groups").data for m in g["members"]]
    parents = {str(d.get("Parent") or "").upper() for d in devices}
    leaves = [d for d in devices if str(d.get("InstanceId") or "").upper() not in parents]
    assert len(grouped) == len(leaves), "every observed PCI leaf belongs to exactly one group"
    assert coverage["placed_devices"] == len(devices)
    assert all(d.get("Parent") for d in devices), "every PCI device reported a parent"
    if not any(m["address"] for m in grouped):
        # A virtual machine's devices hang off a bus that reports no addresses (GitHub's Windows
        # runner, for one); the fabric was read and grouped whole, but this assertion is about hardware.
        pytest.skip("no endpoint reported a bus address: not a machine with a PCI fabric")


@pytest.mark.host
def test_the_transition_ledger_on_this_machine_names_every_record_it_returns():
    bridge = real_bridge_or_skip()
    reading = asyncio.run(take("power", bridge, {}))
    if reading.outcome != "ok":
        pytest.skip("power was not observed")
    counts = reading.section("derived").data["ledger"]["counts"]
    if counts is None:
        pytest.skip("transition records were not observed")
    assert "unnamed transition" not in counts, counts
