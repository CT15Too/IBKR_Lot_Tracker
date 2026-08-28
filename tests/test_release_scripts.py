import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ROOT = Path(__file__).parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_update_manifest.py"
SIGN_SCRIPT = ROOT / "scripts" / "sign_update_manifest.py"
MANIFEST_NAME = "update-manifest.json"


def _run(script, args, *, env=None):
    full_env = dict(os.environ)
    full_env.pop("UPDATE_MANIFEST_PRIVATE_KEY", None)
    full_env.pop("PRODUCTION_RELEASE", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env=full_env,
    )


def _build(tmp_path, *tuples, version="1.2.0"):
    args = [
        "--version",
        version,
        "--output",
        str(tmp_path / MANIFEST_NAME),
    ]
    for tup in tuples:
        args += ["--artifact", *tup]
    return _run(BUILD_SCRIPT, args)


def test_manifest_contains_final_artifact_metadata(tmp_path):
    artifact = tmp_path / "app.AppImage"
    artifact.write_bytes(b"final bytes")
    result = _build(
        tmp_path,
        (
            "linux",
            "x86_64",
            "appimage",
            str(artifact),
            "https://example.com/app.AppImage",
        ),
    )
    assert result.returncode == 0, result.stderr
    body = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert body["version"] == "1.2.0"
    assert body["artifacts"][0]["size"] == len(b"final bytes")
    assert body["artifacts"][0]["sha256"] == hashlib.sha256(b"final bytes").hexdigest()


def test_duplicate_platform_triple_is_rejected(tmp_path):
    artifact = tmp_path / "app.AppImage"
    artifact.write_bytes(b"x")
    result = _build(
        tmp_path,
        ("linux", "x86_64", "appimage", str(artifact), "https://example.com/a"),
        ("linux", "x86_64", "appimage", str(artifact), "https://example.com/b"),
    )
    assert result.returncode != 0
    assert "duplicate" in result.stderr.lower()


def test_production_signing_fails_closed_without_secret(tmp_path):
    result = _run(
        SIGN_SCRIPT,
        [str(tmp_path / MANIFEST_NAME)],
        env={"PRODUCTION_RELEASE": "1"},
    )
    assert result.returncode != 0
    assert "UPDATE_MANIFEST_PRIVATE_KEY is required" in result.stderr
    assert not (tmp_path / "update-manifest.json.sig").exists()


@pytest.fixture
def signing_key(tmp_path):
    private = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private.private_bytes_raw()).decode()
    public_b64 = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    (tmp_path / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "1.2.0",
                "minimum_updater_version": "1.0.0",
                "release_notes": "notes",
                "artifacts": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return private_b64, public_b64


def test_detached_signature_verifies(tmp_path, signing_key):
    private_b64, public_b64 = signing_key
    result = _run(
        SIGN_SCRIPT,
        [str(tmp_path / MANIFEST_NAME)],
        env={"UPDATE_MANIFEST_PRIVATE_KEY": private_b64},
    )
    assert result.returncode == 0, result.stderr
    manifest_bytes = (tmp_path / MANIFEST_NAME).read_bytes()
    signature = base64.b64decode(
        (tmp_path / "update-manifest.json.sig").read_text().strip()
    )
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64)).verify(
        signature, manifest_bytes
    )


def test_local_key_generation_writes_private_and_prints_public(tmp_path):
    private_path = tmp_path / "local-key"
    result = _run(SIGN_SCRIPT, ["--generate-local-key", str(private_path)])
    assert result.returncode == 0, result.stderr
    public_b64 = result.stdout.strip()
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
    assert private_path.exists()
    assert (private_path.stat().st_mode & 0o777) == 0o600
