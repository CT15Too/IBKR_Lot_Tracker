"""
Smoke-tests the FastAPI layer end to end, with IBKR's Flex Web Service and
the price lookup both mocked out -- no real IBKR credentials or network
access needed to run this.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from backend import db as db_module
from backend import main as main_module
from backend.ibkr_flex import FlexReport
from backend.prices import PriceQuote

FIXTURE_XML = (Path(__file__).parent / "fixtures" / "sample_flex.xml").read_text()


def make_client(tmp_path, monkeypatch):
    # Isolate this test's SQLite file and pretend the app is configured.
    test_db_path = str(tmp_path / "test_lots.db")
    monkeypatch.setattr(main_module, "_conn", db_module.get_connection(test_db_path))
    monkeypatch.setattr(main_module.settings, "ibkr_flex_token", "fake-token")
    monkeypatch.setattr(main_module.settings, "ibkr_flex_query_id", "12345")
    monkeypatch.setattr(main_module.settings, "flex_min_refresh_minutes", 15)

    monkeypatch.setattr(main_module, "fetch_flex_report", lambda token, qid: FlexReport(raw_xml=FIXTURE_XML))
    monkeypatch.setattr(
        main_module,
        "get_current_prices",
        lambda symbols, force_refresh=False, overrides=None: {
            "AAPL": PriceQuote(symbol="AAPL", price=228.40, stale=False),
            "TSLA": PriceQuote(symbol="TSLA", price=245.10, stale=False),
        },
    )
    return TestClient(main_module.app)


def test_status_before_any_refresh(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
    assert resp.json()["last_fetched_at"] is None


def test_lots_404_before_refresh(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    resp = client.get("/api/lots")
    assert resp.status_code == 404


def test_full_refresh_and_read_flow(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    refresh_resp = client.post("/api/refresh-lots")
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["ok"] is True

    lots_resp = client.get("/api/lots")
    assert lots_resp.status_code == 200
    body = lots_resp.json()
    symbols = {s["symbol"] for s in body["symbols"]}
    assert symbols == {"AAPL", "TSLA"}

    aapl = next(s for s in body["symbols"] if s["symbol"] == "AAPL")
    assert aapl["total_quantity"] == 15
    assert aapl["sellable_quantity"] == 10  # only the cheap lot is in profit
    assert aapl["lots"][0]["cost_price"] == 150.25  # cheapest lot sorts first


def test_refresh_cooldown_returns_429(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    first = client.post("/api/refresh-lots")
    assert first.status_code == 200

    second = client.post("/api/refresh-lots")
    assert second.status_code == 429

    forced = client.post("/api/refresh-lots?force=true")
    assert forced.status_code == 200


def test_refresh_without_config_returns_400(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module.settings, "ibkr_flex_token", "")
    resp = client.post("/api/refresh-lots")
    assert resp.status_code == 400


def test_index_page_served(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "IBKR Lot Tracker" in resp.text
