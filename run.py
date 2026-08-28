"""Convenience entrypoint: `python run.py` starts the app on APP_HOST:APP_PORT."""
import uvicorn

from backend.config import settings
from backend.main import create_app
from backend.runtime import LaunchMode, build_runtime

if __name__ == "__main__":
    runtime = build_runtime(
        LaunchMode.BROWSER, browser_database_path=settings.database_path
    )
    uvicorn.run(
        create_app(runtime),
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )
