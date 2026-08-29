import json
import plistlib
import shutil
from pathlib import Path

import pytest

from backend.updater.install import (
    InstallError,
    InstallRequest,
    apply_update,
    launch_helper,
)


class FakeRunner:
    def __init__(self, mount=None):
        self.commands = []
        self.mount = mount
        self.writable = True
        self.fail_silent = False
        self.fail_launch = False
        self.fail_ditto = False

    def is_writable(self, path):
        return self.writable

    def run(self, command):
        command = [str(part) for part in command]
        self.commands.append(command)
        if self.fail_silent and "/VERYSILENT" in command:
            raise InstallError("silent install failed")
        if command[:2] == ["hdiutil", "attach"]:
            return plistlib.dumps(
                {"system-entities": [{"mount-point": str(self.mount)}]}
            )
        if command[0] == "ditto":
            if self.fail_ditto:
                raise PermissionError("managed application directory")
            shutil.copytree(command[1], command[2])
        return b""

    def launch(self, command):
        command = [str(part) for part in command]
        self.commands.append(command)
        if self.fail_launch:
            raise OSError("launch failed")


def executable(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    path.chmod(0o755)
    return path


def request(platform, staged, current):
    return InstallRequest(
        platform=platform,
        staged_path=str(staged),
        current_path=str(current),
        relaunch_command=(str(current),),
        parent_pid=999999,
        diagnostic_path=str(current.parent / "update-diagnostic.json"),
    )


def test_helper_request_is_private_and_launch_has_no_shell(tmp_path, assert_mode):
    staged = executable(tmp_path / "updates/new.AppImage", b"new")
    current = executable(tmp_path / "IBKR.AppImage", b"old")
    calls = []

    def launcher(command, **kwargs):
        calls.append((command, kwargs))
        return object()

    path = launch_helper(
        request("linux", staged, current),
        launcher=launcher,
        executable="/app/IBKR",
        request_path=tmp_path / "request.json",
        platform_name="linux",
    )

    assert_mode(path, 0o600)
    assert set(json.loads(path.read_text())) == {
        "platform",
        "staged_path",
        "current_path",
        "relaunch_command",
        "parent_pid",
        "diagnostic_path",
    }
    assert calls[0][0] == ["/app/IBKR", "--apply-update", str(path)]
    assert calls[0][1]["start_new_session"] is True
    assert "shell" not in calls[0][1]


def test_linux_appimage_replaces_atomically_and_keeps_backup(tmp_path):
    current = executable(tmp_path / "IBKR.AppImage", b"old")
    staged = executable(tmp_path / "updates/new.AppImage", b"new")
    runner = FakeRunner()

    result = apply_update(request("linux", staged, current), runner)

    assert result.manual_completion is False
    assert current.read_bytes() == b"new"
    assert current.with_suffix(".AppImage.previous").read_bytes() == b"old"
    assert runner.commands[-1] == [str(current)]


def test_linux_relaunch_failure_restores_previous_appimage(tmp_path):
    current = executable(tmp_path / "IBKR.AppImage", b"old")
    staged = executable(tmp_path / "updates/new.AppImage", b"new")
    runner = FakeRunner()
    runner.fail_launch = True

    with pytest.raises(InstallError):
        apply_update(request("linux", staged, current), runner)

    assert current.read_bytes() == b"old"


def test_macos_verifies_and_swaps_app_or_opens_dmg_when_unwritable(tmp_path):
    dmg = executable(tmp_path / "app.dmg", b"dmg")
    mount = tmp_path / "mount"
    bundled = executable(mount / "IBKR Lot Tracker.app/Contents/MacOS/app", b"new")
    target = executable(tmp_path / "Applications/IBKR Lot Tracker.app/Contents/MacOS/app", b"old")
    target_app = target.parents[2]
    runner = FakeRunner(mount)

    result = apply_update(request("macos", dmg, target_app), runner)

    assert result.manual_completion is False
    assert ["codesign", "--verify", "--deep", "--strict", str(bundled.parents[2])] in runner.commands
    assert (target_app / "Contents/MacOS/app").read_bytes() == b"new"
    assert target_app.with_name(target_app.name + ".previous").exists()

    runner = FakeRunner(mount)
    runner.writable = False
    result = apply_update(request("macos", dmg, target_app), runner)
    assert result.manual_completion is True
    assert runner.commands[-1] == ["open", str(dmg)]

    runner = FakeRunner(mount)
    runner.fail_ditto = True
    result = apply_update(request("macos", dmg, target_app), runner)
    assert result.manual_completion is True
    assert runner.commands[-1] == ["open", str(dmg)]


def test_windows_runs_silent_installer_or_opens_interactively(tmp_path):
    installer = executable(tmp_path / "update.exe", b"signed")
    current = executable(tmp_path / "current.exe", b"old")
    runner = FakeRunner()

    result = apply_update(request("windows", installer, current), runner)
    assert result.manual_completion is False
    assert runner.commands[0] == [
        str(installer),
        "/VERYSILENT",
        "/CURRENTUSER",
        "/NORESTART",
    ]
    assert runner.commands[-1] == [str(current)]

    runner = FakeRunner()
    runner.fail_silent = True
    result = apply_update(request("windows", installer, current), runner)
    assert result.manual_completion is True
    assert runner.commands[-1] == [str(installer)]


def test_invalid_request_paths_and_platform_are_rejected(tmp_path):
    current = executable(tmp_path / "current", b"old")
    staged = executable(tmp_path / "staged", b"new")
    runner = FakeRunner()
    with pytest.raises(InstallError, match="platform"):
        apply_update(request("other", staged, current), runner)

    bad = request("linux", staged, current)
    staged.unlink()
    with pytest.raises(InstallError, match="staged"):
        apply_update(bad, runner)
