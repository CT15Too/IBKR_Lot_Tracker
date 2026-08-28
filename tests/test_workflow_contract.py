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
