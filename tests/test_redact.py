"""The redaction policy: serials, the machine's names, MAC addresses and profile paths go; evidence stays."""

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from sentinel.redact import Identity, redact


def test_serial_fields_by_name():
    out, removed = redact({"BIOS": {"SerialNumber": "ABC123", "Version": "P3.90"}, "board": {"serial_number": "X"}})
    assert out["BIOS"]["SerialNumber"] == "<serial>"
    assert out["BIOS"]["Version"] == "P3.90"
    assert out["board"]["serial_number"] == "<serial>"
    assert removed == ["serial"]


def test_sensitive_fields_do_not_depend_on_the_values_json_type():
    out, removed = redact({"SerialNumber": 123456, "MachineName": {"value": "PRIVATE-HOST"}, "ipv4": [3232235777, {"value": "192.168.1.1"}], "empty": None})
    assert out == {"SerialNumber": "<serial>", "MachineName": "<host>", "ipv4": ["<address>", "<address>"], "empty": None}
    assert removed == ["address", "host", "serial"]


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


# --- The property, over trees nobody wrote by hand ----------------------------------------------
#
# The tests above name the shapes this machine's readings actually produce. These two say something
# stronger about any shape at all: a tree with the machine's name planted anywhere in it comes back
# without it, and a tree that has been redacted once does not change if it is redacted again. The
# names here are invented, as every name in tests/ is.

HOST = "MACHINE-7QX"
USER = "corvus"
IDENTITY = Identity(host=HOST, user=USER)

_ASCII_ALNUM = set(string.ascii_letters + string.digits)

#: The same name as the machine says it, as a log says it, and as a path says it. Case is not a
#: hiding place: the policy matches case-insensitively, so the property has to hold for all of them.
_NAMES = st.sampled_from([HOST, HOST.lower(), HOST.title(), USER, USER.upper(), USER.title()])

_LEAVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=12),
)
_KEYS = st.text(min_size=1, max_size=12)
_TREES = st.recursive(
    _LEAVES,
    lambda children: st.one_of(st.lists(children, max_size=4), st.dictionaries(_KEYS, children, max_size=4)),
    max_leaves=12,
)


def _at_a_word_boundary(text: str, trailing: bool) -> str:
    """Text that ends (or begins) where a word does.

    A name inside a longer run of letters and digits is deliberately not replaced — ``davenport``
    keeps its ``dave`` — so text planted beside a name has to stop at a boundary for the property to
    be about redaction rather than about that rule.
    """
    if not text:
        return text
    edge = text[-1] if trailing else text[0]
    if edge not in _ASCII_ALNUM:
        return text
    return text + " " if trailing else " " + text


@st.composite
def _seeded(draw: st.DrawFn) -> tuple[str, object]:
    """A tree of any shape with one of the machine's names somewhere inside it."""
    name = draw(_NAMES)
    before = _at_a_word_boundary(draw(st.text(max_size=16)), trailing=True)
    after = _at_a_word_boundary(draw(st.text(max_size=16)), trailing=False)
    carrier = draw(
        st.one_of(
            st.just(name),  # the whole value is the name
            st.just(f"{before}{name}{after}"),  # the name inside a sentence
            st.just(rf"C:\Users\{name}\AppData\Local\CrashDumps\x.dmp"),  # the name inside a path
        )
    )
    node: object = {carrier: draw(_TREES)} if draw(st.booleans()) else carrier
    for _ in range(draw(st.integers(min_value=0, max_value=4))):  # then bury it
        node = [draw(_TREES), node] if draw(st.booleans()) else {draw(_KEYS): node, "beside": draw(_TREES)}
    return name, node


def _strings(value):
    """Every string in a tree, keys as well as values: everything a reader could see."""
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                yield k
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, str):
        yield value


@settings(deadline=None)  # a tree can be large and a shared runner can stall; the result is what matters
@given(_seeded())
def test_a_planted_name_never_survives_at_any_depth(seeded):
    """Whatever the shape, in a key or a value, alone or inside text: the name does not come back."""
    name, tree = seeded
    out, removed = redact(tree, IDENTITY)
    assert not any(name.lower() in s.lower() for s in _strings(out))
    assert removed, "something was removed, so the envelope has to say so"


@settings(deadline=None)
@given(_TREES)
def test_redacting_twice_changes_nothing(tree):
    """The placeholders are a fixed point: composing or capturing already-redacted evidence is safe."""
    once, first = redact(tree, IDENTITY)
    twice, second = redact(once, IDENTITY)
    assert twice == once
    # A field is redacted by its name, so a second pass can name the same field again; what it must
    # never do is find a kind of thing the first pass left behind.
    assert set(second) <= set(first)
