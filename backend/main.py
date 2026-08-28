from __future__ import annotations

import dataclasses
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from requests import RequestException

from . import db
from .config import runtime_credentials, settings
from .credentials import CredentialStore
from .flex_parser import FlexParseError, parse_cash_balances, parse_open_position_lots
from .ibkr_flex import (
    FlexServiceError,
    fetch_flex_report,
    validate_flex_credentials,
)
from .lots import build_symbol_summaries, summaries_to_dict
from .prices import get_current_prices
from .runtime import LaunchMode, build_runtime
from .settings_store import SettingsStore
from .updater.models import UpdateSnapshot, UpdateStatus
from .updater.service import UpdateTransitionError
from .version import APP_VERSION

logger = logging.getLogger("ibkr-lot-tracker")


class DisabledUpdater:
    def snapshot(self):
        return UpdateSnapshot(UpdateStatus.IDLE)


class PriceOverrideRequest(BaseModel):
    symbol: str
    price: float


class SettingsRequest(BaseModel):
    flex_query_id: str = ""
    flex_token: Optional[str] = None
    clear_flex_token: bool = False
    auto_check_updates: Optional[bool] = None


def create_app(
    runtime,
    *,
    credential_store=None,
    settings_store=None,
    update_service=None,
    validate_credentials: Callable[[str, str], None] = validate_flex_credentials,
    request_shutdown: Callable[[], None] = lambda: None,
) -> FastAPI:
    """Build an isolated app with all external side effects behind boundaries."""
    conn = db.get_connection(str(runtime.database_path))
    credentials = credential_store
    desktop_settings = settings_store
    if runtime.desktop:
        credentials = credentials or CredentialStore()
        desktop_settings = desktop_settings or SettingsStore(runtime.settings_path)
    updater = update_service or DisabledUpdater()
    settings_lock = threading.RLock()
    application = FastAPI(title="IBKR Lot Tracker")

    def active_credentials():
        return runtime_credentials(runtime, credentials, desktop_settings)

    def settings_response():
        if runtime.mode is LaunchMode.BROWSER:
            query_id = settings.ibkr_flex_query_id
            has_token = bool(settings.ibkr_flex_token)
            auto_check = False
        else:
            persisted = desktop_settings.load()
            query_id = persisted.flex_query_id
            has_token = credentials.has_token()
            auto_check = persisted.auto_check_updates
        return {
            "mode": runtime.mode.value,
            "desktop": runtime.desktop,
            "configured": bool(query_id and has_token),
            "flex_query_id": query_id,
            "has_flex_token": has_token,
            "auto_check_updates": auto_check,
            "app_version": APP_VERSION,
        }

    def require_desktop_updates():
        if runtime.mode is not LaunchMode.PACKAGED_DESKTOP:
            raise HTTPException(
                status_code=409,
                detail="Updates are installed through Git in browser/source mode.",
            )

    def public_snapshot(snapshot=None):
        return (snapshot or updater.snapshot()).to_public_dict()

    def can_refresh(last_fetched_at):
        if last_fetched_at is None:
            return True
        last = datetime.fromisoformat(last_fetched_at)
        return datetime.now(timezone.utc) - last >= timedelta(
            minutes=settings.flex_min_refresh_minutes
        )

    @application.get("/api/health")
    def health():
        return {"ok": True}

    @application.get("/api/settings")
    def get_settings():
        return settings_response()

    @application.put("/api/settings")
    def put_settings(request: SettingsRequest):
        if not runtime.desktop:
            raise HTTPException(
                status_code=409,
                detail="Browser settings are configured through .env.",
            )
        if request.clear_flex_token and request.flex_token is not None:
            raise HTTPException(
                status_code=400,
                detail="Cannot replace and clear the Flex token together.",
            )
        query_id = request.flex_query_id.strip()
        if not query_id:
            raise HTTPException(status_code=400, detail="Flex query ID is required.")
        with settings_lock:
            previous = desktop_settings.load()
            old_token = credentials.get_token()
            candidate_token = (
                request.flex_token.strip()
                if request.flex_token is not None
                else old_token
            )
            if request.clear_flex_token:
                candidate_token = ""
            if candidate_token:
                try:
                    validate_credentials(candidate_token, query_id)
                except (FlexServiceError, RequestException) as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="IBKR could not validate those credentials; existing settings were kept.",
                    ) from exc
            auto_check = (
                previous.auto_check_updates
                if request.auto_check_updates is None
                else request.auto_check_updates
            )
            replacement = dataclasses.replace(
                previous,
                flex_query_id=query_id,
                auto_check_updates=auto_check,
            )
            try:
                if request.flex_token is not None:
                    if not candidate_token:
                        raise HTTPException(
                            status_code=400,
                            detail="Flex token must not be empty.",
                        )
                    credentials.set_token(candidate_token)
                elif request.clear_flex_token:
                    credentials.clear_token()
                desktop_settings.save(replacement)
            except HTTPException:
                raise
            except Exception as exc:
                try:
                    if old_token:
                        credentials.set_token(old_token)
                    else:
                        credentials.clear_token()
                except Exception:
                    logger.error("Could not restore credential after settings failure")
                raise HTTPException(
                    status_code=500, detail="Could not save settings."
                ) from exc
        return settings_response()

    @application.get("/api/updates/status")
    def update_status():
        return public_snapshot()

    @application.post("/api/updates/check")
    def update_check():
        require_desktop_updates()
        try:
            updater.check(manual=True)
        except UpdateTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return public_snapshot()

    @application.post("/api/updates/download")
    def update_download():
        require_desktop_updates()
        try:
            return public_snapshot(updater.approve_download())
        except UpdateTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/updates/cancel")
    def update_cancel():
        require_desktop_updates()
        try:
            return public_snapshot(updater.cancel_download())
        except UpdateTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/updates/defer")
    def update_defer():
        require_desktop_updates()
        try:
            return public_snapshot(updater.defer())
        except UpdateTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/updates/restart", status_code=202)
    def update_restart(background_tasks: BackgroundTasks):
        require_desktop_updates()
        try:
            updater.restart_and_update()
        except UpdateTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail="Could not start the update installer."
            ) from exc
        background_tasks.add_task(request_shutdown)
        return JSONResponse({"accepted": True}, status_code=202)

    @application.get("/api/status")
    def status():
        snapshot = db.load_snapshot(conn)
        token, query_id = active_credentials()
        return {
            "configured": bool(token and query_id),
            "last_fetched_at": snapshot[1] if snapshot else None,
            "flex_min_refresh_minutes": settings.flex_min_refresh_minutes,
        }

    @application.post("/api/refresh-lots")
    def refresh_lots(
        force: bool = Query(
            False, description="Bypass the min-refresh-interval guard"
        )
    ):
        token, query_id = active_credentials()
        if not token or not query_id:
            raise HTTPException(
                status_code=400,
                detail="IBKR Flex token and query ID are not configured.",
            )
        snapshot = db.load_snapshot(conn)
        last_fetched_at = snapshot[1] if snapshot else None
        if not force and not can_refresh(last_fetched_at):
            raise HTTPException(
                status_code=429,
                detail=(
                    "Flex data was refreshed less than {} minutes ago. "
                    "Pass ?force=true to override."
                ).format(settings.flex_min_refresh_minutes),
            )
        try:
            report = fetch_flex_report(token, query_id)
        except FlexServiceError as exc:
            logger.error("Flex Web Service call failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail="IBKR Flex request failed; check credentials and try again.",
            ) from exc
        try:
            parse_open_position_lots(report.raw_xml)
        except FlexParseError as exc:
            raise HTTPException(
                status_code=502,
                detail="IBKR returned a report that could not be parsed.",
            ) from exc
        fetched_at = datetime.now(timezone.utc).isoformat()
        db.save_snapshot(conn, report.raw_xml, fetched_at)
        return {"ok": True, "fetched_at": fetched_at}

    @application.get("/api/lots")
    def get_lots(
        refresh_prices: bool = Query(
            True, description="Force a fresh price lookup instead of using the cache"
        )
    ):
        snapshot = db.load_snapshot(conn)
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail="No lot data yet. Call POST /api/refresh-lots first.",
            )
        raw_xml, fetched_at = snapshot
        try:
            lots = parse_open_position_lots(raw_xml)
        except FlexParseError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        symbols = sorted({lot.symbol for lot in lots})
        overrides = db.load_price_overrides(conn)
        quotes = get_current_prices(
            symbols, force_refresh=refresh_prices, overrides=overrides
        )
        summaries = build_symbol_summaries(
            lots, quotes, force_refresh=refresh_prices
        )
        return {
            "fetched_at": fetched_at,
            "symbols": summaries_to_dict(summaries),
        }

    @application.post("/api/prices/override")
    def set_price_override(request: PriceOverrideRequest):
        if request.price <= 0:
            raise HTTPException(status_code=400, detail="Price must be positive.")
        db.save_price_override(conn, request.symbol, request.price)
        return {
            "ok": True,
            "symbol": request.symbol.upper(),
            "price": request.price,
        }

    @application.get("/api/cash")
    def get_cash():
        snapshot = db.load_snapshot(conn)
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail="No lot data yet. Call POST /api/refresh-lots first.",
            )
        raw_xml, fetched_at = snapshot
        balances = parse_cash_balances(raw_xml)
        result = []
        for balance in balances:
            fx_rate = None
            if balance.currency != "USD":
                from .prices import get_fx_rate

                fx_rate = get_fx_rate(balance.currency, "USD")
            result.append(
                {
                    "currency": balance.currency,
                    "ending_cash": round(balance.ending_cash, 2),
                    "ending_settled_cash": round(
                        balance.ending_settled_cash, 2
                    ),
                    "ending_cash_usd": (
                        round(balance.ending_cash * fx_rate, 2)
                        if fx_rate
                        else None
                    ),
                    "fx_rate": fx_rate,
                }
            )
        return {"fetched_at": fetched_at, "balances": result}

    @application.get("/api/debug/raw-flex")
    def debug_raw_flex():
        snapshot = db.load_snapshot(conn)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No snapshot stored yet.")
        return {"raw_xml": snapshot[0], "fetched_at": snapshot[1]}

    application.mount(
        "/static",
        StaticFiles(directory=str(runtime.frontend_dir)),
        name="static",
    )

    @application.get("/")
    def index():
        return FileResponse(str(runtime.frontend_dir / "index.html"))

    return application


browser_runtime = build_runtime(
    LaunchMode.BROWSER, browser_database_path=settings.database_path
)
app = create_app(browser_runtime)
