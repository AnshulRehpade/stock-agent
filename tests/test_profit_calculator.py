# Tests for the PROFIT Agent (profit_calculator.py)
#
# No mocking needed here — this agent is pure maths.
# We just call it with known inputs and check the outputs.
#
# Run: pytest tests/test_profit_calculator.py -v

import pytest
from agents.profit_calculator import calculate_profit


# ── Helper: a standard investment used across many tests ──────────

# Client bought 10 shares of AAPL at $180 each → $1800 invested
BASE = {
    "purchase_price": 180.0,
    "shares": 10,
    "total_invested": 1800.0,
}


# ── Profit scenarios ──────────────────────────────────────────────

class TestProfitScenarios:

    def test_5_percent_profit(self):
        """
        Stock went from $180 to $189 (+5%).
        Should return profit_loss_pct = 5.0.
        """
        result = calculate_profit(
            current_price=189.0, **BASE
        )
        assert result["success"] is True
        assert result["current_value"] == 1890.0
        assert result["profit_loss_dollars"] == 90.0
        assert result["profit_loss_pct"] == 5.0
        assert result["reached_5_pct"] is True
        assert result["reached_3_pct"] is True
        assert result["is_in_profit"] is True
        assert result["is_in_loss"] is False

    def test_3_percent_profit(self):
        """
        Stock went from $180 to $185.40 (+3%).
        The 3% threshold should be flagged.
        """
        result = calculate_profit(
            current_price=185.40, **BASE
        )
        assert result["success"] is True
        assert result["profit_loss_pct"] == 3.0
        assert result["reached_3_pct"] is True
        assert result["reached_5_pct"] is False

    def test_just_below_3_percent(self):
        """
        Stock at 2.99% profit — should NOT trigger the 3% flag.
        """
        # $180 × 1.0299 = $185.382
        result = calculate_profit(
            current_price=185.382, **BASE
        )
        assert result["success"] is True
        assert result["profit_loss_pct"] < 3.0
        assert result["reached_3_pct"] is False

    def test_no_change(self):
        """
        Stock price exactly matches purchase price — no profit, no loss.
        """
        result = calculate_profit(
            current_price=180.0, **BASE
        )
        assert result["success"] is True
        assert result["profit_loss_pct"] == 0.0
        assert result["profit_loss_dollars"] == 0.0
        assert result["is_in_profit"] is False
        assert result["is_in_loss"] is False


# ── Loss scenarios ────────────────────────────────────────────────

class TestLossScenarios:

    def test_minor_loss_under_1_pct(self):
        """
        Stock dropped from $180 to $179 (-0.56%).
        loss_exceeds_1_pct should be False.
        """
        result = calculate_profit(
            current_price=179.0, **BASE
        )
        assert result["success"] is True
        assert result["profit_loss_pct"] < 0
        assert result["is_in_loss"] is True
        assert result["loss_exceeds_1_pct"] is False

    def test_major_loss_over_1_pct(self):
        """
        Stock dropped from $180 to $177.30 (-1.5%).
        loss_exceeds_1_pct should be True — triggers Situation Analyser.
        """
        result = calculate_profit(
            current_price=177.30, **BASE
        )
        assert result["success"] is True
        assert result["profit_loss_pct"] <= -1.0
        assert result["loss_exceeds_1_pct"] is True

    def test_exactly_1_pct_loss(self):
        """
        Exactly -1% loss: $180 × 0.99 = $178.20.
        loss_exceeds_1_pct should be True (>= -1.0).
        """
        result = calculate_profit(
            current_price=178.20, **BASE
        )
        assert result["success"] is True
        assert result["profit_loss_pct"] == pytest.approx(-1.0, abs=0.01)
        assert result["loss_exceeds_1_pct"] is True


# ── Change since last poll ────────────────────────────────────────

class TestChangeSinceLastPoll:

    def test_profit_increased_since_last_poll(self):
        """
        Last poll: 2.5% profit. Current: 3.2% profit.
        Change = +0.7% — enough to trigger a new upside alert.
        """
        result = calculate_profit(
            current_price=185.76,   # ~3.2% above $180
            previous_profit_pct=2.5,
            **BASE
        )
        assert result["success"] is True
        assert result["change_since_last_poll"] > 0

    def test_profit_unchanged_since_last_poll(self):
        """
        Same price as last poll — change should be 0.0.
        No new alert should fire.
        """
        # Price at exactly 3% profit
        result = calculate_profit(
            current_price=185.40,
            previous_profit_pct=3.0,
            **BASE
        )
        assert result["success"] is True
        assert result["change_since_last_poll"] == pytest.approx(0.0, abs=0.001)

    def test_profit_dropped_since_last_poll(self):
        """
        Stock dipped since the last check.
        Change should be negative.
        """
        result = calculate_profit(
            current_price=183.0,   # ~1.67% profit
            previous_profit_pct=3.0,
            **BASE
        )
        assert result["success"] is True
        assert result["change_since_last_poll"] < 0

    def test_default_previous_profit_is_zero(self):
        """
        If no previous_profit_pct is passed, it defaults to 0.0.
        On the very first poll, change_since_last_poll equals profit_loss_pct.
        """
        result = calculate_profit(
            current_price=185.40,  # 3% profit
            **BASE
            # previous_profit_pct not passed — should default to 0.0
        )
        assert result["success"] is True
        assert result["previous_profit_pct"] == 0.0
        assert result["change_since_last_poll"] == pytest.approx(
            result["profit_loss_pct"], abs=0.001
        )


# ── Input validation ──────────────────────────────────────────────

class TestInputValidation:

    def test_zero_current_price_fails(self):
        result = calculate_profit(current_price=0, **BASE)
        assert result["success"] is False
        assert "current_price" in result["error"]

    def test_negative_current_price_fails(self):
        result = calculate_profit(current_price=-10.0, **BASE)
        assert result["success"] is False

    def test_zero_purchase_price_fails(self):
        result = calculate_profit(
            current_price=189.0,
            purchase_price=0,
            shares=10,
            total_invested=1800.0
        )
        assert result["success"] is False
        assert "purchase_price" in result["error"]

    def test_zero_shares_fails(self):
        result = calculate_profit(
            current_price=189.0,
            purchase_price=180.0,
            shares=0,
            total_invested=1800.0
        )
        assert result["success"] is False
        assert "shares" in result["error"]

    def test_zero_total_invested_fails(self):
        result = calculate_profit(
            current_price=189.0,
            purchase_price=180.0,
            shares=10,
            total_invested=0
        )
        assert result["success"] is False
        assert "total_invested" in result["error"]


# ── Rounding and precision ────────────────────────────────────────

class TestPrecision:

    def test_dollar_values_rounded_to_2_decimal_places(self):
        """
        Dollar figures should always be rounded to 2 decimal places
        to avoid floating point noise like $90.000000000001.
        """
        result = calculate_profit(current_price=189.0, **BASE)
        assert result["success"] is True
        # Check they are proper 2 decimal place floats
        assert result["current_value"] == round(result["current_value"], 2)
        assert result["profit_loss_dollars"] == round(
            result["profit_loss_dollars"], 2
        )

    def test_percentage_rounded_to_4_decimal_places(self):
        """
        Percentage is rounded to 4 decimal places.
        Enough precision to detect the 0.25% jump rule without noise.
        """
        result = calculate_profit(current_price=185.40, **BASE)
        assert result["success"] is True
        assert result["profit_loss_pct"] == round(result["profit_loss_pct"], 4)
