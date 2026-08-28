#!/usr/bin/env python3
"""Detached Ed25519 signing for the update manifest.

The application embeds only the public key (backend/update_key.py); the private
key lives exclusively in the GitHub Actions secret UPDATE_MANIFEST_PRIVATE_KEY
(base64 of exactly 32 raw Ed25519 private-key bytes). This script signs the
manifest bytes verbatim (no reserialization) and writes only a base64 detached
signature plus a trailing newline.

Usage:
    UPDATE_MANIFEST_PRIVATE_KEY=... python3 scripts/sign_update_manifest.py \
        update-manifest.json

Key ceremony (local generation; print the public key and store the private key
in the secret store, then embed the public key in backend/update_key.py):
    python3 scripts/sign_update_manifest.py --generate-local-key .production-update-key
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PRIVATE_KEY_LENGTH = 32


def _generate_local_key(path_str: str) -> int:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes_raw()
    target = Path(path_str)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(base64.b64encode(raw) + b"\n")
    public_b64 = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    print(public_b64)
    return 0


def _decode_private_key(secret: str):
    try:
        raw = base64.b64decode(secret, validate=True)
    except Exception:
        return None
    if len(raw) != PRIVATE_KEY_LENGTH:
        return None
    return raw


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] == "--generate-local-key":
        if len(args) != 2:
            print(
                "usage: sign_update_manifest.py --generate-local-key PATH",
                file=sys.stderr,
            )
            return 2
        return _generate_local_key(args[1])

    parser = argparse.ArgumentParser(
        description="Sign an update manifest with Ed25519."
    )
    parser.add_argument("manifest", help="Path to the manifest JSON.")
    parser.add_argument(
        "--output",
        default=None,
        help="Signature output path (default: <manifest>.sig).",
    )
    parsed = parser.parse_args(args)

    secret = os.environ.get("UPDATE_MANIFEST_PRIVATE_KEY", "")
    if not secret:
        print("UPDATE_MANIFEST_PRIVATE_KEY is required", file=sys.stderr)
        return 1
    raw = _decode_private_key(secret)
    if raw is None:
        print(
            "UPDATE_MANIFEST_PRIVATE_KEY must be base64-encoded 32 bytes",
            file=sys.stderr,
        )
        return 1

    try:
        manifest_bytes = Path(parsed.manifest).read_bytes()
    except OSError as exc:
        print("could not read manifest: {}".format(exc), file=sys.stderr)
        return 1

    private_key = Ed25519PrivateKey.from_private_bytes(raw)
    signature = base64.b64encode(private_key.sign(manifest_bytes)).decode("ascii")

    output = parsed.output or (str(parsed.manifest) + ".sig")
    Path(output).write_text(signature + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
