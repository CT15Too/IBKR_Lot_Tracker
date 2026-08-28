import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional, Union


@dataclass
class DesktopSettings:
    schema_version: int = 1
    flex_query_id: str = ""
    auto_check_updates: bool = True
    last_update_check_at: Optional[str] = None


class SettingsStore:
    _locks = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        lock_key = self.path.resolve()
        with self._locks_guard:
            self._lock = self._locks.setdefault(lock_key, threading.RLock())

    def load(self) -> DesktopSettings:
        if not self.path.exists():
            return DesktopSettings()

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        known_keys = {field.name for field in fields(DesktopSettings)}
        values = {key: value for key, value in raw.items() if key in known_keys}
        values.setdefault("schema_version", 1)
        return DesktopSettings(**values)

    def save(self, settings: DesktopSettings) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            try:
                os.chmod(temporary_path, 0o600)
                with os.fdopen(
                    descriptor, "w", encoding="utf-8"
                ) as temporary_file:
                    descriptor = None
                    json.dump(asdict(settings), temporary_file, sort_keys=True)
                    temporary_file.write("\n")
                    temporary_file.flush()
                    os.fsync(temporary_file.fileno())
                os.replace(temporary_path, self.path)
                os.chmod(self.path, 0o600)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                temporary_path.unlink(missing_ok=True)
