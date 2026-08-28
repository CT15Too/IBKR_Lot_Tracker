"""Tiny SQLite persistence layer: just the latest Flex snapshot."""
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS flex_snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    raw_xml TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_overrides (
    symbol TEXT PRIMARY KEY,
    price  REAL NOT NULL
);
"""


def get_connection(database_path: str) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # This app uses one long-lived connection shared across requests, and
    # FastAPI runs sync endpoint functions in a threadpool -- so relax
    # sqlite3's default same-thread check. Writes are small/infrequent
    # (one row, on manual refresh) so this is safe for a single-user app.
    conn = sqlite3.connect(database_path, check_same_thread=False)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def save_snapshot(conn: sqlite3.Connection, raw_xml: str, fetched_at: str) -> None:
    conn.execute(
        "INSERT INTO flex_snapshot (id, raw_xml, fetched_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET raw_xml = excluded.raw_xml, fetched_at = excluded.fetched_at",
        (raw_xml, fetched_at),
    )
    conn.commit()


def load_snapshot(conn: sqlite3.Connection) -> tuple[str, str] | None:
    row = conn.execute("SELECT raw_xml, fetched_at FROM flex_snapshot WHERE id = 1").fetchone()
    if row is None:
        return None
    return row[0], row[1]


def save_price_override(conn: sqlite3.Connection, symbol: str, price: float) -> None:
    conn.execute(
        "INSERT INTO price_overrides (symbol, price) VALUES (?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET price = excluded.price",
        (symbol.upper(), price),
    )
    conn.commit()


def load_price_overrides(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute("SELECT symbol, price FROM price_overrides").fetchall()
    return {row[0]: row[1] for row in rows}
