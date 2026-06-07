# LOG Agent — Decision Logger
#
# This agent has ONE job: persist every event that happens in the system
# to a SQLite database. Every alert fired, every client decision, every
# poll result, every error — all go here with a timestamp.
#
# Why it exists as a separate agent:
#   Logging is a cross-cutting concern. Every other agent needs it.
#   If logging logic were spread across all agents, changing the storage
#   format (e.g. SQLite → Postgres) would require touching every file.
#   Centralising here means one place to change, one place to query.
#
# Why append-only:
#   No log entry is ever modified or deleted. This creates an immutable
#   audit trail — you can always reconstruct exactly what happened, when,
#   and what decision was made. This is critical for debugging, compliance,
#   and understanding why the system behaved a certain way.
#
# Storage: SQLite (built into Python — no installation needed).
# Database file: data/stock_agent.db (created automatically on first run).

import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

# ── Database path ─────────────────────────────────────────────────
# Stored in the data/ folder relative to the project root.
# Path is configurable so tests can use an in-memory database.
DEFAULT_DB_PATH = str(
    Path(__file__).parent.parent / "data" / "stock_agent.db"
)


# ─────────────────────────────────────────────────────────────────
#  DATABASE SETUP
# ─────────────────────────────────────────────────────────────────

def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Creates the database and tables if they don't exist yet.
    Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS.

    Called once at system startup by the Orchestrator.
    """
    # Create the data/ directory if it doesn't exist
    # Skip for in-memory SQLite (used in tests)
    if db_path != ":memory:":
        dir_path = os.path.dirname(db_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS event_log (
            log_id          TEXT PRIMARY KEY,
            investment_id   TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            data            TEXT NOT NULL,    -- JSON blob
            timestamp       TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_investment_id
        ON event_log(investment_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_event_type
        ON event_log(event_type)
    """)

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────
#  MAIN FUNCTION
# ─────────────────────────────────────────────────────────────────

def log_event(
    investment_id: str,
    event_type: str,
    data: dict,
    db_path: str = DEFAULT_DB_PATH,
    timestamp: str = None
) -> dict:
    """
    Records an event to the database. Append-only — never updates or deletes.

    Parameters:
        investment_id : The investment this event belongs to
        event_type    : One of the EVENT_TYPES below
        data          : Any additional context as a dict (stored as JSON)
        db_path       : Path to the SQLite database (override for testing)
        timestamp     : ISO timestamp (defaults to now if not provided)

    Valid event_types:
        INVESTMENT_CREATED    — client confirmed investment
        POLL_COMPLETED        — hourly price check ran
        ALERT_FIRED           — a notification was sent
        CLIENT_DECISION       — client replied CONTINUE / SELL / EXTEND
        NO_RESPONSE           — client didn't respond within 4 hours
        DAILY_SUMMARY_SENT    — daily email was sent
        API_ERROR             — Alpha Vantage or yfinance call failed
        MONITORING_CLOSED     — investment sold or expired

    Returns:
        {"success": True, "log_id": str}
        or
        {"success": False, "error": str}

    Interview explanation:
        "Every meaningful event in the system flows through here.
        The data field is a JSON blob so each event type can store
        different contextual information without requiring schema changes.
        The append-only design means we never lose information — even if
        something went wrong, we can trace exactly what happened."
    """

    VALID_EVENT_TYPES = {
        "INVESTMENT_CREATED",
        "POLL_COMPLETED",
        "ALERT_FIRED",
        "CLIENT_DECISION",
        "NO_RESPONSE",
        "DAILY_SUMMARY_SENT",
        "API_ERROR",
        "MONITORING_CLOSED",
    }

    # ── Validation ────────────────────────────────────────────────
    if not investment_id or not investment_id.strip():
        return {
            "success": False,
            "error": "investment_id cannot be empty."
        }

    if event_type not in VALID_EVENT_TYPES:
        return {
            "success": False,
            "error": f"Unknown event_type '{event_type}'. "
                     f"Valid types: {sorted(VALID_EVENT_TYPES)}"
        }

    if not isinstance(data, dict):
        return {
            "success": False,
            "error": "data must be a dict."
        }

    # ── Build log entry ───────────────────────────────────────────
    ts = timestamp or datetime.now().isoformat()

    # log_id = timestamp + random suffix for uniqueness
    import uuid
    log_id = f"{ts[:19].replace(':', '-').replace('T', '_')}_{str(uuid.uuid4())[:6].upper()}"

    # ── Write to database ─────────────────────────────────────────
    try:
        # Ensure DB and table exist
        init_db(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO event_log
                (log_id, investment_id, event_type, data, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (
            log_id,
            investment_id.strip(),
            event_type,
            json.dumps(data),
            ts
        ))

        conn.commit()
        conn.close()

        return {
            "success": True,
            "log_id": log_id
        }

    except sqlite3.Error as e:
        return {
            "success": False,
            "error": f"Database error: {str(e)}"
        }


# ─────────────────────────────────────────────────────────────────
#  QUERY FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def get_events(
    investment_id: str,
    event_type: str = None,
    db_path: str = DEFAULT_DB_PATH
) -> dict:
    """
    Retrieves all logged events for a given investment.

    Parameters:
        investment_id : Filter by investment
        event_type    : Optional — filter by event type
        db_path       : Path to the SQLite database

    Returns:
        {
            "success": True,
            "events": [
                {
                    "log_id": str,
                    "investment_id": str,
                    "event_type": str,
                    "data": dict,
                    "timestamp": str
                },
                ...
            ],
            "count": int
        }
    """
    try:
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if event_type:
            cursor.execute("""
                SELECT * FROM event_log
                WHERE investment_id = ? AND event_type = ?
                ORDER BY timestamp ASC
            """, (investment_id, event_type))
        else:
            cursor.execute("""
                SELECT * FROM event_log
                WHERE investment_id = ?
                ORDER BY timestamp ASC
            """, (investment_id,))

        rows = cursor.fetchall()
        conn.close()

        events = []
        for row in rows:
            events.append({
                "log_id":        row["log_id"],
                "investment_id": row["investment_id"],
                "event_type":    row["event_type"],
                "data":          json.loads(row["data"]),
                "timestamp":     row["timestamp"]
            })

        return {
            "success": True,
            "events": events,
            "count": len(events)
        }

    except sqlite3.Error as e:
        return {
            "success": False,
            "error": f"Database error: {str(e)}"
        }


def get_last_client_decision(
    investment_id: str,
    db_path: str = DEFAULT_DB_PATH
) -> dict:
    """
    Returns the most recent CLIENT_DECISION event for an investment.
    Used by the Orchestrator to check what the client last decided.

    Returns:
        {"success": True, "decision": "CONTINUE"|"SELL"|"EXTEND"|None}
    """
    result = get_events(investment_id, "CLIENT_DECISION", db_path)
    if not result["success"]:
        return result

    if not result["events"]:
        return {"success": True, "decision": None}

    # Last event is most recent (ordered ASC, so take [-1])
    last = result["events"][-1]
    return {
        "success": True,
        "decision": last["data"].get("decision"),
        "timestamp": last["timestamp"]
    }
