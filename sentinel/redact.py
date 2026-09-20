"""The redaction policy, applied at the boundary.

By default nothing that leaves the API carries a serial number, the computer name,
a user account name or a MAC address, and a path under a user profile reads
``C:\\Users\\<user>\\...``. Message text is kept: it is the evidence. Device
instance identifiers are kept: they are how PCIe endpoints are told apart.

One function, :func:`redact`, walks a JSON tree and returns a new one plus the
list of what it removed. Every route, the composed handoff and the capture pack
pass through it unless the caller asked for ``unredacted`` by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PLACEHOLDER_HOST = "<host>"
PLACEHOLDER_USER = "<user>"
PLACEHOLDER_SERIAL = "<serial>"
PLACEHOLDER_MAC = "<mac>"

# Field names whose values are identifiers of the physical part, not evidence of its state.
_SERIAL_KEY = re.compile(r"(serial|uuid|productkey|product_key|hardwareid$)", re.I)
_MAC_KEY = re.compile(r"(macaddress|mac_address|physicaladdress)$", re.I)

_MAC_VALUE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_PROFILE_PATH = re.compile(r"(?i)((?:[A-Z]:\\|/mnt/[a-z]/)Users[\\/])([^\\/\"'<>|]+)")
_WSL_HOME = re.compile(r"(?i)((?:\\\\wsl(?:\.localhost)?\\[^\\]+\\|/)home[\\/])([^\\/\"'<>|]+)")


@dataclass(frozen=True)
class Identity:
    """What this machine calls itself: replaced wherever it appears."""

    host: str | None = None
    user: str | None = None
    extra_users: tuple[str, ...] = field(default_factory=tuple)

    def names(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if self.host:
            pairs.append((self.host, PLACEHOLDER_HOST))
        for u in (self.user, *self.extra_users):
            if u:
                pairs.append((u, PLACEHOLDER_USER))
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

    def _walk(self, value: Any, removed: set[str], key: str | None) -> Any:
        if isinstance(value, dict):
            return {k: self._walk(v, removed, key=str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self._walk(v, removed, key=key) for v in value]
        if isinstance(value, str):
            return self._string(value, removed, key)
        return value

    def _string(self, s: str, removed: set[str], key: str | None) -> str:
        if key is not None and s.strip():
            if _SERIAL_KEY.search(key):
                removed.add("serial")
                return PLACEHOLDER_SERIAL
            if _MAC_KEY.search(key):
                removed.add("mac")
                return PLACEHOLDER_MAC
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


def redact(value: Any, identity: Identity | None = None) -> tuple[Any, list[str]]:
    return Redactor(identity).redact(value)
