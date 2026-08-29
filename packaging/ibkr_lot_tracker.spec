# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the IBKR Lot Tracker desktop application.

Builds platform-specific packages from the `run_desktop.py` entry point,
bundling the shared FastAPI backend, the single-file frontend, and the
pywebview runtime resources. Output binaries differ by platform:

- macOS: an ``IBKR Lot Tracker.app`` bundle (identifier com.ibkrlottracker.app)
- Windows: a GUI executable (no console window)
- Linux: a console-enabled executable consumed by the AppImage assembly
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Resolve paths relative to this spec file's directory (packaging/), so the
# build works regardless of the directory PyInstaller is invoked from.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# Application icon, in the format native to the target platform. macOS uses a
# .icns on the app bundle; Windows embeds a .ico in the executable; Linux is
# handled by the AppImage .desktop entry + PNG (see packaging/linux/).
APP_ICON = os.path.join(
    ROOT, "assets", "icon.icns" if sys.platform == "darwin" else "icon.ico"
)

datas = [(os.path.join(ROOT, "frontend"), "frontend")]
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
]

# Bundle the entire backend package, including backend/version.py (the
# embedded app version and update key) and backend/update_key.py.
hiddenimports += collect_submodules("backend")

# pywebview ships platform resources (Qt/GTK assets) that must be collected.
webview_datas, webview_binaries, webview_hidden = collect_all("webview")
datas += webview_datas
binaries += webview_binaries
hiddenimports += webview_hidden

a = Analysis(
    [os.path.join(ROOT, "run_desktop.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

APP_NAME = "IBKR Lot Tracker"

if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
    )
    app = BUNDLE(
        exe,
        name="IBKR Lot Tracker.app",
        icon=APP_ICON,
        bundle_identifier="com.ibkrlottracker.app",
        info_plist={
            "CFBundleDisplayName": "IBKR Lot Tracker",
            "NSHighResolutionCapable": True,
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        # Linux keeps a console so packaged smoke output is captured; Windows
        # builds a GUI subsystem binary with no console window.
        console=(sys.platform != "win32"),
        icon=APP_ICON,
    )
