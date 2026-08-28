import json
import os
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import settings_store as settings_store_module
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


def test_settings_save_is_atomic(tmp_path):
    path = tmp_path / "nested/settings.json"
    SettingsStore(path).save(DesktopSettings(flex_query_id="42"))

    assert json.loads(path.read_text())["flex_query_id"] == "42"
    assert not [candidate for candidate in path.parent.iterdir() if candidate != path]


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not portable")
def test_settings_file_permissions_are_private(tmp_path):
    path = tmp_path / "settings.json"
    SettingsStore(path).save(DesktopSettings())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_concurrent_store_instances_use_unique_atomic_files(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    stores = [SettingsStore(path), SettingsStore(path)]
    settings = [
        DesktopSettings(flex_query_id="first"),
        DesktopSettings(flex_query_id="second"),
    ]
    sources = []
    sources_lock = threading.Lock()
    real_replace = settings_store_module.os.replace

    def delayed_replace(source, destination):
        with sources_lock:
            sources.append(source)
        time.sleep(0.05)
        real_replace(source, destination)

    monkeypatch.setattr(settings_store_module.os, "replace", delayed_replace)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(store.save, value)
            for store, value in zip(stores, settings)
        ]
        for future in futures:
            future.result()

    assert len(set(sources)) == 2
    assert {source.parent for source in sources} == {path.parent}
    assert json.loads(path.read_text())["flex_query_id"] in {"first", "second"}
    assert not [candidate for candidate in path.parent.iterdir() if candidate != path]


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
