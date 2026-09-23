"""The redaction policy, applied at the boundary.

By default the API masks recognized serial and identity fields, the computer and user
names it learned, MAC and network addresses, and user-profile path segments. Message
text is kept as evidence, so an unknown identifier inside free text is not guaranteed
to be removed. Device instance identifiers are kept to distinguish PCIe endpoints.

Two layers, so the policy cannot fail open. Fields are redacted **by name** wherever
they appear (``MachineName``, ``SerialNumber``, ``mac_address``, ``ipv4`` and their
kin), which needs nothing learned about the machine. Then the machine's own names,
once learned from it, are replaced **by value** wherever they occur in text, so a host
name quoted in a message goes too, and so does one used as a key. If the names were
never learned, the first layer still holds.

CPER binary values are withheld by their hex signature wherever they appear. Their sections
can contain identifiers that do not have field names; an explicit unredacted request is the
path to exact bytes. Other unknown binary formats are not parsed by this rule.

One function, :func:`redact`, walks a JSON tree and returns a new one plus the list
of what it removed. Every route, the composed handoff and the capture pack pass
through it unless the caller asked for ``unredacted`` by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PLACEHOLDER_HOST = "<host>"
PLACEHOLDER_USER = "<user>"
PLACEHOLDER_SERIAL = "<serial>"
PLACEHOLDER_MAC = "<mac>"
PLACEHOLDER_ADDRESS = "<address>"
PLACEHOLDER_CPER = "<cper bytes withheld; request unredacted for exact payload>"

# Fields whose values identify the physical part, the machine, the person or the network,
# not the machine's state. Matched on the field name, case-insensitively.
# PlatformId and FRUId are the stable machine and part identifiers a decoded CPER record carries.
_SERIAL_KEY = re.compile(r"(serial|uuid|productkey|product_key|hardwareid$|platformid|platform_id|fruid|fru_id|partitionid|partition_id|^frutext$|^fru_text$)", re.I)
_MAC_KEY = re.compile(r"(macaddress|mac_address|physicaladdress)$", re.I)
_HOST_KEY = re.compile(r"^(machinename|machine_name|computername|computer_name|host|hostname|host_name|__server|pscomputername|dnshostname)$", re.I)
_USER_KEY = re.compile(r"^(user|username|user_name|registereduser|registered_user|owner|loggedonuser|logged_on_user|account)$", re.I)
_ADDRESS_KEY = re.compile(r"^(ip|ipv4|ipv6|ipaddress|ip_address|ip_addresses|gateway|default_gateway|dns|dns_servers|dnsservers)$", re.I)

_FIELD_REPLACEMENTS = (
    (_SERIAL_KEY, "serial", PLACEHOLDER_SERIAL),
    (_MAC_KEY, "mac", PLACEHOLDER_MAC),
    (_HOST_KEY, "host", PLACEHOLDER_HOST),
    (_USER_KEY, "user", PLACEHOLDER_USER),
    (_ADDRESS_KEY, "address", PLACEHOLDER_ADDRESS),
)

_MAC_VALUE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_PROFILE_PATH = re.compile(r"(?i)((?:[A-Z]:\\|/mnt/[a-z]/)Users[\\/])([^\\/\"'<>|]+)")
_WSL_HOME = re.compile(r"(?i)((?:\\\\wsl(?:\.localhost)?\\[^\\]+\\|/)home[\\/])([^\\/\"'<>|]+)")


@dataclass(frozen=True)
class Identity:
    """What this machine calls itself: replaced wherever it appears in text, once learned."""

    host: str | None = None
    user: str | None = None

    def names(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if self.host:
            pairs.append((self.host, PLACEHOLDER_HOST))
        if self.user:
            pairs.append((self.user, PLACEHOLDER_USER))
        # Longest first so a user name that contains the host name is replaced whole.
        return sorted(pairs, key=lambda p: -len(p[0]))


class Redactor:
    def __init__(self, identity: Identity | None = None):
        self.identity = identity or Identity()
        self._name_patterns = [
            (re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.I), placeholder)
            for name, placeholder in self.identity.names()
            if len(name) >= 3
        ]

    def redact(self, value: Any) -> tuple[Any, list[str]]:
        removed: set[str] = set()
        out = self._walk(value, removed, key=None)
        return out, sorted(removed)

    def attach(self, payload: Any) -> Any:
        """Redact a payload and, when it is an object, record what was removed on it as ``redacted``."""
        body, removed = self.redact(payload)
        if isinstance(body, dict):
            body["redacted"] = removed
        return body

    def _walk(self, value: Any, removed: set[str], key: str | None) -> Any:
        # A field's name classifies the value, whatever JSON type Windows used for it. Numeric
        # serials and structured identity fields must not escape just because they are not text.
        if key is not None and value is not None and not isinstance(value, (list, str)):
            replacement = _field_replacement(key)
            if replacement is not None:
                kind, placeholder = replacement
                removed.add(kind)
                return placeholder
        if isinstance(value, dict):
            # The key is walked too, by the value layer alone: a tree keyed by the machine's own
            # name would otherwise carry it out whole. The field policy is deliberately not applied
            # to a key, because ``SerialNumber`` has to stay ``SerialNumber`` for a reader to know
            # which field was removed.
            return {(self._string(k, removed, None) if isinstance(k, str) else k): self._walk(v, removed, key=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self._walk(v, removed, key=key) for v in value]
        if isinstance(value, str):
            return self._string(value, removed, key)
        return value

    def _string(self, s: str, removed: set[str], key: str | None) -> str:
        # CPER is binary evidence. Its header and sections can contain stable machine, part and
        # partition identifiers that a key-name redactor cannot see. Withhold the whole hex value
        # in default output, including the duplicate copy Windows puts in Properties. A caller
        # must explicitly request unredacted output for exact bytes.
        if s[:8].lower() == "43504552":
            removed.add("cper")
            return PLACEHOLDER_CPER
        if key is not None and s.strip():
            replacement = _field_replacement(key)
            if replacement is not None:
                kind, placeholder = replacement
                removed.add(kind)
                return placeholder
        if _MAC_VALUE.search(s):
            s = _MAC_VALUE.sub(PLACEHOLDER_MAC, s)
            removed.add("mac")
        if _PROFILE_PATH.search(s):
            s = _PROFILE_PATH.sub(lambda m: m.group(1) + PLACEHOLDER_USER, s)
            removed.add("user")
        if _WSL_HOME.search(s):
            s = _WSL_HOME.sub(lambda m: m.group(1) + PLACEHOLDER_USER, s)
            removed.add("user")
        for pattern, placeholder in self._name_patterns:
            if pattern.search(s):
                s = pattern.sub(placeholder, s)
                removed.add("host" if placeholder == PLACEHOLDER_HOST else "user")
        return s


def _field_replacement(key: str) -> tuple[str, str] | None:
    for pattern, kind, placeholder in _FIELD_REPLACEMENTS:
        if pattern.search(key):
            return kind, placeholder
    return None


def redact(value: Any, identity: Identity | None = None) -> tuple[Any, list[str]]:
    return Redactor(identity).redact(value)
