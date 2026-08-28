from pathlib import Path

ROOT = Path(__file__).parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_readme_documents_launch_modes_and_desktop_flow():
    readme = _read("README.md")
    for launch in ["python3 run.py", "python3 run_desktop.py"]:
        assert launch in readme
    # Desktop users install a package rather than cloning source.
    assert "downloaded" in readme or "installed" in readme
    # First-launch Settings, persistence paths, update approval/defer, and tests.
    for required in [
        "Settings",
        "Application Support",
        "IBKR Lot Tracker",
        "up to date",
        "Defer",
        "python3 -m pytest",
    ]:
        assert required in readme
    # Browser/source mode updates through Git, not the in-app updater.
    assert "Git" in readme


def test_env_example_is_browser_development_only():
    env = _read(".env.example")
    assert "browser" in env.lower() or "development" in env.lower()


def test_release_docs_name_secrets_and_operations():
    doc = _read("docs/desktop-release.md")
    for secret in [
        "UPDATE_MANIFEST_PRIVATE_KEY",
        "MACOS_CERTIFICATE_P12",
        "MACOS_CERTIFICATE_PASSWORD",
        "MACOS_SIGNING_IDENTITY",
        "APPLE_ID",
        "APPLE_APP_PASSWORD",
        "APPLE_TEAM_ID",
        "WINDOWS_CERTIFICATE_PFX",
        "WINDOWS_CERTIFICATE_PASSWORD",
    ]:
        assert secret in doc
    for required in [
        "native runner",
        "PyInstaller",
        "fail closed",
        "update_key.py",
        "DMG",
        "AppImage",
        "per-user",
    ]:
        assert required in doc
    assert "v" in doc  # version/tag rule references a v-prefixed tag
