"""The machine readings: the shape of each envelope, the rules the derived and
inferred sections are made of, and what this machine returns today.

The unit tests answer with a fixed payload through the fake bridge, so the rules
are tested where they live (in Python) rather than through PowerShell. The host
tests are marked ``host`` and establish only what this machine returned: the
outcome is one the machine can answer with and the fields a reader depends on
are there.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import PureWindowsPath
from typing import Any

import pytest

from sentinel import readings  # noqa: F401  (registers the catalog)
from sentinel.bridge import BridgeResult
from sentinel.reading import REGISTRY, take
from sentinel.readings import system as system_readings
from tests.conftest import FakeBridge, real_bridge_or_skip
from tests.test_dump_inventory import dump_inventory

HARDWARE_DOMAINS = ("hardware.cpu", "hardware.gpu", "hardware.board", "hardware.storage", "hardware.network")


def answer(payload: Any, outcome: str = "ok") -> FakeBridge:
    return FakeBridge(BridgeResult(outcome, items=[payload] if outcome == "ok" else [], took_ms=11))


def taken(name: str, bridge: FakeBridge, params: dict[str, Any] | None = None):
    return asyncio.run(take(name, bridge, params or {}))


def classes(reading) -> dict[str, str]:
    return {s.name: s.cls for s in reading.sections}


def observed(reading):
    """The reading, once the machine has answered.

    ``unavailable`` is this environment and not a finding: from WSL the interop layer
    intermittently refuses to start powershell.exe, so that outcome skips. ``failed``,
    ``denied`` and ``timeout`` are about the machine or the query and still fail.
    """
    if reading.outcome == "unavailable":
        pytest.skip(f"the bridge did not start: {(reading.error or {}).get('detail', '')}")
    assert reading.outcome in ("ok", "empty"), reading.error
    return reading


def days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# system
# ---------------------------------------------------------------------------

SNAPSHOT = {
    "os_caption": "Microsoft Windows 11 Pro",
    "os_version": "10.0.26200",
    "os_build": "26200",
    "architecture": "64-bit",
    "boot_time": "2026-09-18T06:11:02.0000000Z",
    "uptime_seconds": 190_000,
    "processor_load_percent": 7,
    "memory_total_kb": 16_707_072,
    "memory_free_kb": 5_112_320,
}


def test_system_is_one_raw_snapshot():
    r = taken("system", answer(SNAPSHOT))
    assert r.outcome == "ok"
    assert classes(r) == {"snapshot": "raw"}
    data = r.section("snapshot").data
    assert data["boot_time"].endswith("Z")
    assert {"os_caption", "uptime_seconds", "processor_load_percent", "memory_total_kb", "memory_free_kb"} <= set(data)
    assert r.method["query"] == system_readings.SYSTEM_SCRIPT.strip()


# ---------------------------------------------------------------------------
# hardware
# ---------------------------------------------------------------------------

HARDWARE = {
    "fingerprint": {
        "cpu": {"name": "AMD Ryzen 7 5800X 8-Core Processor", "cores": 8, "logical": 16, "manufacturer": "AuthenticAMD", "description": "AMD64 Family"},
        "gpu": {"name": "NVIDIA GeForce RTX 3080", "driver_version": "580.97", "vram_mb": 4095, "date": days_ago(40)},
        "board": {"product": "B550 Taichi", "manufacturer": "ASRock", "version": "", "bios_version": "P3.90", "bios_date": "2024-11-04"},
        "storage": {"disk0_model": "Samsung SSD 990 PRO 2TB", "size_gb": 1863, "media_type": "Fixed hard disk media", "interface": "SCSI"},
    },
    "config": {
        "secure_boot": False,
        "fast_startup": True,
        "virtualization_firmware": True,
        "hyperv_running": True,
        "os_version": "10.0.26200 (26200)",
        "uptime_seconds": 700_000,
        "system_drive_free_percent": 6.4,
    },
    "warnings": [],
}


def test_hardware_keeps_the_fingerprint_the_configuration_and_the_observations_apart():
    r = taken("hardware", answer(HARDWARE))
    assert classes(r) == {"fingerprint": "derived", "config": "raw", "risks": "inferred"}
    assert r.section("risks").basis
    assert "disk index 0" in r.section("fingerprint").basis
    assert set(r.section("fingerprint").data) == {"cpu", "gpu", "board", "storage"}
    assert "serial" not in str(r.section("fingerprint").data).lower()


def test_hardware_risks_are_observations_and_never_advice():
    risks = system_readings.hardware_risks(HARDWARE["config"])
    ids = [x["id"] for x in risks]
    assert ids == ["secure-boot-off", "fast-startup-on", "system-drive-low", "uptime-long"]
    text = " ".join(x["observation"] for x in risks).lower()
    assert "recommend" not in text and "should" not in text
    assert all(set(x) == {"id", "observation", "domain"} for x in risks)


def test_hardware_risks_need_the_configuration_to_support_them():
    quiet = {"secure_boot": True, "fast_startup": False, "uptime_seconds": 400, "system_drive_free_percent": 51.0}
    assert system_readings.hardware_risks(quiet) == []
    # A value the machine did not report is not an observation either way.
    assert system_readings.hardware_risks({"secure_boot": None, "fast_startup": None, "uptime_seconds": None, "system_drive_free_percent": None}) == []


# ---------------------------------------------------------------------------
# hardware.cpu
# ---------------------------------------------------------------------------

CPU = {
    "processors": [
        {
            "name": "AMD Ryzen 7 5800X 8-Core Processor",
            "manufacturer": "AuthenticAMD",
            "processor_id": "0000000000000000",
            "revision": 8192,
            "stepping": None,
            "max_clock_mhz": 3801,
            "cores": 8,
            "logical_processors": 16,
            "l2_cache_kb": 4096,
            "l3_cache_kb": 32768,
            "virtualization_firmware_enabled": True,
            "load_percent": 5,
        }
    ],
    "device_guard": {"vbs_status": 2, "services_configured": [1], "services_running": [1]},
    "hyperv_service": {"name": "vmms", "status": "Running", "start_type": "Automatic"},
    "caches": [{"device_id": "Cache Memory 0", "level": 3, "max_cache_size_kb": 512, "installed_size_kb": 512, "cache_type": 5, "status": "OK"}],
    "warnings": [],
}


def test_hardware_cpu_separates_what_windows_said_from_what_follows():
    r = taken("hardware.cpu", answer(CPU))
    assert classes(r) == {"raw": "raw", "derived": "derived"}
    assert r.section("derived").basis
    raw = r.section("raw").data
    assert raw["processors"][0]["cores"] == 8 and raw["caches"]
    derived = r.section("derived").data
    assert derived["virtualization"] == {"vbs_available": True, "vbs_running": True, "hyperv_running": True}
    assert derived["processors"][0]["threads_per_core"] == 2.0


def test_hardware_cpu_unobserved_virtualization_is_unknown_not_off():
    payload = dict(CPU, device_guard=None, hyperv_service=None)
    derived = system_readings.cpu_derived(payload)
    assert derived["virtualization"] == {"vbs_available": None, "vbs_running": None, "hyperv_running": None}


def test_a_failed_sub_query_is_lifted_into_the_envelope_as_a_warning():
    payload = dict(CPU, device_guard=None, warnings=["Win32_DeviceGuard did not answer: virtualization-based security was not observed."])
    r = taken("hardware.cpu", answer(payload))
    assert r.warnings and "DeviceGuard" in r.warnings[0]
    assert "warnings" not in r.section("raw").data  # it belongs to the envelope, not to the evidence


# ---------------------------------------------------------------------------
# hardware.gpu
# ---------------------------------------------------------------------------

GPU = {
    "adapters": [
        {
            "name": "NVIDIA GeForce RTX 3080",
            "video_processor": "GeForce RTX 3080",
            "adapter_ram_bytes": 4_293_918_720,
            "video_mode": "3440 x 1440 x 4294967296 colors",
            "driver_version": "32.0.15.8097",
            "driver_date": days_ago(40),
            "inf_file": "oem123.inf",
            "installed_display_drivers": "C:\\Windows\\System32\\DriverStore\\nvldumdx.dll",
            "status": "OK",
            "config_manager_error_code": 0,
        }
    ],
    "tdr_registry": {"TdrLevel": None, "TdrDelay": None, "TdrDdiDelay": None},
    "warnings": [],
}


def test_hardware_gpu_reports_the_windows_default_when_the_registry_is_silent():
    r = taken("hardware.gpu", answer(GPU))
    derived = r.section("derived").data
    assert derived["tdr"] == {"level": 3, "delay_seconds": 2, "ddi_delay_seconds": 5, "at_defaults": True}
    assert derived["adapters"][0]["vram_mb"] == 4095
    assert derived["adapters"][0]["driver_age_days"] == 40
    assert "TdrLevel" in r.section("raw").data["tdr_registry"]  # the value the registry held is still there


def test_hardware_gpu_reports_a_changed_timeout_as_set():
    payload = dict(GPU, tdr_registry={"TdrLevel": 0, "TdrDelay": 10, "TdrDdiDelay": None})
    derived = system_readings.gpu_derived(payload)
    assert derived["tdr"] == {"level": 0, "delay_seconds": 10, "ddi_delay_seconds": 5, "at_defaults": False}


# ---------------------------------------------------------------------------
# hardware.board
# ---------------------------------------------------------------------------

BOARD = {
    "board": {"manufacturer": "ASRock", "product": "B550 Taichi", "version": "", "serial": "M80-XXXXXXXXXX", "hosting_board": True},
    "bios": {"manufacturer": "American Megatrends", "smbios_version": "P3.90", "version": "ALASKA - 1072009", "release_date": "2024-11-04", "caption": "P3.90"},
    "tpm": {"present": True, "ready": True, "enabled": True, "activated": True, "owned": True, "managed_auth_level": "Full", "spec_version": "2.0, 0, 1.38"},
    "firmware": {"firmware_type": "UEFI", "uefi_secure_boot_enabled": 0, "dma_security_key_present": True},
    "warnings": [],
}


def test_hardware_board_derives_secure_boot_and_the_tpm_specification():
    r = taken("hardware.board", answer(BOARD))
    derived = r.section("derived").data
    assert derived["secure_boot_enabled"] is False
    assert derived["tpm_spec_major"] == "2.0"
    assert derived["bios_age_days"] > 0
    assert r.section("raw").data["board"]["serial"]  # carried; the boundary's redaction removes it


def test_hardware_board_absent_secure_boot_value_is_unknown_not_disabled():
    payload = {**BOARD, "firmware": {"firmware_type": "Legacy", "uefi_secure_boot_enabled": None, "dma_security_key_present": False}}
    derived = system_readings.board_derived(payload)
    assert derived["secure_boot_enabled"] is None


def test_the_dma_security_key_is_reported_as_it_stands():
    """Its presence is not kernel DMA protection being on; the old reader concluded that, this one does not."""
    assert "dma" not in str(system_readings.board_derived(BOARD)).lower()
    assert BOARD["firmware"]["dma_security_key_present"] is True


# ---------------------------------------------------------------------------
# hardware.storage
# ---------------------------------------------------------------------------

STORAGE = {
    "disks": [
        {
            "device_id": "0",
            "friendly_name": "Samsung SSD 990 PRO 2TB",
            "serial_number": "S0000000000000X",
            "firmware_version": "4B2QJXD7",
            "bus_type": "NVMe",
            "media_type": "SSD",
            "health_status": "Healthy",
            "operational_status": "OK",
            "size_bytes": 2_000_398_934_016,
            "reliability": {"wear_percent": 3, "temperature_c": 41, "power_on_hours": 9_000, "read_errors_total": 0, "write_errors_total": 0},
            "volumes": [{"drive_letter": "C", "label": "Windows", "file_system": "NTFS", "size_bytes": 1_000_000_000_000, "size_remaining_bytes": 250_000_000_000}],
        },
        {
            "device_id": "1",
            "friendly_name": "Spare",
            "serial_number": None,
            "bus_type": "SATA",
            "media_type": "HDD",
            "health_status": "Healthy",
            "operational_status": "OK",
            "size_bytes": 500_107_862_016,
            "reliability": {"temperature_c": 60000},
            "volumes": [],
        },
    ],
    "warnings": ["Reliability counters were not available for disk 1."],
}


def test_hardware_storage_derives_capacity_and_keeps_an_impossible_temperature_out():
    r = taken("hardware.storage", answer(STORAGE))
    disks = r.section("derived").data["disks"]
    assert disks[0]["size_gb"] == 1863
    assert disks[0]["temperature_c"] == 41
    assert disks[0]["volumes"][0] == {"drive_letter": "C", "free_gb": 232.8, "used_percent": 75.0}
    assert disks[1]["temperature_c"] is None  # a drive that reports no temperature returns a value outside the range
    assert r.warnings == ["Reliability counters were not available for disk 1."]
    assert r.section("raw").data["disks"][0]["serial_number"]


# ---------------------------------------------------------------------------
# hardware.network
# ---------------------------------------------------------------------------

NETWORK = {
    "adapters": [
        {
            "name": "Ethernet",
            "interface_description": "Intel Ethernet Controller I225-V",
            "interface_index": 11,
            "mac_address": "AA-BB-CC-DD-EE-FF",
            "status": "Up",
            "admin_status": "Up",
            "link_speed": "2.5 Gbps",
            "media_type": "802.3",
            "hardware_interface": True,
            "virtual": False,
            "pnp_device_id": "PCI\\VEN_10EC&DEV_8125",
            "config_manager_error_code": 0,
            "driver": {"provider": "Intel", "version": "2.1.5.7", "date": days_ago(300), "file": "e2fn.sys", "description": "Intel Ethernet Controller"},
            "ip": {"ipv4": ["192.168.1.20"], "ipv6": [], "gateway": ["192.168.1.1"], "dns": ["192.168.1.1"], "dhcp": "Enabled"},
        },
        {
            "name": "Wi-Fi",
            "interface_description": "Intel Wi-Fi 6",
            "interface_index": 14,
            "mac_address": "11-22-33-44-55-66",
            "status": "Disabled",
            "admin_status": "Down",
            "link_speed": "0 bps",
            "media_type": "Native 802.11",
            "hardware_interface": True,
            "virtual": False,
            "pnp_device_id": "PCI\\VEN_8086&DEV_2723",
            "config_manager_error_code": 22,
            "driver": {"provider": "Intel", "version": "23.0", "date": None, "file": "Netwtw08.sys", "description": "Intel Wi-Fi 6"},
            "ip": {"ipv4": [], "ipv6": [], "gateway": [], "dns": [], "dhcp": "Enabled"},
        },
    ],
    "warnings": [],
}


def test_hardware_network_tells_up_from_disabled():
    r = taken("hardware.network", answer(NETWORK))
    adapters = r.section("derived").data["adapters"]
    assert adapters[0] == {"name": "Ethernet", "up": True, "disabled": False, "driver_age_days": 300}
    assert adapters[1] == {"name": "Wi-Fi", "up": False, "disabled": True, "driver_age_days": None}
    assert r.section("raw").data["adapters"][0]["mac_address"]  # carried; the boundary's redaction removes it


@pytest.mark.host
def test_network_keeps_adapter_inventory_when_windows_cannot_assemble_ip_configuration(monkeypatch):
    # Get-NetIPConfiguration can throw while joining multiple adapters on a Windows host.
    # The adapter list remains evidence; the missing IP details must be unknown, not empty.
    monkeypatch.setattr(
        system_readings,
        "NETWORK_SCRIPT",
        "function Get-NetIPConfiguration { [CmdletBinding()] param([switch]$All) throw 'test lookup failure' }\n"
        + system_readings.NETWORK_SCRIPT,
    )
    r = observed(asyncio.run(take("hardware.network", real_bridge_or_skip(), {})))
    assert any("IP addresses, gateways and DNS were not observed" in warning for warning in r.warnings)
    assert all(adapter["ip"] is None for adapter in r.section("raw").data["adapters"])


# ---------------------------------------------------------------------------
# drivers, dumps
# ---------------------------------------------------------------------------

DRIVERS = [
    {"device_name": "NVIDIA GeForce RTX 3080", "device_class": "DISPLAY", "driver_version": "32.0.15.8097", "driver_provider": "NVIDIA", "driver_date": days_ago(40), "inf_name": "oem123.inf"},
    {"device_name": "Realtek Gaming 2.5GbE", "device_class": "NET", "driver_version": "10.62.1201.2024", "driver_provider": "Realtek", "driver_date": days_ago(300), "inf_name": "oem45.inf"},
]


def test_drivers_are_raw_records_and_the_count_reaches_the_query():
    bridge = FakeBridge(BridgeResult("ok", items=DRIVERS, took_ms=4000))
    r = taken("drivers", bridge, {"count": 5})
    assert classes(r) == {"drivers": "raw"}
    assert r.count == 2 and r.params["count"] == 5
    assert "-First 5" in bridge.scripts[0]
    assert {"device_name", "driver_version", "driver_provider", "driver_date", "inf_name"} <= set(r.section("drivers").data[0])


DUMP_FILES = [
    {"name": "092026-11234-01.dmp", "path": "C:\\Windows\\Minidump\\092026-11234-01.dmp", "bytes": 1_638_400, "modified": "2026-09-19T21:04:11.0000000Z"},
]


def test_dumps_lists_the_files_windows_wrote():
    r = taken("dumps", FakeBridge(BridgeResult("ok", items=[dump_inventory(DUMP_FILES, application=True)], took_ms=300)))
    assert classes(r) == {"files": "raw", "collection": "raw"}
    row = r.section("files").data[0]
    assert set(row) == {"name", "path", "bytes", "modified", "source"}
    assert row["path"].startswith("C:\\Windows")  # kept: a path under Windows is not a person's path


def test_an_empty_inventory_is_a_finding_not_a_failure():
    r = taken("dumps", FakeBridge(BridgeResult("ok", items=[dump_inventory(application=True)], took_ms=120)))
    assert r.outcome == "empty" and r.observed
    assert r.section("files").data == [] and r.count == 0
    assert r.error is None


def test_a_query_that_did_not_run_carries_no_sections():
    for name in ("hardware", *HARDWARE_DOMAINS):
        r = taken(name, FakeBridge(BridgeResult("unavailable", error="powershell.exe was not found")))
        assert r.sections == [] and not r.observed
        assert r.error == {"kind": "unavailable", "detail": "powershell.exe was not found"}


# ---------------------------------------------------------------------------
# the catalog
# ---------------------------------------------------------------------------


def test_the_catalog_carries_every_machine_reading_as_designed():
    assert {"system", "hardware", "drivers", "dumps", *HARDWARE_DOMAINS} <= set(REGISTRY)
    assert all(REGISTRY[name].heavy for name in HARDWARE_DOMAINS)
    assert not REGISTRY["system"].heavy and not REGISTRY["dumps"].heavy
    assert REGISTRY["hardware"].classes == ("derived", "raw", "inferred")
    assert REGISTRY["drivers"].params[0].name == "count" and REGISTRY["drivers"].params[0].default == 30
    assert REGISTRY["hardware.board"].private == ("board.serial",)
    assert REGISTRY["hardware.storage"].private == ("disks[].serial_number",)
    assert REGISTRY["hardware.network"].private == ("adapters[].mac_address",)


def test_every_machine_reading_publishes_the_query_it_ran():
    for name in ("system", "hardware", "drivers", "dumps", *HARDWARE_DOMAINS):
        bridge = FakeBridge(BridgeResult("ok", items=[{}]))
        r = taken(name, bridge)
        assert r.method["kind"] == "powershell"
        assert r.method["query"] == bridge.scripts[0].strip()


# ---------------------------------------------------------------------------
# On this machine
# ---------------------------------------------------------------------------


@pytest.mark.host
def test_system_snapshot_on_the_host():
    r = observed(asyncio.run(take("system", real_bridge_or_skip(), {})))
    data = r.section("snapshot").data
    assert data["os_caption"] and data["boot_time"].endswith("Z")
    assert data["uptime_seconds"] > 0 and data["memory_total_kb"] > 0


@pytest.mark.host
def test_hardware_on_the_host():
    r = observed(asyncio.run(take("hardware", real_bridge_or_skip(), {})))
    assert classes(r) == {"fingerprint": "derived", "config": "raw", "risks": "inferred"}
    assert r.section("fingerprint").data["cpu"]["name"]
    assert r.section("fingerprint").data["board"]["manufacturer"]
    config = r.section("config").data
    assert config["os_version"] and config["uptime_seconds"] > 0
    for risk in r.section("risks").data:
        assert set(risk) == {"id", "observation", "domain"}


@pytest.mark.host
@pytest.mark.parametrize("name", HARDWARE_DOMAINS)
def test_the_hardware_domains_on_the_host(name: str):
    r = observed(asyncio.run(take(name, real_bridge_or_skip(), {})))
    assert classes(r) == {"raw": "raw", "derived": "derived"}
    assert r.section("derived").basis


@pytest.mark.host
def test_the_hardware_domains_answer_with_this_machines_parts():
    bridge = real_bridge_or_skip()
    cpu = observed(asyncio.run(take("hardware.cpu", bridge, {})))
    assert cpu.section("raw").data["processors"][0]["cores"] >= 1
    gpu = observed(asyncio.run(take("hardware.gpu", bridge, {})))
    assert gpu.section("raw").data["adapters"][0]["name"]
    board = observed(asyncio.run(take("hardware.board", bridge, {})))
    assert board.section("raw").data["board"]["manufacturer"]
    storage = observed(asyncio.run(take("hardware.storage", bridge, {})))
    assert storage.section("raw").data["disks"][0]["size_bytes"] > 0
    network = observed(asyncio.run(take("hardware.network", bridge, {})))
    assert network.section("raw").data["adapters"][0]["interface_description"]


@pytest.mark.host
def test_drivers_on_the_host():
    r = observed(asyncio.run(take("drivers", real_bridge_or_skip(), {"count": 5})))
    if r.outcome == "ok":
        assert r.count <= 5
        row = r.section("drivers").data[0]
        assert {"device_name", "driver_version", "driver_date", "inf_name"} <= set(row)
        assert len(row["driver_date"]) == 10


@pytest.mark.host
def test_dumps_on_the_host():
    r = observed(asyncio.run(take("dumps", real_bridge_or_skip(), {})))
    roots = {source["id"]: PureWindowsPath(source["path"]) for source in r.section("collection").data["locations"] if source["path"]}
    for row in r.section("files").data:
        assert set(row) == {"name", "path", "bytes", "modified", "source"}
        assert row["source"] in {"minidump", "memory", "live_kernel", "application"}
        assert PureWindowsPath(row["path"]).is_relative_to(roots[row["source"]])
        assert row["modified"].endswith("Z") and row["bytes"] >= 0
