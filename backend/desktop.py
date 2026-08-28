from __future__ import annotations

import logging
import socket
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

import requests
import uvicorn
from filelock import FileLock, Timeout

from .main import create_app
from .runtime import LaunchMode


LOOPBACK_HOST = "127.0.0.1"
READINESS_TIMEOUT = 5.0
SHUTDOWN_TIMEOUT = 5.0


class AlreadyRunningError(RuntimeError):
    pass


class DesktopStartupError(RuntimeError):
    pass


class SingleInstanceLock:
    def __init__(self, path):
        self._lock = FileLock(str(path))
        self._held = False

    def acquire(self):
        Path(self._lock.lock_file).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire(timeout=0)
        except Timeout as exc:
            raise AlreadyRunningError(
                "IBKR Lot Tracker is already running"
            ) from exc
        self._held = True
        return self

    def release(self):
        if self._held:
            self._lock.release()
            self._held = False


class TokenRedactionFilter(logging.Filter):
    def __init__(self, token_provider: Callable[[], object]):
        super().__init__()
        self._token_provider = token_provider

    def filter(self, record):
        message = record.getMessage()
        try:
            tokens = self._token_provider() or ()
        except Exception:
            tokens = ()
        if isinstance(tokens, str):
            tokens = (tokens,)
        for token in tokens:
            if isinstance(token, str) and token:
                message = message.replace(token, "[REDACTED]")
        record.msg = message
        record.args = ()
        return True


def configure_desktop_logging(runtime, token_provider=lambda: ()):
    runtime.data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        runtime.log_path,
        maxBytes=1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.addFilter(TokenRedactionFilter(token_provider))
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger = logging.getLogger("ibkr-lot-tracker")
    logger.setLevel(logging.INFO)
    if not any(
        isinstance(existing, RotatingFileHandler)
        and Path(existing.baseFilename) == runtime.log_path.resolve()
        for existing in logger.handlers
    ):
        logger.addHandler(handler)
    else:
        handler.close()
    return logger


class UvicornThread:
    def __init__(self, app, host, port, listener):
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._listener = listener
        self._error = None
        self._thread = threading.Thread(
            target=self._serve,
            name="ibkr-loopback-server",
            daemon=True,
        )
        self.host = host
        self.port = port

    @property
    def should_exit(self):
        return self._server.should_exit

    @should_exit.setter
    def should_exit(self, value):
        self._server.should_exit = value

    @property
    def error(self):
        return self._error

    @property
    def alive(self):
        return self._thread.is_alive()

    def _serve(self):
        try:
            self._server.run(sockets=[self._listener])
        except BaseException as exc:
            self._error = exc

    def start(self):
        self._thread.start()

    def stop(self, timeout=SHUTDOWN_TIMEOUT):
        self._server.should_exit = True
        if self._thread.ident is not None:
            self._thread.join(timeout)
        try:
            self._listener.close()
        except OSError:
            pass


def _reserve_loopback_socket():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((LOOPBACK_HOST, 0))
        listener.listen(128)
        listener.set_inheritable(False)
        return listener
    except Exception:
        listener.close()
        raise


def _wait_until_ready(
    server,
    health_url,
    health_client,
    *,
    timeout=READINESS_TIMEOUT,
    monotonic=time.monotonic,
    sleep=time.sleep,
):
    deadline = monotonic() + timeout
    last_error = None
    while monotonic() < deadline:
        error = getattr(server, "error", None)
        if error is not None:
            raise DesktopStartupError("Desktop server could not start") from error
        try:
            response = health_client(health_url, timeout=0.25)
            if response.status_code == 200 and response.json() == {"ok": True}:
                return
        except Exception as exc:
            last_error = exc
        sleep(0.05)
    raise DesktopStartupError("Desktop server could not start") from last_error


def _show_native_error(webview_module, message):
    if hasattr(webview_module, "show_error"):
        webview_module.show_error("IBKR Lot Tracker", message)
        return
    webview_module.create_window(
        "IBKR Lot Tracker",
        html="<h3>IBKR Lot Tracker could not start.</h3>",
        width=460,
        height=180,
    )
    webview_module.start()


def _start_server(
    runtime,
    server_factory,
    health_client,
    app_factory,
    update_service,
    request_shutdown,
):
    listener = _reserve_loopback_socket()
    host, port = listener.getsockname()
    server = None
    try:
        app = app_factory(
            runtime,
            update_service=update_service,
            request_shutdown=request_shutdown,
        )
        server = server_factory(app, host, port, listener)
        server.start()
        health_url = f"http://{host}:{port}/api/health"
        _wait_until_ready(server, health_url, health_client)
        return server, health_url
    except Exception:
        if server is not None:
            try:
                server.stop(SHUTDOWN_TIMEOUT)
            except Exception:
                listener.close()
        else:
            listener.close()
        raise


def run_desktop(
    runtime,
    webview_module,
    server_factory=UvicornThread,
    *,
    health_client=requests.get,
    app_factory=create_app,
    update_service=None,
    token_provider=lambda: (),
):
    configure_desktop_logging(runtime, token_provider)
    instance = None
    server_holder = {"server": None}
    window_holder = {"window": None}
    shutdown_lock = threading.Lock()
    shutdown_complete = False

    def request_shutdown():
        server = server_holder["server"]
        if server is not None:
            server.should_exit = True
        window = window_holder["window"]
        if window is not None and hasattr(window, "destroy"):
            window.destroy()

    def shutdown():
        nonlocal shutdown_complete
        with shutdown_lock:
            if shutdown_complete:
                return
            shutdown_complete = True
            server = server_holder["server"]
            if server is not None:
                server.stop(SHUTDOWN_TIMEOUT)
            if instance is not None:
                instance.release()

    try:
        if runtime.mode is LaunchMode.PACKAGED_DESKTOP:
            instance = SingleInstanceLock(runtime.data_dir / "instance.lock")
            instance.acquire()
        server, health_url = _start_server(
            runtime,
            server_factory,
            health_client,
            app_factory,
            update_service,
            request_shutdown,
        )
        server_holder["server"] = server
        if update_service is not None:
            threading.Thread(
                target=lambda: update_service.check(manual=False),
                name="ibkr-update-check",
                daemon=True,
            ).start()
        window = webview_module.create_window(
            "IBKR Lot Tracker",
            health_url.rsplit("/api/health", 1)[0] + "/",
        )
        window_holder["window"] = window
        window.events.closed += shutdown
        webview_module.start()
        return 0
    except AlreadyRunningError as exc:
        _show_native_error(webview_module, str(exc))
        return 1
    except Exception:
        _show_native_error(
            webview_module,
            "IBKR Lot Tracker could not start its local server.",
        )
        return 1
    finally:
        shutdown()


def run_smoke(
    runtime,
    server_factory=UvicornThread,
    *,
    health_client=requests.get,
    app_factory=create_app,
    update_service=None,
):
    server_holder = {"server": None}

    def shutdown():
        server = server_holder["server"]
        if server is not None:
            server.stop(SHUTDOWN_TIMEOUT)

    try:
        server, health_url = _start_server(
            runtime,
            server_factory,
            health_client,
            app_factory,
            update_service,
            shutdown,
        )
        server_holder["server"] = server
        response = health_client(health_url, timeout=1)
        if response.status_code != 200 or response.json() != {"ok": True}:
            return 1
        print(f"SMOKE_OK {health_url}", flush=True)
        return 0
    except Exception:
        return 1
    finally:
        shutdown()
