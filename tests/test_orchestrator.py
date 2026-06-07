# Tests for the ORCH Agent (orchestrator.py)
#
# We mock all specialist agents so the Orchestrator tests only
# test ROUTING logic — not the business logic of each agent.
#
# Run: pytest tests/test_orchestrator.py -v

import pytest
from unittest.mock import patch, MagicMock
from agents.orchestrator import StockAgentOrchestrator


# ─────────────────────────────────────────────────────────────────
#  FIXTURES
# ─────────────────────────────────────────────────────────────────

@pytest.fixture
def orch(tmp_path):
    """Fresh Orchestrator in dry_run mode with a temp DB per test."""
    return StockAgentOrchestrator(
        db_path=str(tmp_path / "test.db"),
        dry_run=True
    )

def mock_quote(price=189.50):
    return {
        "success": True, "ticker": "AAPL",
        "price": price, "change": 1.2, "change_pct": "0.64%",
        "volume": 50_000_000, "fetched_at": "2026-06-05T10:00:00"
    }

def mock_history(days=90):
    closes = [round(180.0 + 0.1 * i, 2) for i in range(days)]
    data = []
    from datetime import date, timedelta
    base = date(2026, 1, 2)
    for i, close in enumerate(closes):
        data.append({
            "date": (base + timedelta(days=i)).isoformat(),
            "open": close, "high": close * 1.01,
            "low": close * 0.99, "close": close,
            "volume": 5_000_000
        })
    return {"success": True, "ticker": "AAPL",
            "days_returned": days, "daily_data": data}

def mock_investment_record():
    from datetime import date, timedelta
    today = date.today()
    return {
        "success": True,
        "investment_id": "inv_TEST01",
        "ticker": "AAPL",
        "purchase_price": 189.50,
        "shares": 26,
        "total_invested": 4927.00,
        "remaining_cash": 73.00,
        "budget": 5000.00,
        "investment_date": today.isoformat(),
        "deadline_date": (today + timedelta(days=30)).isoformat(),
        "days_total": 30,
        "status": "ACTIVE",
        "confirmation_summary": "  You are investing in AAPL\n"
    }


# ─────────────────────────────────────────────────────────────────
#  PHASE TESTS
# ─────────────────────────────────────────────────────────────────

class TestPhaseTransitions:

    def test_starts_in_pre_investment(self, orch):
        assert orch.phase == "PRE_INVESTMENT"

    def test_transitions_to_monitoring_after_confirm(self, orch, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote())
        mocker.patch("agents.orchestrator.create_investment",
                     return_value=mock_investment_record())
        mocker.patch("agents.orchestrator.get_historical_data",
                     return_value=mock_history())

        result = orch.confirm_investment("AAPL", 5000.0)
        assert result["success"] is True
        assert orch.phase == "MONITORING"

    def test_transitions_to_closed_on_sell(self, orch, mocker):
        # Setup: put orchestrator in MONITORING phase
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote())
        mocker.patch("agents.orchestrator.create_investment",
                     return_value=mock_investment_record())
        mocker.patch("agents.orchestrator.get_historical_data",
                     return_value=mock_history())
        orch.confirm_investment("AAPL", 5000.0)

        result = orch.record_client_decision("SELL")
        assert result["success"] is True
        assert orch.phase == "CLOSED"

    def test_cannot_invest_twice(self, orch, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote())
        mocker.patch("agents.orchestrator.create_investment",
                     return_value=mock_investment_record())
        mocker.patch("agents.orchestrator.get_historical_data",
                     return_value=mock_history())

        orch.confirm_investment("AAPL", 5000.0)
        result = orch.confirm_investment("MSFT", 3000.0)

        assert result["success"] is False
        assert "active investment" in result["error"].lower()


# ─────────────────────────────────────────────────────────────────
#  CONFIRM INVESTMENT
# ─────────────────────────────────────────────────────────────────

class TestConfirmInvestment:

    def test_fails_if_quote_fetch_fails(self, orch, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value={"success": False, "error": "API limit"})
        result = orch.confirm_investment("AAPL", 5000.0)
        assert result["success"] is False
        assert "API limit" in result["error"]

    def test_fails_if_budget_too_low(self, orch, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote(price=500.0))
        mocker.patch("agents.orchestrator.create_investment",
                     return_value={"success": False,
                                   "error": "Budget too low"})
        mocker.patch("agents.orchestrator.get_historical_data",
                     return_value=mock_history())
        result = orch.confirm_investment("AAPL", 100.0)
        assert result["success"] is False

    def test_investment_stored_in_state(self, orch, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote())
        mocker.patch("agents.orchestrator.create_investment",
                     return_value=mock_investment_record())
        mocker.patch("agents.orchestrator.get_historical_data",
                     return_value=mock_history())

        orch.confirm_investment("AAPL", 5000.0)
        assert orch.investment is not None
        assert orch.investment["ticker"] == "AAPL"


# ─────────────────────────────────────────────────────────────────
#  HOURLY POLL
# ─────────────────────────────────────────────────────────────────

class TestHourlyPoll:

    @pytest.fixture(autouse=True)
    def setup_monitoring(self, orch, mocker):
        """Put the orchestrator in MONITORING phase before each test."""
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote())
        mocker.patch("agents.orchestrator.create_investment",
                     return_value=mock_investment_record())
        mocker.patch("agents.orchestrator.get_historical_data",
                     return_value=mock_history())
        orch.confirm_investment("AAPL", 5000.0)
        self.orch = orch

    def test_fails_when_not_in_monitoring(self, tmp_path):
        fresh_orch = StockAgentOrchestrator(
            db_path=str(tmp_path / "fresh.db"), dry_run=True
        )
        result = fresh_orch.run_hourly_poll()
        assert result["success"] is False

    def test_no_action_when_no_threshold_crossed(self, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote(price=190.0))  # ~0.26% profit
        result = self.orch.run_hourly_poll()
        assert result["success"] is True
        assert result["action"] == "NO_ACTION"

    def test_upside_alert_when_profit_above_3pct(self, mocker):
        # Price 3.5% above purchase price of 189.50
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote(price=196.12))  # ~3.5%
        result = self.orch.run_hourly_poll()
        assert result["success"] is True
        assert result["action"] == "UPSIDE_ALERT"

    def test_target_reached_when_profit_above_5pct(self, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote(price=199.0))  # ~5% above 189.50
        result = self.orch.run_hourly_poll()
        assert result["success"] is True
        assert result["action"] == "TARGET_REACHED"

    def test_loss_alert_when_price_drops(self, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote(price=185.0))  # ~2.4% loss
        result = self.orch.run_hourly_poll()
        assert result["success"] is True
        assert result["action"] in ("LOSS_ALERT_MINOR", "LOSS_ALERT_MAJOR")

    def test_poll_fails_gracefully_on_api_error(self, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value={"success": False, "error": "API limit"})
        result = self.orch.run_hourly_poll()
        assert result["success"] is False
        assert "API limit" in result["error"]

    def test_alert_state_updated_after_poll(self, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote(price=196.12))
        self.orch.run_hourly_poll()
        # last_poll_profit_pct should be updated
        assert self.orch.last_poll_profit_pct != 0.0


# ─────────────────────────────────────────────────────────────────
#  CLIENT DECISIONS
# ─────────────────────────────────────────────────────────────────

class TestClientDecisions:

    @pytest.fixture(autouse=True)
    def setup_monitoring(self, orch, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote())
        mocker.patch("agents.orchestrator.create_investment",
                     return_value=mock_investment_record())
        mocker.patch("agents.orchestrator.get_historical_data",
                     return_value=mock_history())
        orch.confirm_investment("AAPL", 5000.0)
        self.orch = orch

    def test_continue_resets_thresholds(self):
        result = self.orch.record_client_decision("CONTINUE")
        assert result["success"] is True
        assert result["decision"] == "CONTINUE"
        assert self.orch.alert_state["upside_alert_armed"] is True
        assert self.orch.alert_state["last_alerted_profit_pct"] == 0.0

    def test_sell_closes_investment(self):
        result = self.orch.record_client_decision("SELL")
        assert result["success"] is True
        assert self.orch.phase == "CLOSED"
        assert self.orch.investment["status"] == "SOLD"

    def test_extend_opens_new_30_day_window(self):
        from datetime import date, timedelta
        result = self.orch.record_client_decision("EXTEND")
        assert result["success"] is True
        assert "new_deadline" in result
        expected = (date.today() + timedelta(days=30)).isoformat()
        assert result["new_deadline"] == expected

    def test_invalid_decision_returns_error(self):
        result = self.orch.record_client_decision("MAYBE")
        assert result["success"] is False
        assert "MAYBE" in result["error"]

    def test_decision_fails_when_not_monitoring(self, tmp_path):
        fresh = StockAgentOrchestrator(
            db_path=str(tmp_path / "fresh.db"), dry_run=True
        )
        result = fresh.record_client_decision("SELL")
        assert result["success"] is False


# ─────────────────────────────────────────────────────────────────
#  STATUS
# ─────────────────────────────────────────────────────────────────

class TestGetStatus:

    def test_status_in_pre_investment(self, orch):
        status = orch.get_status()
        assert status["phase"] == "PRE_INVESTMENT"

    def test_status_in_monitoring(self, orch, mocker):
        mocker.patch("agents.orchestrator.get_stock_quote",
                     return_value=mock_quote())
        mocker.patch("agents.orchestrator.create_investment",
                     return_value=mock_investment_record())
        mocker.patch("agents.orchestrator.get_historical_data",
                     return_value=mock_history())
        orch.confirm_investment("AAPL", 5000.0)

        status = orch.get_status()
        assert status["phase"] == "MONITORING"
        assert status["ticker"] == "AAPL"
        assert status["purchase_price"] == 189.50
        assert status["shares"] == 26
        assert "days_remaining" in status
        assert "alert_state" in status
