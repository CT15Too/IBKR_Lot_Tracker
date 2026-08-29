from pathlib import Path

import pytest

from backend.flex_parser import FlexParseError, parse_open_position_lots

FIXTURE = Path(__file__).parent / "fixtures" / "sample_flex.xml"


def test_parses_all_lots():
    xml = FIXTURE.read_text(encoding="utf-8")
    lots = parse_open_position_lots(xml)
    assert len(lots) == 4


def test_groups_correctly_by_symbol():
    xml = FIXTURE.read_text(encoding="utf-8")
    lots = parse_open_position_lots(xml)
    symbols = {lot.symbol for lot in lots}
    assert symbols == {"AAPL", "TSLA"}


def test_lot_fields_parsed():
    xml = FIXTURE.read_text(encoding="utf-8")
    lots = parse_open_position_lots(xml)
    aapl_lots = sorted([l for l in lots if l.symbol == "AAPL"], key=lambda l: l.cost_price)
    assert aapl_lots[0].quantity == 10
    assert aapl_lots[0].cost_price == 150.25
    assert aapl_lots[0].open_datetime == "20260615;103200"
    assert aapl_lots[1].cost_price == 240.00


def test_rejects_non_flex_xml():
    with pytest.raises(FlexParseError):
        parse_open_position_lots("<NotAFlexResponse></NotAFlexResponse>")


def test_rejects_garbage():
    with pytest.raises(FlexParseError):
        parse_open_position_lots("not xml at all")
