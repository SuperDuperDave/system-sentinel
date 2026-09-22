"""A person-triggered release check and verified download.

Nothing here runs on a timer. The tray or CLI asks GitHub only when a person asks for an update.
The published asset digest and the release's checksum list must agree with the bytes received
before the existing executable handoff is allowed to run them. One cached executable is kept in
the data directory so an interrupted attempt has a useful recovery file, not a trail of copies.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

REPO = "SuperDuperDave/system-sentinel"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "SystemSentinel.exe"
SUMS_NAME = "SHA256SUMS.txt"
MAX_METADATA = 1_000_000
MAX_SUMS = 65_536
MAX_EXE = 512 * 1024 * 1024
CHUNK = 1024 * 1024
ALLOWED_HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
_DIGEST = re.compile(r"sha256:([0-9a-fA-F]{64})\Z")
_SUM_LINE = re.compile(r"([0-9a-fA-F]{64})  (\S+)\Z")


class UpdateError(Exception):
    """The check or download could not establish a safe update."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    page: str
    exe: Asset
    sums: Asset


def version_parts(version: str) -> tuple[int, int, int]:
    match = _TAG.fullmatch("v" + version)
    if match is None:
        raise UpdateError(f"unsupported version: {version}")
    a, b, c = match.groups()
    return int(a), int(b), int(c)


def _allowed_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        allowed = parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS and parsed.port in (None, 443) and not parsed.username and not parsed.password
    except ValueError:
        allowed = False
    if not allowed:
        raise UpdateError("the update address left GitHub over HTTPS")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _allowed_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect())


def _open(url: str, timeout: float = 30):  # type: ignore[no-untyped-def]
    _allowed_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "SystemSentinel-update", "Accept": "application/vnd.github+json"})
    try:
        response = _OPENER.open(request, timeout=timeout)
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"GitHub did not answer: {exc}") from exc
    _allowed_url(response.geturl())
    return response


def _read_bounded(stream: BinaryIO, limit: int) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("the release response exceeded its size limit")
    return data


def _asset(row: Any, tag: str, name: str, maximum: int) -> Asset:
    if not isinstance(row, dict) or row.get("name") != name or row.get("state") != "uploaded":
        raise UpdateError(f"the release has no uploaded {name}")
    size = row.get("size")
    if type(size) is not int or not 0 < size <= maximum:
        raise UpdateError(f"the release gives {name} an invalid size")
    digest = _DIGEST.fullmatch(str(row.get("digest") or ""))
    if digest is None:
        raise UpdateError(f"the release gives {name} no SHA-256 digest")
    url = row.get("browser_download_url")
    expected = f"https://github.com/{REPO}/releases/download/{tag}/{name}"
    if url != expected:
        raise UpdateError(f"the release gives {name} an unexpected download address")
    return Asset(name=name, url=url, size=size, sha256=digest.group(1).lower())


def parse_release(payload: Any) -> Release:
    if not isinstance(payload, dict) or payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise UpdateError("GitHub did not return a published stable release")
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or _TAG.fullmatch(tag) is None:
        raise UpdateError("the release tag is not a three-part version")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("the release has no asset list")
    by_name: dict[str, Any] = {}
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") in (ASSET_NAME, SUMS_NAME):
            name = asset["name"]
            if name in by_name:
                raise UpdateError(f"the release lists {name} twice")
            by_name[name] = asset
    return Release(
        version=tag[1:], tag=tag,
        page=f"https://github.com/{REPO}/releases/tag/{tag}",
        exe=_asset(by_name.get(ASSET_NAME), tag, ASSET_NAME, MAX_EXE),
        sums=_asset(by_name.get(SUMS_NAME), tag, SUMS_NAME, MAX_SUMS),
    )


def latest_release() -> Release:
    """A read-only check initiated by the person; no machine data or token is sent."""
    with _open(LATEST) as response:
        try:
            return parse_release(json.loads(_read_bounded(response, MAX_METADATA)))
        except (ValueError, UnicodeError) as exc:
            raise UpdateError("GitHub returned an unreadable release description") from exc


def _read_asset(asset: Asset, maximum: int) -> bytes:
    with _open(asset.url) as response:
        data = _read_bounded(response, maximum)
    if len(data) != asset.size or hashlib.sha256(data).hexdigest() != asset.sha256:
        raise UpdateError(f"{asset.name} did not match its published digest and size")
    return data


def _listed_digest(sums: bytes) -> str:
    try:
        lines = sums.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise UpdateError("the checksum list is not UTF-8") from exc
    matches = []
    for line in lines:
        match = _SUM_LINE.fullmatch(line)
        if match and match.group(2) == ASSET_NAME:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise UpdateError("the checksum list must name the executable exactly once")
    return matches[0]


def stage(release: Release, home: Path) -> Path:
    """Keep one verified candidate; the installed copy is untouched until its own handoff runs."""
    expected = _listed_digest(_read_asset(release.sums, MAX_SUMS))
    if expected != release.exe.sha256:
        raise UpdateError("the executable digest disagrees with the checksum list")
    directory = home / "updates"
    directory.mkdir(parents=True, exist_ok=True)
    partial = directory / ".SystemSentinel.download"
    ready = directory / ASSET_NAME
    partial.unlink(missing_ok=True)
    try:
        digest = hashlib.sha256()
        total = 0
        with _open(release.exe.url, timeout=60) as response, partial.open("wb") as target:
            while chunk := response.read(CHUNK):
                total += len(chunk)
                if total > release.exe.size or total > MAX_EXE:
                    raise UpdateError("the executable exceeded its published size")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if total != release.exe.size or digest.hexdigest() != expected:
            raise UpdateError("the downloaded executable did not match its published SHA-256 and size")
        os.replace(partial, ready)
        return ready
    finally:
        partial.unlink(missing_ok=True)
