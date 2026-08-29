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

import requests

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - exercised only if dependency missing
    yf = None

# Yahoo Finance rate-limits aggressively (HTTP 429), especially requests that
# carry no User-Agent or an ancient one. A modern browser UA on a reused
# session is far less likely to be throttled, and the longer cache TTL halves
# how often we hit Yahoo relative to the frontend's 60s polling.
_YF_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_RETRY_BACKOFF_SECONDS = (0.5, 1.5)
_MAX_ATTEMPTS = 3

_CACHE_TTL_SECONDS = 120
_cache: dict[str, tuple[float, float | None]] = {}  # symbol -> (fetched_at, price)

_fx_cache: dict[str, tuple[float, float | None]] = {}  # "SGDUSD" -> (fetched_at, rate)


def _yf_session() -> requests.Session:
    """A shared session pinned to a real browser User-Agent."""
    session = requests.Session()
    session.headers["User-Agent"] = _YF_USER_AGENT
    return session


def _last_price(fast_info) -> float | None:
    """Read the quote price, preferring the cheap direct field.

    ``lastPrice`` is a field on the quote response. ``last_price`` instead
    triggers a separate 1y chart request (``_get_1y_prices``) that doubles the
    load on Yahoo and raises ``KeyError`` when the quote came back empty due to
    rate-limiting, so try the cheap path first.
    """
    price = fast_info.get("lastPrice")
    if price is None:
        try:
            price = fast_info.get("last_price")
        except Exception:
            price = None
    return float(price) if price else None


def get_fx_rate(from_ccy: str, to_ccy: str = "USD", force_refresh: bool = False) -> float | None:
    """Return exchange rate from_ccy -> to_ccy, cached briefly. None on failure."""
    if from_ccy == to_ccy:
        return 1.0
    key = f"{from_ccy}{to_ccy}"
    now = time.time()
    cached = _fx_cache.get(key)
    if not force_refresh and cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    result = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            ticker = yf.Ticker(f"{key}=X", session=_yf_session())
            rate = _last_price(ticker.fast_info)
            result = float(rate) if rate else None
            if result is not None:
                break
        except Exception:
            result = None
        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_SECONDS[attempt % len(_RETRY_BACKOFF_SECONDS)])
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

    results: dict[str, float | None] = {s: None for s in symbols}
    for attempt in range(_MAX_ATTEMPTS):
        got_any = False
        try:
            tickers = yf.Tickers(" ".join(symbols), session=_yf_session())
            for symbol in symbols:
                try:
                    price = _last_price(tickers.tickers[symbol].fast_info)
                    if price is not None:
                        results[symbol] = price
                        got_any = True
                except Exception:
                    continue
        except Exception:
            # Network hiccup or bad symbol batch -- retry below; on the last
            # attempt fall back to "unknown" rather than crash the refresh.
            pass
        if got_any:
            return results
        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_SECONDS[attempt % len(_RETRY_BACKOFF_SECONDS)])
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
