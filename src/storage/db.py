"""SQLite persistence — the single place that touches the database.

Two responsibilities:
  1. Holdings  — the user's current positions (synced manually from Midas).
  2. Recommendation log — an append-only audit trail of every call the system makes,
     so performance can be reviewed honestly later (required by SPEC section 6b).

Stdlib `sqlite3` only — no DB server, zero setup. The store is intentionally tiny
and synchronous; the bot calls it from short handlers so blocking is negligible.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.models import Action, Holding, Market, Recommendation

_SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    symbol    TEXT NOT NULL,
    market    TEXT NOT NULL,
    quantity  REAL NOT NULL,
    avg_cost  REAL NOT NULL,
    note      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (symbol, market)
);

CREATE TABLE IF NOT EXISTS recommendations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    market           TEXT NOT NULL,
    action           TEXT NOT NULL,
    score            REAL NOT NULL,
    price            REAL NOT NULL,
    stop_loss        REAL,
    take_profit      REAL,
    suggested_weight REAL,
    rationale        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_reco_created ON recommendations (created_at);
"""


class Store:
    """Thin repository over a SQLite file. Construct once, share across the app."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the scheduler thread and bot loop can share it.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ----------------------------- holdings -----------------------------

    def upsert_holding(self, holding: Holding) -> None:
        """Insert or update a position (keyed by symbol+market)."""
        self._conn.execute(
            """
            INSERT INTO holdings (symbol, market, quantity, avg_cost, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol, market) DO UPDATE SET
                quantity = excluded.quantity,
                avg_cost = excluded.avg_cost,
                note     = excluded.note
            """,
            (
                holding.symbol.upper(),
                holding.market.value,
                holding.quantity,
                holding.avg_cost,
                holding.note,
            ),
        )
        self._conn.commit()

    def remove_holding(self, symbol: str, market: Market) -> bool:
        """Delete a position. Returns True if a row was actually removed."""
        cur = self._conn.execute(
            "DELETE FROM holdings WHERE symbol = ? AND market = ?",
            (symbol.upper(), market.value),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_holdings(self) -> list[Holding]:
        rows = self._conn.execute(
            "SELECT symbol, market, quantity, avg_cost, note "
            "FROM holdings ORDER BY market, symbol"
        ).fetchall()
        return [
            Holding(
                symbol=r["symbol"],
                market=Market(r["market"]),
                quantity=r["quantity"],
                avg_cost=r["avg_cost"],
                note=r["note"],
            )
            for r in rows
        ]

    # -------------------------- recommendations -------------------------

    def log_recommendation(self, reco: Recommendation) -> int:
        """Append a recommendation to the audit log. Returns its row id."""
        created = (reco.created_at or datetime.now(timezone.utc)).isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO recommendations
                (created_at, symbol, market, action, score, price,
                 stop_loss, take_profit, suggested_weight, rationale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created,
                reco.symbol.upper(),
                reco.market.value,
                reco.action.value,
                reco.score,
                reco.price,
                reco.stop_loss,
                reco.take_profit,
                reco.suggested_weight,
                reco.rationale,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def recent_recommendations(self, limit: int = 20) -> list[Recommendation]:
        rows = self._conn.execute(
            "SELECT * FROM recommendations ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Recommendation(
                symbol=r["symbol"],
                market=Market(r["market"]),
                action=Action(r["action"]),
                score=r["score"],
                price=r["price"],
                stop_loss=r["stop_loss"],
                take_profit=r["take_profit"],
                suggested_weight=r["suggested_weight"],
                rationale=r["rationale"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]
