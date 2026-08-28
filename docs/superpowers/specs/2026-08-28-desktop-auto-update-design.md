# Desktop Application and Automatic Updates

## Goal

Distribute IBKR Lot Tracker as a desktop application for macOS, Windows, and
Linux while preserving the existing browser-based development workflow.
Desktop installations must detect public releases, ask before downloading, and
install a verified update when the user chooses to restart.

## Launch Modes

The project supports three launch paths:

1. `python3 run.py` starts the existing FastAPI server for use in a browser.
2. `python3 run_desktop.py` starts FastAPI on an available loopback port and
   opens the application in a native pywebview window.
3. A downloaded package launches the bundled desktop entry point directly.

All paths use the same FastAPI application and `frontend/index.html`. Browser
mode continues to support `.env` configuration for development.

## Desktop Architecture

`run_desktop.py` owns the desktop lifecycle. It selects an unused local port,
starts Uvicorn in a background thread, waits until the server is ready, and
opens pywebview at the loopback URL. Closing the final window shuts down the
server cleanly.

Only one packaged desktop instance may run at a time. A per-user lock prevents
two processes from opening the same SQLite database and local port lifecycle.
The server binds only to `127.0.0.1`; it is never exposed to the local network.

PyInstaller bundles the Python runtime, backend modules, frontend assets, and
desktop launcher. Resource lookup distinguishes immutable bundled files from
writable user data.

## Persistent Data and Credentials

Packaged builds store writable files outside the application directory:

- macOS: `~/Library/Application Support/IBKR Lot Tracker`
- Windows: `%LOCALAPPDATA%\IBKR Lot Tracker`
- Linux: `$XDG_DATA_HOME/ibkr-lot-tracker`, falling back to
  `~/.local/share/ibkr-lot-tracker`

The SQLite database and non-secret settings persist in this directory, so
reinstalling or replacing the executable cannot erase them.

The first desktop launch opens Settings when credentials are absent. The IBKR
Flex query ID and ordinary preferences are stored in the settings file. The
Flex token is stored through the operating system credential store. Secret
values are never returned by an API, written to logs, included in an update
manifest, or displayed after being saved.

Saving credentials performs a lightweight validation when the network is
available. A temporary IBKR or network failure does not discard previously
working credentials. Browser development can continue reading credentials from
`.env`.

## Settings and Update UI

The existing single-page frontend gains a Settings panel containing:

- IBKR Flex query ID and token controls
- current application version
- automatic update-check preference
- last update-check result
- a **Check for updates** action

Update status is represented explicitly as: idle, checking, update available,
downloading, ready to restart, up to date, or failed. When a release is
available, the app displays its version and release notes before downloading.
The user must approve the download. Once verification succeeds, the UI offers
**Restart and update** and permits the user to defer it.

Browser mode hides or disables desktop-only update actions because source
checkouts are updated through Git.

## Release Discovery

Packaged builds contain an application version and public GitHub repository
identifier. On desktop startup, and no more than once every 24 hours, the
updater requests the latest stable public GitHub Release. Users can also check
manually. Pre-releases are ignored in the initial version.

Versions follow semantic versioning. The updater compares parsed versions,
never raw strings, and refuses downgrades or a release for a different
platform, architecture, or package format.

Each release includes a machine-readable manifest with:

- release version
- supported operating system and architecture combinations
- artifact name, URL, size, and SHA-256 digest
- minimum supported updater version

The release also includes a detached Ed25519 signature for the manifest. The
application contains only the public verification key; the private signing key
remains in GitHub Actions secrets. The application rejects an unsigned or
invalid manifest before processing any artifact URL.

The application downloads only the asset selected from a verified manifest.
HTTPS is required, redirects are limited to GitHub-controlled release asset
hosts, and the SHA-256 digest must match before installation is offered.

## Installation Flow

The update is downloaded to a staging directory within the user's application
data directory. Partial files use a temporary name and are not considered
installable. A failed or cancelled download leaves the running installation
untouched.

After the user selects **Restart and update**, the application launches a small
updater helper and exits. The helper waits for the main process to terminate,
applies the platform-specific package, and relaunches the app:

- macOS uses a signed and notarized application package distributed in a DMG.
- Windows uses a signed per-user installer so elevation is not normally needed.
- Linux uses AppImage as the supported initial distribution format.

Where operating-system policy prevents unattended replacement, the staged
installer opens and the UI gives concise completion instructions. Update
failure preserves the old executable and records a non-secret diagnostic for
the next launch.

The first release does not include a system tray, launch-at-login behavior,
background operation after the window closes, delta updates, or multiple update
channels.

## Build and Release Automation

GitHub Actions builds on native macOS, Windows, and Linux runners because
PyInstaller output is platform-specific. Each job:

1. installs pinned dependencies;
2. runs the complete test suite;
3. builds the platform package;
4. performs a packaged-app smoke test;
5. signs the package where signing credentials are configured;
6. calculates the final SHA-256 digest.

A release job assembles the manifest and uploads all artifacts to a public
GitHub Release. It signs the manifest after all final artifact digests are
known. Manifest signing keys, platform certificates, and notarization
credentials remain in GitHub Actions secrets and are never committed.

Unsigned local development builds remain possible. Production releases clearly
fail rather than silently publishing an unsigned macOS or Windows artifact when
required signing credentials are absent.

## Error Handling

- If the desktop server cannot start, the app shows a native error and exits.
- If another instance owns the lock, the new process reports that the app is
  already running instead of starting another server.
- Network and GitHub rate-limit failures do not interrupt normal app use.
- Invalid manifests, unknown platforms, checksum mismatches, and unsupported
  updater versions prevent installation and produce an actionable message.
- Existing credentials, data, and the current executable remain intact after
  every failed update path.
- Logs redact tokens and are written to the per-user data directory.

## Testing

Unit tests cover:

- bundled-resource and writable-data path selection;
- settings migration and credential-store boundaries;
- semantic-version comparison;
- release and platform artifact selection;
- manifest signature validation and artifact checksum verification;
- interrupted downloads and rollback behavior;
- single-instance locking;
- desktop server startup and shutdown.

API tests cover the Settings and update-status contracts without contacting
IBKR or GitHub. Update HTTP requests, credential storage, and process launches
are replaced with fakes.

CI retains all existing browser-mode tests. Each operating-system job also
starts the packaged application in smoke-test mode, verifies that the local
health endpoint responds, and exits without opening a permanent window.

## Success Criteria

- The existing `python3 run.py` browser workflow continues to work.
- `python3 run_desktop.py` opens and closes a native window cleanly.
- A first-time desktop user can configure IBKR without creating `.env`.
- Packaged applications preserve settings and SQLite data across upgrades.
- A newer public release is detected and presented before download.
- Only the correct platform artifact can be installed, and only after checksum
  verification.
- Cancelling or failing an update leaves the current version usable.
- macOS, Windows, and Linux release artifacts are reproducibly built by CI.
