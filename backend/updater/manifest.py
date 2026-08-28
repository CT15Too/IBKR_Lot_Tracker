import base64
import binascii
import json
import platform as platform_module
import re
import sys
from typing import Any, Dict, Iterable, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from semantic_version import Version

from .models import Artifact, PlatformIdentity, VerifiedUpdate


class ManifestError(ValueError):
    pass


_MANIFEST_KEYS = {
    "schema_version",
    "version",
    "minimum_updater_version",
    "release_notes",
    "artifacts",
}
_ARTIFACT_KEYS = {"os", "arch", "package", "name", "url", "size", "sha256"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _decode_b64(value: str, label: str) -> bytes:
    if not isinstance(value, str):
        raise ManifestError("{} must be base64 text".format(label))
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ManifestError("Invalid {} encoding".format(label)) from exc


def _verify_signature(
    manifest_bytes: bytes, signature_b64: str, public_key_b64: str
) -> None:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            _decode_b64(public_key_b64, "public key")
        )
        public_key.verify(
            _decode_b64(signature_b64, "signature"), manifest_bytes
        )
    except InvalidSignature as exc:
        raise ManifestError("Manifest signature verification failed") from exc
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError("Invalid update signature material") from exc


def _require_exact_keys(
    value: Dict[str, Any], expected: Iterable[str], label: str
) -> None:
    if set(value) != set(expected):
        raise ManifestError("{} has invalid keys".format(label))


def _parse_version(value: Any, label: str) -> Version:
    if not isinstance(value, str):
        raise ManifestError("{} must be a semantic version".format(label))
    try:
        return Version(value)
    except ValueError as exc:
        raise ManifestError("{} is not a valid semantic version".format(label)) from exc


def _without_build(version: Version) -> Version:
    return Version(str(version).split("+", 1)[0])


def _parse_artifact(raw: Any) -> Artifact:
    if not isinstance(raw, dict):
        raise ManifestError("Artifact must be an object")
    _require_exact_keys(raw, _ARTIFACT_KEYS, "Artifact")
    for key in ("os", "arch", "package", "name", "url", "sha256"):
        if not isinstance(raw[key], str) or not raw[key]:
            raise ManifestError("Artifact {} must be non-empty text".format(key))
    if (
        isinstance(raw["size"], bool)
        or not isinstance(raw["size"], int)
        or raw["size"] <= 0
    ):
        raise ManifestError("Artifact size must be a positive integer")
    if not _SHA256.fullmatch(raw["sha256"]):
        raise ManifestError("Artifact SHA-256 must be 64 lowercase hex characters")
    return Artifact(**raw)


def verify_and_select(
    manifest_bytes: bytes,
    signature_b64: str,
    public_key_b64: str,
    current_version: str,
    updater_version: str,
    platform: PlatformIdentity,
) -> VerifiedUpdate:
    if not isinstance(manifest_bytes, bytes):
        raise ManifestError("Manifest must be bytes")
    _verify_signature(manifest_bytes, signature_b64, public_key_b64)

    try:
        raw = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("Signed manifest is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise ManifestError("Signed manifest must be an object")
    _require_exact_keys(raw, _MANIFEST_KEYS, "Manifest")
    if isinstance(raw["schema_version"], bool) or raw["schema_version"] != 1:
        raise ManifestError("Unsupported manifest schema version")
    if not isinstance(raw["release_notes"], str):
        raise ManifestError("Release notes must be text")
    if not isinstance(raw["artifacts"], list):
        raise ManifestError("Artifacts must be a list")

    release = _parse_version(raw["version"], "Release version")
    current = _parse_version(current_version, "Current version")
    minimum_updater = _parse_version(
        raw["minimum_updater_version"], "Minimum updater version"
    )
    updater = _parse_version(updater_version, "Updater version")
    if release.prerelease:
        raise ManifestError("prerelease updates are not accepted")
    if _without_build(release) <= _without_build(current):
        raise ManifestError("Release is not newer than the installed version")
    if _without_build(minimum_updater) > _without_build(updater):
        raise ManifestError("Release requires a newer updater")

    artifacts = [_parse_artifact(item) for item in raw["artifacts"]]
    matches = [
        artifact
        for artifact in artifacts
        if (
            artifact.os,
            artifact.arch,
            artifact.package,
        )
        == (platform.os, platform.arch, platform.package)
    ]
    if len(matches) != 1:
        raise ManifestError("Manifest must contain exactly one compatible artifact")
    return VerifiedUpdate(raw["version"], raw["release_notes"], matches[0])


def current_platform_identity(
    system: str = None, machine: str = None
) -> PlatformIdentity:
    raw_system = sys.platform if system is None else system
    raw_machine = platform_module.machine() if machine is None else machine
    systems = {
        "darwin": ("macos", "dmg"),
        "win32": ("windows", "exe"),
        "windows": ("windows", "exe"),
        "linux": ("linux", "appimage"),
    }
    architectures = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    system_key = raw_system.casefold()
    machine_key = raw_machine.casefold()
    if system_key not in systems:
        raise ManifestError("Unsupported operating system: {}".format(raw_system))
    if machine_key not in architectures:
        raise ManifestError("Unsupported architecture: {}".format(raw_machine))
    os_name, package = systems[system_key]
    return PlatformIdentity(os_name, architectures[machine_key], package)
