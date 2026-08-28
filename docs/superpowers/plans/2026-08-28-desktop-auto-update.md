# Desktop Application and Automatic Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship IBKR Lot Tracker as a single-instance pywebview desktop application for macOS, Windows, and Linux with persistent settings, OS-backed credentials, and verified user-approved automatic updates, while preserving browser source mode.

**Architecture:** Keep FastAPI and the existing single-file frontend shared across browser, source-desktop, and packaged-desktop modes, and inject a small runtime context into an application factory. Isolate paths/settings/credentials, release verification/download state, platform installation, and desktop lifecycle in focused modules; the updater verifies a signed manifest before trusting artifact metadata and only hands a fully staged artifact to a separate restart helper.

**Tech Stack:** Python 3.11, FastAPI, Uvicorn, requests, pywebview, PyInstaller, keyring, cryptography Ed25519, semantic-version, filelock, pytest, GitHub Actions, macOS DMG, Inno Setup per-user Windows installer, Linux AppImage

---

## Focused file responsibilities

The implementation keeps each policy boundary independently testable:

- `backend/runtime.py`: detects `browser`, `source_desktop`, or `packaged_desktop`; resolves immutable resources and per-platform writable paths.
- `backend/version.py`: owns `APP_VERSION`, `UPDATER_VERSION`, and the public GitHub repository identifier.
- `backend/update_key.py`: contains the non-secret Ed25519 public verification key embedded in packaged applications.
- `backend/settings_store.py`: versioned JSON persistence for non-secret desktop settings and migration/default behavior.
- `backend/credentials.py`: keyring boundary for the Flex token; never serializes or returns the saved secret.
- `backend/config.py`: merges `.env` browser configuration with desktop persisted settings and credential lookup.
- `backend/main.py`: exposes an application factory plus existing portfolio, settings, health, and update routes; contains no update cryptography or installer commands.
- `backend/updater/models.py`: typed update states, manifest records, platform identity, and public API serialization.
- `backend/updater/manifest.py`: Ed25519 verification, semantic-version checks, updater compatibility, and exact artifact selection.
- `backend/updater/http.py`: HTTPS-only GitHub release discovery and redirect-host policy.
- `backend/updater/download.py`: bounded streaming download to `.partial`, size/SHA-256 verification, and atomic staging.
- `backend/updater/service.py`: thread-safe state machine and automatic/manual check, approval, cancellation, and ready-to-restart orchestration.
- `backend/updater/install.py`: restart-helper request format, helper launch, and macOS/Windows/Linux install strategies with safe interactive fallback.
- `backend/desktop.py`: loopback Uvicorn thread, readiness/shutdown, pywebview lifecycle, smoke mode, logging, and single-instance lock.
- `run.py`: unchanged browser-mode entry point except creating the browser runtime explicitly.
- `run_desktop.py`: source and frozen desktop entry point.
- `frontend/index.html`: Settings panel and explicit update-state UI; no secret is re-rendered after save.
- `packaging/ibkr_lot_tracker.spec`: PyInstaller resources, entry point, metadata, and helper collection.
- `packaging/windows/installer.iss`: signed per-user Inno Setup package.
- `packaging/linux/build-appimage.sh`: AppDir/AppImage assembly.
- `scripts/build_update_manifest.py`: deterministic release manifest from final artifact metadata.
- `scripts/sign_update_manifest.py`: detached Ed25519 signing, with required production secret.
- `scripts/packaged_smoke.py`: launches a built artifact in smoke mode and verifies `/api/health`.
- `.github/workflows/ci.yml`: source tests on all supported operating systems.
- `.github/workflows/release.yml`: native-runner package/sign/smoke/hash jobs followed by manifest signing and release publication.
- `requirements.txt`: pinned runtime dependencies.
- `requirements-dev.txt`: pinned test/build tooling, including `pytest`.
- `tests/`: focused unit, API, frontend-contract, lifecycle, update integration, and packaging-script tests.
- `README.md` and `docs/desktop-release.md`: user installation/settings/update guidance and maintainer release/signing procedure.

## Delivery rules

- Run commands from `/Users/bytedance/Desktop/ibkr-lot-tracker` on branch `feature/desktop-auto-update`.
- Keep `python3 run.py` browser mode working after every task.
- Never log, persist in JSON, return from an API, or place in a manifest the Flex token.
- Production release jobs must fail closed if manifest/platform signing secrets are absent. `python3 -m PyInstaller packaging/ibkr_lot_tracker.spec` remains an explicitly unsigned local build.
- PyInstaller output is platform-specific; macOS, Windows, and Linux packages and smoke tests run on their matching native GitHub-hosted CI runners.

### Task 1: Repair and establish the executable test baseline

**Files:**
- Create: `requirements-dev.txt`
- Modify: `tests/test_api.py:27-34`
- Test: `tests/test_api.py::test_full_refresh_and_read_flow`

- [ ] **Step 1: Record the current baseline defect**

Run: `python3 -m pytest -q`

Expected in the current checkout: `1 failed, 12 passed`; `tests/test_api.py::test_full_refresh_and_read_flow` fails with `TypeError: <lambda>() got an unexpected keyword argument 'overrides'`.

- [ ] **Step 2: Add pinned development dependencies**

Create `requirements-dev.txt`:

```text
-r requirements.txt
pytest==8.4.1
pyinstaller==6.15.0
```

Run: `python3 -m pip install -r requirements-dev.txt`

Expected: installation succeeds and `python3 -m pytest --version` prints `pytest 8.4.1`; fresh development environments now receive pytest through the documented dependency file instead of relying on a user-level installation.

- [ ] **Step 3: Run the existing suite and confirm the known fake-signature defect**

Run: `python3 -m pytest tests/test_api.py::test_full_refresh_and_read_flow -v`

Expected: FAIL with `TypeError: ... got an unexpected keyword argument 'overrides'` because `backend/main.py` passes `overrides=` but the test fake does not accept it.

- [ ] **Step 4: Make the fake match the production interface**

Replace the fake with:

```python
    monkeypatch.setattr(
        main_module,
        "get_current_prices",
        lambda symbols, force_refresh=False, overrides=None: {
            "AAPL": PriceQuote(symbol="AAPL", price=228.40, stale=False),
            "TSLA": PriceQuote(symbol="TSLA", price=245.10, stale=False),
        },
    )
```

- [ ] **Step 5: Verify targeted and full baseline tests**

Run: `python3 -m pytest tests/test_api.py::test_full_refresh_and_read_flow -v`

Expected: PASS.

Run: `python3 -m pytest -q`

Expected: all existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add requirements-dev.txt tests/test_api.py
git commit -m "test: restore executable API test baseline"
```

### Task 2: Define launch modes, resources, writable paths, and version metadata

**Files:**
- Create: `backend/runtime.py`
- Create: `backend/version.py`
- Create: `tests/test_runtime.py`

- [ ] **Step 1: Write failing path and mode tests**

Create `tests/test_runtime.py` with platform cases and frozen/source resource cases:

```python
from pathlib import Path

from backend.runtime import LaunchMode, build_runtime


def test_browser_uses_source_resources_and_configured_database(tmp_path):
    runtime = build_runtime(
        LaunchMode.BROWSER, platform_name="linux", home=tmp_path,
        browser_database_path=tmp_path / "custom/lots.db",
    )
    assert runtime.frontend_dir.name == "frontend"
    assert runtime.data_dir == tmp_path / "custom"
    assert runtime.database_path == tmp_path / "custom/lots.db"
    assert runtime.desktop is False


def test_source_desktop_uses_linux_xdg_data(monkeypatch, tmp_path):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    runtime = build_runtime(LaunchMode.SOURCE_DESKTOP, platform_name="linux", home=tmp_path)
    assert runtime.data_dir == xdg / "ibkr-lot-tracker"
    assert runtime.database_path == runtime.data_dir / "lots.db"


def test_packaged_platform_data_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    mac = build_runtime(LaunchMode.PACKAGED_DESKTOP, platform_name="darwin", home=tmp_path)
    win = build_runtime(LaunchMode.PACKAGED_DESKTOP, platform_name="win32", home=tmp_path)
    linux = build_runtime(LaunchMode.PACKAGED_DESKTOP, platform_name="linux", home=tmp_path)
    assert mac.data_dir == tmp_path / "Library/Application Support/IBKR Lot Tracker"
    assert win.data_dir == tmp_path / "Local/IBKR Lot Tracker"
    assert linux.data_dir == tmp_path / ".local/share/ibkr-lot-tracker"


def test_frozen_resources_use_meipass(tmp_path):
    runtime = build_runtime(
        LaunchMode.PACKAGED_DESKTOP,
        platform_name="darwin",
        home=tmp_path,
        frozen_root=tmp_path / "_MEI",
    )
    assert runtime.frontend_dir == tmp_path / "_MEI/frontend"
```

- [ ] **Step 2: Run tests to verify missing module failure**

Run: `python3 -m pytest tests/test_runtime.py -v`

Expected: collection FAILS with `ModuleNotFoundError: No module named 'backend.runtime'`.

- [ ] **Step 3: Implement the minimal runtime model and version constants**

Create `backend/version.py`:

```python
APP_VERSION = "0.1.0"
UPDATER_VERSION = "1.0.0"
GITHUB_REPOSITORY = "CT15Too/IBKR_Lot_Tracker"
```

Create `backend/runtime.py` with `LaunchMode(str, Enum)` values `browser`, `source_desktop`, and `packaged_desktop`; a frozen `RuntimePaths` dataclass containing `mode`, `resource_root`, `frontend_dir`, `data_dir`, `database_path`, `settings_path`, `staging_dir`, `log_path`, and `desktop`; and:

```python
def build_runtime(mode, *, platform_name=sys.platform, home=None, frozen_root=None,
                  browser_database_path="./data/lots.db"):
    home = Path.home() if home is None else Path(home)
    resource_root = Path(frozen_root) if frozen_root else Path(__file__).resolve().parent.parent
    if mode is LaunchMode.BROWSER:
        database_path = Path(browser_database_path)
        data_dir = database_path.parent
    elif platform_name == "darwin":
        data_dir = home / "Library/Application Support/IBKR Lot Tracker"
    elif platform_name == "win32":
        data_dir = Path(os.environ["LOCALAPPDATA"]) / "IBKR Lot Tracker"
    else:
        data_dir = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "ibkr-lot-tracker"
    return RuntimePaths(
        mode=mode,
        resource_root=resource_root,
        frontend_dir=resource_root / "frontend",
        data_dir=data_dir,
        database_path=database_path if mode is LaunchMode.BROWSER else data_dir / "lots.db",
        settings_path=data_dir / "settings.json",
        staging_dir=data_dir / "updates",
        log_path=data_dir / "app.log",
        desktop=mode is not LaunchMode.BROWSER,
    )
```

- [ ] **Step 4: Verify targeted and full tests**

Run: `python3 -m pytest tests/test_runtime.py -v`

Expected: 4 tests PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/runtime.py backend/version.py tests/test_runtime.py
git commit -m "feat: define desktop runtime paths and modes"
```

### Task 3: Persist non-secret settings and isolate credentials in the OS store

**Files:**
- Create: `backend/settings_store.py`
- Create: `backend/credentials.py`
- Create: `tests/test_settings_store.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Write failing migration and credential-boundary tests**

Create `tests/test_settings_store.py`:

```python
import json

from backend.credentials import CredentialStore
from backend.settings_store import DesktopSettings, SettingsStore


class MemoryKeyring:
    def __init__(self):
        self.values = {}
    def get_password(self, service, username):
        return self.values.get((service, username))
    def set_password(self, service, username, value):
        self.values[(service, username)] = value
    def delete_password(self, service, username):
        self.values.pop((service, username), None)


def test_missing_settings_get_safe_defaults(tmp_path):
    loaded = SettingsStore(tmp_path / "settings.json").load()
    assert loaded == DesktopSettings(schema_version=1, flex_query_id="", auto_check_updates=True)


def test_legacy_settings_are_migrated_without_secret(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"flex_query_id": "42"}))
    loaded = SettingsStore(path).load()
    assert loaded.schema_version == 1
    assert loaded.flex_query_id == "42"
    assert "token" not in path.read_text()


def test_token_round_trip_only_crosses_keyring_boundary():
    backend = MemoryKeyring()
    store = CredentialStore(backend)
    store.set_token("secret")
    assert store.has_token() is True
    assert store.get_token() == "secret"
    store.clear_token()
    assert store.has_token() is False
```

- [ ] **Step 2: Run tests to verify expected import failures**

Run: `python3 -m pytest tests/test_settings_store.py -v`

Expected: collection FAILS because `backend.credentials` and `backend.settings_store` do not exist.

- [ ] **Step 3: Add runtime libraries and minimal stores**

Append to `requirements.txt`:

```text
pywebview==5.4
keyring==25.6.0
cryptography==45.0.6
semantic-version==2.10.0
filelock==3.19.1
```

Run: `python3 -m pip install -r requirements-dev.txt`

Expected: the new pinned runtime libraries install successfully.

Implement `DesktopSettings(schema_version=1, flex_query_id="", auto_check_updates=True, last_update_check_at=None)` as a dataclass. `SettingsStore.load()` must map a missing `schema_version` to version 1, accept only known keys, and return defaults for a missing file; `save()` must create the parent directory and atomically replace `settings.json.tmp` with JSON mode `0o600`.

Implement `CredentialStore` using injectable `keyring` backend and fixed identifiers:

```python
SERVICE = "IBKR Lot Tracker"
ACCOUNT = "ibkr-flex-token"

class CredentialStore:
    def __init__(self, backend=keyring):
        self._backend = backend
    def get_token(self):
        return self._backend.get_password(SERVICE, ACCOUNT) or ""
    def has_token(self):
        return bool(self.get_token())
    def set_token(self, token):
        if not token:
            raise ValueError("Flex token must not be empty")
        self._backend.set_password(SERVICE, ACCOUNT, token)
    def clear_token(self):
        try:
            self._backend.delete_password(SERVICE, ACCOUNT)
        except keyring.errors.PasswordDeleteError:
            pass
```

- [ ] **Step 4: Verify stores and regression suite**

Run: `python3 -m pytest tests/test_settings_store.py -v`

Expected: 3 tests PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt backend/settings_store.py backend/credentials.py tests/test_settings_store.py
git commit -m "feat: persist desktop settings and secure credentials"
```

### Task 4: Model signed releases and select only compatible newer artifacts

**Files:**
- Create: `backend/updater/__init__.py`
- Create: `backend/updater/models.py`
- Create: `backend/updater/manifest.py`
- Create: `backend/update_key.py`
- Create: `tests/test_update_manifest.py`

- [ ] **Step 1: Write failing semantic version, signature, and selection tests**

Create `tests/test_update_manifest.py` using a generated Ed25519 key and canonical fixture bytes:

```python
import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.updater.manifest import ManifestError, verify_and_select
from backend.updater.models import PlatformIdentity


def signed_manifest(version="1.2.0", os_name="macos", arch="arm64", package="dmg",
                    minimum_updater_version="1.0.0"):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    body = json.dumps({
        "schema_version": 1,
        "version": version,
        "minimum_updater_version": minimum_updater_version,
        "release_notes": "Safer desktop updates.",
        "artifacts": [{
            "os": os_name, "arch": arch, "package": package,
            "name": "IBKR-Lot-Tracker-1.2.0-arm64.dmg",
            "url": "https://github.com/CT15Too/IBKR_Lot_Tracker/releases/download/v1.2.0/app.dmg",
            "size": 123, "sha256": "a" * 64,
        }],
    }, sort_keys=True, separators=(",", ":")).encode()
    signature = base64.b64encode(private.sign(body)).decode()
    return body, signature, base64.b64encode(public).decode()


def test_selects_only_newer_exact_platform_artifact():
    body, signature, public = signed_manifest()
    selected = verify_and_select(
        body, signature, public, current_version="1.1.9",
        updater_version="1.0.0",
        platform=PlatformIdentity("macos", "arm64", "dmg"),
    )
    assert selected.version == "1.2.0"
    assert selected.artifact.package == "dmg"


@pytest.mark.parametrize("version", ["1.1.9", "1.1.9+build.2"])
def test_refuses_downgrade_or_same_precedence(version):
    body, signature, public = signed_manifest(version)
    with pytest.raises(ManifestError, match="not newer"):
        verify_and_select(body, signature, public, "1.1.9", "1.0.0",
                          PlatformIdentity("macos", "arm64", "dmg"))


def test_rejects_tampered_manifest_before_parsing_urls():
    body, signature, public = signed_manifest()
    with pytest.raises(ManifestError, match="signature"):
        verify_and_select(body + b" ", signature, public, "1.0.0", "1.0.0",
                          PlatformIdentity("macos", "arm64", "dmg"))


def test_rejects_wrong_platform_and_newer_required_updater():
    body, signature, public = signed_manifest(os_name="windows", package="exe")
    with pytest.raises(ManifestError, match="compatible artifact"):
        verify_and_select(body, signature, public, "1.0.0", "1.0.0",
                          PlatformIdentity("linux", "x86_64", "appimage"))
    body, signature, public = signed_manifest(minimum_updater_version="2.0.0")
    with pytest.raises(ManifestError, match="updater"):
        verify_and_select(body, signature, public, "1.0.0", "1.0.0",
                          PlatformIdentity("macos", "arm64", "dmg"))


@pytest.mark.parametrize(("system", "machine", "expected"), [
    ("darwin", "arm64", PlatformIdentity("macos", "arm64", "dmg")),
    ("win32", "AMD64", PlatformIdentity("windows", "x86_64", "exe")),
    ("linux", "aarch64", PlatformIdentity("linux", "arm64", "appimage")),
])
def test_current_platform_identity_is_normalized(system, machine, expected):
    assert current_platform_identity(system, machine) == expected
```

- [ ] **Step 2: Run tests to verify missing updater modules**

Run: `python3 -m pytest tests/test_update_manifest.py -v`

Expected: collection FAILS with `ModuleNotFoundError: No module named 'backend.updater'`.

- [ ] **Step 3: Implement typed records and verify-before-parse behavior**

Define `UpdateStatus` values `idle`, `checking`, `update_available`, `downloading`, `ready_to_restart`, `up_to_date`, and `failed`; `PlatformIdentity(os, arch, package)`; `Artifact`; `VerifiedUpdate`; and `UpdateSnapshot` in `models.py`.

Create `backend/update_key.py` with the RFC 8032 development vector public key `UPDATE_PUBLIC_KEY_B64 = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="`. Tests inject generated keys and do not depend on this value. Before the first production tag, use Task 14's production-key ceremony to replace it and store its private counterpart only in GitHub Actions. The packaged app imports only `UPDATE_PUBLIC_KEY_B64`, and the release workflow explicitly rejects the development value.

In `manifest.py`, base64-decode the embedded X.509-free raw Ed25519 public key and detached signature, call `Ed25519PublicKey.from_public_bytes(...).verify(signature, manifest_bytes)` before `json.loads`, validate exact keys/types, parse versions with `semantic_version.Version`, compare `precedence_key` so SemVer build metadata does not make a release newer, reject prerelease release versions, reject `release <= current`, reject `minimum_updater_version > updater`, require exactly one artifact matching all three platform fields, and validate a 64-lowercase-hex SHA-256 and positive size. Convert `InvalidSignature`, malformed base64/JSON, and invalid versions to actionable `ManifestError` messages without including manifest contents. `current_platform_identity()` maps Darwin to `macos/dmg`, Windows to `windows/exe`, Linux to `linux/appimage`, normalizes `AMD64`/`x86_64` and `aarch64`/`arm64`, and rejects unknown systems or architectures before release discovery.

- [ ] **Step 4: Verify manifest behavior and full suite**

Run: `python3 -m pytest tests/test_update_manifest.py -v`

Expected: all manifest tests PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/update_key.py backend/updater tests/test_update_manifest.py
git commit -m "feat: verify and select signed update manifests"
```

### Task 5: Restrict release discovery to HTTPS and GitHub-controlled hosts

**Files:**
- Create: `backend/updater/http.py`
- Create: `tests/test_update_http.py`

- [ ] **Step 1: Write failing host, redirect, stable-release, and rate-limit tests**

Create fake `Session`/`Response` objects and tests asserting:

```python
def test_rejects_http_and_non_github_redirects():
    client = GithubReleaseClient(FakeSession([
        response(302, headers={"Location": "https://evil.example/update.json"})
    ]), "CT15Too/IBKR_Lot_Tracker")
    with pytest.raises(UpdateNetworkError, match="redirect host"):
        client.fetch_bytes("https://github.com/owner/repo/releases/download/v1/update.json")
    with pytest.raises(UpdateNetworkError, match="HTTPS"):
        client.fetch_bytes("http://github.com/update.json")


def test_latest_release_ignores_prereleases_and_fetches_named_assets():
    session = FakeSession([response(200, json_body=[
        {"tag_name": "v2.0.0-rc.1", "prerelease": True, "draft": False, "assets": []},
        {"tag_name": "v1.2.0", "prerelease": False, "draft": False, "body": "notes",
         "assets": [
             {"name": "update-manifest.json", "browser_download_url": MANIFEST_URL},
             {"name": "update-manifest.json.sig", "browser_download_url": SIGNATURE_URL},
         ]},
    ])])
    release = GithubReleaseClient(session, REPOSITORY).latest_stable()
    assert release.tag == "v1.2.0"
    assert release.manifest_url == MANIFEST_URL


def test_rate_limit_is_actionable():
    client = GithubReleaseClient(FakeSession([response(403, headers={"X-RateLimit-Remaining": "0"})]), REPOSITORY)
    with pytest.raises(UpdateNetworkError, match="rate limit"):
        client.latest_stable()
```

- [ ] **Step 2: Run tests to verify missing client**

Run: `python3 -m pytest tests/test_update_http.py -v`

Expected: collection FAILS importing `backend.updater.http`.

- [ ] **Step 3: Implement the constrained client**

Use `requests.Session.get(..., allow_redirects=False, timeout=(5, 30), stream=...)`; permit at most five redirects and only HTTPS URLs whose lowercase hostname is exactly one of:

```python
ALLOWED_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
```

Query `https://api.github.com/repos/{repository}/releases?per_page=10`, choose the first non-draft, non-prerelease release, require exact manifest and signature asset names, and cap each metadata response at 1 MiB. Preserve no authorization token and translate timeout/status/rate-limit failures to `UpdateNetworkError`.

- [ ] **Step 4: Verify network policy and regression suite**

Run: `python3 -m pytest tests/test_update_http.py -v`

Expected: all update HTTP tests PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/updater/http.py tests/test_update_http.py
git commit -m "feat: constrain update discovery to GitHub HTTPS"
```

### Task 6: Stage downloads atomically and verify size plus SHA-256

**Files:**
- Create: `backend/updater/download.py`
- Create: `tests/test_update_download.py`

- [ ] **Step 1: Write failing success, interruption, cancellation, and mismatch tests**

Create `tests/test_update_download.py` with a fake chunk stream:

```python
def test_verified_download_atomically_loses_partial_suffix(tmp_path):
    payload = b"signed installer"
    artifact = artifact_for(payload)
    result = download_artifact(FakeClient([payload[:4], payload[4:]]), artifact, tmp_path)
    assert result == tmp_path / artifact.name
    assert result.read_bytes() == payload
    assert not list(tmp_path.glob("*.partial"))


def test_interruption_keeps_current_install_and_removes_partial(tmp_path):
    with pytest.raises(DownloadError, match="interrupted"):
        download_artifact(InterruptingClient(), artifact_for(b"abc"), tmp_path)
    assert not list(tmp_path.iterdir())


def test_cancel_removes_partial(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DownloadCancelled):
        download_artifact(FakeClient([b"abc"]), artifact_for(b"abc"), tmp_path, cancel=cancel)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("change", ["size", "sha256"])
def test_mismatch_is_never_installable(tmp_path, change):
    artifact = dataclasses.replace(artifact_for(b"abc"), **{change: 99 if change == "size" else "0" * 64})
    with pytest.raises(DownloadError, match=change):
        download_artifact(FakeClient([b"abc"]), artifact, tmp_path)
    assert not (tmp_path / artifact.name).exists()
```

- [ ] **Step 2: Run tests to verify missing downloader**

Run: `python3 -m pytest tests/test_update_download.py -v`

Expected: collection FAILS importing `backend.updater.download`.

- [ ] **Step 3: Implement streaming staging**

Create the staging directory with user-only permissions, reject artifact names containing path separators, set `partial = staging_dir / f"{artifact.name}.partial"`, stream 64 KiB chunks into it, update `hashlib.sha256`, enforce that bytes written never exceed manifest size, flush and `os.fsync`, verify exact size and digest using `hmac.compare_digest`, then `os.replace(partial, final)`. In every exception/cancellation path unlink only the partial file. Never modify the running executable or an existing final artifact until verification succeeds.

- [ ] **Step 4: Verify download behavior and full suite**

Run: `python3 -m pytest tests/test_update_download.py -v`

Expected: all download tests PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/updater/download.py tests/test_update_download.py
git commit -m "feat: stage and verify update downloads"
```

### Task 7: Implement the explicit update state machine

**Files:**
- Create: `backend/updater/service.py`
- Create: `tests/test_update_service.py`

- [ ] **Step 1: Write failing transition and 24-hour cadence tests**

Test fake clock/discovery/downloader dependencies:

```python
def test_manual_check_download_and_ready_flow(service):
    assert service.snapshot().status == UpdateStatus.IDLE
    service.check(manual=True)
    assert service.snapshot().status == UpdateStatus.UPDATE_AVAILABLE
    assert service.snapshot().release_notes == "Safer desktop updates."
    service.approve_download()
    assert service.snapshot().status == UpdateStatus.READY_TO_RESTART
    assert service.snapshot().staged_path.endswith(".dmg")


def test_automatic_check_runs_at_most_once_per_24_hours(service, clock):
    assert service.check(manual=False) is True
    assert service.check(manual=False) is False
    clock.advance(hours=24)
    assert service.check(manual=False) is True


def test_failures_preserve_use_of_application_and_hide_paths(service):
    service.discovery.raise_error = UpdateNetworkError("GitHub rate limit exceeded")
    service.check(manual=True)
    public = service.snapshot().to_public_dict()
    assert public["status"] == "failed"
    assert public["error"] == "GitHub rate limit exceeded"
    assert "staged_path" not in public


def test_invalid_transition_is_rejected(service):
    with pytest.raises(UpdateTransitionError, match="idle"):
        service.approve_download()
```

- [ ] **Step 2: Run tests to verify missing service**

Run: `python3 -m pytest tests/test_update_service.py -v`

Expected: collection FAILS importing `backend.updater.service`.

- [ ] **Step 3: Implement synchronized transitions**

Implement `UpdateService` with a `threading.RLock`, injected clock/discovery/downloader/installer, and legal transitions:

```text
idle|up_to_date|failed -> checking
checking -> update_available|up_to_date|failed
update_available -> downloading|idle
downloading -> ready_to_restart|update_available|failed
ready_to_restart -> idle (defer) or helper launch
```

`check(manual=False)` must skip source/browser mode and checks within 24 hours; manual checks bypass cadence. Startup calls the automatic path without blocking the UI. Persist `last_update_check_at` through `SettingsStore` after every completed attempt so restarts cannot bypass the cadence. `approve_download()` is the only download entry, `cancel_download()` sets the downloader cancellation event, `defer()` preserves the verified staged file for the current version, and all public snapshots exclude local paths and secrets.

- [ ] **Step 4: Verify transitions and full suite**

Run: `python3 -m pytest tests/test_update_service.py -v`

Expected: all service tests PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/updater/service.py tests/test_update_service.py
git commit -m "feat: orchestrate the desktop update state machine"
```

### Task 8: Add settings and update APIs through an application factory

**Files:**
- Modify: `backend/config.py`
- Modify: `backend/main.py`
- Modify: `backend/ibkr_flex.py`
- Modify: `run.py`
- Create: `tests/test_settings_update_api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing API-contract tests with fakes**

Create tests against `create_app(runtime, credential_store, settings_store, update_service, validate_credentials)`:

```python
def test_get_settings_never_returns_token(client):
    body = client.get("/api/settings").json()
    assert body == {
        "mode": "packaged_desktop", "desktop": True, "configured": False,
        "flex_query_id": "", "has_flex_token": False,
        "auto_check_updates": True, "app_version": "0.1.0",
    }
    assert "token" not in body


def test_save_settings_validates_then_persists_secret(client, keyring, validator):
    response = client.put("/api/settings", json={
        "flex_query_id": "12345", "flex_token": "secret", "auto_check_updates": False,
    })
    assert response.status_code == 200
    validator.assert_called_once_with("secret", "12345")
    assert keyring.get_token() == "secret"
    assert response.json()["has_flex_token"] is True
    assert "flex_token" not in response.json()


def test_temporary_validation_failure_does_not_erase_existing_token(client, keyring, validator):
    keyring.set_token("working")
    validator.side_effect = FlexServiceError("network unavailable")
    response = client.put("/api/settings", json={"flex_query_id": "12345", "flex_token": "new"})
    assert response.status_code == 503
    assert keyring.get_token() == "working"


def test_update_routes_delegate_without_network(client, updater):
    assert client.post("/api/updates/check").json()["status"] == "update_available"
    assert client.post("/api/updates/download").json()["status"] == "ready_to_restart"
    assert client.post("/api/updates/restart").status_code == 202


def test_browser_mode_disables_update_mutations(browser_client):
    assert browser_client.post("/api/updates/check").status_code == 409
```

- [ ] **Step 2: Run tests to verify missing factory/routes**

Run: `python3 -m pytest tests/test_settings_update_api.py -v`

Expected: FAIL because `create_app` and the settings/update routes do not exist.

- [ ] **Step 3: Refactor configuration and construct the app**

Make `create_app(...)` build its own SQLite connection from `runtime.database_path`, static mount from `runtime.frontend_dir`, and route dependencies. Keep `app = create_app(build_runtime(LaunchMode.BROWSER))` for test/import compatibility. Browser mode reads `.env`; desktop modes read query ID/preferences from `SettingsStore` and token from `CredentialStore`. Add `validate_flex_credentials(token, query_id)` to `backend/ibkr_flex.py`; it performs only `_send_request` and does not poll/download a report. Add `GET /api/health`, `GET/PUT /api/settings`, `GET /api/updates/status`, and POST routes `/check`, `/download`, `/cancel`, `/defer`, `/restart`.

`PUT /api/settings` accepts optional `flex_token`: omission preserves the token, non-empty replaces it only after validation succeeds, and an explicit `clear_flex_token: true` deletes it. Return 503 on temporary validation failure while retaining existing values. Update `run.py` to run the browser application on the configured loopback host/port. Adapt `tests/test_api.py` to build an isolated factory app rather than replacing a module-global connection.

- [ ] **Step 4: Verify API contracts and browser regressions**

Run: `python3 -m pytest tests/test_settings_update_api.py tests/test_api.py -v`

Expected: all API tests PASS without IBKR, GitHub, keyring, or process launches.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/ibkr_flex.py backend/main.py run.py tests/test_api.py tests/test_settings_update_api.py
git commit -m "feat: expose secure settings and update APIs"
```

### Task 9: Build the Settings panel and update-state frontend

**Files:**
- Modify: `frontend/index.html`
- Create: `tests/test_frontend_contract.py`

- [ ] **Step 1: Write failing static frontend contract tests**

Create `tests/test_frontend_contract.py`:

```python
from pathlib import Path

HTML = (Path(__file__).parents[1] / "frontend/index.html").read_text()


def test_settings_panel_contains_required_controls():
    for element_id in [
        "settingsBtn", "settingsPanel", "flexQueryId", "flexToken",
        "autoCheckUpdates", "appVersion", "lastUpdateCheck", "checkUpdatesBtn",
        "downloadUpdateBtn", "restartUpdateBtn", "deferUpdateBtn",
    ]:
        assert f'id="{element_id}"' in HTML


def test_frontend_uses_settings_and_update_contracts():
    for route in ["/api/settings", "/api/updates/status", "/api/updates/check",
                  "/api/updates/download", "/api/updates/restart", "/api/updates/defer"]:
        assert route in HTML
    assert "savedToken" not in HTML
```

- [ ] **Step 2: Run tests to verify missing UI**

Run: `python3 -m pytest tests/test_frontend_contract.py -v`

Expected: both tests FAIL because the panel IDs and API calls are absent.

- [ ] **Step 3: Add accessible settings and explicit update rendering**

Add a Settings button/dialog with labeled query ID, password-type token field whose value is cleared after a successful save, auto-check checkbox, version text, and save feedback. Render every `UpdateStatus` with a `switch`: `checking` disables check; `update_available` shows version and escaped release notes plus Download; `downloading` shows progress and Cancel; `ready_to_restart` shows Restart and update plus Defer; `up_to_date` shows last checked time; `failed` shows an actionable escaped message without disrupting positions.

On initial load, fetch settings and status. If `desktop && !configured`, open Settings. If `desktop` is false, hide download/restart/defer and disable Check with text `Updates are installed through Git in browser mode`. Poll status every two seconds only while checking/downloading. Use `textContent` for release notes and errors, not `innerHTML`.

- [ ] **Step 4: Verify frontend/API/full suite**

Run: `python3 -m pytest tests/test_frontend_contract.py tests/test_settings_update_api.py -v`

Expected: all contract tests PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html tests/test_frontend_contract.py
git commit -m "feat: add desktop settings and update interface"
```

### Task 10: Implement safe restart-helper installation strategies

**Files:**
- Create: `backend/updater/install.py`
- Create: `tests/test_update_install.py`

- [ ] **Step 1: Write failing platform strategy and rollback tests**

Use an injected command runner/process launcher:

```python
def test_linux_appimage_replaces_atomically_and_keeps_backup(tmp_path, runner):
    current = executable(tmp_path / "IBKR.AppImage", b"old")
    staged = executable(tmp_path / "updates/new.AppImage", b"new")
    apply_update(request("linux", staged, current), runner)
    assert current.read_bytes() == b"new"
    assert current.with_suffix(".AppImage.previous").read_bytes() == b"old"
    assert runner.last == [str(current)]


def test_linux_failure_restores_previous_appimage(tmp_path, failing_runner):
    current = executable(tmp_path / "IBKR.AppImage", b"old")
    with pytest.raises(InstallError):
        apply_update(request("linux", broken_staged(tmp_path), current), failing_runner)
    assert current.read_bytes() == b"old"


def test_macos_uses_hdiutil_and_ditto_or_opens_dmg_when_target_not_writable(runner):
    apply_update(request("macos", Path("/tmp/app.dmg"), Path("/Applications/IBKR Lot Tracker.app")), runner)
    assert runner.commands[0][:2] == ["hdiutil", "attach"]
    runner.make_target_unwritable()
    result = apply_update(request("macos", Path("/tmp/app.dmg"), Path("/Applications/IBKR Lot Tracker.app")), runner)
    assert result.manual_completion is True
    assert runner.commands[-1] == ["open", "/tmp/app.dmg"]


def test_windows_runs_signed_per_user_installer_or_opens_interactively(runner):
    result = apply_update(request("windows", Path("update.exe"), Path("current.exe")), runner)
    assert runner.commands[0] == ["update.exe", "/VERYSILENT", "/CURRENTUSER", "/NORESTART"]
    runner.fail_next()
    result = apply_update(request("windows", Path("update.exe"), Path("current.exe")), runner)
    assert result.manual_completion is True
    assert runner.commands[-1] == ["update.exe"]
```

- [ ] **Step 2: Run tests to verify missing installer**

Run: `python3 -m pytest tests/test_update_install.py -v`

Expected: collection FAILS importing `backend.updater.install`.

- [ ] **Step 3: Implement helper request and strategies**

Define a JSON `InstallRequest` containing only platform, staged path, current executable/app path, relaunch command, parent PID, and diagnostic path. `launch_helper()` writes it mode `0o600`, launches `[sys.executable, "--apply-update", str(request_path)]` as a detached process, and returns before the main app exits.

The helper waits up to 60 seconds for the parent PID to disappear. Linux chmods the staged AppImage, copies it beside the target, fsyncs, renames current to `.previous`, atomically replaces current, relaunches, and restores `.previous` if replacement/relaunch fails. macOS attaches the DMG read-only/no-browse, locates exactly `IBKR Lot Tracker.app`, verifies with `codesign --verify --deep --strict`, uses `ditto` to a sibling temporary app then swaps while retaining `.previous`, detaches, and relaunches; if policy/permissions prevent replacement, open the DMG and return concise manual instructions. Windows runs the already Authenticode-signed Inno Setup per-user installer with `/VERYSILENT /CURRENTUSER /NORESTART`; on policy/installer failure, launch it interactively. Record only exception type, safe message, version, and timestamp in the data-directory diagnostic; never include environment variables, settings JSON, or command output that could contain secrets.

- [ ] **Step 4: Verify install safety and full suite**

Run: `python3 -m pytest tests/test_update_install.py -v`

Expected: all strategy and rollback tests PASS using fakes; no real installer runs.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/updater/install.py tests/test_update_install.py
git commit -m "feat: apply staged updates with safe platform fallbacks"
```

### Task 11: Add single-instance desktop lifecycle and packaged smoke mode

**Files:**
- Create: `backend/desktop.py`
- Create: `run_desktop.py`
- Create: `tests/test_desktop.py`

- [ ] **Step 1: Write failing lock, lifecycle, bind, and smoke tests**

Test injected Uvicorn/webview/lock/socket dependencies:

```python
def test_second_instance_reports_existing_owner(tmp_path):
    first = SingleInstanceLock(tmp_path / "app.lock")
    first.acquire()
    with pytest.raises(AlreadyRunningError, match="already running"):
        SingleInstanceLock(tmp_path / "app.lock").acquire()
    first.release()


def test_server_binds_loopback_available_port_and_stops_on_final_window_close(fakes):
    code = run_desktop(fakes.runtime, fakes.webview, fakes.server_factory)
    assert code == 0
    assert fakes.server.host == "127.0.0.1"
    assert fakes.server.port > 0
    assert fakes.webview.window_url == f"http://127.0.0.1:{fakes.server.port}/"
    assert fakes.server.should_exit is True


def test_server_start_failure_shows_native_error_and_exits(fakes):
    fakes.server.fail_start = True
    assert run_desktop(fakes.runtime, fakes.webview, fakes.server_factory) == 1
    assert "could not start" in fakes.webview.error.lower()


def test_smoke_mode_checks_health_without_window(fakes):
    assert run_smoke(fakes.runtime, fakes.server_factory, fakes.health_client) == 0
    assert fakes.webview.create_calls == 0
    assert fakes.server.should_exit is True
```

- [ ] **Step 2: Run tests to verify missing desktop module**

Run: `python3 -m pytest tests/test_desktop.py -v`

Expected: collection FAILS importing `backend.desktop`.

- [ ] **Step 3: Implement lifecycle**

Acquire `FileLock(data_dir / "instance.lock", timeout=0)` only for packaged mode and translate timeout to `AlreadyRunningError`. Reserve an ephemeral socket on `127.0.0.1`, pass its selected port to a Uvicorn `Server` thread, and poll `/api/health` with a five-second deadline. Create one window using `webview.create_window("IBKR Lot Tracker", loopback_url)`, subscribe cleanup to `window.events.closed += shutdown`, then call `webview.start()` on the main thread. Cleanup sets `server.should_exit`, joins with a five-second deadline, and releases the lock. Configure a rotating data-directory log whose filter replaces known token values with `[REDACTED]`.

`run_desktop.py` chooses `PACKAGED_DESKTOP` when `getattr(sys, "frozen", False)`, otherwise `SOURCE_DESKTOP`; support `--smoke-test` and `--apply-update REQUEST`. After server readiness, start `UpdateService.check(manual=False)` on a daemon thread so launch is never blocked. Smoke mode starts the real app, checks health, prints `SMOKE_OK` followed by the concrete loopback health URL, shuts down, and never creates a window.

- [ ] **Step 4: Verify desktop and complete suite**

Run: `python3 -m pytest tests/test_desktop.py -v`

Expected: all lifecycle tests PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS, including existing browser-mode tests.

- [ ] **Step 5: Commit**

```bash
git add backend/desktop.py run_desktop.py tests/test_desktop.py
git commit -m "feat: add single-instance pywebview desktop lifecycle"
```

### Task 12: Integrate release discovery through restart in a network-free test

**Files:**
- Create: `tests/test_update_integration.py`
- Modify: `backend/desktop.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Write the failing end-to-end integration test**

Compose real manifest verification, selection, downloader, service, and API with fake HTTP/keyring/process launch:

```python
def test_signed_release_reaches_helper_without_external_side_effects(update_harness):
    client = update_harness.client
    assert client.post("/api/updates/check").json()["status"] == "update_available"
    available = client.get("/api/updates/status").json()
    assert available["version"] == "1.2.0"
    assert available["release_notes"] == "Safer desktop updates."

    ready = client.post("/api/updates/download").json()
    assert ready["status"] == "ready_to_restart"
    assert update_harness.staged_artifact.read_bytes() == update_harness.artifact_bytes

    response = client.post("/api/updates/restart")
    assert response.status_code == 202
    assert update_harness.launcher.request.parent_pid == os.getpid()
    assert update_harness.shutdown_requested.is_set()
    assert update_harness.external_requests == []
```

- [ ] **Step 2: Run test to expose missing shutdown/helper wiring**

Run: `python3 -m pytest tests/test_update_integration.py -v`

Expected: FAIL because the restart route does not yet request desktop shutdown after launching the helper.

- [ ] **Step 3: Add the minimal lifecycle callback**

Inject `request_shutdown: Callable[[], None]` into `create_app`/`UpdateService`. `/api/updates/restart` must first launch the helper successfully, then return HTTP 202 and schedule shutdown. If helper launch fails, return 500, transition to `failed`, and keep the desktop process running.

- [ ] **Step 4: Verify integration and all source tests**

Run: `python3 -m pytest tests/test_update_integration.py -v`

Expected: PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/desktop.py tests/test_update_integration.py
git commit -m "feat: connect verified updates to desktop restart"
```

### Task 13: Package PyInstaller, DMG, per-user Windows installer, and AppImage

**Files:**
- Create: `packaging/ibkr_lot_tracker.spec`
- Create: `packaging/windows/installer.iss`
- Create: `packaging/linux/build-appimage.sh`
- Create: `scripts/packaged_smoke.py`
- Create: `tests/test_packaging_contract.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing packaging contract tests**

Create assertions that the PyInstaller spec targets `run_desktop.py`, includes `frontend`, collects pywebview data, embeds `backend/version.py`, and names `IBKR Lot Tracker`; Inno Setup contains `PrivilegesRequired=lowest`, `ArchitecturesInstallIn64BitMode=x64compatible`, and per-user `{localappdata}`; AppImage script invokes `appimagetool`; smoke script invokes the artifact with `--smoke-test` and requires `SMOKE_OK`.

- [ ] **Step 2: Run tests to verify packaging files are absent**

Run: `python3 -m pytest tests/test_packaging_contract.py -v`

Expected: FAIL with missing packaging/script paths.

- [ ] **Step 3: Add deterministic local packaging definitions**

Create a PyInstaller spec using `Analysis(["run_desktop.py"], datas=[("frontend", "frontend")], hiddenimports=["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto"], ...)`, pywebview hook collection, a console-enabled executable on Linux/smoke builds, macOS `.app` bundle with identifier `com.ibkrlottracker.app`, and Windows GUI executable metadata.

The Inno Setup script installs under `{localappdata}\Programs\IBKR Lot Tracker`, sets `PrivilegesRequired=lowest`, and supports `/CURRENTUSER`. The Linux script creates `IBKR_Lot_Tracker.AppDir` with desktop file/icon/AppRun forwarding arguments and invokes a pinned `appimagetool` supplied by CI. `scripts/packaged_smoke.py ARTIFACT` runs the native executable/AppImage or macOS bundle executable with `--smoke-test`, 30-second timeout, and requires exit 0 plus `SMOKE_OK`.

Append `.spec`, `.AppDir/`, `*.AppImage`, `*.dmg`, and local installer output directories to `.gitignore`; do not ignore source packaging definitions.

- [ ] **Step 4: Verify contracts and build locally without signing**

Run: `python3 -m pytest tests/test_packaging_contract.py -v`

Expected: all packaging contract tests PASS.

Run on the current native platform: `python3 -m PyInstaller --clean --noconfirm packaging/ibkr_lot_tracker.spec`

Expected: an unsigned local artifact is created under `dist/`; no signing environment variable is required.

Run: `python3 scripts/packaged_smoke.py "dist/IBKR Lot Tracker.app/Contents/MacOS/IBKR Lot Tracker"` on macOS, `"dist/IBKR Lot Tracker/IBKR Lot Tracker.exe"` on Windows, or `"dist/IBKR Lot Tracker/IBKR Lot Tracker"` on Linux.

Expected: exit 0 and output containing `SMOKE_OK`.

- [ ] **Step 5: Commit**

```bash
git add .gitignore packaging scripts/packaged_smoke.py tests/test_packaging_contract.py
git commit -m "build: package native desktop distributions"
```

### Task 14: Generate and sign deterministic release manifests

**Files:**
- Create: `scripts/build_update_manifest.py`
- Create: `scripts/sign_update_manifest.py`
- Create: `tests/test_release_scripts.py`

- [ ] **Step 1: Write failing deterministic manifest and fail-closed signing tests**

Create subprocess tests:

```python
def test_manifest_contains_final_artifact_metadata(tmp_path):
    artifact = tmp_path / "app.AppImage"
    artifact.write_bytes(b"final bytes")
    run_build_manifest(tmp_path, artifact, version="1.2.0", os_name="linux",
                       arch="x86_64", package="appimage")
    body = json.loads((tmp_path / "update-manifest.json").read_text())
    assert body["version"] == "1.2.0"
    assert body["artifacts"][0]["size"] == 11
    assert body["artifacts"][0]["sha256"] == hashlib.sha256(b"final bytes").hexdigest()


def test_production_signing_fails_closed_without_secret(tmp_path):
    result = run_signer(tmp_path, env={"PRODUCTION_RELEASE": "1"})
    assert result.returncode != 0
    assert "UPDATE_MANIFEST_PRIVATE_KEY is required" in result.stderr
    assert not (tmp_path / "update-manifest.json.sig").exists()


def test_detached_signature_verifies(tmp_path, signing_key):
    run_signer(tmp_path, env={"UPDATE_MANIFEST_PRIVATE_KEY": signing_key})
    verify_detached(tmp_path / "update-manifest.json", tmp_path / "update-manifest.json.sig")
```

- [ ] **Step 2: Run tests to verify scripts are absent**

Run: `python3 -m pytest tests/test_release_scripts.py -v`

Expected: FAIL because manifest build/sign scripts do not exist.

- [ ] **Step 3: Implement deterministic generation and strict signing**

`build_update_manifest.py` accepts repeated exact tuples `--artifact OS ARCH PACKAGE PATH URL`, verifies every file exists, hashes final bytes, emits schema/version/minimum-updater-version/release-notes/artifacts with sorted keys and compact separators, and rejects duplicate platform triples.

`sign_update_manifest.py` requires base64 `UPDATE_MANIFEST_PRIVATE_KEY` containing exactly 32 raw Ed25519 private-key bytes whenever `PRODUCTION_RELEASE=1`; signs the manifest bytes without reserialization and writes only base64 signature plus newline. It must exit nonzero before creating a signature if the secret is absent/malformed. Permit key generation only via explicit `--generate-local-key PATH`, write it mode `0o600`, and print the corresponding public key. The production-key ceremony is exact: run `python3 scripts/sign_update_manifest.py --generate-local-key .production-update-key`, store the file's base64 value as the `UPDATE_MANIFEST_PRIVATE_KEY` GitHub secret, replace `UPDATE_PUBLIC_KEY_B64` in `backend/update_key.py` with the printed public value, verify a signed fixture, and securely delete the local private-key file. The release workflow derives the public key from the secret and fails unless it equals the committed `UPDATE_PUBLIC_KEY_B64`; it never generates or substitutes a production key.

- [ ] **Step 4: Verify scripts and full suite**

Run: `python3 -m pytest tests/test_release_scripts.py -v`

Expected: all release-script tests PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_update_manifest.py scripts/sign_update_manifest.py tests/test_release_scripts.py
git commit -m "build: generate and sign update manifests"
```

### Task 15: Add native cross-platform CI and fail-closed release automation

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/release.yml`
- Create: `tests/test_workflow_contract.py`

- [ ] **Step 1: Write failing workflow contract tests**

Parse workflow YAML as text and assert:

```python
def test_ci_uses_all_native_runners():
    workflow = read(".github/workflows/ci.yml")
    for runner in ["macos-14", "windows-2022", "ubuntu-24.04"]:
        assert runner in workflow
    assert "python -m pytest -q" in workflow


def test_release_tests_builds_smokes_signs_hashes_then_publishes():
    workflow = read(".github/workflows/release.yml")
    for required in ["python -m pytest -q", "PyInstaller", "packaged_smoke.py",
                     "build_update_manifest.py", "sign_update_manifest.py",
                     "UPDATE_MANIFEST_PRIVATE_KEY", "PRODUCTION_RELEASE: \"1\""]:
        assert required in workflow
    assert "if: ${{ secrets.UPDATE_MANIFEST_PRIVATE_KEY != '' }}" not in workflow
```

- [ ] **Step 2: Run tests to verify workflows are absent**

Run: `python3 -m pytest tests/test_workflow_contract.py -v`

Expected: FAIL because CI/release workflow files do not exist.

- [ ] **Step 3: Add source CI on native operating systems**

Create `ci.yml` for pull requests and pushes with Python 3.11 and a matrix of `macos-14`, `windows-2022`, and `ubuntu-24.04`; install `requirements-dev.txt` and run `python -m pytest -q`. Linux installs pywebview/GTK build prerequisites but tests use fakes and do not open a permanent window.

- [ ] **Step 4: Add native package jobs and release assembly**

Create tag-triggered `release.yml` (`v*.*.*`) with separate native jobs:

- macOS on `macos-14`: test, PyInstaller `.app`, codesign with imported Developer ID secret, notarize/staple, create signed DMG, run packaged smoke before DMG creation and `spctl` after, hash final DMG.
- Windows on `windows-2022`: test, PyInstaller, sign executable and Inno Setup installer using certificate/password secrets, run packaged smoke before installer creation and `Get-AuthenticodeSignature` after, hash final EXE installer.
- Linux on `ubuntu-24.04`: test, PyInstaller, AppImage assembly, packaged AppImage smoke, hash final AppImage.

Each platform job starts with a shell guard that exits nonzero when its required production signing/notarization secrets are empty. Do not wrap signing in secret-presence conditionals. Upload final artifacts and metadata to a release assembly job. That job downloads all outputs, validates tag equals `APP_VERSION`, builds the manifest using final URLs/digests, runs `PRODUCTION_RELEASE=1 python scripts/sign_update_manifest.py ...` with `UPDATE_MANIFEST_PRIVATE_KEY`, verifies with the embedded public key, then creates the public GitHub Release and uploads artifacts, `update-manifest.json`, and `update-manifest.json.sig`. Give only `contents: write` to the release job and `contents: read` elsewhere.

- [ ] **Step 5: Verify workflow contracts and source suite**

Run: `python3 -m pytest tests/test_workflow_contract.py -v`

Expected: all workflow contract tests PASS.

Run: `python3 -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/release.yml tests/test_workflow_contract.py
git commit -m "ci: build signed desktop releases on native runners"
```

### Task 16: Document desktop use, security boundaries, and release operations

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Create: `docs/desktop-release.md`
- Create: `tests/test_docs_contract.py`

- [ ] **Step 1: Write failing documentation contract tests**

Assert README documents all three launch commands/modes, first-launch Settings, data paths, update approval/defer flow, browser-mode Git updates, and `python3 -m pytest`; assert release docs name every secret, native runner, local unsigned command, production fail-closed behavior, version/tag rule, manifest/public-key rotation procedure, and macOS/Windows/Linux package formats.

- [ ] **Step 2: Run tests to verify missing guidance**

Run: `python3 -m pytest tests/test_docs_contract.py -v`

Expected: FAIL because desktop/release guidance is absent.

- [ ] **Step 3: Update user documentation**

Document:

```text
Browser source mode: python3 run.py -> http://127.0.0.1:8000
Desktop source mode: python3 run_desktop.py -> native pywebview window
Downloaded mode: launch the installed IBKR Lot Tracker package
Tests: python3 -m pip install -r requirements-dev.txt && python3 -m pytest
```

Explain OS data directories, keyring token storage, first-launch Settings, manual/24-hour update checks, signed release notes shown before download, approval, Restart and update, Defer, safe fallback instructions, and that browser mode updates through Git. Keep `.env.example` explicitly browser-development-only and retain loopback defaults.

- [ ] **Step 4: Add maintainer release documentation**

List GitHub Actions secrets with exact purpose: `UPDATE_MANIFEST_PRIVATE_KEY`, `MACOS_CERTIFICATE_P12`, `MACOS_CERTIFICATE_PASSWORD`, `MACOS_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_APP_PASSWORD`, `APPLE_TEAM_ID`, `WINDOWS_CERTIFICATE_PFX`, and `WINDOWS_CERTIFICATE_PASSWORD`. Explain native runner requirement, public-key embedding/rotation, bumping `APP_VERSION`, tagging the identical `vX.Y.Z`, local unsigned build/smoke commands, production guards, final-digest-before-manifest ordering, and rollback by removing a bad release while clients retain the existing executable.

- [ ] **Step 5: Verify docs and complete suite**

Run: `python3 -m pytest tests/test_docs_contract.py -v`

Expected: all documentation contract tests PASS.

Run: `python3 -m pytest -q`

Expected: all unit, API, integration, browser-regression, packaging-contract, workflow-contract, and documentation tests PASS.

- [ ] **Step 6: Commit**

```bash
git add README.md .env.example docs/desktop-release.md tests/test_docs_contract.py
git commit -m "docs: explain desktop updates and release security"
```

### Task 17: Perform final native packaged smoke and release-candidate verification

**Files:**
- Modify only if verification exposes a defect: the exact source/test/packaging file responsible for that defect

- [ ] **Step 1: Run the complete source suite**

Run: `python3 -m pytest -q`

Expected: all tests PASS on the current native platform.

- [ ] **Step 2: Build an unsigned local package**

Run: `python3 -m PyInstaller --clean --noconfirm packaging/ibkr_lot_tracker.spec`

Expected: build succeeds without production signing secrets, demonstrating that local unsigned builds remain possible.

- [ ] **Step 3: Run packaged smoke mode**

Run the current-platform command documented in Task 13.

Expected: exit 0, one loopback health response, output containing `SMOKE_OK`, no permanent pywebview window, and no surviving server process.

- [ ] **Step 4: Manually exercise source launch modes**

Run: `python3 run.py`, request `http://127.0.0.1:8000/api/health`, then stop it.

Expected: HTTP 200 and no desktop-only update action enabled in the page.

Run: `python3 run_desktop.py`, close the final native window, then rerun it.

Expected: the first process shuts down its server and releases the lock; the second starts successfully. While the first packaged process is running, a second packaged launch must report `IBKR Lot Tracker is already running` and exit.

- [ ] **Step 5: Verify production signing fails closed locally**

Run: `env -u UPDATE_MANIFEST_PRIVATE_KEY PRODUCTION_RELEASE=1 python3 scripts/sign_update_manifest.py update-manifest.json`

Expected: nonzero exit with `UPDATE_MANIFEST_PRIVATE_KEY is required`; no signature file is created.

- [ ] **Step 6: Commit verification fixes if any**

If Steps 1-5 expose a code correction, return to the task that owns that behavior, add a failing regression test there, follow its test/implementation/full-suite cycle, and use that task's exact `git add` and commit command. If no correction is needed, do not create an empty commit.

## Final implementation acceptance checklist

- [ ] Browser `run.py`, source `run_desktop.py`, and frozen downloaded launch modes share one FastAPI app/frontend and bind only to `127.0.0.1`.
- [ ] Immutable bundled resources and macOS/Windows/Linux user-writable data paths are separate.
- [ ] Query ID/preferences persist in JSON, Flex token stays in OS keyring, temporary validation errors preserve working credentials, and APIs/logs never expose secrets.
- [ ] Settings and all seven update states are visible; release notes precede user-approved download; restart can be deferred; browser update controls are disabled.
- [ ] SemVer, stable releases, updater minimum, exact OS/architecture/package match, Ed25519 signature-before-parse, HTTPS/GitHub redirect policy, size, and SHA-256 are enforced.
- [ ] Downloads use `.partial` staging and atomic completion; cancellation/failure leaves the current installation usable.
- [ ] The helper waits for exit, safely handles DMG/per-user EXE/AppImage installation, restores or preserves the old executable on failure, and offers concise interactive fallback.
- [ ] Single-instance locking, readiness, final-window shutdown, server-start native errors, redacted logs, and no-window packaged smoke mode are covered.
- [ ] PyInstaller and each native package format are built and smoke-tested on matching native CI runners.
- [ ] Production macOS/Windows package signing and manifest signing fail closed when secrets are absent; explicit local unsigned builds work.
- [ ] Complete unit, API, update integration, browser regression, packaging, workflow, docs, and packaged smoke checks pass.
