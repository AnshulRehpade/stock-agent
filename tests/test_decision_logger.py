# Tests for the LOG Agent (decision_logger.py)
#
# All tests use an in-memory SQLite database (:memory:) so they
# don't touch the filesystem and run instantly.
#
# Run: pytest tests/test_decision_logger.py -v

import pytest
from agents.decision_logger import log_event, get_events, get_last_client_decision, init_db

# Use a temp file DB for all tests — in-memory SQLite doesn't
# persist between connections so it can't be shared across calls.
import tempfile
import os

@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    """Create a fresh temp DB file before each test."""
    global DB
    DB = str(tmp_path / "test_stock_agent.db")
    init_db(DB)


# ─────────────────────────────────────────────────────────────────
#  LOG EVENT — HAPPY PATH
# ─────────────────────────────────────────────────────────────────

class TestLogEvent:

    def test_basic_log_succeeds(self):
        result = log_event(
            investment_id="inv_001",
            event_type="INVESTMENT_CREATED",
            data={"ticker": "AAPL", "shares": 26},
            db_path=DB
        )
        assert result["success"] is True
        assert "log_id" in result

    def test_log_id_is_non_empty_string(self):
        result = log_event("inv_001", "POLL_COMPLETED",
                           {"profit_pct": 2.5}, db_path=DB)
        assert isinstance(result["log_id"], str)
        assert len(result["log_id"]) > 0

    def test_all_valid_event_types_accepted(self):
        valid_types = [
            "INVESTMENT_CREATED", "POLL_COMPLETED", "ALERT_FIRED",
            "CLIENT_DECISION", "NO_RESPONSE", "DAILY_SUMMARY_SENT",
            "API_ERROR", "MONITORING_CLOSED"
        ]
        for event_type in valid_types:
            result = log_event("inv_001", event_type, {}, db_path=DB)
            assert result["success"] is True, f"Failed for {event_type}"

    def test_multiple_events_for_same_investment(self):
        for i in range(5):
            log_event("inv_001", "POLL_COMPLETED",
                      {"profit_pct": i * 0.5}, db_path=DB)
        events = get_events("inv_001", db_path=DB)
        assert events["count"] == 5

    def test_data_dict_stored_and_retrieved_correctly(self):
        payload = {
            "ticker": "AAPL",
            "profit_pct": 3.5,
            "alert_type": "UPSIDE_ALERT",
            "nested": {"key": "value"}
        }
        log_event("inv_001", "ALERT_FIRED", payload, db_path=DB)
        events = get_events("inv_001", db_path=DB)
        stored_data = events["events"][0]["data"]
        assert stored_data["ticker"] == "AAPL"
        assert stored_data["profit_pct"] == 3.5
        assert stored_data["nested"]["key"] == "value"

    def test_events_are_ordered_oldest_first(self):
        """Events should be returned in chronological order."""
        log_event("inv_001", "POLL_COMPLETED", {"step": 1},
                  timestamp="2026-06-05T10:00:00", db_path=DB)
        log_event("inv_001", "POLL_COMPLETED", {"step": 2},
                  timestamp="2026-06-05T11:00:00", db_path=DB)
        log_event("inv_001", "POLL_COMPLETED", {"step": 3},
                  timestamp="2026-06-05T12:00:00", db_path=DB)

        events = get_events("inv_001", db_path=DB)
        steps = [e["data"]["step"] for e in events["events"]]
        assert steps == [1, 2, 3]

    def test_custom_timestamp_stored_correctly(self):
        ts = "2026-06-10T14:32:00"
        log_event("inv_001", "CLIENT_DECISION",
                  {"decision": "SELL"}, db_path=DB, timestamp=ts)
        events = get_events("inv_001", db_path=DB)
        assert events["events"][0]["timestamp"] == ts

    def test_empty_data_dict_is_valid(self):
        result = log_event("inv_001", "NO_RESPONSE", {}, db_path=DB)
        assert result["success"] is True


# ─────────────────────────────────────────────────────────────────
#  LOG EVENT — VALIDATION
# ─────────────────────────────────────────────────────────────────

class TestLogEventValidation:

    def test_empty_investment_id_fails(self):
        result = log_event("", "POLL_COMPLETED", {}, db_path=DB)
        assert result["success"] is False
        assert "investment_id" in result["error"]

    def test_whitespace_only_investment_id_fails(self):
        result = log_event("   ", "POLL_COMPLETED", {}, db_path=DB)
        assert result["success"] is False

    def test_invalid_event_type_fails(self):
        result = log_event("inv_001", "MADE_UP_EVENT", {}, db_path=DB)
        assert result["success"] is False
        assert "Unknown event_type" in result["error"]

    def test_non_dict_data_fails(self):
        result = log_event("inv_001", "POLL_COMPLETED",
                           "not a dict", db_path=DB)
        assert result["success"] is False
        assert "dict" in result["error"]

    def test_list_data_fails(self):
        result = log_event("inv_001", "POLL_COMPLETED", [1, 2, 3], db_path=DB)
        assert result["success"] is False


# ─────────────────────────────────────────────────────────────────
#  GET EVENTS
# ─────────────────────────────────────────────────────────────────

class TestGetEvents:

    def test_returns_all_events_for_investment(self):
        log_event("inv_001", "POLL_COMPLETED", {"n": 1}, db_path=DB)
        log_event("inv_001", "ALERT_FIRED",    {"n": 2}, db_path=DB)
        log_event("inv_001", "CLIENT_DECISION",{"n": 3}, db_path=DB)
        result = get_events("inv_001", db_path=DB)
        assert result["success"] is True
        assert result["count"] == 3

    def test_filter_by_event_type(self):
        log_event("inv_001", "POLL_COMPLETED",  {"n": 1}, db_path=DB)
        log_event("inv_001", "ALERT_FIRED",     {"n": 2}, db_path=DB)
        log_event("inv_001", "POLL_COMPLETED",  {"n": 3}, db_path=DB)
        result = get_events("inv_001", "POLL_COMPLETED", db_path=DB)
        assert result["count"] == 2
        for e in result["events"]:
            assert e["event_type"] == "POLL_COMPLETED"

    def test_different_investments_isolated(self):
        """Events for inv_001 should not appear in inv_002 query."""
        log_event("inv_001", "POLL_COMPLETED", {}, db_path=DB)
        log_event("inv_002", "POLL_COMPLETED", {}, db_path=DB)
        result = get_events("inv_001", db_path=DB)
        assert result["count"] == 1

    def test_no_events_returns_empty_list(self):
        result = get_events("inv_nonexistent", db_path=DB)
        assert result["success"] is True
        assert result["count"] == 0
        assert result["events"] == []

    def test_event_has_all_required_fields(self):
        log_event("inv_001", "ALERT_FIRED",
                  {"alert_type": "UPSIDE_ALERT"}, db_path=DB)
        result = get_events("inv_001", db_path=DB)
        event = result["events"][0]
        assert "log_id"        in event
        assert "investment_id" in event
        assert "event_type"    in event
        assert "data"          in event
        assert "timestamp"     in event


# ─────────────────────────────────────────────────────────────────
#  GET LAST CLIENT DECISION
# ─────────────────────────────────────────────────────────────────

class TestGetLastClientDecision:

    def test_returns_most_recent_decision(self):
        log_event("inv_001", "CLIENT_DECISION",
                  {"decision": "CONTINUE"},
                  timestamp="2026-06-05T10:00:00", db_path=DB)
        log_event("inv_001", "CLIENT_DECISION",
                  {"decision": "SELL"},
                  timestamp="2026-06-07T14:00:00", db_path=DB)
        result = get_last_client_decision("inv_001", db_path=DB)
        assert result["success"] is True
        assert result["decision"] == "SELL"

    def test_returns_none_when_no_decisions(self):
        result = get_last_client_decision("inv_no_decisions", db_path=DB)
        assert result["success"] is True
        assert result["decision"] is None

    def test_ignores_non_decision_events(self):
        """Only CLIENT_DECISION events should be returned."""
        log_event("inv_001", "POLL_COMPLETED",
                  {"profit_pct": 3.5}, db_path=DB)
        log_event("inv_001", "ALERT_FIRED",
                  {"alert_type": "UPSIDE_ALERT"}, db_path=DB)
        result = get_last_client_decision("inv_001", db_path=DB)
        assert result["decision"] is None

    def test_timestamp_included_in_result(self):
        log_event("inv_001", "CLIENT_DECISION",
                  {"decision": "CONTINUE"},
                  timestamp="2026-06-08T09:30:00", db_path=DB)
        result = get_last_client_decision("inv_001", db_path=DB)
        assert result["timestamp"] == "2026-06-08T09:30:00"
