import json
import stat

import pytest

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
    assert loaded == DesktopSettings(
        schema_version=1, flex_query_id="", auto_check_updates=True
    )


def test_legacy_settings_are_migrated_without_secret(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"flex_query_id": "42", "token": "secret"}))
    store = SettingsStore(path)

    loaded = store.load()
    store.save(loaded)

    assert loaded.schema_version == 1
    assert loaded.flex_query_id == "42"
    assert "token" not in path.read_text()


def test_settings_save_is_atomic_and_private(tmp_path):
    path = tmp_path / "nested/settings.json"
    SettingsStore(path).save(DesktopSettings(flex_query_id="42"))

    assert json.loads(path.read_text())["flex_query_id"] == "42"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.with_name("settings.json.tmp").exists()


def test_token_round_trip_only_crosses_keyring_boundary():
    backend = MemoryKeyring()
    store = CredentialStore(backend)
    store.set_token("secret")
    assert store.has_token() is True
    assert store.get_token() == "secret"
    store.clear_token()
    assert store.has_token() is False


def test_empty_token_is_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        CredentialStore(MemoryKeyring()).set_token("")
