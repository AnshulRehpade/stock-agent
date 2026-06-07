# Tests for the INV Agent (investment_manager.py)
#
# No mocking needed — pure calculations and date arithmetic.
# Run: pytest tests/test_investment_manager.py -v

import pytest
from datetime import date, timedelta
from agents.investment_manager import create_investment, get_days_remaining


# ── Happy path tests ──────────────────────────────────────────────

class TestCreateInvestmentSuccess:

    def test_basic_investment_succeeds(self):
        result = create_investment(
            ticker="AAPL",
            budget=5000.00,
            current_price=189.50
        )
        assert result["success"] is True
        assert result["ticker"] == "AAPL"

    def test_shares_calculated_correctly(self):
        """$5000 at $189.50 = floor(26.38) = 26 shares"""
        result = create_investment("AAPL", 5000.00, 189.50)
        assert result["shares"] == 26

    def test_total_invested_is_shares_times_price(self):
        """26 × $189.50 = $4,927.00"""
        result = create_investment("AAPL", 5000.00, 189.50)
        assert result["total_invested"] == round(26 * 189.50, 2)

    def test_remaining_cash_is_budget_minus_invested(self):
        """$5,000 - $4,927 = $73.00"""
        result = create_investment("AAPL", 5000.00, 189.50)
        assert result["remaining_cash"] == round(5000.00 - 26 * 189.50, 2)

    def test_deadline_is_30_days_after_investment(self):
        inv_date = date(2026, 6, 5)
        result = create_investment("AAPL", 5000.00, 189.50,
                                   investment_date=inv_date)
        assert result["investment_date"] == "2026-06-05"
        assert result["deadline_date"] == "2026-07-05"

    def test_investment_date_defaults_to_today(self):
        result = create_investment("AAPL", 5000.00, 189.50)
        assert result["investment_date"] == date.today().isoformat()

    def test_status_is_active_on_creation(self):
        result = create_investment("AAPL", 5000.00, 189.50)
        assert result["status"] == "ACTIVE"

    def test_ticker_uppercased(self):
        result = create_investment("aapl", 5000.00, 189.50)
        assert result["ticker"] == "AAPL"

    def test_ticker_stripped_of_whitespace(self):
        result = create_investment("  MSFT  ", 5000.00, 300.00)
        assert result["ticker"] == "MSFT"

    def test_confirmation_summary_included(self):
        result = create_investment("AAPL", 5000.00, 189.50)
        assert "confirmation_summary" in result
        assert "AAPL" in result["confirmation_summary"]
        assert "5.00%" in result["confirmation_summary"]

    def test_budget_preserved_in_record(self):
        result = create_investment("AAPL", 7500.00, 189.50)
        assert result["budget"] == 7500.00

    def test_high_price_stock_buys_few_shares(self):
        """$5,000 budget for a $1,500 stock = 3 shares"""
        result = create_investment("LLY", 5000.00, 1500.00)
        assert result["success"] is True
        assert result["shares"] == 3
        assert result["total_invested"] == 4500.00
        assert result["remaining_cash"] == 500.00

    def test_exact_budget_leaves_zero_cash(self):
        """$1,000 at $100 exactly = 10 shares, $0 remaining"""
        result = create_investment("TEST", 1000.00, 100.00)
        assert result["shares"] == 10
        assert result["remaining_cash"] == 0.00


# ── Validation / error tests ──────────────────────────────────────

class TestCreateInvestmentValidation:

    def test_budget_too_low_for_one_share(self):
        """$100 can't buy 1 share at $500"""
        result = create_investment("GOOGL", 100.00, 500.00)
        assert result["success"] is False
        assert "insufficient" in result["error"].lower()
        assert "GOOGL" in result["error"]

    def test_zero_budget_fails(self):
        result = create_investment("AAPL", 0, 189.50)
        assert result["success"] is False
        assert "Budget" in result["error"]

    def test_negative_budget_fails(self):
        result = create_investment("AAPL", -100.0, 189.50)
        assert result["success"] is False

    def test_zero_price_fails(self):
        result = create_investment("AAPL", 5000.00, 0)
        assert result["success"] is False
        assert "price" in result["error"].lower()

    def test_negative_price_fails(self):
        result = create_investment("AAPL", 5000.00, -50.0)
        assert result["success"] is False

    def test_empty_ticker_fails(self):
        result = create_investment("", 5000.00, 189.50)
        assert result["success"] is False
        assert "Ticker" in result["error"]

    def test_none_ticker_fails(self):
        result = create_investment(None, 5000.00, 189.50)
        assert result["success"] is False


# ── Share calculation edge cases ──────────────────────────────────

class TestShareCalculation:

    def test_fractional_shares_floored(self):
        """$999 at $100 = floor(9.99) = 9 shares, not 10"""
        result = create_investment("TEST", 999.00, 100.00)
        assert result["shares"] == 9

    def test_large_budget_many_shares(self):
        """$100,000 at $50 = 2,000 shares"""
        result = create_investment("TEST", 100_000.00, 50.00)
        assert result["shares"] == 2000

    def test_total_invested_never_exceeds_budget(self):
        """total_invested should always be <= budget"""
        for price in [10.00, 99.99, 189.50, 500.00, 1500.00]:
            result = create_investment("TEST", 5000.00, price)
            if result["success"]:
                assert result["total_invested"] <= 5000.00


# ── Days remaining tests ──────────────────────────────────────────

class TestGetDaysRemaining:

    def test_day_0_returns_30(self):
        """On the investment date itself, 30 days remain"""
        inv_date = date(2026, 6, 5)
        result = get_days_remaining("2026-06-05", as_of_date=inv_date)
        assert result == 30

    def test_day_15_returns_15(self):
        inv_date = date(2026, 6, 5)
        as_of = inv_date + timedelta(days=15)
        result = get_days_remaining("2026-06-05", as_of_date=as_of)
        assert result == 15

    def test_deadline_day_returns_0(self):
        """On exactly day 30, 0 days remain"""
        inv_date = date(2026, 6, 5)
        deadline = inv_date + timedelta(days=30)
        result = get_days_remaining("2026-06-05", as_of_date=deadline)
        assert result == 0

    def test_past_deadline_returns_0_not_negative(self):
        """After day 30, should return 0, never a negative number"""
        inv_date = date(2026, 6, 5)
        way_past = inv_date + timedelta(days=45)
        result = get_days_remaining("2026-06-05", as_of_date=way_past)
        assert result == 0

    def test_defaults_to_today(self):
        """Calling without as_of_date should use today's date.
        If investment was made 5 days ago, 25 days should remain."""
        past_date = (date.today() - timedelta(days=5)).isoformat()
        result = get_days_remaining(past_date)
        assert result == 25
