#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from backend.credentials import CredentialStore
from backend.desktop import run_desktop, run_smoke
from backend.runtime import LaunchMode, build_runtime
from backend.settings_store import SettingsStore
from backend.update_key import UPDATE_PUBLIC_KEY_B64
from backend.updater.download import download_artifact
from backend.updater.http import GithubReleaseClient, UpdateNetworkError
from backend.updater.install import InstallRequest, launch_helper, run_helper
from backend.updater.manifest import current_platform_identity, verify_and_select
from backend.updater.service import UpdateService
from backend.version import APP_VERSION, GITHUB_REPOSITORY, UPDATER_VERSION


def _current_install_path(platform_name, executable):
    path = Path(executable).resolve()
    if platform_name == "macos":
        for parent in path.parents:
            if parent.suffix == ".app":
                return parent
    return path


def _relaunch_command(platform_name, current_path):
    if platform_name == "macos":
        return ("open", str(current_path))
    return (str(current_path),)


def build_desktop_update_service(runtime):
    settings_store = SettingsStore(runtime.settings_path)
    platform = current_platform_identity()
    client = GithubReleaseClient(repository=GITHUB_REPOSITORY)

    def discover():
        release = client.latest_stable()
        manifest = client.fetch_bytes(release.manifest_url)
        signature_bytes = client.fetch_bytes(release.signature_url)
        try:
            signature = signature_bytes.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise UpdateNetworkError("Update signature is not ASCII text") from exc
        return verify_and_select(
            manifest,
            signature,
            UPDATE_PUBLIC_KEY_B64,
            APP_VERSION,
            UPDATER_VERSION,
            platform,
        )

    def downloader(artifact, staging_dir, cancel):
        return download_artifact(
            client, artifact, staging_dir, cancel=cancel
        )

    def installer(staged_path):
        current = _current_install_path(platform.os, sys.executable)
        request = InstallRequest(
            platform=platform.os,
            staged_path=str(staged_path),
            current_path=str(current),
            relaunch_command=_relaunch_command(platform.os, current),
            parent_pid=os.getpid(),
            diagnostic_path=str(runtime.data_dir / "update-diagnostic.json"),
        )
        return launch_helper(request)

    return UpdateService(
        runtime=runtime,
        settings_store=settings_store,
        discovery=discover,
        downloader=downloader,
        installer=installer,
    )


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="IBKR Lot Tracker desktop app")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--smoke-test", action="store_true")
    modes.add_argument("--apply-update", metavar="REQUEST")
    return parser.parse_args(argv)


def main(
    argv=None,
    *,
    frozen=None,
    webview_module=None,
    runtime_builder=build_runtime,
    service_builder=build_desktop_update_service,
    desktop_runner=run_desktop,
    smoke_runner=run_smoke,
    helper_runner=run_helper,
):
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.apply_update:
        return helper_runner(args.apply_update)

    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    mode = (
        LaunchMode.PACKAGED_DESKTOP
        if is_frozen
        else LaunchMode.SOURCE_DESKTOP
    )
    frozen_root = getattr(sys, "_MEIPASS", None) if is_frozen else None
    runtime = runtime_builder(mode, frozen_root=frozen_root)
    update_service = service_builder(runtime)

    if args.smoke_test:
        return smoke_runner(runtime, update_service=update_service)

    if webview_module is None:
        import webview as webview_module

    credentials = CredentialStore()
    return desktop_runner(
        runtime,
        webview_module,
        update_service=update_service,
        token_provider=lambda: [credentials.get_token()],
    )


if __name__ == "__main__":
    raise SystemExit(main())
