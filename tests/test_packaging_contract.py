from pathlib import Path

ROOT = Path(__file__).parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_pyinstaller_spec_targets_desktop_entry_and_bundles_assets():
    spec = _read("packaging/ibkr_lot_tracker.spec")
    assert "run_desktop.py" in spec
    assert "frontend" in spec
    # pywebview ships GTK/Qt/share resources that must be collected
    assert "collect_data_files" in spec or "webview" in spec
    assert "backend/version.py" in spec or "backend" in spec
    assert "IBKR Lot Tracker" in spec


def test_windows_installer_is_per_user_and_no_elevation():
    iss = _read("packaging/windows/installer.iss")
    assert "PrivilegesRequired=lowest" in iss
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in iss
    assert "{localappdata}" in iss


def test_appimage_script_invokes_appimagetool():
    script = _read("packaging/linux/build-appimage.sh")
    assert "appimagetool" in script


def test_packaged_smoke_script_requires_smoke_ok():
    smoke = _read("scripts/packaged_smoke.py")
    assert "--smoke-test" in smoke
    assert "SMOKE_OK" in smoke
