"""The space walk: coverage decides the bound, a skipped or unreached folder is never a zero, and no path
leaves by default."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from sentinel.bridge import BridgeResult
from sentinel.reading import REGISTRY, automatic_params
from sentinel.readings.space import MAX_GROUPS, WALKER, _group, _scope_root, build, space_script, take_space, take_space_page
from tests.conftest import FakeBridge, real_bridge_or_skip

PARAMS = {"seconds": 30, "max_entries": 1_000_000, "include_names": False, "scope_id": ""}


def group(kind="directory", *, label=None, name=None, skipped=None, files=0, directories=0, logical=0, allocated=0, links=0,
          skips=None, unreadable=None, cross=0, visited=1, unvisited=0, **extra):
    return {
        "kind": kind, "label": label, "name": name, "skipped": skipped,
        "files": files, "directories": directories, "logical_bytes": logical, "allocated_bytes": allocated,
        "link_repeats": links, "placeholder_files": 0, "placeholder_logical_bytes": 0, "compressed_or_sparse_files": 0, "reparse_files": 0,
        "skipped_directories": {"mount_point": 0, "symlink": 0, "cloud": 0, "other": 0, **(skips or {})},
        "unreadable_directories": {"denied": 0, "vanished": 0, "other": 0, **(unreadable or {})},
        "last_write_age_allocated_bytes": {"last_7_days": 0, "days_7_to_30": 0, "days_30_to_180": 0,
                                           "days_180_to_365": 0, "older_than_365_days": 0, "unknown": allocated},
        "cross_volume": cross, "visited_directories": visited, "unvisited_directories": unvisited, **extra,
    }


def payload(groups, **extra):
    return {
        "root": {"status": "ok", "is_reparse_point": False}, "placeholders_exposed": True,
        "volume": {"name": "C:\\", "file_system": "NTFS", "total_bytes": 1000, "free_bytes": 400, "available_bytes": 400},
        "limits": {"seconds": 30, "max_entries": 1_000_000}, "elapsed_ms": 12, "entries": 9, "stopped": None,
        "unidentified_files": 0, "error_codes": {}, "other_reparse_tags": {}, "examples": None, "groups": groups, **extra,
    }


def taken(data, **params):
    return take_space(FakeBridge(result=BridgeResult("ok", items=[data], took_ms=5)), {**PARAMS, **params})


def test_a_finished_walk_reconciles_with_the_volume_and_keeps_links_out_of_the_gaps():
    top = group("top_level_files", files=2, logical=50, allocated=64, skips={"mount_point": 3})
    docs = group(label="Documents", name="Private Project", files=5, directories=2, logical=300, allocated=320, links=1, skips={"symlink": 1})
    reading = taken(payload([top, docs]))
    assert reading.outcome == "ok" and reading.count == 2 and reading.warnings == []
    totals = reading.section("totals").data
    assert totals["files"] == 7 and totals["logical_bytes"] == 350 and totals["allocated_bytes"] == 384
    assert totals["link_repeats"] == 1 and totals["complete"] is True and totals["lower_bound"] is False
    assert reading.section("reconciliation").data == {"volume_used_bytes": 600, "scan_allocated_bytes": 384, "scan_lower_bound": False, "not_attributed_bytes": 216}
    rows = reading.section("groups").data
    assert [r["label"] for r in rows] == ["Documents", None] and {r["status"] for r in rows} == {"complete"}
    assert reading.section("coverage").data["skipped_directories"] == {"mount_point": 3, "symlink": 1, "cloud": 0, "other": 0}
    assert "not reclaimable" in reading.section("reconciliation").basis


def test_names_and_paths_stay_home_unless_asked_for():
    raw = payload([group(name="Private Project", files=1, logical=1, allocated=1)], examples=[{"reason": "unreadable_denied", "path": "Private Project\\x"}])
    default = json.dumps(taken(raw).to_dict()["sections"])
    assert "Private Project" not in default and "examples" not in default
    named = taken(raw, include_names=True)
    assert named.section("groups").data[0]["name"] == "Private Project"
    assert named.section("examples").data[0]["path"] == "Private Project\\x"
    script = space_script(30, 1000, False)
    assert "GetFolderPath('UserProfile')" in script and "Users\\" not in script
    assert "'" not in WALKER  # it travels as one PowerShell literal


def test_an_opaque_handle_drills_into_an_observed_child_without_returning_its_path():
    raw = payload([group("top_level_files"), group(name="Private Project", files=1, logical=10, allocated=16)])
    bridge = FakeBridge(result=BridgeResult("ok", items=[raw], took_ms=5))
    home = take_space(bridge, PARAMS)
    assert len(home.method["query"]) < 120 and "SentinelSpaceV1" in home.method["source"]
    child = next(row for row in home.section("groups").data if row["kind"] == "directory")
    assert child["name"] is None and child["scope_id"]
    scoped = take_space(bridge, {**PARAMS, "scope_id": child["scope_id"]})
    assert scoped.outcome == "ok" and scoped.section("collection").data["scope_depth"] == 1
    assert scoped.section("collection").data["parent_comparable"] is False
    assert any("independent walk" in warning for warning in scoped.warnings)
    assert "Private Project" not in json.dumps(scoped.to_dict())
    assert "FromBase64String" not in json.dumps(scoped.to_dict())
    assert "FromBase64String" in bridge.scripts[-1]
    expired = take_space(bridge, {**PARAMS, "scope_id": "missing"})
    assert expired.outcome == "failed" and "expired or is unknown" in expired.error["detail"]


def test_largest_files_are_bounded_metadata_leads_and_private_by_default():
    file = {"allocated_bytes": 2048, "logical_bytes": 4096, "name": "private.vhdx", "relative_path": "Work\\private.vhdx",
            "group_label": None, "group_name": "Work", "file_id": "0000000000000001", "last_write_utc": "2026-09-25T00:00:00Z",
            "placeholder": False, "compressed_or_sparse": True, "reparse_file": False}
    raw = payload([group(name="Work", files=1, logical=4096, allocated=2048)], largest_files=[file])
    hidden = taken(raw)
    lead = hidden.section("largest_files").data[0]
    assert lead["allocated_bytes"] == 2048 and lead["compressed_or_sparse"] is True
    assert lead["name"] is lead["relative_path"] is lead["group_name"] is None
    assert "private.vhdx" not in json.dumps(hidden.to_dict())
    shown = taken(raw, include_names=True)
    assert shown.section("largest_files").data[0]["relative_path"] == "Work\\private.vhdx"


def test_a_walk_stopped_at_its_limit_is_a_lower_bound_and_an_unreached_folder_has_no_measures():
    done = group(label="AppData", files=10, directories=3, logical=900, allocated=1000, visited=4, unvisited=2)
    unreached = group(name="Later", visited=0, unvisited=1)
    reading = taken(payload([group("top_level_files"), done, unreached], stopped="time_limit"))
    assert reading.outcome == "ok"
    rows = {r["label"] or r["kind"]: r for r in reading.section("groups").data}
    assert rows["AppData"]["status"] == "partial" and rows["AppData"]["allocated_bytes"] == 1000
    assert rows["directory"]["status"] == "not_scanned" and rows["directory"]["allocated_bytes"] is None
    assert reading.section("totals").data["lower_bound"] is True
    assert reading.section("coverage").data == {**reading.section("coverage").data, "complete": False, "stopped": "time_limit", "unvisited_directories": 3}
    assert reading.section("reconciliation").data["scan_lower_bound"] is True
    assert any("time limit" in w and "lower bound" in w for w in reading.warnings)


def test_nothing_found_by_an_unfinished_walk_is_not_empty():
    stopped = taken(payload([group("top_level_files"), group(visited=0, unvisited=1)], stopped="entry_limit"))
    assert stopped.outcome == "ok" and stopped.section("totals").data["lower_bound"] is True
    denied_inside = taken(payload([group("top_level_files", unreadable={"denied": 1})]))
    assert denied_inside.outcome == "ok" and denied_inside.section("coverage").data["complete"] is False
    empty = taken(payload([group("top_level_files")]))
    assert empty.outcome == "empty" and empty.count == 0


def test_cloud_and_unknown_reparse_folders_are_gaps_and_skipped_rows_have_no_measures():
    onedrive = group(label="OneDrive", skipped="cloud", skips={"cloud": 1}, visited=0)
    inner = group(label="Documents", files=1, logical=5, allocated=8, skips={"other": 1})
    reading = taken(payload([group("top_level_files"), onedrive, inner]))
    rows = reading.section("groups").data
    assert rows[-1]["label"] == "OneDrive" and rows[-1]["status"] == "skipped" and rows[-1]["allocated_bytes"] is None
    assert rows[0]["status"] == "partial"
    assert reading.section("totals").data["lower_bound"] is True
    assert any("cloud or unrecognized" in w for w in reading.warnings)


def test_a_top_level_folder_reached_but_not_listed_has_no_measures():
    locked = group(name="locked", visited=0, unreadable={"denied": 1})
    reading = taken(payload([group("top_level_files", files=1, logical=1, allocated=1), locked]), include_names=True)
    row = next(r for r in reading.section("groups").data if r["name"] == "locked")
    assert row["status"] == "not_listed" and row["allocated_bytes"] is None
    assert reading.outcome == "ok" and reading.section("totals").data["lower_bound"] is True


def test_a_root_windows_refused_is_denied_not_empty():
    reading = taken({"root": {"status": "denied", "code": 5}, "placeholders_exposed": True})
    assert reading.outcome == "denied" and reading.sections == [] and "error 5" in reading.error["detail"]
    failed = taken({"root": {"status": "failed", "code": 3}})
    assert failed.outcome == "failed"


def test_more_allocation_than_the_volume_uses_leaves_the_remainder_unknown():
    reading = taken(payload([group("top_level_files", files=1, logical=1, allocated=5000)]))
    assert reading.section("reconciliation").data["not_attributed_bytes"] is None
    assert any("more allocation than the volume" in w for w in reading.warnings)


def test_many_top_level_folders_fold_into_one_row_and_keep_their_sum():
    rows = [group("top_level_files")] + [group(files=1, logical=i, allocated=i) for i in range(1, 61)]
    reading = taken(payload(rows))
    out = reading.section("groups").data
    assert len(out) == MAX_GROUPS and out[0]["allocated_bytes"] == 60 and out[-1]["kind"] == "other_groups"
    assert sum(r["allocated_bytes"] for r in out) == sum(range(1, 61)) == reading.section("totals").data["allocated_bytes"]


def test_folded_groups_have_an_exact_replayable_continuation_with_child_handles():
    raw = payload([group("top_level_files")] + [group(name=f"folder {i}", files=1, allocated=100 - i) for i in range(60)])
    bridge = FakeBridge(result=BridgeResult("ok", items=[raw], took_ms=5))
    first = take_space(bridge, PARAMS)
    p = first.section("pagination").data
    assert p["folded_groups"] == 22 and p["next_page_id"] and first.section("groups").data[-1]["kind"] == "other_groups"
    original_calls = len(bridge.scripts)
    second = take_space_page(bridge, {"page_id": p["next_page_id"], "include_names": False})
    assert second.outcome == "ok" and second.count == 22 and len(bridge.scripts) == original_calls
    assert second.section("pagination").data["scan_id"] == p["scan_id"]
    assert second.section("pagination").data["next_page_id"] is None
    assert all(row["name"] is None and (row["scope_id"] if row["kind"] == "directory" else True) for row in second.section("groups").data)
    named = take_space_page(bridge, {"page_id": p["next_page_id"], "include_names": True})
    assert named.section("groups").data[0]["name"].startswith("folder ")
    assert len(bridge.scripts) == original_calls
    child = next(row for row in named.section("groups").data if row["kind"] == "directory")
    drilled = take_space(bridge, {**PARAMS, "scope_id": child["scope_id"]})
    assert drilled.outcome == "ok" and "FromBase64String" in bridge.scripts[-1]


def test_authenticated_api_exposes_a_private_exact_page_without_a_second_walk():
    from fastapi.testclient import TestClient

    from sentinel.app import State, create_app
    from tests.conftest import identity_result

    raw = payload([group("top_level_files")] + [group(name=f"Private {i}", files=1, allocated=100 - i) for i in range(60)])
    bridge = FakeBridge(result=BridgeResult("ok", items=[raw], took_ms=5),
                        by_marker={"$env:COMPUTERNAME": identity_result("TESTBOX", "tester")})
    with TestClient(create_app(State(bridge=bridge, token="test-space-token"))) as client:
        assert client.get("/api/readings/space_page?page_id=missing").status_code == 401
        auth = {"Authorization": "Bearer test-space-token"}
        home = client.get("/api/readings/space", headers=auth).json()
        assert home["outcome"] == "ok" and "Private" not in json.dumps(home)
        continuation = next(s["data"] for s in home["sections"] if s["name"] == "pagination")["next_page_id"]
        before = len(bridge.scripts)
        page = client.get(f"/api/readings/space_page?page_id={continuation}", headers=auth).json()
        assert page["outcome"] == "ok" and "Private" not in json.dumps(page)
        assert len(bridge.scripts) == before
        named = client.get(f"/api/readings/space_page?page_id={continuation}&include_names=true", headers=auth).json()
        assert named["outcome"] == "ok" and "Private" in json.dumps(named)
        assert len(bridge.scripts) == before


def test_a_malformed_answer_fails_rather_than_reading_as_measured():
    reading = taken(payload([group(files=-1)]))
    assert reading.outcome == "failed" and "unexpected shape" in reading.error["detail"]
    with pytest.raises(ValueError):
        _group({"kind": "file"})


def test_the_walk_is_never_taken_by_capture_or_bench():
    assert REGISTRY["space"].requires_selection and REGISTRY["space"].heavy
    with pytest.raises(ValueError, match="exact selection"):
        automatic_params("space")
    assert REGISTRY["space"].coerce({}) == PARAMS


@pytest.mark.host
def test_a_synthetic_tree_walked_on_windows():
    """Only a tree this test builds under %TEMP% is walked; it is removed afterwards."""
    bridge = real_bridge_or_skip()
    probe_name = f"sentinel-space-probe-{uuid4().hex}"
    # Links are removed before the tree, so no recursive delete can follow one.
    clear = r"""
$r = Join-Path $env:TEMP '__PROBE__'
if (Test-Path $r) {
  cmd /c "icacls `"$r\locked`" /remove:d $env:USERNAME >nul 2>&1"
  foreach ($l in "$r\a\junction", "$r\b\symlink") { if (Test-Path $l) { cmd /c "rmdir `"$l`"" } }
  Remove-Item -LiteralPath $r -Recurse -Force
}
""".replace("__PROBE__", probe_name)
    setup = clear + r"""
New-Item -ItemType Directory -Path $r, "$r\a", "$r\a\deep", "$r\b", "$r\locked", "$r\target" | Out-Null
[IO.File]::WriteAllBytes("$r\a\plain.bin", (New-Object byte[] 100000))
[IO.File]::WriteAllBytes("$r\a\tiny.txt", (New-Object byte[] 10))
[IO.File]::WriteAllBytes("$r\a\deep\comp.bin", (New-Object byte[] 1000000))
compact /c "$r\a\deep\comp.bin" | Out-Null
New-Item -ItemType HardLink -Path "$r\b\link.bin" -Target "$r\a\plain.bin" | Out-Null
[IO.File]::WriteAllBytes("$r\target\t.bin", (New-Object byte[] 4096))
New-Item -ItemType Junction -Path "$r\a\junction" -Target "$r\target" | Out-Null
try { New-Item -ItemType SymbolicLink -Path "$r\b\symlink" -Target "$r\target" -ErrorAction Stop | Out-Null; $sym = $true } catch { $sym = $false }
[IO.File]::WriteAllBytes("$r\locked\hidden.bin", (New-Object byte[] 5000))
cmd /c "icacls `"$r\locked`" /deny $env:USERNAME`:(RX) >nul 2>&1"
[pscustomobject]@{ symlink = $sym; compressed = [bool]((Get-Item "$r\a\deep\comp.bin").Attributes -band [IO.FileAttributes]::Compressed) }
"""
    cleanup = clear + "[pscustomobject]@{ left = (Test-Path $r) }"
    try:
        made = bridge.run(setup, timeout=120)
        assert made.outcome == "ok", made.error
        symlink = made.items[0]["symlink"]
        root = f"Join-Path $env:TEMP '{probe_name}'"
        walked = {}
        for label, names, cap in (("named", True, 100_000), ("default", False, 100_000), ("capped", False, 3), ("mid-directory", False, 6)):
            result = bridge.run(space_script(30, cap, names, root=root), timeout=180, depth=8)
            assert result.outcome == "ok", (label, result.error)
            sections, facts = build(dict(result.items[0]), names)
            walked[label] = ({s.name: s.data for s in sections}, facts)
            print(label, result.took_ms, "ms", made.items[0], json.dumps(walked[label][0], indent=1))
        scoped_root = _scope_root(("a",), home=root)
        scoped_result = bridge.run(space_script(30, 100_000, True, root=scoped_root), timeout=180, depth=8)
        assert scoped_result.outcome == "ok", scoped_result.error
        scoped_sections, _ = build(dict(scoped_result.items[0]), True)
        scoped = {section.name: section.data for section in scoped_sections}
        junction_root = _scope_root(("a", "junction"), home=root)
        refused = bridge.run(space_script(30, 100_000, False, root=junction_root), timeout=180, depth=8)
        assert refused.outcome == "ok" and refused.items[0]["root"]["reason"] == "root_reparse_point"
    finally:
        gone = bridge.run(cleanup, timeout=60)
        assert gone.outcome == "ok" and gone.items[-1]["left"] is False, gone.error

    named, _ = walked["named"]
    rows = {r["name"]: r for r in named["groups"]}
    assert set(rows) == {None, "a", "b", "locked", "target"}
    a, b = rows["a"], rows["b"]
    assert a["status"] == "complete" and a["files"] == 3 and a["directories"] == 1 and a["logical_bytes"] == 1_100_010
    assert a["skipped_directories"]["mount_point"] == 1  # the junction, counted and not followed
    assert b["files"] == 0 and b["link_repeats"] == 1 and b["logical_bytes"] == 0  # the hard link, counted once in a
    assert b["skipped_directories"]["symlink"] == (1 if symlink else 0)
    assert rows["locked"]["status"] == "not_listed" and rows["locked"]["allocated_bytes"] is None
    assert rows["target"]["files"] == 1 and rows["target"]["logical_bytes"] == 4096
    assert named["totals"]["lower_bound"] is True and named["coverage"]["unreadable_directories"]["denied"] == 1
    assert {e["path"] for e in named["examples"]} >= {"locked", "a\\junction"}
    assert a["compressed_or_sparse_files"] == 1 and a["allocated_bytes"] is not None
    assert len(a["file_id_64"]) == 16 and len(named["collection"]["volume_serial"]) == 8
    assert len(named["collection"]["root_file_id_64"]) == 16
    assert named["collection"]["started_utc"] <= named["collection"]["finished_utc"]
    assert sum(named["totals"]["last_write_age_allocated_bytes"].values()) == named["totals"]["allocated_bytes"]
    assert len(named["largest_files"]) == 4 and named["largest_files"][0]["allocated_bytes"] >= named["largest_files"][-1]["allocated_bytes"]
    assert any(file["name"] == "comp.bin" and file["compressed_or_sparse"] for file in named["largest_files"])
    assert all(file["last_write_utc"] for file in named["largest_files"])
    assert scoped["totals"]["files"] == 3 and scoped["totals"]["logical_bytes"] == 1_100_010

    default, _ = walked["default"]
    assert probe_name not in json.dumps(default) and all(r["name"] is None for r in default["groups"])

    capped, _ = walked["capped"]
    assert capped["coverage"]["stopped"] == "entry_limit" and capped["totals"]["lower_bound"] is True
    assert capped["collection"]["entries"] <= 3
    assert {r["status"] for r in capped["groups"] if r["kind"] == "directory"} <= {"not_scanned", "not_listed"}
    middle, _ = walked["mid-directory"]
    assert middle["coverage"]["stopped"] == "entry_limit" and middle["collection"]["entries"] <= 6
    assert any(r["status"] == "partial" for r in middle["groups"])
