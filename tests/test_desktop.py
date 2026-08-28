import logging
import threading
from types import SimpleNamespace

import pytest

from backend.desktop import (
    AlreadyRunningError,
    SingleInstanceLock,
    TokenRedactionFilter,
    run_desktop,
    run_smoke,
)
from backend.runtime import LaunchMode, build_runtime
import run_desktop as desktop_entry


class Event:
    def __init__(self):
        self.callback = None

    def __iadd__(self, callback):
        self.callback = callback
        return self


class FakeWindow:
    def __init__(self):
        self.events = SimpleNamespace(closed=Event())


class FakeWebview:
    def __init__(self):
        self.window = None
        self.window_url = None
        self.create_calls = 0
        self.error = None

    def create_window(self, title, url=None, **kwargs):
        self.create_calls += 1
        self.window_url = url
        self.window = FakeWindow()
        return self.window

    def start(self):
        if self.window and self.window.events.closed.callback:
            self.window.events.closed.callback()

    def show_error(self, title, message):
        self.error = message


class FakeServer:
    def __init__(self, app, host, port, listener, fail_start=False):
        self.app = app
        self.host = host
        self.port = port
        self.listener = listener
        self.fail_start = fail_start
        self.should_exit = False
        self.joined = False

    def start(self):
        if self.fail_start:
            raise RuntimeError("bind failed")

    def stop(self, timeout=5):
        self.should_exit = True
        self.joined = True


class FakeHealth:
    def __init__(self):
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})


class FakeUpdater:
    def __init__(self):
        self.checked = threading.Event()

    def check(self, *, manual):
        assert manual is False
        self.checked.set()


def runtime(tmp_path, mode=LaunchMode.SOURCE_DESKTOP):
    return build_runtime(mode, platform_name="darwin", home=tmp_path)


def test_second_instance_reports_existing_owner(tmp_path):
    first = SingleInstanceLock(tmp_path / "app.lock")
    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError, match="already running"):
            SingleInstanceLock(tmp_path / "app.lock").acquire()
    finally:
        first.release()


def test_server_binds_loopback_and_stops_on_final_window_close(tmp_path):
    webview = FakeWebview()
    health = FakeHealth()
    updater = FakeUpdater()
    servers = []

    def factory(app, host, port, listener):
        server = FakeServer(app, host, port, listener)
        servers.append(server)
        return server

    code = run_desktop(
        runtime(tmp_path),
        webview,
        factory,
        health_client=health,
        app_factory=lambda runtime, **kwargs: object(),
        update_service=updater,
    )

    server = servers[0]
    assert code == 0
    assert server.host == "127.0.0.1"
    assert server.port > 0
    assert server.listener.getsockname()[0] == "127.0.0.1"
    assert webview.window_url == f"http://127.0.0.1:{server.port}/"
    assert health.urls == [f"http://127.0.0.1:{server.port}/api/health"]
    assert server.should_exit is True
    assert server.joined is True
    assert updater.checked.wait(1)


def test_server_start_failure_shows_native_error_and_exits(tmp_path):
    webview = FakeWebview()

    def factory(app, host, port, listener):
        return FakeServer(app, host, port, listener, fail_start=True)

    assert run_desktop(
        runtime(tmp_path),
        webview,
        factory,
        health_client=FakeHealth(),
        app_factory=lambda runtime, **kwargs: object(),
    ) == 1
    assert "could not start" in webview.error.lower()
    assert webview.create_calls == 0


def test_smoke_mode_checks_health_without_window(tmp_path, capsys):
    health = FakeHealth()
    servers = []

    def factory(app, host, port, listener):
        server = FakeServer(app, host, port, listener)
        servers.append(server)
        return server

    assert run_smoke(
        runtime(tmp_path),
        factory,
        health_client=health,
        app_factory=lambda runtime, **kwargs: object(),
    ) == 0
    assert servers[0].should_exit is True
    assert f"SMOKE_OK http://127.0.0.1:{servers[0].port}/api/health" in capsys.readouterr().out


def test_log_filter_redacts_every_known_token():
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "failed for %s", ("secret-token",), None
    )
    assert TokenRedactionFilter(lambda: ["secret-token"]).filter(record)
    assert record.getMessage() == "failed for [REDACTED]"
    assert "secret-token" not in record.getMessage()


def test_entrypoint_helper_mode_never_builds_runtime_or_window():
    called = []
    result = desktop_entry.main(
        ["--apply-update", "/tmp/request.json"],
        helper_runner=lambda path: called.append(path) or 7,
        runtime_builder=lambda *args, **kwargs: pytest.fail("runtime built"),
    )
    assert result == 7
    assert called == ["/tmp/request.json"]


def test_entrypoint_smoke_mode_uses_packaged_runtime_without_webview(tmp_path):
    captured = {}
    fake_runtime = runtime(tmp_path, LaunchMode.PACKAGED_DESKTOP)

    def runtime_builder(mode, **kwargs):
        captured["mode"] = mode
        return fake_runtime

    def smoke_runner(runtime, update_service=None):
        captured["smoke"] = (runtime, update_service)
        return 0

    assert desktop_entry.main(
        ["--smoke-test"],
        frozen=True,
        runtime_builder=runtime_builder,
        service_builder=lambda value: "updater",
        smoke_runner=smoke_runner,
        webview_module=pytest.fail,
    ) == 0
    assert captured["mode"] is LaunchMode.PACKAGED_DESKTOP
    assert captured["smoke"] == (fake_runtime, "updater")


def test_linux_packaged_install_path_uses_absolute_appimage(tmp_path):
    appimage = tmp_path / "IBKR Lot Tracker.AppImage"
    appimage.write_bytes(b"appimage")
    appimage.chmod(0o755)

    assert desktop_entry._current_install_path(
        "linux",
        "/tmp/.mount_ibkr/usr/bin/python",
        environ={"APPIMAGE": str(appimage)},
    ) == appimage.resolve()


@pytest.mark.parametrize(
    "appimage",
    [None, "relative.AppImage", "/definitely/missing/IBKR.AppImage", "\x00"],
)
def test_linux_packaged_install_path_rejects_missing_or_malformed_appimage(
    appimage,
):
    environment = {} if appimage is None else {"APPIMAGE": appimage}
    with pytest.raises(ValueError, match="APPIMAGE"):
        desktop_entry._current_install_path(
            "linux",
            "/tmp/.mount_ibkr/usr/bin/python",
            environ=environment,
        )
