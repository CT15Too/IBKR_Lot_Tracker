from pathlib import Path

ROOT = Path(__file__).parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_pyinstaller_spec_targets_desktop_entry_and_bundles_assets():
    spec = _read("packaging/ibkr_lot_tracker.spec")
    assert "run_desktop.py" in spec
    assert "frontend" in spec
    # pywebview ships GTK/Qt/share resources that must be collected
    assert "collect_data_files" in spec or "webview" in spec
    assert "backend/version.py" in spec or "backend" in spec
    assert "IBKR Lot Tracker" in spec
    # The entry script and assets must be resolved relative to the repo root
    # (SPECPATH), not as bare paths relative to the spec's packaging/ dir.
    assert "SPECPATH" in spec
    assert '["run_desktop.py"]' not in spec
    # The app icon is wired per-platform: icns on macOS, ico elsewhere.
    assert "APP_ICON" in spec
    assert "icon.icns" in spec
    assert "icon.ico" in spec


def test_macos_bundle_collects_python_library_and_assets():
    spec = _read("packaging/ibkr_lot_tracker.spec")
    # The macOS .app must be assembled via COLLECT(exe, a.binaries, a.datas, ...)
    # before BUNDLE wraps it. Bundling `exe` alone — built with
    # exclude_binaries=True — drops a.binaries (Python shared library +
    # extension modules) and a.datas (frontend/webview resources), leaving the
    # bundle without Contents/Frameworks/Python. The bootloader then fails at
    # launch with "Failed to load Python shared library".
    assert "COLLECT(" in spec
    assert "a.binaries" in spec
    assert "a.datas" in spec
    assert "BUNDLE(" in spec
    assert "exclude_binaries=True" in spec


def test_macos_bundle_is_foreground_gui_app_not_background_only():
    spec = _read("packaging/ibkr_lot_tracker.spec")
    # The macOS desktop app must be a foreground GUI app. If the macOS EXE is
    # built with console=True, PyInstaller emits LSBackgroundOnly=true into the
    # .app Info.plist (osx.py BUNDLE: "Setting EXE console=True implies
    # LSBackgroundOnly=True"), which hides the Dock icon and every window. The
    # backend starts and the WKWebView still loads the page, so no error is
    # raised — the app just appears to "do nothing". console=True must never
    # appear in the spec: Windows/Linux use the conditional console=False for
    # win32, and macOS is console=False too.
    assert "console=True" not in spec
    assert "console=False" in spec


def test_windows_linux_build_onedir_for_packaging_scripts():
    spec = _read("packaging/ibkr_lot_tracker.spec")
    # installer.iss (Source: dist\IBKR Lot Tracker\*) and build-appimage.sh
    # (test -d dist/IBKR Lot Tracker) both consume the one-folder layout, as
    # does the packaged smoke test. Every branch must therefore build with
    # exclude_binaries=True and assemble via COLLECT — a onefile EXE (passing
    # a.binaries/a.datas straight to EXE) yields a single file that none of the
    # scripts can locate.
    assert spec.count("exclude_binaries=True") >= 2
    assert spec.count("COLLECT(") >= 2


def test_windows_installer_is_per_user_and_no_elevation():
    iss = _read("packaging/windows/installer.iss")
    assert "PrivilegesRequired=lowest" in iss
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in iss
    assert "{localappdata}" in iss
    assert "SetupIconFile" in iss
    assert "icon.ico" in iss
    # Source files and installer output must be repo-root relative (via ..\..\),
    # because Inno Setup resolves bare "dist\..." against the script's own
    # packaging/windows/ directory — not the PyInstaller output at the repo root.
    assert r"..\..\dist\{#MyAppName}\*" in iss
    assert r"OutputDir=..\..\dist\installer" in iss


def test_appimage_script_invokes_appimagetool():
    script = _read("packaging/linux/build-appimage.sh")
    assert "appimagetool" in script
    # Ships the real icon and references it from the .desktop entry (no more
    # placeholder PNG).
    assert "assets/icon.png" in script
    assert "Icon=$APP_NAME" in script
    assert "Placeholder" not in script


def test_packaged_smoke_script_requires_smoke_ok():
    smoke = _read("scripts/packaged_smoke.py")
    assert "--smoke-test" in smoke
    assert "SMOKE_OK" in smoke
