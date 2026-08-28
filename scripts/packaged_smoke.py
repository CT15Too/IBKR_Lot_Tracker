#!/usr/bin/env python3
"""Run a packaged desktop artifact in smoke-test mode and verify health.

Usage: python3 scripts/packaged_smoke.py ARTIFACT

ARTIFACT is the native executable (macOS .app inner binary, Windows .exe, or
Linux AppImage). The artifact is launched with --smoke-test, which starts the
real loopback FastAPI server, checks /api/health, prints SMOKE_OK, and exits
without opening a permanent window. Exit code 0 requires BOTH a clean exit and
SMOKE_OK in the captured standard output.
"""

from __future__ import annotations

import subprocess
import sys

TIMEOUT_SECONDS = 30


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: packaged_smoke.py ARTIFACT", file=sys.stderr)
        return 2

    artifact = args[0]
    try:
        result = subprocess.run(
            [artifact, "--smoke-test"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"error: smoke test timed out after {TIMEOUT_SECONDS}s", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: could not launch {artifact}: {exc}", file=sys.stderr)
        return 1

    output = result.stdout + result.stderr
    if result.returncode != 0 or "SMOKE_OK" not in output:
        print(output, file=sys.stderr)
        print(
            "error: smoke test failed (exit "
            f"{result.returncode}) or SMOKE_OK not observed",
            file=sys.stderr,
        )
        return 1

    print(output.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
