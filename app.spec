# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — produces a single .exe (Windows)
# Build: pyinstaller app.spec   (must run on Windows, not WSL)

import os

# JSON config/data files that must travel with the exe.
# match_aliases.json is written at runtime if it doesn't exist, but bundling the
# current copy means the shipped renames carry over into the freshly built exe.
# ctr_presets.json is deliberately NOT bundled: a preset is a person's own
# client/location/path, and the one that used to ship was a developer preset
# (with a Linux path) that came back on every "Reset presets".
_DATA_FILES = [
    ("ctr_tools/template_config.json", "ctr_tools"),
    ("VERSION", "."),
]
for _fname in ("match_aliases.json",):
    _src = os.path.join("ctr_tools", _fname)
    if os.path.exists(_src):
        _DATA_FILES.append((_src, "ctr_tools"))

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
        "PySide6.QtCharts",   # F12 Inspector charts (ctr_tools/inspector.py)
        "ctr_tools.presets",
        "ctr_tools.config",
        # CTR Tracker's "Add as separate revision" row-insertion path
        # (ctr_tools/tracker_xlwings.py) drives real Excel via COM —
        # xlwings and its pywin32 dependencies aren't otherwise reachable
        # from a static import scan on a machine without them installed,
        # so list them explicitly rather than relying on PyInstaller's
        # own xlwings hook always being present/complete.
        "xlwings",
        "win32com",
        "win32com.client",
        "pythoncom",
        "pywintypes",
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
