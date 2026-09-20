"""The deep diagnostics: the fabric is assembled correctly, a transition is named by its
provider as well as its id, and signals says what it could not see.

The unit tests hold the derivations to fixtures, because the shapes that matter on this
machine (a root port with two endpoints under it, a WHEA record in the ledger, a disabled
device) are not all present here: this machine holds no WHEA-Logger record at all, so the
non-empty ledger path is tested here and only the empty one is observed on the host.
"""

import asyncio

import pytest

from sentinel import readings  # noqa: F401  (registers the catalog)
from sentinel.bridge import BridgeResult
from sentinel.reading import REGISTRY, take
from sentinel.readings.diagnostics import (
    CONSTRAINTS_SCRIPT,
    MEMORY_SCRIPT,
    PCIE_SCRIPT,
    _aspm,
    constraints_derived,
    memory_derived,
    pcie_address,
    pcie_topology,
    power_derived,
    power_script,
    sleep_model,
    take_signals_sync,
    transition_kind,
    transitions_query,
)
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

POWER_PAYLOAD = {
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
    "array": {"MaxCapacity": 134217728, "MemoryDevices": 4, "MemoryErrorCorrection": 3},
    "ledger": [
        {"RecordId": 5, "Id": 17, "LevelDisplayName": "Warning", "ProviderName": "Microsoft-Windows-WHEA-Logger", "TimeCreated": "2026-09-01T00:00:00.000Z", "Kind": "whea"},
        {"RecordId": 4, "Id": 1001, "LevelDisplayName": "Error", "ProviderName": "Microsoft-Windows-WER-SystemErrorReporting", "TimeCreated": "2026-08-30T00:00:00.000Z", "Kind": "bugcheck"},
    ],
    "ledger_days": 30,
}

CONSTRAINT_DEVICES = [
    {"Name": "Realtek Audio", "InstanceId": "HDAUDIO\\A", "Class": "MEDIA", "Status": "Error", "Problem": "CM_PROB_DISABLED", "ProblemDescription": "This device is disabled. (Code 22)."},
    {"Name": "Intel Wireless Bluetooth", "InstanceId": "USB\\B", "Class": "Bluetooth", "Status": "Error", "Problem": "CM_PROB_FAILED_START", "ProblemDescription": "This device cannot start. (Code 10)."},
]


def payload_bridge() -> FakeBridge:
    """Each script is answered by its own fixture, matched on a phrase only that script has."""
    return FakeBridge(
        by_marker={
            "pnputil": BridgeResult("ok", items=[{"devices": PCI_DEVICES, "present_devices": 40, "warnings": []}], took_ms=11),
            "powercfg": BridgeResult("ok", items=[dict(POWER_PAYLOAD, warnings=["powercfg /a produced no output: the supported sleep states were not observed."])], took_ms=12),
            "Win32_PhysicalMemory": BridgeResult("ok", items=[dict(MEMORY_PAYLOAD)], took_ms=13),
            "CM_PROB_NONE": BridgeResult("ok", items=[{"devices": CONSTRAINT_DEVICES, "warnings": []}], took_ms=14),
        }
    )


# ---------------------------------------------------------------- pcie


def test_the_fabric_splits_into_bridges_and_endpoints():
    endpoints, roots, _ = pcie_topology(PCI_DEVICES)
    assert [r["Name"] for r in roots] == ["PCI Express Root Port"]
    assert sorted(e["Name"] for e in endpoints) == ["AMD SMBus", "High Definition Audio Controller", "NVIDIA GeForce RTX 3080"]


def test_endpoints_under_one_root_port_are_one_group_whatever_the_case():
    _, _, groups = pcie_topology(PCI_DEVICES)
    shared = groups[0]  # the largest group first
    assert shared["root_port"] == {"instance_id": ROOT_PORT, "name": "PCI Express Root Port"}
    assert sorted(m["name"] for m in shared["members"]) == ["High Definition Audio Controller", "NVIDIA GeForce RTX 3080"]
    assert all(m["upstream"] == [ROOT_PORT] for m in shared["members"])


def test_a_device_on_the_root_complex_is_grouped_under_its_own_parent():
    _, _, groups = pcie_topology(PCI_DEVICES)
    complex_group = next(g for g in groups if g["root_port"]["instance_id"] == "ACPI\\PNP0A08\\0")
    assert [m["name"] for m in complex_group["members"]] == ["AMD SMBus"]
    assert complex_group["root_port"]["name"] == "PCI Express Root Complex"
    assert complex_group["members"][0]["upstream"] == []


def test_a_chain_that_points_at_itself_does_not_loop():
    looped = [{"Name": "A", "InstanceId": "PCI\\A", "Parent": "PCI\\B"}, {"Name": "B", "InstanceId": "PCI\\B", "Parent": "PCI\\A"}]
    endpoints, roots, groups = pcie_topology(looped)
    assert endpoints == [] and len(roots) == 2 and groups == []


def test_the_bus_address_is_read_from_the_enumerators_location_string():
    assert pcie_address("@System32\\drivers\\pci.sys,#65536;PCI bus %1, device %2, function %3;(1,0,1)") == {"bus": 1, "device": 0, "function": 1, "address": "01:00.1"}
    assert pcie_address(None) is None
    assert pcie_address("PCI bus 1, device 0, function 0") is None


def test_pcie_returns_three_sections_and_counts_its_endpoints():
    reading = asyncio.run(take("pcie", payload_bridge(), {}))
    assert reading.outcome == "ok" and reading.count == 3
    assert [(s.name, s.cls) for s in reading.sections] == [("endpoints", "raw"), ("roots", "raw"), ("groups", "derived")]
    assert reading.section("groups").basis
    assert "pnputil" in reading.method["query"] and reading.method["kind"] == "powershell"


def test_pcie_says_unavailable_rather_than_empty_when_the_bridge_did_not_answer():
    reading = asyncio.run(take("pcie", FakeBridge(BridgeResult("unavailable", error="powershell.exe was not found")), {}))
    assert reading.outcome == "unavailable" and reading.sections == []
    assert reading.error == {"kind": "unavailable", "detail": "powershell.exe was not found"}


def test_a_machine_with_no_pci_device_is_empty_not_ok():
    bridge = FakeBridge(BridgeResult("ok", items=[{"devices": [], "present_devices": 3, "warnings": []}]))
    reading = asyncio.run(take("pcie", bridge, {}))
    assert reading.outcome == "empty" and reading.count == 0


# ---------------------------------------------------------------- power


def test_a_transition_is_named_by_its_provider_as_well_as_its_id():
    assert transition_kind({"ProviderName": "Microsoft-Windows-Power-Troubleshooter", "Id": 1}) == "wake"
    assert transition_kind({"ProviderName": "Microsoft-Windows-Kernel-General", "Id": 1}) == "unnamed transition"
    assert transition_kind({"ProviderName": "Microsoft-Windows-Kernel-Power", "Id": 41}) == "unexpected shutdown"


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
    assert derived["power_source"] == "mains (no battery is present)"
    assert derived["fast_startup"] is True
    assert derived["wake_armed"] == [] and derived["wake_armed_count"] == 0  # powercfg prints NONE for nothing armed
    assert derived["ledger"]["counts"] == {"display driver reset": 1, "unexpected shutdown": 1, "unnamed transition": 1, "wake": 1}
    assert derived["ledger"]["window"] == {"first": "2026-09-16T10:00:00.000Z", "last": "2026-09-19T10:00:00.000Z"}
    assert derived["uptime_seconds"] > 0


def test_power_lifts_a_sub_query_failure_into_the_envelope():
    reading = asyncio.run(take("power", payload_bridge(), {}))
    assert reading.outcome == "ok"
    assert [(s.name, s.cls) for s in reading.sections] == [("raw", "raw"), ("derived", "derived")]
    assert any("powercfg /a" in w for w in reading.warnings)
    assert "warnings" not in reading.section("raw").data  # a warning is not a finding


# ---------------------------------------------------------------- memory


def test_memory_reads_the_slots_the_kit_and_the_ledger():
    derived = memory_derived(MEMORY_PAYLOAD)
    assert derived["installed_gb"] == 16.0
    assert (derived["slots_used"], derived["slots_total"], derived["slots_free"]) == (2, 4, 2)
    assert derived["modules"][0]["locator"] == "P0 CHANNEL A - DIMM 1"
    assert derived["modules"][0]["error_correction"] is False and derived["modules"][1]["error_correction"] is True
    assert derived["mixed_kit"] is True and derived["kits"] == ["Corsair CMK16", "Kingston KF432"]
    assert derived["below_rated_speed"] == ["DIMM 1"]
    assert derived["ledger"] == {"window_days": 30, "records": 2, "counts": {"bugcheck": 1, "whea": 1}, "most_recent": "2026-09-01T00:00:00.000Z"}


def test_an_empty_ledger_is_a_finding_not_an_absence():
    derived = memory_derived({"modules": MEMORY_PAYLOAD["modules"], "array": MEMORY_PAYLOAD["array"], "ledger": [], "ledger_days": 30})
    assert derived["ledger"]["records"] == 0 and derived["ledger"]["most_recent"] is None


def test_memory_returns_raw_and_derived_and_counts_its_modules():
    reading = asyncio.run(take("memory", payload_bridge(), {}))
    assert reading.outcome == "ok" and reading.count == 2
    assert [(s.name, s.cls) for s in reading.sections] == [("raw", "raw"), ("derived", "derived")]
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


def test_constraints_returns_raw_and_derived():
    reading = asyncio.run(take("constraints", payload_bridge(), {}))
    assert reading.outcome == "ok" and reading.count == 2
    assert [(s.name, s.cls) for s in reading.sections] == [("raw", "raw"), ("derived", "derived")]


# ---------------------------------------------------------------- signals


def _reading(name: str, sections: list[tuple[str, str, object]], outcome: str = "ok"):
    from sentinel.reading import Reading, Section

    return Reading(reading=name, params={}, outcome=outcome, method={"kind": "powershell", "query": "q"}, sections=[Section(n, c, d) for n, c, d in sections])


def _inputs(**over):
    base = {
        "hardware": _reading("hardware", [("fingerprint", "invariant", {"gpu": {"date": "2018-01-01", "driver_version": "1.0"}}), ("config", "raw", {"fast_startup": False})]),
        "pcie": _reading("pcie", [("groups", "derived", [{"root_port": {"instance_id": "PCI\\R", "name": "Root Port"}, "members": [{"name": "GPU", "status": "OK"}, {"name": "Audio", "status": "Error"}]}])]),
        "power": _reading("power", [("derived", "derived", {"fast_startup": True, "uptime_seconds": 30 * 86400, "link_power_management": {"ac": {"index": "0x2", "setting": "L1"}}, "ledger": {"counts": {"unexpected shutdown": 2, "wake": 1, "display driver reset": 1}, "window": {"first": "a", "last": "b"}}})]),
        "constraints": _reading("constraints", [("derived", "derived", constraints_derived(CONSTRAINT_DEVICES))]),
        "events": _reading("events", [("records", "raw", [{"ProviderName": "Service Control Manager", "TimeCreated": f"2026-09-0{i % 9 + 1}T00:00:00Z"} for i in range(30)] + [{"ProviderName": "Quiet", "TimeCreated": "2026-09-01T00:00:00Z"}])]),
    }
    base.update(over)
    return base


def test_every_class_can_fire_and_each_signal_names_the_readings_it_drew_on():
    signals, basis = take_signals_sync(_inputs())
    classes = {s["class"] for s in signals}
    assert classes == {"suppressions", "gaps", "pressure", "transitions", "mismatches"}
    assert [s["class"] for s in signals] == sorted((s["class"] for s in signals), key=["suppressions", "gaps", "pressure", "transitions", "mismatches"].index)
    assert all(s["readings"] and s["id"] and s["title"] and s["summary"] and isinstance(s["evidence"], dict) for s in signals)
    assert "Observed: hardware, pcie, power, constraints, events." in basis
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


def test_signals_is_empty_rather_than_ok_when_the_inputs_are_observed_and_quiet():
    quiet = {
        "hardware": _reading("hardware", [("fingerprint", "invariant", {}), ("config", "raw", {"fast_startup": False})]),
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
    assert [r["name"] for r in reading.method["readings"]] == ["hardware", "pcie", "power", "constraints", "events"]
    assert all("outcome" in r and "params" in r for r in reading.method["readings"])
    assert [(s.name, s.cls) for s in reading.sections] == [("signals", "inferred")]
    assert reading.section("signals").basis
    assert reading.outcome in ("ok", "empty")


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


def test_the_five_readings_are_registered_with_what_they_carry():
    for name in ("pcie", "power", "memory", "constraints", "signals"):
        assert name in REGISTRY and REGISTRY[name].description
    assert REGISTRY["signals"].classes == ("inferred",)
    assert REGISTRY["memory"].private == ("modules[].serial_number",)
    assert [REGISTRY[n].heavy for n in ("pcie", "power", "memory", "signals")] == [True] * 4
    assert REGISTRY["constraints"].heavy is False


def test_the_scripts_ask_for_what_the_readings_claim():
    assert "Get-PnpDevice -PresentOnly" in PCIE_SCRIPT and "/enum-devices /connected /relations /format xml" in PCIE_SCRIPT
    assert "Win32_PhysicalMemoryArray" in MEMORY_SCRIPT and "Microsoft-Windows-WHEA-Logger" in MEMORY_SCRIPT
    assert "CM_PROB_NONE" in CONSTRAINTS_SCRIPT
    assert "powercfg.exe /devicequery wake_armed" in power_script()


# ---------------------------------------------------------------- on this machine


@pytest.mark.host
@pytest.mark.parametrize("name", ["pcie", "power", "memory", "constraints", "signals"])
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
    endpoints = reading.section("endpoints").data
    roots = reading.section("roots").data
    grouped = [m for g in reading.section("groups").data for m in g["members"]]
    assert len(grouped) == len(endpoints), "every endpoint belongs to exactly one group"
    assert all(e.get("Parent") for e in endpoints + roots), "every PCI device reported a parent"
    assert any(m["address"] for m in grouped), "at least one endpoint reported its bus address"


@pytest.mark.host
def test_the_transition_ledger_on_this_machine_names_every_record_it_returns():
    bridge = real_bridge_or_skip()
    reading = asyncio.run(take("power", bridge, {}))
    if reading.outcome != "ok":
        pytest.skip("power was not observed")
    counts = reading.section("derived").data["ledger"]["counts"]
    assert "unnamed transition" not in counts, counts
