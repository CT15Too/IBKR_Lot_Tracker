from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence, Tuple


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstallRequest:
    platform: str
    staged_path: str
    current_path: str
    relaunch_command: Tuple[str, ...]
    parent_pid: int
    diagnostic_path: str

    def to_dict(self):
        value = asdict(self)
        value["relaunch_command"] = list(self.relaunch_command)
        return value

    @classmethod
    def from_file(cls, path):
        request_path = Path(path)
        try:
            raw = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise InstallError("Invalid update helper request") from exc
        expected = {
            "platform",
            "staged_path",
            "current_path",
            "relaunch_command",
            "parent_pid",
            "diagnostic_path",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise InstallError("Invalid update helper request fields")
        command = raw["relaunch_command"]
        if (
            raw["platform"] not in {"linux", "macos", "windows"}
            or not all(
                isinstance(raw[key], str) and raw[key]
                for key in ("staged_path", "current_path", "diagnostic_path")
            )
            or not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
            or not isinstance(raw["parent_pid"], int)
            or raw["parent_pid"] <= 0
        ):
            raise InstallError("Invalid update helper request values")
        return cls(
            platform=raw["platform"],
            staged_path=raw["staged_path"],
            current_path=raw["current_path"],
            relaunch_command=tuple(command),
            parent_pid=raw["parent_pid"],
            diagnostic_path=raw["diagnostic_path"],
        )


@dataclass(frozen=True)
class InstallResult:
    manual_completion: bool = False
    instructions: str = ""


class CommandRunner:
    def run(self, command: Sequence[str]) -> bytes:
        try:
            completed = subprocess.run(
                list(command),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise InstallError("Update command failed") from exc
        return completed.stdout

    def launch(self, command: Sequence[str]) -> None:
        try:
            subprocess.Popen(list(command), close_fds=True, shell=False)
        except OSError as exc:
            raise InstallError("Could not launch updated application") from exc

    def is_writable(self, path) -> bool:
        target = Path(path)
        existing = target if target.exists() else target.parent
        return os.access(existing, os.W_OK)


def _write_private_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary_path), str(path))
        os.chmod(path, 0o600)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def launch_helper(
    request: InstallRequest,
    *,
    launcher: Callable = subprocess.Popen,
    executable: str = sys.executable,
    request_path=None,
    platform_name: str = sys.platform,
):
    path = (
        Path(request_path)
        if request_path is not None
        else Path(request.diagnostic_path).parent / "update-helper-request.json"
    )
    _write_private_json(path, request.to_dict())
    command = [str(executable), "--apply-update", str(path)]
    kwargs = {"close_fds": True}
    if platform_name.startswith("win"):
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        launcher(command, **kwargs)
    except OSError as exc:
        raise InstallError("Could not launch update helper") from exc
    return path


def _validate_files(request: InstallRequest):
    if request.platform not in {"linux", "macos", "windows"}:
        raise InstallError("Unsupported update platform")
    staged = Path(request.staged_path)
    current = Path(request.current_path)
    if not staged.is_file():
        raise InstallError("Verified staged update is missing")
    if not current.exists():
        raise InstallError("Current application path is missing")
    return staged, current


def _fsync_file(path: Path) -> None:
    if os.name == "nt":
        # fsync on a read-only handle is unsupported on Windows; this helper
        # only runs on the Linux AppImage install path anyway.
        return
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _apply_linux(request, runner, staged: Path, current: Path):
    replacement = current.with_name(current.name + ".new")
    backup = current.with_suffix(current.suffix + ".previous")
    replacement.unlink(missing_ok=True)
    try:
        staged.chmod(staged.stat().st_mode | 0o700)
        shutil.copyfile(staged, replacement)
        replacement.chmod(staged.stat().st_mode)
        _fsync_file(replacement)
        os.replace(current, backup)
        try:
            os.replace(replacement, current)
            runner.launch(request.relaunch_command)
        except Exception:
            current.unlink(missing_ok=True)
            os.replace(backup, current)
            raise
    except Exception as exc:
        replacement.unlink(missing_ok=True)
        if not current.exists() and backup.exists():
            os.replace(backup, current)
        if isinstance(exc, InstallError):
            raise
        raise InstallError("Linux update failed; the previous AppImage was restored") from exc
    return InstallResult()


def _mount_point(attach_output: bytes) -> Path:
    try:
        payload = plistlib.loads(attach_output)
        points = [
            entity["mount-point"]
            for entity in payload.get("system-entities", [])
            if isinstance(entity, dict) and entity.get("mount-point")
        ]
    except Exception as exc:
        raise InstallError("Could not identify mounted update image") from exc
    if len(points) != 1:
        raise InstallError("Update image did not provide exactly one mount point")
    return Path(points[0])


def _manual_macos(staged, runner):
    runner.launch(["open", str(staged)])
    return InstallResult(
        manual_completion=True,
        instructions="The update image was opened. Replace the app, then launch it again.",
    )


def _apply_macos(request, runner, staged: Path, target: Path):
    if not runner.is_writable(target.parent):
        return _manual_macos(staged, runner)
    mount = None
    replacement = target.with_name(target.name + ".new")
    backup = target.with_name(target.name + ".previous")
    try:
        attach_output = runner.run(
            ["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(staged)]
        )
        mount = _mount_point(attach_output)
        applications = list(mount.glob("IBKR Lot Tracker.app"))
        if len(applications) != 1 or not applications[0].is_dir():
            raise InstallError(
                "Update image must contain exactly one IBKR Lot Tracker.app"
            )
        source = applications[0]
        runner.run(["codesign", "--verify", "--deep", "--strict", str(source)])
        if replacement.exists():
            shutil.rmtree(replacement)
        try:
            runner.run(["ditto", str(source), str(replacement)])
        except Exception:
            runner.run(["hdiutil", "detach", str(mount)])
            mount = None
            return _manual_macos(staged, runner)
        if not replacement.is_dir():
            raise InstallError("Could not prepare replacement application")
        if backup.exists():
            shutil.rmtree(backup)
        os.replace(target, backup)
        try:
            os.replace(replacement, target)
            runner.launch(request.relaunch_command)
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            os.replace(backup, target)
            raise
        return InstallResult()
    except PermissionError:
        if not target.exists() and backup.exists():
            os.replace(backup, target)
        if mount is not None:
            try:
                runner.run(["hdiutil", "detach", str(mount)])
            except Exception:
                pass
            mount = None
        return _manual_macos(staged, runner)
    except InstallError:
        raise
    except Exception as exc:
        if not target.exists() and backup.exists():
            os.replace(backup, target)
        raise InstallError("macOS update failed; the previous app was preserved") from exc
    finally:
        if replacement.exists():
            shutil.rmtree(replacement, ignore_errors=True)
        if mount is not None:
            try:
                runner.run(["hdiutil", "detach", str(mount)])
            except Exception:
                pass


def _apply_windows(request, runner, staged: Path):
    silent = [str(staged), "/VERYSILENT", "/CURRENTUSER", "/NORESTART"]
    try:
        runner.run(silent)
        runner.launch(request.relaunch_command)
        return InstallResult()
    except Exception:
        try:
            runner.launch([str(staged)])
        except Exception as exc:
            raise InstallError("Windows update installer could not be opened") from exc
        return InstallResult(
            manual_completion=True,
            instructions="Complete the opened installer, then launch the app again.",
        )


def apply_update(request: InstallRequest, runner=None) -> InstallResult:
    runner = runner or CommandRunner()
    staged, current = _validate_files(request)
    if request.platform == "linux":
        return _apply_linux(request, runner, staged, current)
    if request.platform == "macos":
        return _apply_macos(request, runner, staged, current)
    if request.platform == "windows":
        return _apply_windows(request, runner, staged)
    raise InstallError("Unsupported update platform")


def wait_for_parent_exit(
    parent_pid: int,
    *,
    timeout: float = 60.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        sleep(0.1)
    raise InstallError("Application did not exit before the update timeout")


def _write_diagnostic(request: InstallRequest, exc: Exception) -> None:
    diagnostic = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "unknown",
        "exception_type": type(exc).__name__,
        "message": "Update installation failed. The previous application was preserved.",
    }
    try:
        _write_private_json(Path(request.diagnostic_path), diagnostic)
    except Exception:
        pass


def run_helper(request_path, *, runner=None) -> int:
    try:
        request = InstallRequest.from_file(request_path)
        wait_for_parent_exit(request.parent_pid)
        apply_update(request, runner)
        Path(request_path).unlink(missing_ok=True)
        return 0
    except Exception as exc:
        if "request" in locals():
            _write_diagnostic(request, exc)
        return 1
