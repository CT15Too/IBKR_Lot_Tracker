from pathlib import Path

ROOT = Path(__file__).parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


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

