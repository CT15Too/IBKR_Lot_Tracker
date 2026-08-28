import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Union


class LaunchMode(str, Enum):
    BROWSER = "browser"
    SOURCE_DESKTOP = "source_desktop"
    PACKAGED_DESKTOP = "packaged_desktop"


@dataclass(frozen=True)
class RuntimePaths:
    mode: LaunchMode
    resource_root: Path
    frontend_dir: Path
    data_dir: Path
    database_path: Path
    settings_path: Path
    staging_dir: Path
    log_path: Path
    desktop: bool


def build_runtime(
    mode: LaunchMode,
    *,
    platform_name: str = sys.platform,
    home: Optional[Union[str, Path]] = None,
    frozen_root: Optional[Union[str, Path]] = None,
    browser_database_path: Union[str, Path] = "./data/lots.db",
) -> RuntimePaths:
    home_path = Path.home() if home is None else Path(home)
    resource_root = (
        Path(frozen_root)
        if frozen_root is not None
        else Path(__file__).resolve().parent.parent
    )

    if mode is LaunchMode.BROWSER:
        database_path = Path(browser_database_path)
        data_dir = database_path.parent
    elif platform_name == "darwin":
        data_dir = home_path / "Library/Application Support/IBKR Lot Tracker"
        database_path = data_dir / "lots.db"
    elif platform_name == "win32":
        data_dir = Path(os.environ["LOCALAPPDATA"]) / "IBKR Lot Tracker"
        database_path = data_dir / "lots.db"
    else:
        xdg_data_home = Path(
            os.environ.get("XDG_DATA_HOME") or home_path / ".local/share"
        )
        data_dir = xdg_data_home / "ibkr-lot-tracker"
        database_path = data_dir / "lots.db"

    return RuntimePaths(
        mode=mode,
        resource_root=resource_root,
        frontend_dir=resource_root / "frontend",
        data_dir=data_dir,
        database_path=database_path,
        settings_path=data_dir / "settings.json",
        staging_dir=data_dir / "updates",
        log_path=data_dir / "app.log",
        desktop=mode is not LaunchMode.BROWSER,
    )
