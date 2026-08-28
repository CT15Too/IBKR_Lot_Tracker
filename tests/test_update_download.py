import dataclasses
import hashlib
import os
import threading

import pytest

from backend.updater.download import (
    DownloadCancellationToken,
    DownloadCancelled,
    DownloadError,
    download_artifact,
)
from backend.updater.models import Artifact


class FakeClient:
    def __init__(self, chunks):
        self.chunks = chunks
        self.requested_url = None

    def stream(self, url):
        self.requested_url = url
        yield from self.chunks


class InterruptingClient:
    def stream(self, url):
        yield b"a"
        raise OSError("connection secret")


def artifact_for(payload, name="IBKR-Lot-Tracker.dmg"):
    return Artifact(
        os="macos",
        arch="arm64",
        package="dmg",
        name=name,
        url="https://github.com/owner/repo/releases/download/v1/app.dmg",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def test_verified_download_atomically_loses_partial_suffix(tmp_path):
    payload = b"signed installer"
    artifact = artifact_for(payload)
    client = FakeClient([payload[:4], payload[4:]])
    result = download_artifact(client, artifact, tmp_path)
    assert result == tmp_path / artifact.name
    assert result.read_bytes() == payload
    assert client.requested_url == artifact.url
    assert not list(tmp_path.glob("*.partial"))
    assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_interruption_keeps_current_install_and_removes_only_own_partial(tmp_path):
    unrelated = tmp_path / "other.partial"
    unrelated.write_bytes(b"keep")
    final = tmp_path / "IBKR-Lot-Tracker.dmg"
    final.write_bytes(b"existing verified version")

    with pytest.raises(DownloadError, match="interrupted"):
        download_artifact(
            InterruptingClient(), artifact_for(b"abc"), tmp_path
        )

    assert final.read_bytes() == b"existing verified version"
    assert unrelated.read_bytes() == b"keep"
    assert list(tmp_path.glob("IBKR-Lot-Tracker.dmg.*.partial")) == []


def test_cancel_removes_partial(tmp_path):
    cancel = DownloadCancellationToken()
    cancel.cancel()
    with pytest.raises(DownloadCancelled):
        download_artifact(
            FakeClient([b"abc"]),
            artifact_for(b"abc"),
            tmp_path,
            cancel=cancel,
        )
    assert not list(tmp_path.iterdir())


def test_cancel_is_serialized_with_atomic_publication(tmp_path, monkeypatch):
    token = DownloadCancellationToken()
    entered_replace = threading.Event()
    allow_replace = threading.Event()
    cancel_returned = threading.Event()
    errors = []
    real_replace = os.replace

    def blocking_replace(source, destination):
        entered_replace.set()
        assert allow_replace.wait(timeout=2)
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", blocking_replace)
    artifact = artifact_for(b"abc")

    def run_download():
        try:
            download_artifact(
                FakeClient([b"abc"]), artifact, tmp_path, cancel=token
            )
        except Exception as exc:
            errors.append(exc)

    download_thread = threading.Thread(target=run_download)
    download_thread.start()
    assert entered_replace.wait(timeout=1)
    cancel_thread = threading.Thread(
        target=lambda: (token.cancel(), cancel_returned.set())
    )
    cancel_thread.start()
    assert not cancel_returned.wait(timeout=0.1)
    allow_replace.set()
    download_thread.join(timeout=2)
    cancel_thread.join(timeout=2)

    assert errors == []
    assert cancel_returned.is_set()
    assert (tmp_path / artifact.name).read_bytes() == b"abc"


@pytest.mark.parametrize("change", ["size", "sha256"])
def test_mismatch_is_never_installable(tmp_path, change):
    artifact = dataclasses.replace(
        artifact_for(b"abc"),
        **{change: 99 if change == "size" else "0" * 64},
    )
    with pytest.raises(DownloadError, match=change):
        download_artifact(FakeClient([b"abc"]), artifact, tmp_path)
    assert not (tmp_path / artifact.name).exists()
    assert not list(tmp_path.glob("*.partial"))


def test_oversize_stream_stops_without_publishing(tmp_path):
    artifact = artifact_for(b"abc")
    with pytest.raises(DownloadError, match="size"):
        download_artifact(FakeClient([b"abcdef"]), artifact, tmp_path)
    assert not (tmp_path / artifact.name).exists()


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../update.dmg",
        "nested/update.dmg",
        r"nested\update.exe",
        "/tmp/update.dmg",
        "bad\x00name.dmg",
    ],
)
def test_rejects_unsafe_artifact_names_without_creating_staging(tmp_path, name):
    with pytest.raises(DownloadError, match="name"):
        download_artifact(
            FakeClient([b"abc"]), artifact_for(b"abc", name=name), tmp_path
        )
    assert not list(tmp_path.iterdir())


def test_published_file_has_user_only_permissions(tmp_path):
    result = download_artifact(
        FakeClient([b"abc"]), artifact_for(b"abc"), tmp_path
    )
    assert result.stat().st_mode & 0o777 == 0o600
