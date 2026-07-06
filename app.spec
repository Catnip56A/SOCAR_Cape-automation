# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — produces a single .exe (Windows)
# Build: pyinstaller app.spec   (must run on Windows, not WSL)

import os

# JSON config/data files that must travel with the exe.
# match_aliases.json and ctr_presets.json are written at runtime if they
# don't exist, but bundling any current copy means user renames/presets
# carry over into the freshly built exe automatically.
_DATA_FILES = [
    ("ctr_generator/template_config.json", "ctr_generator"),
]
for _fname in ("match_aliases.json", "ctr_presets.json"):
    _src = os.path.join("ctr_generator", _fname)
    if os.path.exists(_src):
        _DATA_FILES.append((_src, "ctr_generator"))

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=_DATA_FILES,
    hiddenimports=[
        "openpyxl",
        "openpyxl.cell._writer",
        "openpyxl.styles.stylesheet",
        "pandas",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "ctr_generator.presets",
        "ctr_generator.config",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "streamlit",
        "matplotlib",
        "scipy",
        "tkinter",
        "unittest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Commercial-automation",
    debug=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # no black console window behind the app
    windowed=True,
    icon="app_icon.ico",
)
