from pathlib import Path

from backend.runtime import LaunchMode, build_runtime


def test_browser_uses_source_resources_and_configured_database(tmp_path):
    runtime = build_runtime(
        LaunchMode.BROWSER,
        platform_name="linux",
        home=tmp_path,
        browser_database_path=tmp_path / "custom/lots.db",
    )
    assert runtime.frontend_dir.name == "frontend"
    assert runtime.data_dir == tmp_path / "custom"
    assert runtime.database_path == tmp_path / "custom/lots.db"
    assert runtime.desktop is False


def test_source_desktop_uses_linux_xdg_data(monkeypatch, tmp_path):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    runtime = build_runtime(
        LaunchMode.SOURCE_DESKTOP, platform_name="linux", home=tmp_path
    )
    assert runtime.data_dir == xdg / "ibkr-lot-tracker"
    assert runtime.database_path == runtime.data_dir / "lots.db"


def test_packaged_platform_data_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    mac = build_runtime(
        LaunchMode.PACKAGED_DESKTOP, platform_name="darwin", home=tmp_path
    )
    win = build_runtime(
        LaunchMode.PACKAGED_DESKTOP, platform_name="win32", home=tmp_path
    )
    linux = build_runtime(
        LaunchMode.PACKAGED_DESKTOP, platform_name="linux", home=tmp_path
    )
    assert mac.data_dir == tmp_path / "Library/Application Support/IBKR Lot Tracker"
    assert win.data_dir == tmp_path / "Local/IBKR Lot Tracker"
    assert linux.data_dir == tmp_path / ".local/share/ibkr-lot-tracker"


def test_frozen_resources_use_meipass(tmp_path):
    runtime = build_runtime(
        LaunchMode.PACKAGED_DESKTOP,
        platform_name="darwin",
        home=tmp_path,
        frozen_root=tmp_path / "_MEI",
    )
    assert runtime.frontend_dir == tmp_path / "_MEI/frontend"
