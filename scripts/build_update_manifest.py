#!/usr/bin/env python3
"""Build a deterministic update manifest from final release artifacts.

The manifest is the single machine-readable source of truth consumed by the
desktop updater: it records the release version, the minimum supported updater
version, release notes, and one entry per platform artifact (OS, architecture,
package format) with its download URL, size, and SHA-256 digest.

Usage:
    python3 scripts/build_update_manifest.py \
        --version 1.2.0 \
        --output update-manifest.json \
        --artifact linux x86_64 appimage ./IBKR.AppImage https://.../IBKR.AppImage \
        --artifact macos arm64 dmg ./IBKR.dmg https://.../IBKR.dmg

Artifact digests are computed from the final file bytes, so this must run only
after every package has been built and signed. Duplicate platform triples are
rejected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an update manifest from final release artifacts."
    )
    parser.add_argument("--version", required=True, help="Release semantic version.")
    parser.add_argument(
        "--minimum-updater-version",
        default="1.0.0",
        help="Minimum updater version required (default 1.0.0).",
    )
    parser.add_argument("--release-notes", default="", help="Release notes text.")
    parser.add_argument("--output", required=True, help="Manifest output path.")
    parser.add_argument(
        "--artifact",
        nargs=5,
        action="append",
        required=True,
        metavar=("OS", "ARCH", "PACKAGE", "PATH", "URL"),
        help="Platform tuple, repeatable: OS ARCH PACKAGE PATH URL.",
    )
    parsed = parser.parse_args(sys.argv[1:] if argv is None else argv)

    seen = set()
    artifacts = []
    for os_name, arch, package, path_str, url in parsed.artifact:
        triple = (os_name, arch, package)
        if triple in seen:
            print(
                "duplicate artifact for {}/{}/{}".format(*triple),
                file=sys.stderr,
            )
            return 1
        seen.add(triple)

        path = Path(path_str)
        if not path.is_file():
            print("artifact not found: {}".format(path_str), file=sys.stderr)
            return 1
        data = path.read_bytes()
        artifacts.append(
            {
                "os": os_name,
                "arch": arch,
                "package": package,
                "name": path.name,
                "url": url,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    body = {
        "schema_version": 1,
        "version": parsed.version,
        "minimum_updater_version": parsed.minimum_updater_version,
        "release_notes": parsed.release_notes,
        "artifacts": artifacts,
    }
    output = Path(parsed.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
