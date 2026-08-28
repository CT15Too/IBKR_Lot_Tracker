import dataclasses
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from backend.runtime import LaunchMode

from .download import (
    DownloadCancellationToken,
    DownloadCancelled,
    DownloadError,
)
from .http import UpdateNetworkError
from .manifest import ManifestError
from .models import UpdateSnapshot, UpdateStatus, VerifiedUpdate


class UpdateTransitionError(RuntimeError):
    pass


_CHECK_INTERVAL = timedelta(hours=24)
_CHECKABLE = {
    UpdateStatus.IDLE,
    UpdateStatus.UP_TO_DATE,
    UpdateStatus.FAILED,
}


class UpdateService:
    def __init__(
        self,
        *,
        runtime,
        settings_store,
        discovery: Callable[[], Optional[VerifiedUpdate]],
        downloader,
        installer=None,
        clock=None,
    ):
        self.runtime = runtime
        self._settings_store = settings_store
        self._discovery = discovery
        self._downloader = downloader
        self._installer = installer
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._status = UpdateStatus.IDLE
        self._update = None
        self._error = None
        self._staged_path = None
        self._cancel = None
        self._launch_committed = False
        try:
            self._last_checked_at = (
                self._settings_store.load().last_update_check_at
            )
        except Exception:
            self._last_checked_at = None

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _require_status(self, *allowed: UpdateStatus) -> None:
        if self._status not in allowed:
            expected = ", ".join(status.value for status in allowed)
            raise UpdateTransitionError(
                "Cannot perform this action while update state is {}; "
                "expected {}".format(self._status.value, expected)
            )

    def _persist_completed_check(self, completed_at: datetime) -> None:
        settings = self._settings_store.load()
        timestamp = completed_at.isoformat()
        self._settings_store.save(
            dataclasses.replace(settings, last_update_check_at=timestamp)
        )
        self._last_checked_at = timestamp

    def snapshot(self) -> UpdateSnapshot:
        with self._lock:
            return UpdateSnapshot(
                status=self._status,
                version=self._update.version if self._update else None,
                release_notes=(
                    self._update.release_notes if self._update else None
                ),
                error=self._error,
                last_checked_at=self._last_checked_at,
            )

    def check(self, *, manual: bool = False) -> bool:
        with self._lock:
            if self.runtime.mode is not LaunchMode.PACKAGED_DESKTOP:
                return False
            settings = self._settings_store.load()
            if not manual:
                if not settings.auto_check_updates:
                    return False
                now = self._now()
                last = self._parse_timestamp(settings.last_update_check_at)
                if last is not None and last > now:
                    clamped = now.isoformat()
                    self._last_checked_at = clamped
                    try:
                        self._settings_store.save(
                            dataclasses.replace(
                                settings, last_update_check_at=clamped
                            )
                        )
                    except Exception:
                        pass
                    return False
                if (
                    last is not None
                    and now - last < _CHECK_INTERVAL
                ):
                    self._last_checked_at = settings.last_update_check_at
                    return False
                if self._status not in _CHECKABLE:
                    return False
            else:
                self._require_status(*_CHECKABLE)
            self._status = UpdateStatus.CHECKING
            self._error = None

        update = None
        check_error = None
        try:
            update = self._discovery()
        except (UpdateNetworkError, ManifestError) as exc:
            check_error = str(exc)[:500]
        except Exception:
            check_error = "Update check failed"

        completed_at = self._now()
        try:
            self._persist_completed_check(completed_at)
        except Exception:
            check_error = "Could not persist update check time"

        with self._lock:
            if check_error is not None:
                self._status = UpdateStatus.FAILED
                self._error = check_error
            elif update is None:
                self._status = UpdateStatus.UP_TO_DATE
                self._update = None
                self._error = None
            elif not isinstance(update, VerifiedUpdate):
                self._status = UpdateStatus.FAILED
                self._error = "Update discovery returned invalid data"
            else:
                self._status = UpdateStatus.UPDATE_AVAILABLE
                self._update = update
                self._error = None
            return True

    def approve_download(self) -> UpdateSnapshot:
        with self._lock:
            self._require_status(UpdateStatus.UPDATE_AVAILABLE)
            update = self._update
            if update is None:
                raise UpdateTransitionError(
                    "No verified update is available for download"
                )
            self._status = UpdateStatus.DOWNLOADING
            self._error = None
            self._cancel = DownloadCancellationToken()
            cancel = self._cancel

        try:
            staged_path = self._downloader(
                update.artifact, self.runtime.staging_dir, cancel
            )
        except DownloadCancelled:
            with self._lock:
                self._status = UpdateStatus.UPDATE_AVAILABLE
                self._error = None
                self._cancel = None
            return self.snapshot()
        except DownloadError as exc:
            with self._lock:
                self._status = UpdateStatus.FAILED
                self._error = str(exc)[:500]
                self._cancel = None
            return self.snapshot()
        except Exception:
            with self._lock:
                self._status = UpdateStatus.FAILED
                self._error = "Update download failed"
                self._cancel = None
            return self.snapshot()

        with self._lock:
            self._staged_path = Path(staged_path)
            self._cancel = None
            self._launch_committed = False
            self._status = UpdateStatus.READY_TO_RESTART
            return self.snapshot()

    def cancel_download(self) -> UpdateSnapshot:
        with self._lock:
            self._require_status(UpdateStatus.DOWNLOADING)
            if self._cancel is None:
                raise UpdateTransitionError("No update download can be cancelled")
            self._cancel.set()
            return self.snapshot()

    def defer(self) -> UpdateSnapshot:
        with self._lock:
            self._require_status(
                UpdateStatus.UPDATE_AVAILABLE, UpdateStatus.READY_TO_RESTART
            )
            self._status = UpdateStatus.IDLE
            self._error = None
            return self.snapshot()

    def restart_and_update(self):
        with self._lock:
            if self._launch_committed:
                raise UpdateTransitionError(
                    "Update installer launch is already committed"
                )
            self._require_status(UpdateStatus.READY_TO_RESTART)
            if self._installer is None or self._staged_path is None:
                raise UpdateTransitionError(
                    "No staged update installer is available"
                )
            installer = self._installer
            staged_path = self._staged_path
            self._launch_committed = True
        try:
            return installer(staged_path)
        except Exception:
            with self._lock:
                self._status = UpdateStatus.FAILED
                self._error = "Could not start the update installer"
                self._launch_committed = False
            raise
