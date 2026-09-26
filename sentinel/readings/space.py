"""``space``: where the home folder's space is, measured on request and honest about its reach.

One bounded walk of the current Windows user's home folder, through the bridge like every other
reading. Each directory is listed with ``GetFileInformationByHandleEx(FileIdBothDirectoryInfo)``,
which returns every entry's logical length, allocation, attributes, reparse tag and file ID from the
directory itself: no file is opened or read, and the walk never requests placeholder content.
Links, mount points, cloud and unrecognized reparse directories are counted and never followed.

The walk stops at a time or entry limit. What it saw is kept and its totals are lower bounds; an
unfinished walk is never ``empty`` and never a complete total. Names are returned only on
``include_names``; the home folder's absolute path never leaves the machine in this reading.
"""

from __future__ import annotations

import base64
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any

from ..bridge import Bridge
from ..reading import Param, Reading, Section, Spec, from_object, register

MAX_GROUPS = 40
"""Top-level rows returned; the rest fold into one ``other_groups`` row and exact pages."""
MAX_LARGEST = 20
"""Largest observed files returned as metadata-only leads, not deletion candidates."""

_SCOPE_TTL = 30 * 60
_SCOPE_CAP = 1024
_scope_lock = threading.Lock()
_scopes: OrderedDict[str, tuple[float, tuple[str, ...]]] = OrderedDict()
_page_lock = threading.Lock()
_pages: OrderedDict[str, tuple[float, str, list[dict[str, Any]], int, dict[str, Any]]] = OrderedDict()
_PAGE_CAP = 256


def _resolve_scope(token: str) -> tuple[str, ...] | None:
    """Navigation handles are local, short-lived and never caller-chosen paths."""
    if not token:
        return ()
    with _scope_lock:
        value = _scopes.get(token)
        if value is None or time.monotonic() - value[0] > _SCOPE_TTL:
            _scopes.pop(token, None)
            return None
        _scopes.move_to_end(token)
        return value[1]


def _issue_scope(parts: tuple[str, ...]) -> str:
    with _scope_lock:
        token = secrets.token_urlsafe(18)
        _scopes[token] = (time.monotonic(), parts)
        while len(_scopes) > _SCOPE_CAP:
            _scopes.popitem(last=False)
        return token


def _issue_page(scan_id: str, rows: list[dict[str, Any]], offset: int, collection: dict[str, Any]) -> str:
    with _page_lock:
        token = secrets.token_urlsafe(18)
        _pages[token] = (time.monotonic(), scan_id, rows, offset, collection)
        while len(_pages) > _PAGE_CAP:
            _pages.popitem(last=False)
        return token


def _resolve_page(token: str) -> tuple[str, list[dict[str, Any]], int, dict[str, Any]] | None:
    with _page_lock:
        value = _pages.get(token)
        if value is None or time.monotonic() - value[0] > _SCOPE_TTL:
            _pages.pop(token, None)
            return None
        _pages.move_to_end(token)
        return value[1:]


def _valid_component(name: str) -> bool:
    return bool(name) and name not in (".", "..") and not any(char in name for char in "\\/:\x00")

TIMEOUT_MARGIN = 90
"""Seconds beyond the walk's own limit for compiling the walker, the volume query and the answer."""

SKIP_KINDS = ("mount_point", "symlink", "cloud", "other")
UNREADABLE_KINDS = ("denied", "vanished", "other")
# A mount point or symbolic link names content that lives somewhere else: not following it leaves
# nothing of this folder unmeasured. A cloud or unrecognized reparse directory may hold local data.
GAP_SKIPS = ("cloud", "other")
MEASURES = ("files", "directories", "logical_bytes", "allocated_bytes", "link_repeats", "placeholder_files", "placeholder_logical_bytes", "compressed_or_sparse_files", "reparse_files")
AGE_BANDS = ("last_7_days", "days_7_to_30", "days_30_to_180", "days_180_to_365", "older_than_365_days", "unknown")

# The walker. Kept free of single quotes so it travels as one PowerShell literal; the type name
# carries a version because a live session keeps a compiled type for its lifetime.
WALKER = r"""
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class SentinelSpaceV1 {
    const uint LIST_DIRECTORY = 1, SHARE_ALL = 7, OPEN_EXISTING = 3;
    const uint BACKUP_SEMANTICS = 0x02000000, OPEN_REPARSE_POINT = 0x00200000;
    const int ID_BOTH_DIR_INFO = 10, NO_MORE_FILES = 18;
    const uint DIRECTORY = 0x10, SPARSE = 0x200, REPARSE = 0x400, COMPRESSED = 0x800, OFFLINE = 0x1000;
    const uint RECALL_ON_OPEN = 0x40000, RECALL_ON_DATA_ACCESS = 0x400000;
    const int BUFFER = 64 * 1024, MAX_EXAMPLES = 20, MAX_LARGEST = 20;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetFileInformationByHandleEx(SafeFileHandle handle, int cls, IntPtr buffer, uint size);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetFileInformationByHandle(SafeFileHandle handle, out HandleInfo info);
    [DllImport("ntdll.dll")]
    static extern sbyte RtlSetThreadPlaceholderCompatibilityMode(sbyte mode);
    [DllImport("shell32.dll")]
    static extern int SHGetKnownFolderPath(ref Guid id, uint flags, IntPtr token, out IntPtr path);

    [StructLayout(LayoutKind.Sequential)]
    struct HandleInfo { public uint Attributes, C1, C2, A1, A2, W1, W2, VolumeSerial, SizeHigh, SizeLow, Links, IndexHigh, IndexLow; }

    struct Entry { public string Name; public uint Attributes, Tag; public long Length, Allocation, Id, LastWrite; }
    struct Pending { public string Path, Relative; public Group Group; }

    sealed class Group {
        public string Kind, Name, Label, Skipped, FileId;
        public long Files, Directories, Logical, Allocated, LinkRepeats, PlaceholderFiles, PlaceholderLogical, CompressedOrSparse, ReparseFiles;
        public long CrossVolume, Visited, Unvisited;
        public Hashtable Skips = Counter("mount_point", "symlink", "cloud", "other");
        public Hashtable Unreadable = Counter("denied", "vanished", "other");
        public Hashtable AgeAllocated = Counter("last_7_days", "days_7_to_30", "days_30_to_180", "days_180_to_365", "older_than_365_days", "unknown");
        public Hashtable ToTable(bool names) {
            Hashtable t = new Hashtable();
            // The bridge keeps names only long enough to mint local navigation handles. Python
            // removes them from the returned envelope unless include_names was requested.
            t["kind"] = Kind; t["label"] = Label; t["name"] = Name; t["skipped"] = Skipped; t["file_id_64"] = FileId;
            t["files"] = Files; t["directories"] = Directories; t["logical_bytes"] = Logical; t["allocated_bytes"] = Allocated;
            t["link_repeats"] = LinkRepeats; t["placeholder_files"] = PlaceholderFiles; t["placeholder_logical_bytes"] = PlaceholderLogical;
            t["compressed_or_sparse_files"] = CompressedOrSparse; t["reparse_files"] = ReparseFiles;
            t["skipped_directories"] = Skips; t["unreadable_directories"] = Unreadable;
            t["last_write_age_allocated_bytes"] = AgeAllocated;
            t["cross_volume"] = CrossVolume; t["visited_directories"] = Visited; t["unvisited_directories"] = Unvisited;
            return t;
        }
    }

    static Hashtable Counter(params string[] keys) { Hashtable t = new Hashtable(); foreach (string k in keys) t[k] = 0L; return t; }
    static void Bump(Hashtable t, string key) { t[key] = (long)t[key] + 1; }

    static string SkipKind(uint tag) {
        if (tag == 0xA0000003) return "mount_point";
        if (tag == 0xA000000C) return "symlink";
        if ((tag & 0xFFFF0FFF) == 0x9000001A) return "cloud";
        return "other";
    }

    static string Unreadable(int code) {
        if (code == 5) return "denied";
        if (code == 2 || code == 3) return "vanished";
        return "other";
    }

    static SafeFileHandle OpenDirectory(string path, bool follow) {
        return CreateFileW(@"\\?\" + path, LIST_DIRECTORY, SHARE_ALL, IntPtr.Zero, OPEN_EXISTING, BACKUP_SEMANTICS | (follow ? 0 : OPEN_REPARSE_POINT), IntPtr.Zero);
    }

    // One directory listing. MS-FSCC FileIdBothDirectoryInformation carries a reparse point tag in EaSize.
    static int List(SafeFileHandle handle, IntPtr buffer, List<Entry> into, long remaining, Stopwatch clock, int seconds, out string stopped) {
        stopped = null;
        while (true) {
            if (into.Count >= remaining) { stopped = "entry_limit"; return 0; }
            if (clock.Elapsed.TotalSeconds >= seconds) { stopped = "time_limit"; return 0; }
            if (!GetFileInformationByHandleEx(handle, ID_BOTH_DIR_INFO, buffer, BUFFER)) {
                int code = Marshal.GetLastWin32Error();
                return code == NO_MORE_FILES ? 0 : code;
            }
            int offset = 0;
            while (true) {
                IntPtr at = IntPtr.Add(buffer, offset);
                int next = Marshal.ReadInt32(at, 0);
                int nameBytes = Marshal.ReadInt32(at, 60);
                string name = Marshal.PtrToStringUni(IntPtr.Add(at, 104), nameBytes / 2);
                if (name != "." && name != "..") {
                    Entry e = new Entry();
                    e.Name = name;
                    e.Length = Marshal.ReadInt64(at, 40);
                    e.Allocation = Marshal.ReadInt64(at, 48);
                    e.LastWrite = Marshal.ReadInt64(at, 24);
                    e.Attributes = (uint)Marshal.ReadInt32(at, 56);
                    e.Tag = (e.Attributes & REPARSE) != 0 ? (uint)Marshal.ReadInt32(at, 64) : 0;
                    e.Id = Marshal.ReadInt64(at, 96);
                    into.Add(e);
                    if (into.Count >= remaining) { stopped = "entry_limit"; return 0; }
                    if (clock.Elapsed.TotalSeconds >= seconds) { stopped = "time_limit"; return 0; }
                }
                if (next == 0) break;
                offset += next;
            }
        }
    }

    static Dictionary<string, string> KnownFolders() {
        Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        Action<string, string> add = (path, label) => { if (!String.IsNullOrEmpty(path)) { string p = path.TrimEnd((char)92); if (!map.ContainsKey(p)) map[p] = label; } };
        add(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Desktop");
        add(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Documents");
        add(Environment.GetFolderPath(Environment.SpecialFolder.MyPictures), "Pictures");
        add(Environment.GetFolderPath(Environment.SpecialFolder.MyMusic), "Music");
        add(Environment.GetFolderPath(Environment.SpecialFolder.MyVideos), "Videos");
        add(Environment.GetFolderPath(Environment.SpecialFolder.Favorites), "Favorites");
        string roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        if (!String.IsNullOrEmpty(roaming)) add(Path.GetDirectoryName(roaming), "AppData");
        string[,] known = {
            { "374DE290-123F-4565-9164-39C4925E467B", "Downloads" }, { "4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4", "Saved Games" },
            { "BFB9D5E0-C6A9-404C-B2B2-AE6DB6AF4968", "Links" }, { "56784854-C6CB-462B-8169-88E350ACB882", "Contacts" },
            { "7D1D3A04-DEBB-4115-95CF-2F29DA2920DA", "Searches" }, { "31C0DD25-9439-4F12-BF41-7FF4EDA38722", "3D Objects" } };
        for (int i = 0; i < known.GetLength(0); i++) {
            Guid id = new Guid(known[i, 0]);
            IntPtr p;
            if (SHGetKnownFolderPath(ref id, 0, IntPtr.Zero, out p) == 0) { add(Marshal.PtrToStringUni(p), known[i, 1]); Marshal.FreeCoTaskMem(p); }
        }
        foreach (string v in new string[] { "OneDrive", "OneDriveConsumer", "OneDriveCommercial" }) add(Environment.GetEnvironmentVariable(v), "OneDrive");
        return map;
    }

    public static Hashtable Scan(string root, int seconds, long maxEntries, bool names) {
        Hashtable result = new Hashtable();
        result["started_utc"] = DateTime.UtcNow.ToString("o");
        result["limits"] = new Hashtable { { "seconds", seconds }, { "max_entries", maxEntries } };
        sbyte previous = -3;
        try { previous = RtlSetThreadPlaceholderCompatibilityMode(2); } catch (EntryPointNotFoundException) { } catch (DllNotFoundException) { }
        result["placeholders_exposed"] = previous >= 0;
        try { Walk(root.TrimEnd((char)92), seconds, maxEntries, names, result); }
        finally { if (previous >= 0) RtlSetThreadPlaceholderCompatibilityMode(previous); result["finished_utc"] = DateTime.UtcNow.ToString("o"); }
        return result;
    }

    static void Walk(string root, int seconds, long maxEntries, bool names, Hashtable result) {
        Stopwatch clock = Stopwatch.StartNew();
        DateTime observedAt = DateTime.UtcNow;
        try {
            DriveInfo drive = new DriveInfo(Path.GetPathRoot(root));
            result["volume"] = new Hashtable { { "name", drive.Name }, { "file_system", drive.DriveFormat }, { "total_bytes", drive.TotalSize }, { "free_bytes", drive.TotalFreeSpace }, { "available_bytes", drive.AvailableFreeSpace } };
        } catch (Exception ex) { result["volume"] = null; result["volume_error"] = ex.GetType().Name; }

        IntPtr buffer = Marshal.AllocHGlobal(BUFFER);
        try {
            HandleInfo info;
            List<Entry> entries = new List<Entry>();
            int code;
            string stopped;
            using (SafeFileHandle h = OpenDirectory(root, false)) {
                if (h.IsInvalid) { code = Marshal.GetLastWin32Error(); result["root"] = RootFailure(code); return; }
                if (!GetFileInformationByHandle(h, out info)) { result["root"] = RootFailure(Marshal.GetLastWin32Error()); return; }
                if ((info.Attributes & REPARSE) != 0) { result["root"] = new Hashtable { { "status", "failed" }, { "code", null }, { "reason", "root_reparse_point" } }; return; }
                code = List(h, buffer, entries, maxEntries, clock, seconds, out stopped);
                // If listing failed after yielding entries, keep those observations and mark
                // the root incomplete. An immediate failure has no useful measurement.
                if (code != 0 && entries.Count == 0) { result["root"] = RootFailure(code); return; }
            }
            uint volume = info.VolumeSerial;
            result["root"] = new Hashtable { { "status", "ok" }, { "is_reparse_point", (info.Attributes & REPARSE) != 0 },
                { "volume_serial", info.VolumeSerial.ToString("X8") }, { "file_id_64", (((ulong)info.IndexHigh << 32) | info.IndexLow).ToString("X16") } };

            Dictionary<string, string> known = KnownFolders();
            Dictionary<long, bool> seen = new Dictionary<long, bool>();
            Hashtable codes = new Hashtable(), otherTags = new Hashtable();
            ArrayList examples = new ArrayList();
            ArrayList largest = new ArrayList();
            List<Group> groups = new List<Group>();
            Group top = new Group(); top.Kind = "top_level_files"; top.Visited = 1;
            groups.Add(top);
            Stack<Pending> pending = new Stack<Pending>();
            long count = 0, unidentified = 0;
            if (stopped != null || code != 0) top.Unvisited++;
            Action<string, string> example = (reason, relative) => { if (names && examples.Count < MAX_EXAMPLES) examples.Add(new Hashtable { { "reason", reason }, { "path", relative } }); };
            Action<int> tally = (c) => { string k = c.ToString(); codes[k] = codes.ContainsKey(k) ? (long)codes[k] + 1 : 1L; };
            if (code != 0) { tally(code); Bump(top.Unreadable, Unreadable(code)); }

            // Accounts one entry to its group; returns true for a directory the walk should enter.
            Func<Entry, Group, string, bool> account = (e, g, relative) => {
                count++;
                if ((e.Attributes & DIRECTORY) != 0) {
                    string skip = null;
                    if (e.Tag != 0) skip = SkipKind(e.Tag);
                    else if ((e.Attributes & (RECALL_ON_OPEN | OFFLINE)) != 0) skip = "cloud";
                    if (skip == null) { g.Directories++; return true; }
                    Bump(g.Skips, skip);
                    if (skip == "other") { string k = "0x" + e.Tag.ToString("X8"); otherTags[k] = otherTags.ContainsKey(k) ? (long)otherTags[k] + 1 : 1L; }
                    example("skipped_" + skip, relative);
                    return false;
                }
                if (e.Id == 0) unidentified++;
                else if (seen.ContainsKey(e.Id)) { g.LinkRepeats++; return false; }
                else seen[e.Id] = true;
                g.Files++; g.Logical += e.Length; g.Allocated += e.Allocation;
                string age = "unknown";
                try {
                    if (e.LastWrite > 0) {
                        double days = (observedAt - DateTime.FromFileTimeUtc(e.LastWrite)).TotalDays;
                        if (days >= 0) age = days < 7 ? "last_7_days" : days < 30 ? "days_7_to_30" : days < 180 ? "days_30_to_180" : days < 365 ? "days_180_to_365" : "older_than_365_days";
                    }
                } catch (ArgumentOutOfRangeException) { }
                g.AgeAllocated[age] = (long)g.AgeAllocated[age] + e.Allocation;
                if (e.Tag != 0) g.ReparseFiles++;
                if ((e.Attributes & (RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE)) != 0 || (e.Tag != 0 && SkipKind(e.Tag) == "cloud")) { g.PlaceholderFiles++; g.PlaceholderLogical += e.Length; }
                if ((e.Attributes & (COMPRESSED | SPARSE)) != 0) g.CompressedOrSparse++;
                Hashtable candidate = new Hashtable();
                candidate["allocated_bytes"] = e.Allocation; candidate["logical_bytes"] = e.Length;
                candidate["name"] = names ? e.Name : null; candidate["relative_path"] = names ? relative : null;
                candidate["group_label"] = g.Label; candidate["group_name"] = names ? g.Name : null;
                candidate["file_id"] = e.Id == 0 ? null : e.Id.ToString("X16");
                candidate["placeholder"] = (e.Attributes & (RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE)) != 0 || (e.Tag != 0 && SkipKind(e.Tag) == "cloud");
                candidate["compressed_or_sparse"] = (e.Attributes & (COMPRESSED | SPARSE)) != 0;
                candidate["reparse_file"] = e.Tag != 0;
                try { candidate["last_write_utc"] = e.LastWrite > 0 ? DateTime.FromFileTimeUtc(e.LastWrite).ToString("o") : null; }
                catch (ArgumentOutOfRangeException) { candidate["last_write_utc"] = null; }
                int rank = 0;
                while (rank < largest.Count && (long)((Hashtable)largest[rank])["allocated_bytes"] >= e.Allocation) rank++;
                if (rank < MAX_LARGEST) { largest.Insert(rank, candidate); if (largest.Count > MAX_LARGEST) largest.RemoveAt(MAX_LARGEST); }
                return false;
            };

            List<Pending> firstLevel = new List<Pending>();
            foreach (Entry e in entries) {
                if ((e.Attributes & DIRECTORY) != 0) {
                    string full = root + @"\" + e.Name;
                    string label; known.TryGetValue(full, out label);
                    string skip = e.Tag != 0 ? SkipKind(e.Tag) : ((e.Attributes & (RECALL_ON_OPEN | OFFLINE)) != 0 ? "cloud" : null);
                    if (skip == "mount_point" || skip == "symlink") { account(e, top, e.Name); continue; }
                    count++;
                    Group g = new Group(); g.Kind = "directory"; g.Name = e.Name; g.Label = label; g.Skipped = skip; g.FileId = e.Id == 0 ? null : e.Id.ToString("X16");
                    groups.Add(g);
                    if (skip != null) { Bump(g.Skips, skip); example("skipped_" + skip, e.Name); continue; }
                    Pending p = new Pending(); p.Path = full; p.Relative = e.Name; p.Group = g;
                    firstLevel.Add(p);
                } else account(e, top, e.Name);
            }
            for (int i = firstLevel.Count - 1; i >= 0; i--) pending.Push(firstLevel[i]);

            while (pending.Count > 0 && stopped == null) {
                if (clock.Elapsed.TotalSeconds >= seconds) { stopped = "time_limit"; break; }
                if (count >= maxEntries) { stopped = "entry_limit"; break; }
                Pending d = pending.Pop();
                entries.Clear();
                using (SafeFileHandle h = OpenDirectory(d.Path, false)) {
                    if (h.IsInvalid) { code = Marshal.GetLastWin32Error(); tally(code); Bump(d.Group.Unreadable, Unreadable(code)); example("unreadable_" + Unreadable(code), d.Relative); continue; }
                    if (!GetFileInformationByHandle(h, out info)) { code = Marshal.GetLastWin32Error(); tally(code); Bump(d.Group.Unreadable, "other"); example("unreadable_other", d.Relative); continue; }
                    if (info.VolumeSerial != volume) { d.Group.CrossVolume++; example("cross_volume", d.Relative); continue; }
                    string listStopped;
                    code = List(h, buffer, entries, maxEntries - count, clock, seconds, out listStopped);
                    if (code != 0) {
                        tally(code); Bump(d.Group.Unreadable, Unreadable(code)); example("unreadable_" + Unreadable(code), d.Relative);
                        if (entries.Count == 0) continue;
                    }
                    stopped = listStopped;
                }
                if (entries.Count > 0 || stopped == null) d.Group.Visited++;
                if (stopped != null || code != 0) d.Group.Unvisited++;
                foreach (Entry e in entries) {
                    string relative = names ? d.Relative + @"\" + e.Name : null;
                    if (account(e, d.Group, relative)) { Pending p = new Pending(); p.Path = d.Path + @"\" + e.Name; p.Relative = relative; p.Group = d.Group; pending.Push(p); }
                }
            }
            foreach (Pending p in pending) p.Group.Unvisited++;

            ArrayList rows = new ArrayList();
            foreach (Group g in groups) rows.Add(g.ToTable(names));
            result["groups"] = rows;
            result["entries"] = count;
            result["unidentified_files"] = unidentified;
            result["stopped"] = stopped;
            result["error_codes"] = codes;
            result["other_reparse_tags"] = otherTags;
            result["examples"] = names ? examples : null;
            result["largest_files"] = largest;
        } finally {
            Marshal.FreeHGlobal(buffer);
            result["elapsed_ms"] = clock.ElapsedMilliseconds;
        }
    }

    static Hashtable RootFailure(int code) {
        return new Hashtable { { "status", code == 5 ? "denied" : "failed" }, { "code", code } };
    }
}
"""


if "'" in WALKER:
    raise AssertionError("the walker must stay free of single quotes to travel as a PowerShell literal")


def space_script(seconds: int, max_entries: int, include_names: bool, root: str = "[Environment]::GetFolderPath('UserProfile')") -> str:
    """The collector. ``root`` is a PowerShell expression evaluated on Windows, so no expanded path
    is ever part of the query; scoped readings pass an encoded relative path from a local handle."""
    flag = "$true" if include_names else "$false"
    return (
        "if (-not ('SentinelSpaceV1' -as [type])) { Add-Type -TypeDefinition '" + WALKER + "' }\n"
        f"$root = {root}\n"
        "if (-not $root) { @{ root = @{ status = 'failed'; code = $null } } }\n"
        f"else {{ [SentinelSpaceV1]::Scan($root, {int(seconds)}, {int(max_entries)}, {flag}) }}"
    )


def _scope_root(parts: tuple[str, ...], home: str = "[Environment]::GetFolderPath('UserProfile')") -> str:
    """Encode observed Windows names as data, never as executable PowerShell source."""
    if not parts:
        return home
    relative = "\\".join(parts)
    encoded = base64.b64encode(relative.encode("utf-16le")).decode("ascii")
    return (f"[IO.Path]::Combine(({home}), "
            f"[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{encoded}')))" )


def _num(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("a measure was not a non-negative integer")
    return value


def _counts(value: Any, keys: tuple[str, ...]) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("a coverage counter was missing")
    return {k: _num(value.get(k, 0)) for k in keys}


def _group(raw: dict[str, Any]) -> dict[str, Any]:
    """One top-level row with its own coverage verdict. A skipped row has no measures at all."""
    if not isinstance(raw, dict) or raw.get("kind") not in ("directory", "top_level_files"):
        raise ValueError("a group had an unexpected shape")
    skipped = raw.get("skipped")
    if skipped is not None and skipped not in SKIP_KINDS:
        raise ValueError("a group had an unknown skip reason")
    scope_id = raw.get("scope_id")
    if scope_id is not None and (not isinstance(scope_id, str) or not scope_id):
        raise ValueError("a group had an invalid scope handle")
    file_id = raw.get("file_id_64")
    if file_id is not None and (not isinstance(file_id, str) or len(file_id) != 16 or any(char not in "0123456789ABCDEF" for char in file_id)):
        raise ValueError("a group had an invalid file ID")
    row: dict[str, Any] = {"kind": raw["kind"], "label": raw.get("label"), "name": raw.get("name"), "scope_id": scope_id, "file_id_64": file_id, "skipped": skipped}
    for k in MEASURES:
        row[k] = _num(raw.get(k))
    row["skipped_directories"] = _counts(raw.get("skipped_directories"), SKIP_KINDS)
    row["unreadable_directories"] = _counts(raw.get("unreadable_directories"), UNREADABLE_KINDS)
    row["last_write_age_allocated_bytes"] = _counts(raw.get("last_write_age_allocated_bytes"), AGE_BANDS)
    if sum(row["last_write_age_allocated_bytes"].values()) != row["allocated_bytes"]:
        raise ValueError("a group's last-write bands did not sum to its allocation")
    for k in ("cross_volume", "visited_directories", "unvisited_directories"):
        row[k] = _num(raw.get(k))
    row["status"] = _status(row)
    if row["status"] in ("skipped", "not_scanned", "not_listed"):
        # Nothing of the row was listed: a zero would read as a measurement.
        for k in MEASURES:
            row[k] = None
        row["last_write_age_allocated_bytes"] = None
    return row


def _status(row: dict[str, Any]) -> str:
    """``complete`` means every directory of the row was listed and nothing that might hold local data
    was skipped. Links and mount points name content elsewhere, so skipping them leaves it complete.
    A row whose own folder was reached but not listed (refused, vanished, another volume) is ``not_listed``."""
    if row["skipped"]:
        return "skipped"
    if not row["visited_directories"]:
        return "not_scanned" if row["unvisited_directories"] else "not_listed"
    gaps = row["unvisited_directories"] + sum(row["unreadable_directories"].values()) + row["cross_volume"] + sum(row["skipped_directories"][k] for k in GAP_SKIPS)
    return "partial" if gaps else "complete"


def _fold(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Largest measured rows first, skipped rows after; beyond :data:`MAX_GROUPS` one summed row."""
    rows = sorted(rows, key=lambda r: (r["allocated_bytes"] is None, -(r["allocated_bytes"] or 0)))
    if len(rows) <= MAX_GROUPS:
        return rows
    kept, rest = rows[: MAX_GROUPS - 1], rows[MAX_GROUPS - 1 :]
    other: dict[str, Any] = {"kind": "other_groups", "label": None, "name": None, "skipped": None, "groups": len(rest)}
    unmeasured = all(r["allocated_bytes"] is None for r in rest)
    for k in MEASURES:
        other[k] = None if unmeasured else sum(r[k] or 0 for r in rest)
    other["skipped_directories"] = {k: sum(r["skipped_directories"][k] for r in rest) for k in SKIP_KINDS}
    other["unreadable_directories"] = {k: sum(r["unreadable_directories"][k] for r in rest) for k in UNREADABLE_KINDS}
    other["last_write_age_allocated_bytes"] = None if unmeasured else {k: sum((r["last_write_age_allocated_bytes"] or {}).get(k, 0) for r in rest) for k in AGE_BANDS}
    for k in ("cross_volume", "visited_directories", "unvisited_directories"):
        other[k] = sum(r[k] for r in rest)
    other["status"] = "complete" if all(r["status"] == "complete" for r in rest) else "partial"
    return [*kept, other]


def _largest(raw: Any, include_names: bool) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("largest files had an unexpected shape")
    files: list[dict[str, Any]] = []
    for item in raw[:MAX_LARGEST]:
        if not isinstance(item, dict):
            raise ValueError("a largest file had an unexpected shape")
        row: dict[str, Any] = {
            "allocated_bytes": _num(item.get("allocated_bytes")),
            "logical_bytes": _num(item.get("logical_bytes")),
            "name": item.get("name") if include_names else None,
            "relative_path": item.get("relative_path") if include_names else None,
            "group_label": item.get("group_label"),
            "group_name": item.get("group_name") if include_names else None,
            "file_id": item.get("file_id"),
            "last_write_utc": item.get("last_write_utc"),
        }
        for key in ("placeholder", "compressed_or_sparse", "reparse_file"):
            if type(item.get(key)) is not bool:
                raise ValueError(f"a largest file had an invalid {key}")
            row[key] = item[key]
        files.append(row)
    files.sort(key=lambda row: -row["allocated_bytes"])
    for rank, row in enumerate(files, 1):
        row["rank"] = rank
    return files


def build(payload: dict[str, Any], include_names: bool) -> tuple[list[Section], dict[str, Any]]:
    """The walk's payload as sections, and the facts the envelope needs: the outcome, warnings and count."""
    rows = [_group(g) for g in payload.get("groups") or []]
    if not include_names:
        for r in rows:
            r["name"] = None
    totals = {k: sum(r[k] or 0 for r in rows) for k in MEASURES}
    totals["last_write_age_allocated_bytes"] = {k: sum((r["last_write_age_allocated_bytes"] or {}).get(k, 0) for r in rows) for k in AGE_BANDS}
    skipped = {k: sum(r["skipped_directories"][k] for r in rows) for k in SKIP_KINDS}
    unreadable = {k: sum(r["unreadable_directories"][k] for r in rows) for k in UNREADABLE_KINDS}
    unvisited = sum(r["unvisited_directories"] for r in rows)
    stopped = payload.get("stopped")
    if stopped not in (None, "time_limit", "entry_limit"):
        raise ValueError("the walk reported an unknown stop")
    complete = stopped is None and all(r["status"] == "complete" for r in rows)
    totals["complete"] = complete
    totals["lower_bound"] = not complete

    warnings: list[str] = []
    if stopped:
        limit = "time" if stopped == "time_limit" else "entry"
        warnings.append(f"The walk stopped at its {limit} limit with {unvisited} directories not listed; every total is a lower bound.")
    gap_skips = sum(skipped[k] for k in GAP_SKIPS)
    if sum(unreadable.values()) or gap_skips:
        warnings.append(f"{sum(unreadable.values())} directories could not be listed and {gap_skips} were skipped as cloud or unrecognized reparse points; the totals exclude whatever they hold.")
    if not payload.get("placeholders_exposed"):
        warnings.append("Windows did not let this walk expose cloud placeholders, so a cloud directory may have looked local; placeholder counts may be low.")
    if payload.get("volume") is None:
        warnings.append("The volume's capacity could not be read; the reconciliation is unknown.")

    volume = payload.get("volume")
    reconciliation: dict[str, Any] = {"volume_used_bytes": None, "scan_allocated_bytes": totals["allocated_bytes"], "scan_lower_bound": not complete, "not_attributed_bytes": None}
    if isinstance(volume, dict):
        used = _num(volume.get("total_bytes")) - _num(volume.get("free_bytes"))
        reconciliation["volume_used_bytes"] = used
        remainder = used - totals["allocated_bytes"]
        if remainder >= 0:
            reconciliation["not_attributed_bytes"] = remainder
        else:
            warnings.append("The walk attributed more allocation than the volume reports in use; the remainder is left unknown rather than negative.")

    coverage = {
        "complete": complete,
        "stopped": stopped,
        "unvisited_directories": unvisited,
        "skipped_directories": skipped,
        "unreadable_directories": unreadable,
        "cross_volume_directories": sum(r["cross_volume"] for r in rows),
        "link_repeats": totals["link_repeats"],
        "unidentified_files": _num(payload.get("unidentified_files", 0)),
    }
    collection = {
        "root": "home",
        "volume": volume,
        "root_is_reparse_point": bool((payload.get("root") or {}).get("is_reparse_point")),
        "volume_serial": (payload.get("root") or {}).get("volume_serial"),
        "root_file_id_64": (payload.get("root") or {}).get("file_id_64"),
        "placeholders_exposed": bool(payload.get("placeholders_exposed")),
        "limits": payload.get("limits"),
        "started_utc": payload.get("started_utc"),
        "finished_utc": payload.get("finished_utc"),
        "elapsed_ms": payload.get("elapsed_ms"),
        "entries": payload.get("entries"),
        "error_codes": payload.get("error_codes") or {},
        "other_reparse_tags": payload.get("other_reparse_tags") or {},
    }
    sections = [
        Section("totals", "derived", totals, basis=(
            "Sums of each file's end-of-file length (logical_bytes) and of the allocation the file system reports for it (allocated_bytes), "
            "as listed by its directory; a file ID seen twice within the walk is counted once and tallied in link_repeats. "
            "Alternate data streams, directory indexes and file-system metadata are not included. Neither figure says what deleting anything would free: "
            "a hard link outside the walk keeps its data, and NTFS may list a hard-linked file's size as of its last update through another name.")),
        Section("groups", "derived", _fold(rows), basis=(
            "The home folder's top-level directories, plus one row for files directly inside it, with the same measures and each row's coverage. "
            "A hard-linked file is attributed to the row where the walk met it first. A skipped, not_scanned or not_listed row has no measures.")),
        Section("largest_files", "raw", _largest(payload.get("largest_files") or [], include_names)),
        Section("coverage", "derived", coverage, basis=(
            "complete is true only if the walk finished and every directory that might hold local data was listed. Mount points and symbolic links are counted and never followed; "
            "cloud and unrecognized reparse directories are skipped, and they, unreadable directories and directories on another volume leave the totals as lower bounds.")),
        Section("reconciliation", "derived", reconciliation, basis=(
            "The volume's used space (total minus free, as Windows reports it) less the walk's allocation. The remainder lies outside the selected folder, "
            "in parts of it the walk skipped or could not list, or in file-system metadata. It is not a folder and not reclaimable space.")),
        Section("collection", "raw", collection),
    ]
    if include_names:
        sections.append(Section("examples", "raw", payload.get("examples") or []))
    empty = complete and totals["files"] == 0 and totals["directories"] == 0 and not any(r["kind"] == "directory" for r in rows)
    return sections, {"warnings": warnings, "empty": empty}


def take_space(bridge: Bridge, params: dict[str, Any]) -> Reading:
    seconds, max_entries, include_names = params["seconds"], params["max_entries"], params["include_names"]
    scope_id = params.get("scope_id", "")
    parts = _resolve_scope(scope_id)
    if parts is None:
        return Reading(reading="space", params=params, outcome="failed", method={"kind": "powershell", "query": "scoped space walk"},
                       error={"kind": "failed", "detail": "This space scope expired or is unknown. Scan Home again to get a fresh navigation handle."})
    script = space_script(seconds, max_entries, include_names, root=_scope_root(parts))
    # The generated PowerShell contains the compiled walker (and, for a child, its encoded
    # relative path). Return a concise method reference instead of megabytes of source or a
    # reversible path token in every API/MCP response.
    method = {"kind": "powershell", "source": "sentinel/readings/space.py:SentinelSpaceV1",
              "query": "GetFileInformationByHandleEx(FileIdBothDirectoryInfo) bounded directory walk",
              "scope": "selected_handle" if scope_id else "home"}
    result = bridge.run(script, timeout=seconds + TIMEOUT_MARGIN, depth=8)
    facts: dict[str, Any] = {}

    def sections(payload: dict[str, Any]) -> list[Section]:
        root = payload.get("root")
        if not isinstance(root, dict) or root.get("status") != "ok":
            facts["root"] = root if isinstance(root, dict) else {"status": "failed", "code": None}
            return []
        built, facts["envelope"] = build(payload, True)
        full_rows = [_group(raw) for raw in payload.get("groups") or []]
        full_rows.sort(key=lambda row: (row["allocated_bytes"] is None, -(row["allocated_bytes"] or 0)))
        for group in full_rows[:MAX_GROUPS if len(full_rows) <= MAX_GROUPS else MAX_GROUPS - 1]:
            name = group.get("name")
            if group["kind"] == "directory" and group["skipped"] is None and isinstance(name, str) and _valid_component(name):
                group["scope_id"] = _issue_scope(parts + (name,))
        groups = next(section for section in built if section.name == "groups")
        groups.data = _fold(full_rows)
        scan_id = secrets.token_urlsafe(12)
        collection = next(section for section in built if section.name == "collection")
        collection.data["root"] = "selected_scope" if scope_id else "home"
        collection.data["scope_id"] = scope_id or None
        collection.data["scope_depth"] = len(parts)
        collection.data["parent_comparable"] = not bool(scope_id)
        collection.data["scan_id"] = scan_id
        scan_complete = next(section for section in built if section.name == "coverage").data["complete"]
        page_context = {**collection.data, "coverage_complete": scan_complete, "stopped": payload.get("stopped"), "_parts": parts}
        next_page = _issue_page(scan_id, full_rows, MAX_GROUPS - 1, page_context) if len(full_rows) > MAX_GROUPS else None
        built.append(Section("pagination", "derived", {
            "scan_id": scan_id, "total_groups": len(full_rows), "returned_groups": len(groups.data),
            "folded_groups": max(0, len(full_rows) - (MAX_GROUPS - 1)), "next_page_id": next_page,
            "scan_complete": scan_complete,
        }, basis="The displayed rows and the opaque continuation refer to this walk's one returned group list; a continuation reads retained rows without walking Windows again."))
        for group in groups.data:
            if not include_names:
                group["name"] = None
        if not include_names:
            largest = next(section for section in built if section.name == "largest_files")
            for file in largest.data:
                file["name"] = file["relative_path"] = file["group_name"] = None
            built = [section for section in built if section.name != "examples"]
        return built

    try:
        reading = from_object("space", params, script, result, sections)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return Reading(reading="space", params=params, outcome="failed", method=method, took_ms=result.took_ms,
                       error={"kind": "failed", "detail": f"the walk's answer had an unexpected shape: {exc}"})
    reading.method = method
    if not reading.observed:
        if scope_id and reading.error:
            reading.error = {"kind": reading.error.get("kind", reading.outcome), "detail": "The selected scope did not answer; scan Home again or retry this folder."}
        return reading
    if "root" in facts:
        status = facts["root"].get("status")
        reading.outcome = "denied" if status == "denied" else "failed"
        reason = facts["root"].get("reason")
        detail = "The selected folder became a reparse point; scan Home again." if reason == "root_reparse_point" else f"the selected folder could not be listed (Windows error {facts['root'].get('code')})"
        reading.error = {"kind": reading.outcome, "detail": detail}
        return reading
    envelope = facts["envelope"]
    reading.warnings.extend(envelope["warnings"])
    if scope_id:
        reading.warnings.append("This scope is a fresh, independent walk. Hardlinks are deduplicated only inside it, so its total may differ from the folder's earlier parent row; separate takes are not growth measurements.")
    reading.outcome = "empty" if envelope["empty"] else "ok"
    groups = reading.section("groups")
    assert groups is not None
    reading.count = 0 if envelope["empty"] else len(groups.data)
    return reading


def take_space_page(_bridge: Bridge, params: dict[str, Any]) -> Reading:
    """Continue a returned group list from local memory, without another filesystem walk."""
    page = _resolve_page(params["page_id"])
    method = {"kind": "cache", "source": "space scan group list", "query": "opaque page continuation"}
    if page is None:
        return Reading(reading="space_page", params=params, outcome="failed", method=method,
                       error={"kind": "failed", "detail": "This space page expired or is unknown. Scan its folder again for a fresh continuation."})
    scan_id, full_rows, offset, collection = page
    end = min(len(full_rows), offset + MAX_GROUPS)
    rows = [{**row, "name": row["name"] if params["include_names"] else None} for row in full_rows[offset:end]]
    parts = collection["_parts"]
    for row, original in zip(rows, full_rows[offset:end], strict=True):
        name = original.get("name")
        if row["kind"] == "directory" and row["skipped"] is None and isinstance(name, str) and _valid_component(name):
            row["scope_id"] = _issue_scope(parts + (name,))
    next_page = _issue_page(scan_id, full_rows, end, collection) if end < len(full_rows) else None
    return Reading(reading="space_page", params=params, outcome="ok", method=method, count=len(rows),
        warnings=[] if collection.get("coverage_complete") else ["These rows came from an incomplete walk; measured figures are lower bounds and other folders may be unlisted."], sections=[
        Section("groups", "derived", rows, basis="Exact rows held from the cited space scan, in the same allocated-byte order; this page did not walk Windows again."),
        Section("pagination", "derived", {"scan_id": scan_id, "offset": offset, "total_groups": len(full_rows),
            "returned_groups": len(rows), "next_page_id": next_page,
            "scan_complete": collection.get("coverage_complete"), "stopped": collection.get("stopped")},
            basis="The opaque continuation refers to remaining groups from the same scan, retained in this server process for at most 30 minutes."),
        Section("collection", "raw", {"scan_id": scan_id, "started_utc": collection.get("started_utc"),
            "finished_utc": collection.get("finished_utc"), "scope_depth": collection.get("scope_depth"),
            "parent_comparable": collection.get("parent_comparable"), "limits": collection.get("limits")}),
    ])


register(Spec(
    name="space",
    description=(
        "Where an observed folder's space is: one bounded, read-only walk on request, starting at Home and following opaque scope_id handles to children, with logical and allocated bytes and last-write age bands per immediate folder, plus up to 20 largest observed files, "
        "the volume's used and free space, and what the walk could not reach. An unfinished walk returns lower bounds, never a complete total; "
        "no measure is reclaimable space. Names only with include_names; the home-folder path never."
    ),
    classes=("raw", "derived"),
    take=take_space,
    params=(
        Param("seconds", "int", 30, "Stop the walk after this many seconds; totals are then lower bounds.", minimum=1, maximum=300),
        Param("max_entries", "int", 1_000_000, "Stop after this many entries, even within a directory; totals are then lower bounds.", minimum=1_000, maximum=5_000_000),
        Param("include_names", "bool", False, "Return top-level folder names and up to 20 relative paths of skipped or unreadable directories. Absolute paths are never returned."),
        Param("scope_id", "str", "", "Opaque, short-lived handle returned on a child directory by a prior space reading; blank scans Home."),
    ),
    private=("groups[].name, largest_files[].name/relative_path/group_name, and examples[].path, only with include_names",),
    heavy=True,
    # Not an exact selection: a walk of the home folder is a deliberate request, never a side effect
    # of a capture or a bench run. The flag is what keeps it out of both.
    requires_selection=True,
))

register(Spec(
    name="space_page",
    description="Continue the exact folder list of a prior space walk by its opaque page_id, without rescanning Windows. Pages expire after 30 minutes or process restart.",
    classes=("raw", "derived"), take=take_space_page,
    params=(
        Param("page_id", "str", None, "Opaque next_page_id from a space or space_page reading."),
        Param("include_names", "bool", False, "Return the names of these held folder rows; names are omitted by default."),
    ),
    private=("groups[].name, only with include_names",),
    requires_selection=True,
))
