from pathlib import Path

ROOT = Path(__file__).parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_ci_uses_all_native_runners():
    workflow = _read(".github/workflows/ci.yml")
    for runner in ["macos-14", "windows-2022", "ubuntu-24.04"]:
        assert runner in workflow
    assert "python -m pytest -q" in workflow


def test_release_tests_builds_smokes_signs_hashes_then_publishes():
    workflow = _read(".github/workflows/release.yml")
    for required in [
        "python -m pytest -q",
        "PyInstaller",
        "packaged_smoke.py",
        "build_update_manifest.py",
        "sign_update_manifest.py",
        "UPDATE_MANIFEST_PRIVATE_KEY",
        'PRODUCTION_RELEASE: "1"',
    ]:
        assert required in workflow
    # Production failures must be loud, not silently skipped behind a
    # secret-presence conditional.
    assert "if: ${{ secrets.UPDATE_MANIFEST_PRIVATE_KEY != '' }}" not in workflow


def test_workflows_pin_node24_actions():
    # Node 20 was removed from GitHub-hosted runners (Sept 2025); every
    # workflow must use the Node 24 action majors so builds keep running.
    for name in ["ci.yml", "build-unsigned.yml", "release.yml"]:
        workflow = _read(f".github/workflows/{name}")
        assert "actions/checkout@v5" in workflow
        assert "actions/setup-python@v6" in workflow
    # Only the build/release pipelines upload artifacts; CI is test-only.
    for name in ["build-unsigned.yml", "release.yml"]:
        workflow = _read(f".github/workflows/{name}")
        assert "actions/upload-artifact@v5" in workflow
    # And no stale Node 20-era majors remain.
    for name in ["ci.yml", "build-unsigned.yml", "release.yml"]:
        workflow = _read(f".github/workflows/{name}")
        for stale in ["checkout@v4", "setup-python@v5", "upload-artifact@v4"]:
            assert stale not in workflow


def test_macos_builds_use_framework_python_for_pyinstaller():
    # PyInstaller's macOS .app bundle needs a framework Python; the stock
    # actions/setup-python build is non-framework, so its bundle is missing
    # Contents/Frameworks/Python and the packaged app fails to launch. The
    # macOS build jobs must install the python.org framework build instead.
    for name in ["build-unsigned.yml", "release.yml"]:
        workflow = _read(f".github/workflows/{name}")
        assert "python.org" in workflow
        assert "Python.framework" in workflow
        assert "macos11.pkg" in workflow


def test_unsigned_build_covers_all_platforms_without_signing_secrets():
    workflow = _read(".github/workflows/build-unsigned.yml")
    for runner in ["macos-14", "windows-2022", "ubuntu-24.04"]:
        assert runner in workflow
    for required in [
        "workflow_dispatch",
        "python -m pytest -q",
        "PyInstaller",
        "packaged_smoke.py",
        "upload-artifact",
        # Artifacts carry the commit short SHA so unsigned downloads are
        # self-identifying (there is otherwise no version tag for them).
        "git rev-parse --short=8 HEAD",
    ]:
        assert required in workflow
    # The unsigned build must never gate on signing/notarization, which is
    # what distinguishes it from the fail-closed release pipeline.
    for forbidden in [
        "APPLE_ID",
        "MACOS_CERTIFICATE_P12",
        "notarytool",
        "codesign",
        "WINDOWS_CERTIFICATE_PFX",
        "Get-AuthenticodeSignature",
    ]:
        assert forbidden not in workflow

