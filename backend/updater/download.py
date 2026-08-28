import hashlib
import hmac
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from .models import Artifact


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(DownloadError):
    pass


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()+-]*$")


def _validated_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or len(name.encode("utf-8")) > 240
        or not _SAFE_NAME.fullmatch(name)
    ):
        raise DownloadError("Artifact name is unsafe")
    return name


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def download_artifact(
    client,
    artifact: Artifact,
    staging_dir,
    *,
    cancel: Optional[object] = None,
) -> Path:
    name = _validated_name(artifact.name)
    directory = Path(staging_dir)
    if directory.is_symlink():
        raise DownloadError("Staging directory must not be a symbolic link")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    final = directory / name
    descriptor = None
    partial = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="{}.".format(name), suffix=".partial", dir=str(directory)
        )
        partial = Path(temporary_name)
        os.chmod(partial, 0o600)
        digest = hashlib.sha256()
        written = 0
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled("Update download was cancelled")
            for chunk in client.stream(artifact.url):
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled("Update download was cancelled")
                if not isinstance(chunk, bytes):
                    raise DownloadError("Update download returned invalid data")
                if not chunk:
                    continue
                written += len(chunk)
                if written > artifact.size:
                    raise DownloadError("Downloaded artifact size exceeds manifest size")
                output.write(chunk)
                digest.update(chunk)
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled("Update download was cancelled")
            output.flush()
            os.fsync(output.fileno())

        if written != artifact.size:
            raise DownloadError("Downloaded artifact size does not match manifest")
        if not hmac.compare_digest(digest.hexdigest(), artifact.sha256):
            raise DownloadError("Downloaded artifact sha256 does not match manifest")
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("Update download was cancelled")
        os.replace(partial, final)
        partial = None
        os.chmod(final, 0o600)
        _fsync_directory(directory)
        return final
    except (DownloadCancelled, DownloadError):
        raise
    except Exception as exc:
        raise DownloadError("Update download was interrupted") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if partial is not None:
            partial.unlink(missing_ok=True)
