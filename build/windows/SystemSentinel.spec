# -*- mode: python ; coding: utf-8 -*-
"""One windowed file: dist\\SystemSentinel.exe.

The package, the built dashboard and the CPER decoder go in under ``sentinel/`` so that the
package's own relative resolution finds them inside the bundle exactly as it finds them on disk:
``sentinel/app.py`` looks for ``static`` beside itself, ``sentinel/readings/health.py`` looks for
``tools/DecodeWheaRecord`` beside the package. PyInstaller sets each frozen module's ``__file__``
under ``sys._MEIPASS``, so both resolve without the code knowing it was frozen.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

HERE = Path(SPECPATH)  # noqa: F821 - PyInstaller injects SPECPATH
ROOT = HERE.parent.parent

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
)
