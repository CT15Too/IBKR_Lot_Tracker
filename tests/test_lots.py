from pathlib import Path

from backend.flex_parser import parse_open_position_lots
from backend.lots import build_symbol_summaries
from backend.prices import PriceQuote

FIXTURE = Path(__file__).parent / "fixtures" / "sample_flex.xml"


def test_pnl_computation_and_sort_order():
    lots = parse_open_position_lots(FIXTURE.read_text())
    quotes = {
        "AAPL": PriceQuote(symbol="AAPL", price=228.40, stale=False),
        "TSLA": PriceQuote(symbol="TSLA", price=245.10, stale=False),
    }
    summaries = build_symbol_summaries(lots, quotes)
    by_symbol = {s.symbol: s for s in summaries}

    aapl = by_symbol["AAPL"]
    # cheapest cost lot (150.25) should sort first
    assert aapl.lots[0].cost_price == 150.25
    assert aapl.lots[0].is_profitable is True
    # the 240.00 cost lot is underwater at 228.40 current price
    assert aapl.lots[1].cost_price == 240.00
    assert aapl.lots[1].is_profitable is False

    # sellable quantity = only the profitable lot's 10 shares
    assert aapl.sellable_quantity == 10

    tsla = by_symbol["TSLA"]
    assert tsla.lots[0].cost_price == 190.00  # cheapest first
    assert tsla.lots[0].is_profitable is True
    assert tsla.lots[1].cost_price == 260.75
    assert tsla.lots[1].is_profitable is False


def test_missing_price_yields_none_pnl():
    lots = parse_open_position_lots(FIXTURE.read_text())
    quotes = {
        "AAPL": PriceQuote(symbol="AAPL", price=None, stale=True),
        "TSLA": PriceQuote(symbol="TSLA", price=None, stale=True),
    }
    summaries = build_symbol_summaries(lots, quotes)
    for s in summaries:
        assert s.current_price is None
        assert s.unrealized_pnl is None
        for lot in s.lots:
            assert lot.is_profitable is None
