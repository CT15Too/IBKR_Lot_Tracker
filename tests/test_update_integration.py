import base64
import hashlib
import json
import os
import threading
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from backend.credentials import CredentialStore
from backend.main import create_app
from backend.runtime import LaunchMode, build_runtime
from backend.settings_store import SettingsStore
from backend.updater.download import download_artifact
from backend.updater.install import InstallRequest, launch_helper
from backend.updater.manifest import verify_and_select
from backend.updater.models import PlatformIdentity
from backend.updater.service import UpdateService


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[(service, username)] = value

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


class MemoryDownloadClient:
    def __init__(self, payload):
        self.payload = payload
        self.external_requests = []

    def stream(self, url):
        assert url.startswith("https://github.com/")
        yield self.payload[:5]
        yield self.payload[5:]


def signed_release(payload):
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    body = json.dumps(
        {
            "schema_version": 1,
            "version": "1.2.0",
            "minimum_updater_version": "1.0.0",
            "release_notes": "Safer desktop updates.",
            "artifacts": [
                {
                    "os": "macos",
                    "arch": "arm64",
                    "package": "dmg",
                    "name": "IBKR-Lot-Tracker-1.2.0.dmg",
                    "url": "https://github.com/CT15Too/IBKR_Lot_Tracker/releases/download/v1.2.0/app.dmg",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = base64.b64encode(private.sign(body)).decode()
    return body, signature, public


def build_harness(tmp_path, *, fail_helper=False):
    payload = b"signed installer bytes"
    body, signature, public = signed_release(payload)
    runtime = build_runtime(
        LaunchMode.PACKAGED_DESKTOP,
        platform_name="darwin",
        home=tmp_path,
    )
    settings = SettingsStore(runtime.settings_path)
    download_client = MemoryDownloadClient(payload)
    launcher = SimpleNamespace(calls=0, request=None)

    def discover():
        return verify_and_select(
            body,
            signature,
            public,
            current_version="1.1.0",
            updater_version="1.0.0",
            platform=PlatformIdentity("macos", "arm64", "dmg"),
        )

    def downloader(artifact, staging_dir, cancel):
        return download_artifact(
            download_client,
            artifact,
            staging_dir,
            cancel=cancel,
        )

    def installer(staged_path):
        launcher.calls += 1
        launcher.request = InstallRequest(
            platform="macos",
            staged_path=str(staged_path),
            current_path="/Applications/IBKR Lot Tracker.app",
            relaunch_command=("open", "/Applications/IBKR Lot Tracker.app"),
            parent_pid=os.getpid(),
            diagnostic_path=str(runtime.data_dir / "update-diagnostic.json"),
        )

        def process_launcher(command, **kwargs):
            if fail_helper:
                raise OSError("process launch denied")
            launcher.command = command
            launcher.kwargs = kwargs
            return object()

        return launch_helper(
            launcher.request,
            launcher=process_launcher,
            executable="/Applications/IBKR Lot Tracker.app/Contents/MacOS/IBKR Lot Tracker",
            request_path=runtime.data_dir / "helper-request.json",
            platform_name="darwin",
        )

    updater = UpdateService(
        runtime=runtime,
        settings_store=settings,
        discovery=discover,
        downloader=downloader,
        installer=installer,
    )
    shutdown = threading.Event()
    app = create_app(
        runtime,
        credential_store=CredentialStore(MemoryKeyring()),
        settings_store=settings,
        update_service=updater,
        request_shutdown=shutdown.set,
    )
    return SimpleNamespace(
        client=TestClient(app),
        updater=updater,
        launcher=launcher,
        shutdown_requested=shutdown,
        artifact_bytes=payload,
        staged_artifact=runtime.staging_dir / "IBKR-Lot-Tracker-1.2.0.dmg",
        external_requests=download_client.external_requests,
    )


def test_signed_release_reaches_helper_without_external_side_effects(tmp_path):
    harness = build_harness(tmp_path)
    client = harness.client

    assert client.post("/api/updates/check").json()["status"] == "update_available"
    available = client.get("/api/updates/status").json()
    assert available["version"] == "1.2.0"
    assert available["release_notes"] == "Safer desktop updates."

    ready = client.post("/api/updates/download").json()
    assert ready["status"] == "ready_to_restart"
    assert harness.staged_artifact.read_bytes() == harness.artifact_bytes

    response = client.post("/api/updates/restart")
    assert response.status_code == 202
    assert harness.launcher.calls == 1
    assert harness.launcher.request.parent_pid == os.getpid()
    assert harness.shutdown_requested.is_set()
    assert harness.external_requests == []

    duplicate = client.post("/api/updates/restart")
    assert duplicate.status_code == 409
    assert harness.launcher.calls == 1


def test_failed_helper_launch_keeps_desktop_running(tmp_path):
    harness = build_harness(tmp_path, fail_helper=True)
    harness.client.post("/api/updates/check")
    harness.client.post("/api/updates/download")

    response = harness.client.post("/api/updates/restart")

    assert response.status_code == 500
    assert harness.launcher.calls == 1
    assert harness.shutdown_requested.is_set() is False
    assert harness.client.get("/api/updates/status").json() == {
        "status": "failed",
        "version": "1.2.0",
        "release_notes": "Safer desktop updates.",
        "error": "Could not start the update installer",
        "last_checked_at": harness.updater.snapshot().last_checked_at,
    }
