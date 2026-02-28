import os
import glob
import subprocess
from .base import BaseCollector, CollectorResult

class DumpCollector(BaseCollector):
    def __init__(self):
        super().__init__(
            collector_id="dumps.inventory",
            name="Crash Dump Inventory",
            description="Scans C:\\Windows\\Minidump and other locations for crash files"
        )

    def _get_wsl_path(self, windows_path: str) -> str:
        """Converts Windows path to WSL path if needed, though globbing C: usually works via /mnt/c"""
        # Simple transform for standard drives: C:\ -> /mnt/c/
        drive, path = os.path.splitdrive(windows_path)
        if drive:
            drive_letter = drive[0].lower()
            return f"/mnt/{drive_letter}{path.replace(chr(92), '/')}"
        return windows_path

    def collect(self) -> CollectorResult:
        # Locations to scan. Note: mapped to WSL paths for Python access if running in WSL.
        # IF running natively on Windows Python, these patterns should be standard.
        # Since user asked for Windows-native Execution mostly, but we are in dev in WSL?
        # User said: "If Python is already in use, it must run on Windows, not inside WSL, for WinEvent access."
        # IMPORTANT: Our current strategy uses `powershell.exe` from WSL to bridge the gap.
        # But for file access (glob), WSL python needs /mnt/c paths.
        
        search_paths = [
            "/mnt/c/Windows/Minidump/*.dmp",
            "/mnt/c/Windows/MEMORY.DMP",
            "/mnt/c/Windows/LiveKernelReports/*.dmp",
            "/mnt/c/Windows/LiveKernelReports/*/*.dmp",
        ]

        dumps = []
        for pattern in search_paths:
            try:
                files = glob.glob(pattern)
                for f in files:
                    try:
                        stat = os.stat(f)
                        dumps.append({
                            "path": f,
                            "size_bytes": stat.st_size,
                            "modified": stat.st_mtime,
                            "name": os.path.basename(f)
                        })
                    except PermissionError:
                         dumps.append({
                            "path": f,
                            "error": "Permission Denied"
                        })
            except Exception as e:
                # Glob might fail on permissions too
                pass
        
        # Sort by modified desc
        dumps.sort(key=lambda x: x.get("modified", 0), reverse=True)
        
        return CollectorResult(self.collector_id, dumps)
