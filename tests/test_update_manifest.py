import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.updater.manifest import (
    ManifestError,
    current_platform_identity,
    verify_and_select,
)
from backend.updater.models import PlatformIdentity


def signed_manifest(
    version="1.2.0",
    os_name="macos",
    arch="arm64",
    package="dmg",
    minimum_updater_version="1.0.0",
    *,
    artifact_overrides=None,
    manifest_overrides=None,
):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    artifact = {
        "os": os_name,
        "arch": arch,
        "package": package,
        "name": "IBKR-Lot-Tracker-1.2.0-arm64.dmg",
        "url": (
            "https://github.com/CT15Too/IBKR_Lot_Tracker/"
            "releases/download/v1.2.0/app.dmg"
        ),
        "size": 123,
        "sha256": "a" * 64,
    }
    artifact.update(artifact_overrides or {})
    manifest = {
        "schema_version": 1,
        "version": version,
        "minimum_updater_version": minimum_updater_version,
        "release_notes": "Safer desktop updates.",
        "artifacts": [artifact],
    }
    manifest.update(manifest_overrides or {})
    body = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()
    signature = base64.b64encode(private.sign(body)).decode()
    return body, signature, base64.b64encode(public).decode()


def select(body, signature, public, current="1.1.9", updater="1.0.0"):
    return verify_and_select(
        body,
        signature,
        public,
        current_version=current,
        updater_version=updater,
        platform=PlatformIdentity("macos", "arm64", "dmg"),
    )


def test_selects_only_newer_exact_platform_artifact():
    body, signature, public = signed_manifest()
    selected = select(body, signature, public)
    assert selected.version == "1.2.0"
    assert selected.artifact.package == "dmg"


@pytest.mark.parametrize("version", ["1.1.9", "1.1.8", "1.1.9+build.2"])
def test_refuses_downgrade_or_same_precedence(version):
    body, signature, public = signed_manifest(version)
    with pytest.raises(ManifestError, match="not newer"):
        select(body, signature, public)


def test_rejects_prerelease_update_even_when_semantically_newer():
    body, signature, public = signed_manifest("2.0.0-rc.1")
    with pytest.raises(ManifestError, match="prerelease"):
        select(body, signature, public)


def test_rejects_tampered_manifest_before_parsing_urls(monkeypatch):
    body, signature, public = signed_manifest()

    def must_not_parse(*args, **kwargs):
        raise AssertionError("JSON parsed before signature verification")

    monkeypatch.setattr(json, "loads", must_not_parse)
    with pytest.raises(ManifestError, match="signature"):
        select(body + b" ", signature, public)


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_schema_version_requires_exact_integer(schema_version):
    body, signature, public = signed_manifest(
        manifest_overrides={"schema_version": schema_version}
    )
    with pytest.raises(ManifestError, match="schema"):
        select(body, signature, public)


def test_rejects_wrong_platform_and_newer_required_updater():
    body, signature, public = signed_manifest(
        os_name="windows", package="exe"
    )
    with pytest.raises(ManifestError, match="compatible artifact"):
        select(body, signature, public, current="1.0.0")

    body, signature, public = signed_manifest(
        minimum_updater_version="2.0.0"
    )
    with pytest.raises(ManifestError, match="updater"):
        select(body, signature, public, current="1.0.0")


def test_requires_exactly_one_matching_artifact():
    body, signature, public = signed_manifest(
        manifest_overrides={
            "artifacts": [
                json.loads(signed_manifest()[0])["artifacts"][0],
                json.loads(signed_manifest()[0])["artifacts"][0],
            ]
        }
    )
    with pytest.raises(ManifestError, match="exactly one"):
        select(body, signature, public)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"size": 0}, "size"),
        ({"size": True}, "size"),
        ({"sha256": "A" * 64}, "SHA-256"),
        ({"sha256": "a" * 63}, "SHA-256"),
        ({"unexpected": "value"}, "keys"),
    ],
)
def test_rejects_invalid_artifact_metadata(overrides, message):
    body, signature, public = signed_manifest(artifact_overrides=overrides)
    with pytest.raises(ManifestError, match=message):
        select(body, signature, public)


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("darwin", "arm64", PlatformIdentity("macos", "arm64", "dmg")),
        ("Darwin", "aarch64", PlatformIdentity("macos", "arm64", "dmg")),
        ("win32", "AMD64", PlatformIdentity("windows", "x86_64", "exe")),
        ("Windows", "x86_64", PlatformIdentity("windows", "x86_64", "exe")),
        ("linux", "aarch64", PlatformIdentity("linux", "arm64", "appimage")),
    ],
)
def test_current_platform_identity_is_normalized(system, machine, expected):
    assert current_platform_identity(system, machine) == expected


@pytest.mark.parametrize(
    ("system", "machine"), [("freebsd", "x86_64"), ("linux", "riscv64")]
)
def test_current_platform_identity_rejects_unknown_values(system, machine):
    with pytest.raises(ManifestError, match="Unsupported"):
        current_platform_identity(system, machine)
