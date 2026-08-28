"""Convenience entrypoint for the loopback-only browser application."""
import uvicorn

from backend.config import settings
from backend.main import create_app
from backend.runtime import LaunchMode, build_runtime

def main():
    runtime = build_runtime(
        LaunchMode.BROWSER, browser_database_path=settings.database_path
    )
    uvicorn.run(
        create_app(runtime),
        host="127.0.0.1",
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
