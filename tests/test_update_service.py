import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.runtime import LaunchMode
from backend.settings_store import DesktopSettings, SettingsStore
from backend.updater.download import DownloadCancelled
from backend.updater.http import UpdateNetworkError
from backend.updater.models import (
    Artifact,
    UpdateSnapshot,
    UpdateStatus,
    VerifiedUpdate,
)
from backend.updater.service import UpdateService, UpdateTransitionError


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class FakeDiscovery:
    def __init__(self, update):
        self.update = update
        self.raise_error = None
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.raise_error:
            raise self.raise_error
        return self.update


class FakeDownloader:
    def __init__(self):
        self.calls = 0

    def __call__(self, artifact, staging_dir, cancel):
        self.calls += 1
        path = Path(staging_dir) / artifact.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"verified")
        return path


class BlockingDownloader:
    def __init__(self):
        self.started = threading.Event()

    def __call__(self, artifact, staging_dir, cancel):
        self.started.set()
        cancel.wait(timeout=2)
        raise DownloadCancelled("cancelled")


class BlockingInstaller:
    def __init__(self, *, fail=False):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.fail = fail

    def __call__(self, path):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=2)
        if self.fail:
            raise RuntimeError("launch failed")
        return path.name


@pytest.fixture
def update():
    return VerifiedUpdate(
        version="1.2.0",
        release_notes="Safer desktop updates.",
        artifact=Artifact(
            os="macos",
            arch="arm64",
            package="dmg",
            name="IBKR-Lot-Tracker.dmg",
            url="https://github.com/owner/repo/releases/download/v1/app.dmg",
            size=1,
            sha256="a" * 64,
        ),
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def service_parts(tmp_path, update, clock):
    runtime = SimpleNamespace(
        mode=LaunchMode.PACKAGED_DESKTOP, staging_dir=tmp_path / "updates"
    )
    store = SettingsStore(tmp_path / "settings.json")
    discovery = FakeDiscovery(update)
    downloader = FakeDownloader()
    service = UpdateService(
        runtime=runtime,
        settings_store=store,
        discovery=discovery,
        downloader=downloader,
        installer=lambda path: path.name,
        clock=clock,
    )
    return service, store, discovery, downloader


@pytest.fixture
def service(service_parts):
    return service_parts[0]


def test_manual_check_download_and_ready_flow(service):
    assert service.snapshot().status == UpdateStatus.IDLE
    assert service.check(manual=True) is True
    assert service.snapshot().status == UpdateStatus.UPDATE_AVAILABLE
    assert service.snapshot().release_notes == "Safer desktop updates."
    service.approve_download()
    snapshot = service.snapshot()
    assert snapshot.status == UpdateStatus.READY_TO_RESTART
    assert not hasattr(snapshot, "staged_path")
    assert "staged_path" not in snapshot.to_public_dict()


def test_automatic_check_runs_at_most_once_per_24_hours(
    service_parts, clock, update
):
    service, store, discovery, _ = service_parts
    discovery.update = None
    assert service.check(manual=False) is True
    assert service.snapshot().status == UpdateStatus.UP_TO_DATE
    assert service.check(manual=False) is False
    assert discovery.calls == 1
    persisted = store.load().last_update_check_at
    assert persisted == "2026-08-28T12:00:00+00:00"

    restarted = UpdateService(
        runtime=service.runtime,
        settings_store=store,
        discovery=discovery,
        downloader=FakeDownloader(),
        clock=clock,
    )
    assert restarted.snapshot().last_checked_at == persisted
    assert restarted.check(manual=False) is False
    clock.advance(hours=24)
    assert restarted.check(manual=False) is True
    assert discovery.calls == 2


def test_manual_checks_bypass_persisted_cadence(service_parts):
    service, _, discovery, _ = service_parts
    discovery.update = None
    service.check(manual=False)
    assert service.check(manual=True) is True
    assert discovery.calls == 2


def test_future_persisted_check_is_clamped_and_remains_recent(
    tmp_path, update, clock
):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(
        DesktopSettings(
            last_update_check_at=(clock() + timedelta(days=7)).isoformat()
        )
    )
    discovery = FakeDiscovery(update)
    runtime = SimpleNamespace(
        mode=LaunchMode.PACKAGED_DESKTOP, staging_dir=tmp_path / "updates"
    )
    service = UpdateService(
        runtime=runtime,
        settings_store=store,
        discovery=discovery,
        downloader=FakeDownloader(),
        clock=clock,
    )

    assert service.check(manual=False) is False
    assert discovery.calls == 0
    assert store.load().last_update_check_at == clock().isoformat()

    clock.advance(hours=23)
    restarted = UpdateService(
        runtime=runtime,
        settings_store=store,
        discovery=discovery,
        downloader=FakeDownloader(),
        clock=clock,
    )
    assert restarted.check(manual=False) is False
    clock.advance(hours=1)
    assert restarted.check(manual=False) is True


def test_failures_preserve_use_of_application_and_hide_paths(service_parts):
    service, store, discovery, _ = service_parts
    discovery.raise_error = UpdateNetworkError("GitHub rate limit exceeded")
    assert service.check(manual=True) is True
    public = service.snapshot().to_public_dict()
    assert public["status"] == "failed"
    assert public["error"] == "GitHub rate limit exceeded"
    assert "staged_path" not in public
    assert store.load().last_update_check_at is not None


def test_unexpected_errors_do_not_expose_exception_details(service_parts):
    service, _, discovery, _ = service_parts
    discovery.raise_error = RuntimeError("token=top-secret")
    service.check(manual=True)
    assert service.snapshot().error == "Update check failed"
    assert "top-secret" not in str(service.snapshot().to_public_dict())


def test_invalid_transition_is_rejected(service):
    with pytest.raises(UpdateTransitionError, match="idle"):
        service.approve_download()


def test_cancel_is_synchronized_and_returns_to_available(
    tmp_path, update, clock
):
    runtime = SimpleNamespace(
        mode=LaunchMode.PACKAGED_DESKTOP, staging_dir=tmp_path / "updates"
    )
    downloader = BlockingDownloader()
    service = UpdateService(
        runtime=runtime,
        settings_store=SettingsStore(tmp_path / "settings.json"),
        discovery=FakeDiscovery(update),
        downloader=downloader,
        clock=clock,
    )
    service.check(manual=True)
    thread = threading.Thread(target=service.approve_download)
    thread.start()
    assert downloader.started.wait(timeout=1)
    assert service.snapshot().status == UpdateStatus.DOWNLOADING
    service.cancel_download()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert service.snapshot().status == UpdateStatus.UPDATE_AVAILABLE


def test_defer_preserves_verified_file_but_snapshot_hides_it(service):
    service.check(manual=True)
    service.approve_download()
    staged_files = list(service.runtime.staging_dir.iterdir())
    service.defer()
    assert service.snapshot().status == UpdateStatus.IDLE
    assert staged_files[0].exists()
    assert "path" not in str(service.snapshot().to_public_dict()).lower()


@pytest.mark.parametrize(
    "mode", [LaunchMode.BROWSER, LaunchMode.SOURCE_DESKTOP]
)
def test_non_packaged_modes_skip_update_checks(tmp_path, update, clock, mode):
    discovery = FakeDiscovery(update)
    service = UpdateService(
        runtime=SimpleNamespace(mode=mode, staging_dir=tmp_path / "updates"),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        discovery=discovery,
        downloader=FakeDownloader(),
        clock=clock,
    )
    assert service.check(manual=False) is False
    assert discovery.calls == 0


def test_disabled_automatic_checks_are_skipped(tmp_path, update, clock):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(DesktopSettings(auto_check_updates=False))
    discovery = FakeDiscovery(update)
    service = UpdateService(
        runtime=SimpleNamespace(
            mode=LaunchMode.PACKAGED_DESKTOP, staging_dir=tmp_path / "updates"
        ),
        settings_store=store,
        discovery=discovery,
        downloader=FakeDownloader(),
        clock=clock,
    )
    assert service.check(manual=False) is False
    assert discovery.calls == 0


def test_restart_uses_internal_staged_path_without_exposing_it(service):
    service.check(manual=True)
    service.approve_download()
    assert service.restart_and_update() == "IBKR-Lot-Tracker.dmg"
    assert "path" not in service.snapshot().to_public_dict()


def test_restart_commits_launch_before_calling_external_installer(
    tmp_path, update, clock
):
    installer = BlockingInstaller()
    service = UpdateService(
        runtime=SimpleNamespace(
            mode=LaunchMode.PACKAGED_DESKTOP, staging_dir=tmp_path / "updates"
        ),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        discovery=FakeDiscovery(update),
        downloader=FakeDownloader(),
        installer=installer,
        clock=clock,
    )
    service.check(manual=True)
    service.approve_download()

    thread = threading.Thread(target=service.restart_and_update)
    thread.start()
    assert installer.started.wait(timeout=1)
    with pytest.raises(UpdateTransitionError, match="already"):
        service.restart_and_update()
    installer.release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert installer.calls == 1
    with pytest.raises(UpdateTransitionError, match="already"):
        service.restart_and_update()


def test_restart_launch_failure_transitions_failed_without_retry(
    tmp_path, update, clock
):
    installer = BlockingInstaller(fail=True)
    installer.release.set()
    service = UpdateService(
        runtime=SimpleNamespace(
            mode=LaunchMode.PACKAGED_DESKTOP, staging_dir=tmp_path / "updates"
        ),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        discovery=FakeDiscovery(update),
        downloader=FakeDownloader(),
        installer=installer,
        clock=clock,
    )
    service.check(manual=True)
    service.approve_download()

    with pytest.raises(RuntimeError, match="launch failed"):
        service.restart_and_update()
    assert service.snapshot().status == UpdateStatus.FAILED
    with pytest.raises(UpdateTransitionError, match="failed"):
        service.restart_and_update()
    assert installer.calls == 1
