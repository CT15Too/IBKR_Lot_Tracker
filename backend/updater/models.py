from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class UpdateStatus(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UPDATE_AVAILABLE = "update_available"
    DOWNLOADING = "downloading"
    READY_TO_RESTART = "ready_to_restart"
    UP_TO_DATE = "up_to_date"
    FAILED = "failed"


@dataclass(frozen=True)
class PlatformIdentity:
    os: str
    arch: str
    package: str


@dataclass(frozen=True)
class Artifact:
    os: str
    arch: str
    package: str
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class VerifiedUpdate:
    version: str
    release_notes: str
    artifact: Artifact


@dataclass(frozen=True)
class UpdateSnapshot:
    status: UpdateStatus
    version: Optional[str] = None
    release_notes: Optional[str] = None
    error: Optional[str] = None
    progress: Optional[float] = None
    last_checked_at: Optional[str] = None

    def to_public_dict(self) -> Dict[str, Any]:
        public: Dict[str, Any] = {"status": self.status.value}
        for key in (
            "version",
            "release_notes",
            "error",
            "progress",
            "last_checked_at",
        ):
            value = getattr(self, key)
            if value is not None:
                public[key] = value
        return public
