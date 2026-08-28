from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import settings
from .flex_parser import FlexParseError, parse_cash_balances, parse_open_position_lots
from .ibkr_flex import FlexServiceError, fetch_flex_report
from .lots import build_symbol_summaries, summaries_to_dict
from .prices import get_current_prices

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ibkr-lot-tracker")

app = FastAPI(title="IBKR Lot Tracker")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

_conn = db.get_connection(settings.database_path)


def _fetched_at_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def _can_refresh_flex(last_fetched_at: str | None) -> bool:
    if last_fetched_at is None:
        return True
    last = datetime.fromisoformat(last_fetched_at)
    return datetime.now(timezone.utc) - last >= timedelta(minutes=settings.flex_min_refresh_minutes)


@app.get("/api/status")
def status():
    snapshot = db.load_snapshot(_conn)
    return {
        "configured": settings.is_configured,
        "last_fetched_at": snapshot[1] if snapshot else None,
        "flex_min_refresh_minutes": settings.flex_min_refresh_minutes,
    }


@app.post("/api/refresh-lots")
def refresh_lots(force: bool = Query(False, description="Bypass the min-refresh-interval guard")):
    """Pull a fresh report from IBKR's Flex Web Service and store it."""
    if not settings.is_configured:
        raise HTTPException(
            status_code=400,
            detail="IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID are not set. See the README's setup steps.",
        )

    snapshot = db.load_snapshot(_conn)
    last_fetched_at = snapshot[1] if snapshot else None
    if not force and not _can_refresh_flex(last_fetched_at):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Flex data was refreshed less than {settings.flex_min_refresh_minutes} minutes ago "
                "(IBKR's own report only updates on that cadence anyway). Pass ?force=true to override."
            ),
        )

    try:
        report = fetch_flex_report(settings.ibkr_flex_token, settings.ibkr_flex_query_id)
    except FlexServiceError as exc:
        logger.exception("Flex Web Service call failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Validate it parses before we persist it.
    try:
        parse_open_position_lots(report.raw_xml)
    except FlexParseError as exc:
        raise HTTPException(status_code=502, detail=f"IBKR returned a report we couldn't parse: {exc}") from exc

    fetched_at = _fetched_at_str()
    db.save_snapshot(_conn, report.raw_xml, fetched_at)
    return {"ok": True, "fetched_at": fetched_at}


@app.get("/api/lots")
def get_lots(refresh_prices: bool = Query(True, description="Force a fresh price lookup instead of using the cache")):
    """Return lots grouped by symbol, with current price and unrealized P&L."""
    snapshot = db.load_snapshot(_conn)
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
    overrides = db.load_price_overrides(_conn)
    quotes = get_current_prices(symbols, force_refresh=refresh_prices, overrides=overrides)

    summaries = build_symbol_summaries(lots, quotes, force_refresh=refresh_prices)
    return {
        "fetched_at": fetched_at,
        "symbols": summaries_to_dict(summaries),
    }


class PriceOverrideRequest(BaseModel):
    symbol: str
    price: float


@app.post("/api/prices/override")
def set_price_override(req: PriceOverrideRequest):
    """Manually set a price for a symbol that yfinance can't find."""
    if req.price <= 0:
        raise HTTPException(status_code=400, detail="Price must be positive.")
    db.save_price_override(_conn, req.symbol, req.price)
    return {"ok": True, "symbol": req.symbol.upper(), "price": req.price}


@app.get("/api/cash")
def get_cash():
    """Return cash balances per currency from the latest Flex snapshot."""
    snapshot = db.load_snapshot(_conn)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No lot data yet. Call POST /api/refresh-lots first.")
    raw_xml, fetched_at = snapshot
    balances = parse_cash_balances(raw_xml)
    overrides = db.load_price_overrides(_conn)
    result = []
    for b in balances:
        fx = get_current_prices([f"{b.currency}USD"] if b.currency != "USD" else [], overrides=overrides)
        fx_rate = None
        if b.currency != "USD":
            from .prices import get_fx_rate
            fx_rate = get_fx_rate(b.currency, "USD")
        result.append({
            "currency": b.currency,
            "ending_cash": round(b.ending_cash, 2),
            "ending_settled_cash": round(b.ending_settled_cash, 2),
            "ending_cash_usd": round(b.ending_cash * fx_rate, 2) if fx_rate else None,
            "fx_rate": fx_rate,
        })
    return {"fetched_at": fetched_at, "balances": result}


@app.get("/api/debug/raw-flex")
def debug_raw_flex():
    """Return the raw last-fetched Flex XML, for adjusting FIELD_CANDIDATES in flex_parser.py."""
    snapshot = db.load_snapshot(_conn)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No snapshot stored yet.")
    return {"raw_xml": snapshot[0], "fetched_at": snapshot[1]}


# --- static frontend ---
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
