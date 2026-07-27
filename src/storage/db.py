"""SQLite persistence — the single place that touches the database.

Four responsibilities:
  1. Holdings  — the user's current positions (synced manually from Midas).
  2. Theses — why each position is owned, and what would prove that wrong.
  3. Thesis events — an append-only trail of openings, falsifier firings, reviews
     and closures. This is what makes SPEC section 6b's gate checkable: the count
     of sales with no falsifier behind them is the honest measure of whether the
     discipline is being kept.
  4. Recommendation log — the audit trail of the retired signal engine. Kept for
     history; nothing writes to it on the live path any more.

Stdlib `sqlite3` only — no DB server, zero setup. The store is intentionally tiny
and synchronous; the bot calls it from short handlers so blocking is negligible.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.models import (
    Action,
    Falsifier,
    FalsifierKind,
    Holding,
    Market,
    Recommendation,
    Thesis,
)

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

CREATE TABLE IF NOT EXISTS theses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol            TEXT NOT NULL,
    market            TEXT NOT NULL,
    opened_at         TEXT NOT NULL,
    entry_price       REAL NOT NULL,
    conviction        INTEGER NOT NULL,
    summary           TEXT NOT NULL,
    review_every_days INTEGER NOT NULL DEFAULT 90,
    last_reviewed_at  TEXT,
    closed_at         TEXT,
    closed_reason     TEXT NOT NULL DEFAULT ''
);

-- Only one OPEN thesis per symbol, but the history of closed ones is kept: what
-- you believed the last time you owned something is worth reading before you buy
-- it again.
CREATE UNIQUE INDEX IF NOT EXISTS idx_thesis_open
    ON theses (symbol, market) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS falsifiers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id   INTEGER NOT NULL REFERENCES theses (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL,
    threshold   REAL,
    lookback    INTEGER,
    persistence INTEGER
);

CREATE INDEX IF NOT EXISTS idx_falsifier_thesis ON falsifiers (thesis_id);

CREATE TABLE IF NOT EXISTS thesis_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id  INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    kind       TEXT NOT NULL,   -- OPENED | FIRED | REVIEWED | CLOSED
    detail     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_event_thesis ON thesis_events (thesis_id, created_at);
"""


class Store:
    """Thin repository over a SQLite file. Construct once, share across the app."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the scheduler thread and bot loop can share it.
        # That makes the connection reachable from several threads but does NOT
        # make it safe to use from several threads at once: the digest builds on
        # a worker thread (asyncio.to_thread) while command handlers write from
        # the event loop, so every statement goes through this lock.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----------------------------- holdings -----------------------------

    def upsert_holding(self, holding: Holding) -> None:
        """Insert or update a position (keyed by symbol+market)."""
        with self._lock:
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
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM holdings WHERE symbol = ? AND market = ?",
                (symbol.upper(), market.value),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def list_holdings(self) -> list[Holding]:
        with self._lock:
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

    def log_recommendation(
        self, reco: Recommendation, *, dedupe_same_day: bool = False
    ) -> int:
        """Append a recommendation to the audit log. Returns its row id.

        With `dedupe_same_day`, a call that repeats an identical
        (symbol, market, action) already logged today is skipped and the existing
        row id is returned. /scan is run on demand and the digest fires daily, so
        without this the log fills with copies of the same standing advice and
        /log shows one morning's scan instead of a history.
        """
        created_at = reco.created_at or datetime.now(timezone.utc)
        created = created_at.isoformat()
        with self._lock:
            if dedupe_same_day:
                existing = self._conn.execute(
                    """
                    SELECT id FROM recommendations
                    WHERE symbol = ? AND market = ? AND action = ?
                      AND date(created_at) = date(?)
                    ORDER BY id DESC LIMIT 1
                    """,
                    (reco.symbol.upper(), reco.market.value, reco.action.value, created),
                ).fetchone()
                if existing is not None:
                    return int(existing["id"])

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
        with self._lock:
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

    # ------------------------------ theses ------------------------------

    def open_thesis(self, thesis: Thesis) -> int:
        """Record why a position is owned. Returns the new thesis id.

        Refuses a thesis with no checkable falsifier — SPEC section 6b gate 2.
        A position whose only exit condition is "I'll know it when I see it" has
        no exit condition, and the whole mechanism depends on the calm version of
        the owner leaving something behind for the anxious one to argue with.
        """
        if not thesis.has_checkable_falsifier:
            raise ValueError(
                f"{thesis.symbol}: a thesis needs at least one checkable falsifier "
                f"(SPEC 6b). A MANUAL note alone is a reminder, not an exit condition."
            )
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO theses
                    (symbol, market, opened_at, entry_price, conviction, summary,
                     review_every_days, last_reviewed_at, closed_at, closed_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, '')
                """,
                (
                    thesis.symbol.upper(), thesis.market.value,
                    thesis.opened_at.isoformat(), thesis.entry_price,
                    thesis.conviction, thesis.summary, thesis.review_every_days,
                    thesis.last_reviewed_at.isoformat()
                    if thesis.last_reviewed_at else None,
                ),
            )
            thesis_id = int(cur.lastrowid)
            for f in thesis.falsifiers:
                self._conn.execute(
                    """
                    INSERT INTO falsifiers
                        (thesis_id, kind, text, threshold, lookback, persistence)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (thesis_id, f.kind.value, f.text, f.threshold,
                     f.lookback, f.persistence),
                )
            self._conn.execute(
                "INSERT INTO thesis_events (thesis_id, created_at, kind, detail) "
                "VALUES (?, ?, 'OPENED', ?)",
                (thesis_id, thesis.opened_at.isoformat(),
                 f"entry {thesis.entry_price:g}, conviction {thesis.conviction}"),
            )
            self._conn.commit()
            return thesis_id

    def _falsifiers_for(self, thesis_id: int) -> tuple[Falsifier, ...]:
        rows = self._conn.execute(
            "SELECT kind, text, threshold, lookback, persistence "
            "FROM falsifiers WHERE thesis_id = ? ORDER BY id",
            (thesis_id,),
        ).fetchall()
        return tuple(
            Falsifier(
                kind=FalsifierKind(r["kind"]), text=r["text"],
                threshold=r["threshold"], lookback=r["lookback"],
                persistence=r["persistence"],
            )
            for r in rows
        )

    def _row_to_thesis(self, r) -> Thesis:
        return Thesis(
            id=r["id"], symbol=r["symbol"], market=Market(r["market"]),
            opened_at=datetime.fromisoformat(r["opened_at"]),
            entry_price=r["entry_price"], conviction=r["conviction"],
            summary=r["summary"], review_every_days=r["review_every_days"],
            last_reviewed_at=datetime.fromisoformat(r["last_reviewed_at"])
            if r["last_reviewed_at"] else None,
            closed_at=datetime.fromisoformat(r["closed_at"]) if r["closed_at"] else None,
            closed_reason=r["closed_reason"],
            falsifiers=self._falsifiers_for(r["id"]),
        )

    def get_thesis(self, symbol: str, market: Market) -> Thesis | None:
        """The open thesis for a symbol, if there is one."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM theses WHERE symbol = ? AND market = ? "
                "AND closed_at IS NULL",
                (symbol.upper(), market.value),
            ).fetchone()
            return self._row_to_thesis(row) if row else None

    def list_theses(self, *, include_closed: bool = False) -> list[Thesis]:
        with self._lock:
            sql = "SELECT * FROM theses"
            if not include_closed:
                sql += " WHERE closed_at IS NULL"
            sql += " ORDER BY market, symbol"
            rows = self._conn.execute(sql).fetchall()
            return [self._row_to_thesis(r) for r in rows]

    def theses_due_for_review(self, now: datetime) -> list[Thesis]:
        return [t for t in self.list_theses() if t.review_is_due(now)]

    def record_thesis_event(
        self, thesis_id: int, kind: str, detail: str = "",
        when: datetime | None = None,
    ) -> int:
        """Append-only. Nothing in this table is ever updated or deleted."""
        stamp = (when or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO thesis_events (thesis_id, created_at, kind, detail) "
                "VALUES (?, ?, ?, ?)",
                (thesis_id, stamp, kind, detail),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def thesis_events(self, thesis_id: int, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT created_at, kind, detail FROM thesis_events "
                "WHERE thesis_id = ? ORDER BY id DESC LIMIT ?",
                (thesis_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_reviewed(self, thesis_id: int, when: datetime, note: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE theses SET last_reviewed_at = ? WHERE id = ?",
                (when.isoformat(), thesis_id),
            )
            self._conn.commit()
        self.record_thesis_event(thesis_id, "REVIEWED", note, when)

    def close_thesis(
        self, thesis_id: int, when: datetime, reason: str, *, explained: bool = True
    ) -> None:
        """Close a thesis, recording whether anything actually justified it.

        `explained=False` is the important case: a sale with no falsifier fired.
        It is never blocked — the owner may always sell — but it is counted, and
        that count is the measure SPEC section 6b asks for. Selling because the
        price moved and selling because the reasoning broke look identical in a
        brokerage statement and nothing alike here.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE theses SET closed_at = ?, closed_reason = ? WHERE id = ?",
                (when.isoformat(), reason, thesis_id),
            )
            self._conn.commit()
        self.record_thesis_event(
            thesis_id, "CLOSED",
            reason if explained else f"UNEXPLAINED — no falsifier fired: {reason}",
            when,
        )

    def unexplained_closures(self, since: datetime | None = None) -> int:
        """How often a position was sold with nothing having actually broken."""
        with self._lock:
            sql = ("SELECT COUNT(*) AS n FROM thesis_events "
                   "WHERE kind = 'CLOSED' AND detail LIKE 'UNEXPLAINED%'")
            params: tuple = ()
            if since is not None:
                sql += " AND created_at >= ?"
                params = (since.isoformat(),)
            return int(self._conn.execute(sql, params).fetchone()["n"])
