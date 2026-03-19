"""
SQLite database layer. All state is persisted here so it survives restarts.

Tables:
  port_status  — one row per port, upserted on every successful poll
  watch_state  — single-row table for notification watch mode
  poll_log     — ring-buffer debug log, pruned to last 24h on each poll
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, NamedTuple, Optional

from .chargepoint.base import StationData

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "chargepoint.db"


@contextmanager
def _get_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS port_status (
                port_number     INTEGER PRIMARY KEY,
                is_available    INTEGER NOT NULL DEFAULT 0,
                status_since    TEXT NOT NULL,
                last_polled_at  TEXT NOT NULL,
                last_poll_error TEXT,
                status_source   TEXT NOT NULL DEFAULT 'per_port'
            );

            CREATE TABLE IF NOT EXISTS watch_state (
                id               INTEGER PRIMARY KEY DEFAULT 1,
                is_active        INTEGER NOT NULL DEFAULT 0,
                activated_at     TEXT,
                last_notified_at TEXT,
                last_reminded_at TEXT
            );

            CREATE TABLE IF NOT EXISTS poll_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                polled_at   TEXT NOT NULL,
                success     INTEGER NOT NULL,
                error_msg   TEXT,
                raw_payload TEXT
            );
        """)

        # Seed watch_state row (idempotent)
        conn.execute("INSERT OR IGNORE INTO watch_state (id, is_active) VALUES (1, 0)")

        # Seed port_status rows with placeholder values (idempotent)
        now = _now_iso()
        conn.execute(
            "INSERT OR IGNORE INTO port_status (port_number, is_available, status_since, last_polled_at, status_source) VALUES (1, 0, ?, ?, 'per_port')",
            (now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO port_status (port_number, is_available, status_since, last_polled_at, status_source) VALUES (2, 0, ?, ?, 'per_port')",
            (now, now),
        )

    logger.info("Database initialised at %s", DB_PATH)


# ---------------------------------------------------------------------------
# Port status
# ---------------------------------------------------------------------------

class PortRow(NamedTuple):
    port_number: int
    is_available: bool
    status_since: str       # ISO-8601 UTC
    last_polled_at: str     # ISO-8601 UTC
    last_poll_error: Optional[str]
    status_source: str


def get_all_ports() -> list[PortRow]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT port_number, is_available, status_since, last_polled_at, last_poll_error, status_source "
            "FROM port_status ORDER BY port_number"
        ).fetchall()
    return [PortRow(*row) for row in rows]


def any_port_available() -> bool:
    with _get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM port_status WHERE is_available = 1").fetchone()
    return row[0] > 0


def get_available_ports() -> list[PortRow]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT port_number, is_available, status_since, last_polled_at, last_poll_error, status_source "
            "FROM port_status WHERE is_available = 1 ORDER BY port_number"
        ).fetchall()
    return [PortRow(*row) for row in rows]


def update_port_status(station_data: StationData) -> None:
    """
    Upsert port status from a fresh poll result.
    Only updates status_since when the availability state actually changes.
    """
    now = _now_iso()
    with _get_conn() as conn:
        for port in station_data.ports:
            existing = conn.execute(
                "SELECT is_available FROM port_status WHERE port_number = ?",
                (port.port_number,),
            ).fetchone()

            status_changed = existing is None or bool(existing["is_available"]) != port.is_available

            if status_changed:
                conn.execute(
                    """
                    INSERT INTO port_status
                        (port_number, is_available, status_since, last_polled_at, last_poll_error, status_source)
                    VALUES (?, ?, ?, ?, NULL, ?)
                    ON CONFLICT(port_number) DO UPDATE SET
                        is_available = excluded.is_available,
                        status_since = excluded.status_since,
                        last_polled_at = excluded.last_polled_at,
                        last_poll_error = NULL,
                        status_source = excluded.status_source
                    """,
                    (port.port_number, int(port.is_available), now, now, port.status_source),
                )
                logger.info(
                    "Port %d status changed → %s (source: %s)",
                    port.port_number,
                    "available" if port.is_available else "occupied",
                    port.status_source,
                )
            else:
                conn.execute(
                    "UPDATE port_status SET last_polled_at = ?, last_poll_error = NULL, status_source = ? WHERE port_number = ?",
                    (now, port.status_source, port.port_number),
                )


def set_poll_error(error_msg: str) -> None:
    """Record a poll failure against all port rows."""
    now = _now_iso()
    with _get_conn() as conn:
        conn.execute("UPDATE port_status SET last_poll_error = ?, last_polled_at = ?", (error_msg, now))


# ---------------------------------------------------------------------------
# Watch state
# ---------------------------------------------------------------------------

class WatchRow(NamedTuple):
    is_active: bool
    activated_at: Optional[datetime]
    last_notified_at: Optional[datetime]
    last_reminded_at: Optional[datetime]


def get_watch_state() -> WatchRow:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT is_active, activated_at, last_notified_at, last_reminded_at FROM watch_state WHERE id = 1"
        ).fetchone()
    return WatchRow(
        is_active=bool(row["is_active"]),
        activated_at=_parse_dt(row["activated_at"]),
        last_notified_at=_parse_dt(row["last_notified_at"]),
        last_reminded_at=_parse_dt(row["last_reminded_at"]),
    )


def set_watch_active(active: bool) -> None:
    now = _now_iso() if active else None
    with _get_conn() as conn:
        if active:
            conn.execute(
                "UPDATE watch_state SET is_active = 1, activated_at = ?, last_notified_at = NULL, last_reminded_at = NULL WHERE id = 1",
                (now,),
            )
        else:
            conn.execute(
                "UPDATE watch_state SET is_active = 0, activated_at = NULL, last_notified_at = NULL, last_reminded_at = NULL WHERE id = 1"
            )
    logger.info("Watch mode %s", "enabled" if active else "disabled")


def set_last_notified(dt: datetime) -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE watch_state SET last_notified_at = ? WHERE id = 1", (_dt_iso(dt),))


def set_last_reminded(dt: datetime) -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE watch_state SET last_reminded_at = ? WHERE id = 1", (_dt_iso(dt),))


# ---------------------------------------------------------------------------
# Poll log
# ---------------------------------------------------------------------------

def log_poll(success: bool, payload: object = None, error: Optional[str] = None) -> None:
    now = _now_iso()
    raw = json.dumps(payload, default=str) if payload is not None else None
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO poll_log (polled_at, success, error_msg, raw_payload) VALUES (?, ?, ?, ?)",
            (now, int(success), error, raw),
        )
        # Prune entries older than 24 hours
        conn.execute(
            "DELETE FROM poll_log WHERE polled_at < datetime('now', '-24 hours')"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt_iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# Keep private alias for internal use within this module
_parse_dt = parse_dt
