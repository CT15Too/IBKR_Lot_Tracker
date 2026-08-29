import os
from pathlib import Path

import pytest


@pytest.fixture
def assert_mode():
    """Assert POSIX permission bits, skipping the check on Windows.

    The updater checks in private key files, staging directories, and helper
    request files via ``chmod`` (e.g. ``0o600``/``0o700``). That is a POSIX
    concept: on Windows ``st_mode`` always reports ``0o666`` for files and
    ``0o777`` for directories regardless of ``chmod``, because access is
    governed by ACLs on the user's private temp directory instead. The code
    still calls ``chmod`` with the right intent on every platform; only the
    assertion is POSIX-only.
    """

    def _assert(path: Path, expected: int) -> None:
        if os.name != "posix":
            return
        assert (Path(path).stat().st_mode & 0o777) == expected

    return _assert
