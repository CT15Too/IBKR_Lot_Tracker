from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

from backend.credentials import CredentialStore
from backend.ibkr_flex import FlexServiceError
from backend.main import create_app
from backend.runtime import LaunchMode, build_runtime
from backend.settings_store import SettingsStore
from backend.updater.models import UpdateSnapshot, UpdateStatus


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[(service, username)] = value

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


class FakeUpdater:
    def __init__(self):
        self.state = UpdateSnapshot(UpdateStatus.IDLE)
        self.restart_calls = 0

    def snapshot(self):
        return self.state

    def check(self, *, manual):
        self.state = UpdateSnapshot(
            UpdateStatus.UPDATE_AVAILABLE,
            version="1.2.0",
            release_notes="Notes",
        )

    def approve_download(self):
        self.state = UpdateSnapshot(UpdateStatus.READY_TO_RESTART, version="1.2.0")
        return self.state

    def cancel_download(self):
        return self.state

    def defer(self):
        self.state = UpdateSnapshot(UpdateStatus.IDLE)
        return self.state

    def restart_and_update(self):
        self.restart_calls += 1


@pytest.fixture
def api(tmp_path):
    runtime = build_runtime(
        LaunchMode.PACKAGED_DESKTOP,
        platform_name="darwin",
        home=tmp_path,
    )
    keyring = CredentialStore(MemoryKeyring())
    store = SettingsStore(tmp_path / "settings.json")
    updater = FakeUpdater()
    validator = Mock()
    shutdown = Mock()
    app = create_app(
        runtime,
        credential_store=keyring,
        settings_store=store,
        update_service=updater,
        validate_credentials=validator,
        request_shutdown=shutdown,
    )
    return SimpleNamespace(
        client=TestClient(app),
        keyring=keyring,
        store=store,
        updater=updater,
        validator=validator,
        shutdown=shutdown,
    )


def test_health_and_get_settings_never_return_token(api):
    assert api.client.get("/api/health").json() == {"ok": True}
    body = api.client.get("/api/settings").json()
    assert body == {
        "mode": "packaged_desktop",
        "desktop": True,
        "configured": False,
        "flex_query_id": "",
        "has_flex_token": False,
        "auto_check_updates": True,
        "app_version": "0.1.0",
    }
    assert "token" not in body


def test_save_settings_validates_before_persisting_secret(api):
    response = api.client.put(
        "/api/settings",
        json={
            "flex_query_id": "12345",
            "flex_token": "secret",
            "auto_check_updates": False,
        },
    )
    assert response.status_code == 200
    api.validator.assert_called_once_with("secret", "12345")
    assert api.keyring.get_token() == "secret"
    assert api.store.load().flex_query_id == "12345"
    assert response.json()["has_flex_token"] is True
    assert "flex_token" not in response.json()


def test_temporary_validation_failure_preserves_working_credentials(api):
    api.keyring.set_token("working")
    api.store.save(replace(api.store.load(), flex_query_id="old"))
    api.validator.side_effect = FlexServiceError("network unavailable")

    response = api.client.put(
        "/api/settings",
        json={"flex_query_id": "12345", "flex_token": "new"},
    )

    assert response.status_code == 503
    assert api.keyring.get_token() == "working"
    assert api.store.load().flex_query_id == "old"


def test_transport_validation_failure_preserves_working_credentials(api):
    api.keyring.set_token("working")
    api.store.save(replace(api.store.load(), flex_query_id="old"))
    api.validator.side_effect = requests.Timeout("temporary network failure")

    response = api.client.put(
        "/api/settings",
        json={"flex_query_id": "12345", "flex_token": "new-secret"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "IBKR could not validate those credentials; existing settings were kept."
    )
    assert "new-secret" not in response.text
    assert api.keyring.get_token() == "working"
    assert api.store.load().flex_query_id == "old"


def test_token_omission_preserves_and_explicit_clear_removes(api):
    api.keyring.set_token("working")
    response = api.client.put("/api/settings", json={"flex_query_id": "12345"})
    assert response.status_code == 200
    assert api.keyring.get_token() == "working"
    assert api.validator.call_args.args == ("working", "12345")

    response = api.client.put(
        "/api/settings",
        json={"flex_query_id": "12345", "clear_flex_token": True},
    )
    assert response.status_code == 200
    assert api.keyring.has_token() is False


def test_update_routes_delegate_without_external_work(api):
    assert api.client.post("/api/updates/check").json()["status"] == "update_available"
    assert api.client.post("/api/updates/download").json()["status"] == "ready_to_restart"
    assert api.client.post("/api/updates/restart").status_code == 202
    assert api.updater.restart_calls == 1
    assert api.shutdown.call_count == 1


def test_browser_mode_disables_update_mutations(tmp_path):
    runtime = build_runtime(
        LaunchMode.BROWSER,
        browser_database_path=tmp_path / "browser.db",
    )
    client = TestClient(create_app(runtime, update_service=FakeUpdater()))
    for action in ("check", "download", "cancel", "defer", "restart"):
        assert client.post(f"/api/updates/{action}").status_code == 409
