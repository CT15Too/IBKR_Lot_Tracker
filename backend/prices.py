"""
Current-price lookup, kept deliberately separate from the Flex report.

Flex data (lot quantities/cost basis) only changes when you trade, so it's
fine to refresh every 15-30 min via IBKR's rate-limited service. Price is
the thing you want to refresh often, so it's a free/cheap lookup you can hit
on every page load without touching IBKR's Flex quota at all.

Uses Yahoo Finance (via yfinance) for delayed quotes (~15 min delay for most
US symbols), which is more than good enough for "is this lot in profit"
decisions. Not affiliated with or guaranteed by IBKR.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - exercised only if dependency missing
    yf = None

_CACHE_TTL_SECONDS = 60
_cache: dict[str, tuple[float, float | None]] = {}  # symbol -> (fetched_at, price)

_fx_cache: dict[str, tuple[float, float | None]] = {}  # "SGDUSD" -> (fetched_at, rate)


def get_fx_rate(from_ccy: str, to_ccy: str = "USD", force_refresh: bool = False) -> float | None:
    """Return exchange rate from_ccy -> to_ccy, cached for 60s. Returns None on failure."""
    if from_ccy == to_ccy:
        return 1.0
    key = f"{from_ccy}{to_ccy}"
    now = time.time()
    cached = _fx_cache.get(key)
    if not force_refresh and cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]
    try:
        ticker = yf.Ticker(f"{key}=X")
        rate = ticker.fast_info.get("last_price") or ticker.fast_info.get("lastPrice")
        result = float(rate) if rate else None
    except Exception:
        result = None
    _fx_cache[key] = (now, result)
    return result


@dataclass
class PriceQuote:
    symbol: str
    price: float | None
    stale: bool
    manual: bool = False  # True when price came from a user override


def _fetch_batch(symbols: list[str]) -> dict[str, float | None]:
    if yf is None or not symbols:
        return {s: None for s in symbols}

    results: dict[str, float | None] = {}
    try:
        tickers = yf.Tickers(" ".join(symbols))
        for symbol in symbols:
            try:
                fast_info = tickers.tickers[symbol].fast_info
                price = fast_info.get("last_price") or fast_info.get("lastPrice")
                results[symbol] = float(price) if price else None
            except Exception:
                results[symbol] = None
    except Exception:
        # Network hiccup or bad symbol batch -- fall back to "unknown" rather
        # than crash the whole refresh.
        results = {s: None for s in symbols}
    return results


def get_current_prices(
    symbols: list[str],
    force_refresh: bool = False,
    overrides: dict[str, float] | None = None,
) -> dict[str, PriceQuote]:
    """Return a price quote per symbol, using a short in-memory cache.

    ``overrides`` is a {symbol: price} dict of manually-set prices that are
    used as a fallback when yfinance cannot find a quote.
    """
    overrides = overrides or {}
    now = time.time()
    to_fetch = []
    for symbol in symbols:
        cached = _cache.get(symbol)
        if force_refresh or cached is None or (now - cached[0]) > _CACHE_TTL_SECONDS:
            to_fetch.append(symbol)

    if to_fetch:
        fetched = _fetch_batch(to_fetch)
        for symbol, price in fetched.items():
            _cache[symbol] = (now, price)

    out: dict[str, PriceQuote] = {}
    for symbol in symbols:
        fetched_at, price = _cache.get(symbol, (0.0, None))
        stale = price is None or (now - fetched_at) > _CACHE_TTL_SECONDS * 5
        if price is None and symbol in overrides:
            out[symbol] = PriceQuote(symbol=symbol, price=overrides[symbol], stale=False, manual=True)
        else:
            out[symbol] = PriceQuote(symbol=symbol, price=price, stale=stale)
    return out
