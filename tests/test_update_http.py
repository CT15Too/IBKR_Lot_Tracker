import json

import pytest
import requests

from backend.updater.http import (
    MAX_METADATA_BYTES,
    GithubReleaseClient,
    UpdateNetworkError,
)


REPOSITORY = "CT15Too/IBKR_Lot_Tracker"
MANIFEST_URL = (
    "https://github.com/CT15Too/IBKR_Lot_Tracker/releases/download/"
    "v1.2.0/update-manifest.json"
)
SIGNATURE_URL = MANIFEST_URL + ".sig"


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size):
        for index in range(0, len(self._body), chunk_size):
            yield self._body[index : index + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        next_response = self.responses.pop(0)
        if isinstance(next_response, Exception):
            raise next_response
        return next_response


def response(status, *, body=b"", json_body=None, headers=None):
    if json_body is not None:
        body = json.dumps(json_body).encode()
    return FakeResponse(status, body, headers)


def stable_release(assets=None):
    return {
        "tag_name": "v1.2.0",
        "prerelease": False,
        "draft": False,
        "body": "notes",
        "assets": assets
        or [
            {"name": "update-manifest.json", "browser_download_url": MANIFEST_URL},
            {
                "name": "update-manifest.json.sig",
                "browser_download_url": SIGNATURE_URL,
            },
        ],
    }


def test_rejects_http_and_non_github_redirects():
    client = GithubReleaseClient(
        FakeSession(
            [
                response(
                    302,
                    headers={"Location": "https://evil.example/update.json"},
                )
            ]
        ),
        REPOSITORY,
    )
    with pytest.raises(UpdateNetworkError, match="redirect host"):
        client.fetch_bytes(MANIFEST_URL)
    with pytest.raises(UpdateNetworkError, match="HTTPS"):
        client.fetch_bytes("http://github.com/update.json")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com.evil.example/file",
        "https://user@evil.example/file",
        "https://evil.example/file",
    ],
)
def test_rejects_host_confusion_urls(url):
    with pytest.raises(UpdateNetworkError, match="host"):
        GithubReleaseClient(FakeSession([]), REPOSITORY).fetch_bytes(url)


def test_follows_only_five_allowed_https_redirects():
    redirects = [
        response(302, headers={"Location": "https://objects.githubusercontent.com/a"})
        for _ in range(6)
    ]
    client = GithubReleaseClient(FakeSession(redirects), REPOSITORY)
    with pytest.raises(UpdateNetworkError, match="redirects"):
        client.fetch_bytes(MANIFEST_URL)


def test_latest_release_ignores_prereleases_and_fetches_named_assets():
    session = FakeSession(
        [
            response(
                200,
                json_body=[
                    {
                        "tag_name": "v2.0.0-rc.1",
                        "prerelease": True,
                        "draft": False,
                        "assets": [],
                    },
                    stable_release(),
                ],
            )
        ]
    )
    release = GithubReleaseClient(session, REPOSITORY).latest_stable()
    assert release.tag == "v1.2.0"
    assert release.release_notes == "notes"
    assert release.manifest_url == MANIFEST_URL
    assert session.calls[0][0].endswith("/releases?per_page=10")
    assert session.calls[0][1] == {
        "allow_redirects": False,
        "timeout": (5, 30),
        "stream": True,
    }


def test_release_requires_exact_named_assets():
    duplicate_assets = stable_release()["assets"]
    duplicate_assets.append(
        {"name": "update-manifest.json", "browser_download_url": MANIFEST_URL}
    )
    client = GithubReleaseClient(
        FakeSession([response(200, json_body=[stable_release(duplicate_assets)])]),
        REPOSITORY,
    )
    with pytest.raises(UpdateNetworkError, match="exactly one"):
        client.latest_stable()


def test_rate_limit_is_actionable():
    client = GithubReleaseClient(
        FakeSession(
            [response(403, headers={"X-RateLimit-Remaining": "0"})]
        ),
        REPOSITORY,
    )
    with pytest.raises(UpdateNetworkError, match="rate limit"):
        client.latest_stable()


def test_metadata_size_is_bounded_and_response_closed():
    oversized = response(200, body=b"x" * (MAX_METADATA_BYTES + 1))
    client = GithubReleaseClient(FakeSession([oversized]), REPOSITORY)
    with pytest.raises(UpdateNetworkError, match="too large"):
        client.fetch_bytes(MANIFEST_URL)
    assert oversized.closed is True


def test_timeout_is_actionable():
    client = GithubReleaseClient(
        FakeSession([requests.Timeout("secret request details")]), REPOSITORY
    )
    with pytest.raises(UpdateNetworkError, match="timed out"):
        client.fetch_bytes(MANIFEST_URL)
