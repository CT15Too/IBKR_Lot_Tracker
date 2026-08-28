"""
Parses IBKR's Flex Query XML (an "Open Positions" report configured with
Level of Detail = Lot) into a list of plain-Python lot records.

IMPORTANT: IBKR does not publish a single canonical field list for every
account/report configuration, and their attribute names have shifted over
report-format versions. This parser reads every attribute on each
<OpenPosition> element into a dict, then picks values out of it using an
ordered list of "candidate" attribute names per logical field -- so if your
report uses a slightly different name than we guessed, it still has a good
chance of matching. If a field comes back empty for you, open one of your
raw reports (saved via the /api/debug/raw-flex endpoint) and add the
attribute name you see to the matching list in FIELD_CANDIDATES below.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

FIELD_CANDIDATES: dict[str, list[str]] = {
    "symbol": ["symbol", "underlyingSymbol"],
    "description": ["description"],
    "asset_category": ["assetCategory"],
    "currency": ["currency"],
    "quantity": ["position"],
    "cost_price": ["costBasisPrice", "openPrice", "costPrice", "avgCost"],
    "cost_basis_total": ["costBasisMoney"],
    "mark_price": ["markPrice"],
    "position_value": ["positionValue"],
    "unrealized_pnl": ["fifoPnlUnrealized", "unrealizedPnl"],
    "open_datetime": ["openDateTime", "holdingPeriodDateTime"],
    "side": ["side"],
    "account_id": ["accountId"],
    "level_of_detail": ["levelOfDetail"],
}


class FlexParseError(RuntimeError):
    pass


@dataclass
class Lot:
    symbol: str
    description: str
    asset_category: str
    currency: str
    quantity: float
    cost_price: float
    open_datetime: str
    side: str = "Long"
    account_id: str = ""
    mark_price: float | None = None  # price as of the Flex report itself (may be stale)
    raw: dict = field(default_factory=dict)


def _first_present(attrs: dict[str, str], candidates: list[str]) -> str | None:
    for name in candidates:
        if name in attrs and attrs[name] != "":
            return attrs[name]
    return None


def _to_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass
class CashBalance:
    currency: str
    ending_cash: float
    ending_settled_cash: float


def parse_cash_balances(raw_xml: str) -> list[CashBalance]:
    """Parse CashReportCurrency rows, skipping the BASE_SUMMARY aggregate."""
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return []
    balances = []
    for el in root.findall(".//CashReportCurrency"):
        ccy = el.attrib.get("currency", "")
        if not ccy or ccy == "BASE_SUMMARY":
            continue
        balances.append(CashBalance(
            currency=ccy,
            ending_cash=_to_float(el.attrib.get("endingCash")),
            ending_settled_cash=_to_float(el.attrib.get("endingSettledCash")),
        ))
    return balances


def parse_open_position_lots(raw_xml: str) -> list[Lot]:
    """Parse a Flex Query XML report and return every open lot found in it."""
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise FlexParseError(f"Could not parse Flex XML: {exc}") from exc

    if root.tag != "FlexQueryResponse":
        raise FlexParseError(
            f"Expected a <FlexQueryResponse> document, got <{root.tag}>. "
            "Is this really a Flex Query report XML?"
        )

    open_position_elements = root.findall(".//OpenPosition")
    if not open_position_elements:
        raise FlexParseError(
            "No <OpenPosition> rows found. Check that your Flex Query includes "
            "the 'Open Positions' section."
        )

    lots: list[Lot] = []
    for el in open_position_elements:
        attrs = el.attrib
        symbol = _first_present(attrs, FIELD_CANDIDATES["symbol"])
        quantity = _to_float(_first_present(attrs, FIELD_CANDIDATES["quantity"]))
        cost_price = _to_float(_first_present(attrs, FIELD_CANDIDATES["cost_price"]))

        if not symbol or quantity == 0:
            continue

        mark_price_raw = _first_present(attrs, FIELD_CANDIDATES["mark_price"])

        lots.append(
            Lot(
                symbol=symbol,
                description=_first_present(attrs, FIELD_CANDIDATES["description"]) or symbol,
                asset_category=_first_present(attrs, FIELD_CANDIDATES["asset_category"]) or "STK",
                currency=_first_present(attrs, FIELD_CANDIDATES["currency"]) or "USD",
                quantity=quantity,
                cost_price=cost_price,
                open_datetime=_first_present(attrs, FIELD_CANDIDATES["open_datetime"]) or "",
                side=_first_present(attrs, FIELD_CANDIDATES["side"]) or ("Long" if quantity > 0 else "Short"),
                account_id=_first_present(attrs, FIELD_CANDIDATES["account_id"]) or "",
                mark_price=_to_float(mark_price_raw) if mark_price_raw is not None else None,
                raw=dict(attrs),
            )
        )

    return lots
