# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH)
icon_file = project_root / "mtyh" / "resources" / "text formater.ico"
batch_adapter = project_root / "mtyh" / "synthesis" / "hst_batch.py"

a = Analysis(
    ["mtyh\\__main__.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(icon_file), "mtyh\\resources"),
        (str(batch_adapter), "mtyh\\synthesis"),
    ],
    hiddenimports=["keyboard", "pyautogui"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Qt deliberately imports Windows' system ICU shim.  A Poppler runtime can put
# incompatible generic ICU DLLs on PATH during collection; bundling those makes
# QtGui fail at startup with "procedure could not be found".
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.casefold() not in {"icuuc.dll", "icudt78.dll"}
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MTYH",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file),
)
