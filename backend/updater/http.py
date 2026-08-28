import json
import re
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urljoin, urlsplit

import requests


ALLOWED_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
MAX_METADATA_BYTES = 1024 * 1024
MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class UpdateNetworkError(RuntimeError):
    pass


@dataclass(frozen=True)
class GithubRelease:
    tag: str
    release_notes: str
    manifest_url: str
    signature_url: str


class GithubReleaseClient:
    def __init__(self, session=None, repository: str = ""):
        if not _REPOSITORY.fullmatch(repository):
            raise ValueError("Invalid GitHub repository identifier")
        self._session = requests.Session() if session is None else session
        self._strip_ambient_credentials()
        self._repository = repository

    def _strip_ambient_credentials(self) -> None:
        if hasattr(self._session, "trust_env"):
            self._session.trust_env = False
        if hasattr(self._session, "auth"):
            self._session.auth = None
        headers = getattr(self._session, "headers", None)
        if headers is not None:
            headers.pop("Authorization", None)
        cookies = getattr(self._session, "cookies", None)
        if cookies is not None:
            cookies.clear()

    @staticmethod
    def _validate_url(url: str, *, redirect: bool = False) -> None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise UpdateNetworkError("Invalid update URL") from exc
        if parsed.scheme.lower() != "https":
            raise UpdateNetworkError("Update URLs must use HTTPS")
        if (
            parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
        ):
            raise UpdateNetworkError("Update URL host is not permitted")
        hostname = (parsed.hostname or "").lower()
        if hostname not in ALLOWED_HOSTS:
            label = "redirect host" if redirect else "host"
            raise UpdateNetworkError("Update {} is not permitted".format(label))

    def _open(self, url: str):
        self._validate_url(url)
        redirects = 0
        while True:
            try:
                self._strip_ambient_credentials()
                response = self._session.get(
                    url,
                    allow_redirects=False,
                    headers={"Authorization": None},
                    timeout=(5, 30),
                    stream=True,
                )
            except requests.Timeout as exc:
                raise UpdateNetworkError("Update request timed out") from exc
            except requests.RequestException as exc:
                raise UpdateNetworkError("Update request failed") from exc

            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise UpdateNetworkError(
                        "Update redirect did not provide a location"
                    )
                next_url = urljoin(url, location)
                self._validate_url(next_url, redirect=True)
                if redirects >= MAX_REDIRECTS:
                    raise UpdateNetworkError("Update request exceeded redirects limit")
                redirects += 1
                url = next_url
                continue
            if (
                response.status_code == 403
                and response.headers.get("X-RateLimit-Remaining") == "0"
            ):
                response.close()
                raise UpdateNetworkError(
                    "GitHub API rate limit exceeded; try again later"
                )
            if response.status_code < 200 or response.status_code >= 300:
                status = response.status_code
                response.close()
                raise UpdateNetworkError(
                    "Update request failed with HTTP status {}".format(status)
                )
            return response

    def fetch_bytes(self, url: str, limit: int = MAX_METADATA_BYTES) -> bytes:
        response = self._open(url)
        try:
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > limit:
                        raise UpdateNetworkError("Update metadata is too large")
                except ValueError as exc:
                    raise UpdateNetworkError(
                        "Update response has invalid content length"
                    ) from exc
            body = bytearray()
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > limit:
                    raise UpdateNetworkError("Update metadata is too large")
            return bytes(body)
        finally:
            response.close()

    def stream(self, url: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        response = self._open(url)
        try:
            for chunk in response.iter_content(chunk_size):
                if chunk:
                    yield chunk
        except requests.RequestException as exc:
            raise UpdateNetworkError("Update download was interrupted") from exc
        finally:
            response.close()

    def latest_stable(self) -> GithubRelease:
        url = (
            "https://api.github.com/repos/{}/releases?per_page=10".format(
                self._repository
            )
        )
        body = self.fetch_bytes(url)
        try:
            releases = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateNetworkError("GitHub returned invalid release metadata") from exc
        if not isinstance(releases, list):
            raise UpdateNetworkError("GitHub returned invalid release metadata")

        stable = next(
            (
                release
                for release in releases
                if isinstance(release, dict)
                and release.get("draft") is False
                and release.get("prerelease") is False
            ),
            None,
        )
        if stable is None:
            raise UpdateNetworkError("No stable GitHub release was found")
        tag = stable.get("tag_name")
        notes = stable.get("body")
        assets = stable.get("assets")
        if not isinstance(tag, str) or not isinstance(notes, str):
            raise UpdateNetworkError("GitHub release metadata is incomplete")
        if not isinstance(assets, list):
            raise UpdateNetworkError("GitHub release assets are invalid")

        def asset_url(name: str) -> str:
            matches = [
                asset.get("browser_download_url")
                for asset in assets
                if isinstance(asset, dict) and asset.get("name") == name
            ]
            if len(matches) != 1 or not isinstance(matches[0], str):
                raise UpdateNetworkError(
                    "Release must contain exactly one {} asset".format(name)
                )
            self._validate_url(matches[0])
            return matches[0]

        return GithubRelease(
            tag=tag,
            release_notes=notes,
            manifest_url=asset_url("update-manifest.json"),
            signature_url=asset_url("update-manifest.json.sig"),
        )
