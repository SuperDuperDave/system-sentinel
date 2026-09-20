"""The redaction policy: serials, the machine's names, MAC addresses and profile paths go; evidence stays."""

from sentinel.redact import Identity, redact


def test_serial_fields_by_name():
    out, removed = redact({"BIOS": {"SerialNumber": "ABC123", "Version": "P3.90"}, "board": {"serial_number": "X"}})
    assert out["BIOS"]["SerialNumber"] == "<serial>"
    assert out["BIOS"]["Version"] == "P3.90"
    assert out["board"]["serial_number"] == "<serial>"
    assert removed == ["serial"]


def test_mac_by_field_and_by_value():
    out, removed = redact({"MACAddress": "70-85-C2-11-22-33", "note": "adapter at 70:85:c2:11:22:33 flapped"})
    assert out["MACAddress"] == "<mac>"
    assert out["note"] == "adapter at <mac> flapped"
    assert removed == ["mac"]


def test_profile_paths_lose_the_user_name():
    out, removed = redact({"path": r"C:\Users\someone\AppData\Local\x.dmp", "wsl": "/mnt/c/Users/someone/Desktop/log.csv", "home": "/home/someone/repo"})
    assert out["path"] == r"C:\Users\<user>\AppData\Local\x.dmp"
    assert out["wsl"] == "/mnt/c/Users/<user>/Desktop/log.csv"
    assert out["home"] == "/home/<user>/repo"
    assert removed == ["user"]


def test_system_paths_are_kept():
    out, removed = redact({"path": r"C:\Windows\Minidump\092026-12345-01.dmp"})
    assert out["path"] == r"C:\Windows\Minidump\092026-12345-01.dmp"
    assert removed == []


def test_identity_names_replaced_wherever_they_appear():
    ident = Identity(host="DESKTOP-ABC123", user="dave")
    out, removed = redact(
        {
            "MachineName": "DESKTOP-ABC123",
            "Message": r"DCOM was unable to communicate with the computer DESKTOP-ABC123 using any of the configured protocols; requested by PID 1234 (C:\Users\dave\Tool.exe).",
            "unrelated": "davenport",
        },
        ident,
    )
    assert out["MachineName"] == "<host>"
    assert "<host>" in out["Message"]
    assert "DESKTOP-ABC123" not in out["Message"]
    assert r"C:\Users\<user>\Tool.exe" in out["Message"]
    assert out["unrelated"] == "davenport"
    assert removed == ["host", "user"]


def test_message_text_is_evidence_and_stays():
    msg = "The system has rebooted without cleanly shutting down first. This error could be caused if the system stopped responding, crashed, or lost power unexpectedly."
    out, removed = redact({"Message": msg})
    assert out["Message"] == msg
    assert removed == []


def test_walks_lists_and_nested_structures():
    out, removed = redact([{"a": [{"SerialNumber": "1"}]}, "plain"])
    assert out == [{"a": [{"SerialNumber": "<serial>"}]}, "plain"]
    assert removed == ["serial"]


def test_short_names_are_not_replaced():
    out, _ = redact({"Message": "an ab error"}, Identity(host="ab"))
    assert out["Message"] == "an ab error"


def test_host_and_user_fields_are_redacted_by_name_without_learning():
    """The largest leak must not depend on the startup probe: MachineName goes by field name."""
    out, removed = redact({"MachineName": "DESKTOP-ABC123", "user": "someone", "Owner": "someone", "note": "kept"})
    assert out["MachineName"] == "<host>" and out["user"] == "<user>" and out["Owner"] == "<user>"
    assert out["note"] == "kept"
    assert removed == ["host", "user"]


def test_network_addresses_are_redacted_by_name():
    out, removed = redact({"ipv4": ["192.168.1.20"], "ipv6": "fe80::1", "gateway": "192.168.1.1", "dns": ["1.1.1.1", "8.8.8.8"], "address": "0000:03:00.0"})
    assert out["ipv4"] == ["<address>"] and out["ipv6"] == "<address>" and out["gateway"] == "<address>" and out["dns"] == ["<address>", "<address>"]
    assert out["address"] == "0000:03:00.0"  # a bus address is how a PCIe endpoint is told apart
    assert removed == ["address"]


def test_attach_records_what_was_removed_on_the_object():
    from sentinel.redact import Redactor

    body = Redactor().attach({"MachineName": "X-1", "plain": 1})
    assert body == {"MachineName": "<host>", "plain": 1, "redacted": ["host"]}
    assert Redactor().attach(["a"]) == ["a"]
