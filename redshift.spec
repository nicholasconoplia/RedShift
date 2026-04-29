# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys


ROOT = Path(SPECPATH)
APP_NAME = "RedShift"
APP_VERSION = "1.0.0"
ICON_PATH = ROOT / "assets" / ("redshift.icns" if sys.platform == "darwin" else "redshift.ico")

hiddenimports = []
if sys.platform == "darwin":
    hiddenimports.extend(
        [
            "pystray._darwin",
            "PIL._tkinter_finder",
        ]
    )
elif sys.platform.startswith("win"):
    hiddenimports.extend(
        [
            "pystray._win32",
            "PIL._tkinter_finder",
        ]
    )

a = Analysis(
    ["redshift.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "numpy",
        "charset_normalizer",
        "matplotlib",
        "IPython",
        "jedi",
        "pandas",
        "scipy",
        "pytest",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch="universal2",
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ICON_PATH)],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ICON_PATH),
        bundle_identifier="com.redshift.app",
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": "com.redshift.app",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
else:
    pass
