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


def test_windows_installer_is_per_user_and_no_elevation():
    iss = _read("packaging/windows/installer.iss")
    assert "PrivilegesRequired=lowest" in iss
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in iss
    assert "{localappdata}" in iss
    assert "SetupIconFile" in iss
    assert "icon.ico" in iss


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
