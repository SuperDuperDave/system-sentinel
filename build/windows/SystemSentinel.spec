# -*- mode: python ; coding: utf-8 -*-
"""One windowed file: dist\\SystemSentinel.exe, stamped with the version it was built from.

The package, the built dashboard and the CPER decoder go in under ``sentinel/`` so that the
package's own relative resolution finds them inside the bundle exactly as it finds them on disk:
``sentinel/app.py`` looks for ``static`` beside itself, ``sentinel/readings/health.py`` looks for
``tools/DecodeWheaRecord`` beside the package. PyInstaller sets each frozen module's ``__file__``
under ``sys._MEIPASS``, so both resolve without the code knowing it was frozen.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

HERE = Path(SPECPATH)  # noqa: F821 - PyInstaller injects SPECPATH
ROOT = HERE.parent.parent

# The version resource is written here rather than kept as a file in the repository, so what
# Windows shows in the file's properties is the version this build was made from and cannot be
# left behind at an older one. A copy deciding whether it is an update reads it back.
sys.path.insert(0, str(HERE))
from make_version_file import write_version_file  # noqa: E402

VERSION_FILE = write_version_file(HERE / "version.txt")

datas = [
    (str(ROOT / "sentinel" / "static"), "sentinel/static"),
    (str(ROOT / "sentinel" / "tools" / "DecodeWheaRecord"), "sentinel/tools/DecodeWheaRecord"),
]

# uvicorn resolves its loop, protocol and lifespan classes by name at runtime, so static analysis
# does not see them. The websocket protocol is absent on purpose: the launcher serves with ws="none".
hiddenimports = collect_submodules("sentinel") + [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

a = Analysis(  # noqa: F821
    [str(HERE / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "PIL.ImageTk"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SystemSentinel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=str(HERE / "SystemSentinel.ico"),
    version=str(VERSION_FILE),
)
