"""
Turns raw parsed lots + a current price per symbol into the view the
frontend renders: lots grouped by symbol, each annotated with unrealized
P&L, sorted so the most-profitable (sellable) lots surface first.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .flex_parser import Lot
from .prices import PriceQuote, get_fx_rate


@dataclass
class LotView:
    symbol: str
    description: str
    quantity: float
    cost_price: float
    currency: str
    open_datetime: str
    current_price: float | None
    unrealized_pnl: float | None        # in native currency
    unrealized_pnl_usd: float | None    # converted to USD (None if no FX rate)
    unrealized_pnl_pct: float | None
    is_profitable: bool | None
    price_is_stale: bool


@dataclass
class SymbolSummary:
    symbol: str
    description: str
    currency: str
    total_quantity: float
    avg_cost_price: float
    current_price: float | None
    market_value: float | None
    unrealized_pnl: float | None        # in native currency
    unrealized_pnl_usd: float | None    # converted to USD
    unrealized_pnl_pct: float | None
    fx_rate: float | None               # native -> USD rate used
    sellable_quantity: float
    manual_price: bool
    lots: list[LotView]


def _pnl(cost_price: float, current_price: float | None, quantity: float) -> tuple[float | None, float | None]:
    if current_price is None or cost_price == 0:
        return None, None
    dollar = (current_price - cost_price) * quantity
    pct = (current_price - cost_price) / cost_price * 100
    return round(dollar, 2), round(pct, 2)


def build_symbol_summaries(lots: list[Lot], quotes: dict[str, PriceQuote], force_refresh: bool = False) -> list[SymbolSummary]:
    by_symbol: dict[str, list[Lot]] = {}
    for lot in lots:
        by_symbol.setdefault(lot.symbol, []).append(lot)

    summaries: list[SymbolSummary] = []
    for symbol, symbol_lots in by_symbol.items():
        quote = quotes.get(symbol)
        current_price = quote.price if quote else None
        price_is_stale = quote.stale if quote else True
        manual_price = quote.manual if quote else False

        currency = symbol_lots[0].currency
        fx_rate = get_fx_rate(currency, "USD", force_refresh=force_refresh) if currency != "USD" else 1.0

        lot_views: list[LotView] = []
        for lot in symbol_lots:
            dollar, pct = _pnl(lot.cost_price, current_price, lot.quantity)
            dollar_usd = round(dollar * fx_rate, 2) if (dollar is not None and fx_rate is not None) else None
            lot_views.append(
                LotView(
                    symbol=lot.symbol,
                    description=lot.description,
                    quantity=lot.quantity,
                    cost_price=lot.cost_price,
                    currency=lot.currency,
                    open_datetime=lot.open_datetime,
                    current_price=current_price,
                    unrealized_pnl=dollar,
                    unrealized_pnl_usd=dollar_usd,
                    unrealized_pnl_pct=pct,
                    is_profitable=(dollar > 0) if dollar is not None else None,
                    price_is_stale=price_is_stale,
                )
            )

        lot_views.sort(key=lambda lv: lv.cost_price)

        total_quantity = sum(lv.quantity for lv in symbol_lots)
        total_cost = sum(lv.cost_price * lv.quantity for lv in symbol_lots)
        avg_cost_price = round(total_cost / total_quantity, 4) if total_quantity else 0.0
        market_value = round(current_price * total_quantity, 2) if current_price is not None else None
        symbol_pnl_dollar, symbol_pnl_pct = _pnl(avg_cost_price, current_price, total_quantity)
        symbol_pnl_usd = round(symbol_pnl_dollar * fx_rate, 2) if (symbol_pnl_dollar is not None and fx_rate is not None) else None
        sellable_quantity = sum(lv.quantity for lv in lot_views if lv.is_profitable)

        description = symbol_lots[0].description

        summaries.append(
            SymbolSummary(
                symbol=symbol,
                description=description,
                currency=currency,
                total_quantity=total_quantity,
                avg_cost_price=avg_cost_price,
                current_price=current_price,
                market_value=market_value,
                unrealized_pnl=symbol_pnl_dollar,
                unrealized_pnl_usd=symbol_pnl_usd,
                unrealized_pnl_pct=symbol_pnl_pct,
                fx_rate=fx_rate,
                sellable_quantity=sellable_quantity,
                manual_price=manual_price,
                lots=lot_views,
            )
        )

    summaries.sort(key=lambda s: s.symbol)
    return summaries


def summaries_to_dict(summaries: list[SymbolSummary]) -> list[dict]:
    return [asdict(s) for s in summaries]
