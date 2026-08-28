"""Convenience entrypoint: `python run.py` starts the app on APP_HOST:APP_PORT."""
import uvicorn

from backend.config import settings

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host=settings.app_host, port=settings.app_port, reload=False)
