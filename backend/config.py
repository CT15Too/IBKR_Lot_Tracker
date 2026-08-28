"""App configuration, loaded from environment variables / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .runtime import LaunchMode

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass
class Settings:
    ibkr_flex_token: str = os.getenv("IBKR_FLEX_TOKEN", "")
    ibkr_flex_query_id: str = os.getenv("IBKR_FLEX_QUERY_ID", "")
    flex_min_refresh_minutes: int = int(os.getenv("FLEX_MIN_REFRESH_MINUTES", "15"))
    database_path: str = os.getenv("DATABASE_PATH", "./data/lots.db")
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8000"))

    @property
    def is_configured(self) -> bool:
        return bool(self.ibkr_flex_token and self.ibkr_flex_query_id)


settings = Settings()


def runtime_credentials(runtime, credential_store=None, settings_store=None):
    """Return the active token/query pair without crossing unused boundaries."""
    if runtime.mode is LaunchMode.BROWSER:
        return settings.ibkr_flex_token, settings.ibkr_flex_query_id
    desktop_settings = settings_store.load()
    return credential_store.get_token(), desktop_settings.flex_query_id
