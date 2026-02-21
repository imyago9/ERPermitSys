# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH).resolve().parents[1]
SRC = ROOT / "src"
RUN = ROOT / "run.py"
SETTINGS_DIALOG = SRC / "erpermitsys" / "ui" / "settings_dialog" / "__init__.py"

if not RUN.is_file():
    raise SystemExit(f"PyInstaller spec root resolution failed: missing {RUN}")
if not SETTINGS_DIALOG.is_file():
    raise SystemExit(f"Required module source is missing: {SETTINGS_DIALOG}")

# Ensure hook helpers can import our package when collecting modules/data.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SRC))

datas = []
datas += collect_data_files("erpermitsys", include_py_files=False)
datas += [(str(ROOT / "assets"), "assets")]
datas += [(str(ROOT / "plugins"), "plugins")]
if (ROOT / "config").is_dir():
    datas += [(str(ROOT / "config"), "config")]

hiddenimports = collect_submodules("erpermitsys")
if "erpermitsys.ui.settings_dialog" not in hiddenimports:
    hiddenimports.append("erpermitsys.ui.settings_dialog")


a = Analysis(
    [str(RUN)],
    pathex=[str(ROOT), str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Force-include settings dialog from source path. This avoids CI variability in
# submodule discovery and guarantees startup import availability.
if not any(entry[0] == "erpermitsys.ui.settings_dialog" for entry in a.pure):
    a.pure += TOC(
        [
            (
                "erpermitsys.ui.settings_dialog",
                str(SETTINGS_DIALOG),
                "PYMODULE",
            )
        ]
    )
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="erpermitsys",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="erpermitsys",
)
