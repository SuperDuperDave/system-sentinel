"""One deliberate update: release identity, bounded bytes, agreement, then handoff."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from sentinel import cli, launcher, update


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload(exe: bytes = b"new executable", sums: bytes | None = None, tag: str = "v9.1.0") -> dict:
    sums = sums if sums is not None else f"{sha(exe)}  SystemSentinel.exe\n".encode()

    def asset(name: str, data: bytes) -> dict:
        return {
            "name": name, "state": "uploaded", "size": len(data), "digest": "sha256:" + sha(data),
            "browser_download_url": f"https://github.com/{update.REPO}/releases/download/{tag}/{name}",
        }

    return {"tag_name": tag, "draft": False, "prerelease": False, "assets": [asset(update.ASSET_NAME, exe), asset(update.SUMS_NAME, sums)]}


class Response(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self.url = url

    def geturl(self) -> str:
        return self.url


def fake_open(monkeypatch: pytest.MonkeyPatch, body: dict, exe: bytes, sums: bytes) -> None:
    content = {update.LATEST: json.dumps(body).encode()}
    for asset in body["assets"]:
        content[asset["browser_download_url"]] = exe if asset["name"] == update.ASSET_NAME else sums
    monkeypatch.setattr(update, "_open", lambda url, timeout=30: Response(content[url], url))


def test_only_a_published_three_part_release_with_our_assets_is_accepted():
    valid = payload()
    assert update.parse_release(valid).version == "9.1.0"
    for change in ({"draft": True}, {"prerelease": True}, {"tag_name": "v9.1.0-rc1"}):
        with pytest.raises(update.UpdateError):
            update.parse_release(dict(valid, **change))
    tampered = payload()
    tampered["assets"][0]["browser_download_url"] = "https://elsewhere.example/bad.exe"
    with pytest.raises(update.UpdateError, match="unexpected download address"):
        update.parse_release(tampered)
    duplicated = payload()
    duplicated["assets"].append(dict(duplicated["assets"][0]))
    with pytest.raises(update.UpdateError, match="twice"):
        update.parse_release(duplicated)


def test_update_urls_must_stay_on_github_over_https():
    for url in ("http://github.com/a", "https://example.com/a", "https://github.com:8443/a", "https://github.com@evil.example/a", "https://github.com:wrong/a"):
        with pytest.raises(update.UpdateError):
            update._allowed_url(url)
    with pytest.raises(update.UpdateError):
        update._SafeRedirect().redirect_request(None, None, 302, "Found", {}, "http://example.com/payload")


def test_the_check_reads_metadata_and_staging_keeps_a_versioned_verified_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    exe = b"new executable"
    sums = f"{sha(exe)}  SystemSentinel.exe\n".encode()
    body = payload(exe, sums)
    fake_open(monkeypatch, body, exe, sums)
    release = update.latest_release()
    old_cache = tmp_path / "updates" / "SystemSentinel-8.0.0.exe"
    old_cache.parent.mkdir()
    old_cache.write_bytes(b"previous release")
    staged = update.stage(release, tmp_path)
    assert staged.read_bytes() == exe
    assert staged == tmp_path / "updates" / "SystemSentinel-9.1.0.exe"
    assert not old_cache.exists()
    assert not list(staged.parent.glob(".SystemSentinel.*.download"))


def test_disagreement_or_short_download_never_overwrites_a_cached_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cache = tmp_path / "updates" / "SystemSentinel-9.1.0.exe"
    cache.parent.mkdir()
    cache.write_bytes(b"previous verified copy")
    exe = b"new executable"
    wrong_sums = f"{sha(b'different')}  SystemSentinel.exe\n".encode()
    body = payload(exe, wrong_sums)
    fake_open(monkeypatch, body, exe, wrong_sums)
    with pytest.raises(update.UpdateError, match="disagrees"):
        update.stage(update.latest_release(), tmp_path)
    assert cache.read_bytes() == b"previous verified copy"

    good_sums = f"{sha(exe)}  SystemSentinel.exe\n".encode()
    body = payload(exe, good_sums)
    fake_open(monkeypatch, body, exe[:-1], good_sums)
    with pytest.raises(update.UpdateError, match="did not match"):
        update.stage(update.parse_release(body), tmp_path)
    assert cache.read_bytes() == b"previous verified copy"
    assert not list(cache.parent.glob(".SystemSentinel.*.download"))


def test_a_verified_candidate_is_reused_when_an_earlier_launch_still_has_it_open(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    exe = b"published executable"
    sums = f"{sha(exe)}  SystemSentinel.exe\n".encode()
    body = payload(exe, sums)
    fake_open(monkeypatch, body, exe, sums)
    release = update.latest_release()
    first = update.stage(release, tmp_path)
    original_open = update._open

    def no_second_executable_download(url: str, timeout: float = 30):
        if url == release.exe.url:
            raise AssertionError("the cached verified executable should be reused")
        return original_open(url, timeout)

    monkeypatch.setattr(update, "_open", no_second_executable_download)
    assert update.stage(release, tmp_path) == first


def test_the_tray_confirms_before_staging_and_leaves_current_copy_on_refusal(monkeypatch: pytest.MonkeyPatch):
    release = update.parse_release(payload())
    monkeypatch.setattr(update, "latest_release", lambda: release)
    monkeypatch.setattr(launcher, "file_version", lambda _path: "1.0.1")
    monkeypatch.setattr(launcher, "frozen", lambda: True)
    asked: list[str] = []
    started: list[str] = []
    monkeypatch.setattr(launcher, "ask_yes_no", lambda question: asked.append(question) or False)
    monkeypatch.setattr(launcher, "start_verified_update", lambda r: started.append(r.version))
    launcher.check_for_updates()
    assert "GitHub" in asked[0] and started == []
    monkeypatch.setattr(launcher, "ask_yes_no", lambda _question: True)
    launcher.check_for_updates()
    assert started == ["9.1.0"]


def test_the_cli_checks_without_installing_until_asked(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    release = update.parse_release(payload())
    monkeypatch.setattr(update, "latest_release", lambda: release)
    monkeypatch.setattr(launcher, "file_version", lambda _path: "1.0.1")
    started: list[str] = []
    monkeypatch.setattr(launcher, "start_verified_update", lambda r: started.append(r.version))
    assert cli.main(["update"]) == 0
    assert "9.1.0 is available" in capsys.readouterr().out
    assert started == []
    assert cli.main(["update", "--install"]) == 0
    assert started == ["9.1.0"]


def test_an_install_copy_failure_keeps_the_previous_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    source = tmp_path / "download.exe"
    source.write_bytes(b"new executable")
    home = tmp_path / "home" / "SystemSentinel.exe"
    home.parent.mkdir()
    home.write_bytes(b"old executable")

    def interrupted_copy(_source: Path, target: Path) -> None:
        target.write_bytes(b"only the first bytes")
        raise OSError("copy interrupted")

    monkeypatch.setattr(launcher.shutil, "copy2", interrupted_copy)
    error = launcher.install(source, home, tries=1)
    assert error and "copy interrupted" in error
    assert home.read_bytes() == b"old executable"
    assert list(home.parent.iterdir()) == [home]


def test_a_completed_install_replaces_the_file_and_removes_staging(tmp_path: Path):
    source = tmp_path / "download.exe"
    source.write_bytes(b"new executable")
    home = tmp_path / "home" / "SystemSentinel.exe"
    home.parent.mkdir()
    home.write_bytes(b"old executable")
    assert launcher.install(source, home, tries=1) is None
    assert home.read_bytes() == b"new executable"
    assert list(home.parent.iterdir()) == [home]


def test_a_locked_destination_keeps_the_previous_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    source = tmp_path / "download.exe"
    source.write_bytes(b"new executable")
    home = tmp_path / "home" / "SystemSentinel.exe"
    home.parent.mkdir()
    home.write_bytes(b"old executable")
    def locked(_source: Path, _target: Path) -> None:
        raise OSError("file in use")

    monkeypatch.setattr(launcher.os, "replace", locked)
    error = launcher.install(source, home, tries=1)
    assert error and "file in use" in error
    assert home.read_bytes() == b"old executable"
    assert list(home.parent.iterdir()) == [home]


def test_takeover_waits_for_the_exact_old_process_after_the_port_closes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    events: list[str] = []

    class OldInstance:
        identities = (object(),)

        def stop(self, grace_seconds: float) -> bool:
            assert grace_seconds == launcher.QUIT_TIMEOUT
            events.append("old process exited")
            return True

    monkeypatch.setattr(launcher, "capture_installed_listener", lambda _home, _port: events.append("captured listener") or OldInstance())
    monkeypatch.setattr(launcher, "ask_to_quit", lambda _base, _token: events.append("quit accepted") or True)
    monkeypatch.setattr(launcher, "wait_until_free", lambda _port: events.append("port free") or True)
    monkeypatch.setattr(launcher, "_hand_over", lambda *_args, **_kwargs: events.append("replacement began") or 0)

    assert launcher._take_over("http://127.0.0.1:8030", "token", 8030, tmp_path / "new.exe", tmp_path / "old.exe", "1.0.1") == 0
    assert events == ["captured listener", "quit accepted", "old process exited", "port free", "replacement began"]


def test_denied_replace_restarts_and_verifies_the_intact_previous_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "SystemSentinel.exe"
    home.write_bytes(b"old executable")
    started: list[Path] = []
    shown: list[str] = []
    monkeypatch.setattr(launcher, "install", lambda _here, _home: "[WinError 5] Access is denied")
    monkeypatch.setattr(launcher, "serving_version", lambda _base, _token: None)
    monkeypatch.setattr(launcher, "port_free", lambda _port: True)
    monkeypatch.setattr(launcher, "start_installed", lambda path: started.append(path) or None)
    monkeypatch.setattr(launcher, "wait_until_version", lambda _base, _token, version: version == "1.0.1")
    monkeypatch.setattr(launcher, "message_box", lambda message: shown.append(message))

    assert launcher._hand_over("http://127.0.0.1:8030", "token", 8030, tmp_path / "new.exe", home, copy=True, expected_version="1.10.1", previous_version="1.0.1") == 1
    assert home.read_bytes() == b"old executable"
    assert started == [home]
    assert "Version 1.0.1 was running again at that time" in shown[0]
    assert "The tray shows the version running now" in shown[0]
    assert "WinError" not in shown[0]


def test_handover_does_not_count_an_old_server_as_the_new_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    home = tmp_path / "SystemSentinel.exe"
    shown: list[str] = []
    monkeypatch.setattr(launcher, "install", lambda _here, _home: None)
    monkeypatch.setattr(launcher, "start_installed", lambda _home: None)
    monkeypatch.setattr(launcher, "wait_until_version", lambda _base, _token, _version: False)
    monkeypatch.setattr(launcher, "serving_version", lambda _base, _token: "1.0.1")
    monkeypatch.setattr(launcher, "message_box", lambda message: shown.append(message))

    assert launcher._hand_over("http://127.0.0.1:8030", "token", 8030, tmp_path / "new.exe", home, copy=True, expected_version="1.10.1") == 1
    assert "1.10.1 was installed but did not start answering" in shown[0]
