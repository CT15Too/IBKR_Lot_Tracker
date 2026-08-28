# Desktop release operations

This document is for maintainers who cut and publish releases of the IBKR Lot
Tracker desktop application. It covers the build pipeline, the signing and
notarization secrets, and the update-manifest key ceremony. End-user
installation and update guidance lives in `README.md`.

## Distribution formats

Each release produces three platform packages, all built on their native
GitHub-hosted runners:

| Platform | Runner | Package |
|---|---|---|
| macOS | `macos-14` | Signed and notarized **DMG** containing `IBKR Lot Tracker.app` |
| Windows | `windows-2022` | Authenticode-signed, **per-user** Inno Setup installer (`.exe`) |
| Linux | `ubuntu-24.04` | Self-contained **AppImage** |

PyInstaller output is platform-specific, so packages cannot be cross-compiled;
the `release.yml` workflow runs one job per native runner.

## Required secrets

The following GitHub Actions secrets are never committed and are consumed only
by the release workflow. Production builds fail loudly when any required
secret is missing.

| Secret | Purpose |
|---|---|
| `UPDATE_MANIFEST_PRIVATE_KEY` | Base64 of the 32-byte raw Ed25519 private key that signs the update manifest. |
| `MACOS_CERTIFICATE_P12` | Developer ID application certificate (PKCS#12) for codesigning the `.app`. |
| `MACOS_CERTIFICATE_PASSWORD` | Password for the PKCS#12 certificate. |
| `MACOS_SIGNING_IDENTITY` | Codesigning identity name (e.g. `Developer ID Application: ...`). |
| `APPLE_ID` | Apple ID used for notarization. |
| `APPLE_APP_PASSWORD` | App-specific password for the Apple ID. |
| `APPLE_TEAM_ID` | Apple Developer team identifier. |
| `WINDOWS_CERTIFICATE_PFX` | Authenticode code-signing certificate (PFX). |
| `WINDOWS_CERTIFICATE_PASSWORD` | Password for the PFX certificate. |

## Local unsigned builds

PyInstaller builds do not require signing secrets. An unsigned local build is
always possible for smoke-testing on the current platform:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m PyInstaller --clean --noconfirm packaging/ibkr_lot_tracker.spec
python3 scripts/packaged_smoke.py "dist/IBKR Lot Tracker.app/Contents/MacOS/IBKR Lot Tracker"
```

Use the corresponding artifact path for Windows or Linux. Local builds are
unsigned and are for development only — the updater will only install a
release whose manifest signature and artifact digest verify.

## Production fail closed behavior

The pipeline fails closed: signing is never silently skipped. Each platform job
in `release.yml` begins with a shell guard that exits nonzero
if its required signing/notarization secrets are empty. There are no
`if: secrets.X != ''` conditionals that silently skip signing: a missing
secret aborts the job rather than publishing an unsigned artifact.

The manifest signing step runs with `PRODUCTION_RELEASE: "1"`. The
`sign_update_manifest.py` script refuses to run without a valid
`UPDATE_MANIFEST_PRIVATE_KEY`, and the release job verifies the resulting
signature against the committed public key in `backend/update_key.py` before
publishing.

## Version and tag rule

- The application version lives in `backend/version.py` as `APP_VERSION` and
  follows semantic versioning (`X.Y.Z`, no prerelease for the initial release).
- A release tag must match exactly: tag `vX.Y.Z` requires
  `APP_VERSION = "X.Y.Z"`.
- The release job validates `github.ref_name == f"v{APP_VERSION}"` and fails
  otherwise.
- Pre-releases are ignored by the updater.

## Update-manifest key ceremony

The application embeds only the **public** Ed25519 verification key; the
private key never leaves GitHub Actions secrets.

1. Generate a local key pair and capture the printed public key:

   ```bash
   python3 scripts/sign_update_manifest.py --generate-local-key .production-update-key
   ```

   This writes the private key (base64) to `.production-update-key` with mode
   `0600` and prints the public key (base64) to stdout.

2. Store the base64 private-key value from `.production-update-key` as the
   `UPDATE_MANIFEST_PRIVATE_KEY` GitHub secret.

3. Replace `UPDATE_PUBLIC_KEY_B64` in `backend/update_key.py` with the printed
   public key.

4. Delete `.production-update-key` securely — it must never be committed.

5. Commit the `backend/update_key.py` change. From this point the release
   workflow derives the public key from the secret and fails unless it equals
   the committed `UPDATE_PUBLIC_KEY_B64`.

Rotating the key repeats this ceremony; clients on an older key must receive a
release back-signed by the old key before the new key becomes authoritative,
or their updater will reject every subsequent manifest.

## Release publication ordering

1. Each platform job tests, builds, smoke-tests, signs, and hashes its final
   artifact, then uploads it.
2. The assembly job downloads all artifacts, validates the tag against
   `APP_VERSION`, and runs `build_update_manifest.py` — which hashes the final
   bytes and therefore must run **after** signing (signing changes the digest).
3. `sign_update_manifest.py` signs the manifest; the release job verifies the
   signature against `backend/update_key.py`.
4. The public GitHub Release is created with the artifacts,
   `update-manifest.json`, and `update-manifest.json.sig`.

## Rollback

To withdraw a broken release, delete the GitHub Release and its tag. Existing
clients keep their current executable untouched; the updater simply finds no
newer stable release on its next check. Data and credentials are preserved.
