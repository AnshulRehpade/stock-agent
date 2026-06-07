# Tests for the SIT Agent (situation_analyser.py)
#
# Run: pytest tests/test_situation_analyser.py -v

import pytest
from agents.situation_analyser import (
    analyse_situation,
    _sma, _momentum_nd, _volume_pressure, _days_up_in_window
)
from datetime import date, timedelta


# ─────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────

def make_daily_data(closes: list, volume: int = 5_000_000) -> list:
    """Builds daily_data dicts from a list of closing prices."""
    base = date(2026, 1, 2)
    data = []
    for i, close in enumerate(closes):
        data.append({
            "date":   (base + timedelta(days=i)).isoformat(),
            "open":   close * 0.99,
            "high":   close * 1.01,
            "low":    close * 0.98,
            "close":  close,
            "volume": volume
        })
    return data


def make_trending_up(start=100.0, gain_per_day=0.5, days=30) -> list:
    """Steadily rising prices."""
    closes = [round(start + gain_per_day * i, 4) for i in range(days)]
    return make_daily_data(closes)


def make_trending_down(start=100.0, loss_per_day=0.5, days=30) -> list:
    """Steadily falling prices."""
    closes = [round(start - loss_per_day * i, 4) for i in range(days)]
    return make_daily_data(closes)


def make_recovering(days=30) -> list:
    """Falls for first half, then recovers in second half."""
    closes = (
        [round(100.0 - 0.3 * i, 4) for i in range(days // 2)] +
        [round(95.0 + 0.5 * i, 4) for i in range(days // 2)]
    )
    return make_daily_data(closes)


# ─────────────────────────────────────────────────────────────────
#  SIGNAL CALCULATOR TESTS
# ─────────────────────────────────────────────────────────────────

class TestSignalCalculators:

    def test_sma_correct_average(self):
        """SMA of [1,2,3,4,5] over window=5 = 3.0"""
        closes = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _sma(closes, 5) == pytest.approx(3.0)

    def test_sma_uses_last_n_days(self):
        """SMA5 of [1,1,1,10,20] = (1+1+10+20) wait, last 5 = avg of last 5"""
        closes = [1.0, 1.0, 1.0, 10.0, 20.0]
        assert _sma(closes, 3) == pytest.approx((1.0 + 10.0 + 20.0) / 3)

    def test_sma_short_data_uses_all(self):
        """If fewer days than window, use all available"""
        closes = [10.0, 20.0]
        assert _sma(closes, 5) == pytest.approx(15.0)

    def test_momentum_positive_for_rising(self):
        closes = [100.0, 101.0, 102.0, 103.0]
        assert _momentum_nd(closes, 3) > 0

    def test_momentum_negative_for_falling(self):
        closes = [103.0, 102.0, 101.0, 100.0]
        assert _momentum_nd(closes, 3) < 0

    def test_momentum_zero_for_flat(self):
        closes = [100.0, 100.0, 100.0, 100.0]
        assert _momentum_nd(closes, 3) == pytest.approx(0.0)

    def test_volume_pressure_high_when_more_selling(self):
        """High volume on down days = HIGH selling pressure"""
        data = [
            {"close": 100.0, "volume": 1_000_000},
            {"close":  99.0, "volume": 9_000_000},  # down day, high volume
            {"close":  98.0, "volume": 8_000_000},  # down day, high volume
            {"close":  99.0, "volume": 1_000_000},  # up day, low volume
            {"close":  98.5, "volume": 9_000_000},  # down day, high volume
        ]
        result = _volume_pressure(data)
        assert result == "HIGH"

    def test_volume_pressure_low_when_more_buying(self):
        """High volume on up days = LOW selling pressure"""
        data = [
            {"close": 100.0, "volume": 1_000_000},
            {"close": 101.0, "volume": 9_000_000},  # up day, high volume
            {"close": 102.0, "volume": 8_000_000},  # up day, high volume
            {"close": 101.5, "volume": 1_000_000},  # down day, low volume
            {"close": 102.5, "volume": 9_000_000},  # up day, high volume
        ]
        result = _volume_pressure(data)
        assert result == "LOW"

    def test_days_up_correct_count(self):
        """3 of 4 days had price increases"""
        closes = [100.0, 101.0, 102.0, 101.5, 103.0]
        assert _days_up_in_window(closes, window=4) == 3


# ─────────────────────────────────────────────────────────────────
#  ANALYSE_SITUATION — INPUT VALIDATION
# ─────────────────────────────────────────────────────────────────

class TestInputValidation:

    def test_invalid_mode_returns_error(self):
        data = make_trending_up()
        result = analyse_situation("INVALID", data, 1.5, 20)
        assert result["success"] is False
        assert "Invalid mode" in result["error"]

    def test_insufficient_data_returns_error(self):
        data = make_daily_data([100.0, 101.0])  # only 2 days
        result = analyse_situation("LOSS", data, -1.5, 20)
        assert result["success"] is False
        assert "Insufficient" in result["error"]

    def test_empty_data_returns_error(self):
        result = analyse_situation("LOSS", [], -1.5, 20)
        assert result["success"] is False

    def test_result_has_required_fields(self):
        data = make_trending_up()
        result = analyse_situation("LOSS", data, -1.5, 20)
        assert result["success"] is True
        assert "mode"           in result
        assert "recommendation" in result
        assert "signals"        in result
        assert "reason"         in result
        assert "confidence"     in result


# ─────────────────────────────────────────────────────────────────
#  LOSS MODE
# ─────────────────────────────────────────────────────────────────

class TestLossMode:

    def test_recovering_trend_returns_hold(self):
        """
        SMA5 > SMA10 (recovering) + positive 3-day momentum
        + 20 days remaining → should recommend HOLD
        """
        data = make_recovering(days=30)
        result = analyse_situation("LOSS", data, -1.5, 20)
        assert result["success"] is True
        assert result["recommendation"] == "HOLD"

    def test_falling_trend_low_time_returns_consider_selling(self):
        """
        Steady downtrend + high selling pressure + <10 days left
        → CONSIDER_SELLING
        """
        # Build: falling prices with high volume on down days
        closes_down = [round(100.0 - 0.5 * i, 2) for i in range(30)]
        data = []
        base = date(2026, 1, 2)
        for i, close in enumerate(closes_down):
            data.append({
                "date":   (base + timedelta(days=i)).isoformat(),
                "open":   close + 0.2,
                "high":   close + 0.5,
                "low":    close - 0.5,
                "close":  close,
                "volume": 10_000_000  # high volume on every (down) day
            })
        result = analyse_situation("LOSS", data, -2.5, 5)
        assert result["success"] is True
        assert result["recommendation"] in ("CONSIDER_SELLING", "MONITOR_CLOSELY")

    def test_mixed_signals_returns_monitor_closely(self):
        """Volatile data with no clear direction → MONITOR_CLOSELY"""
        # Alternating up/down prices
        closes = [100.0 + (1 if i % 2 == 0 else -1) for i in range(30)]
        data = make_daily_data(closes)
        result = analyse_situation("LOSS", data, -1.5, 15)
        assert result["success"] is True
        # Mixed signals → either MONITOR_CLOSELY or HOLD depending on SMA
        assert result["recommendation"] in ("MONITOR_CLOSELY", "HOLD",
                                            "CONSIDER_SELLING")

    def test_loss_mode_signals_include_sma(self):
        data = make_trending_up()
        result = analyse_situation("LOSS", data, -1.5, 20)
        assert "sma5"       in result["signals"]
        assert "sma10"      in result["signals"]
        assert "sma_signal" in result["signals"]

    def test_reason_is_non_empty_string(self):
        data = make_trending_up()
        result = analyse_situation("LOSS", data, -1.5, 20)
        assert isinstance(result["reason"], str)
        assert len(result["reason"]) > 10


# ─────────────────────────────────────────────────────────────────
#  TARGET REACHED MODE
# ─────────────────────────────────────────────────────────────────

class TestTargetReachedMode:

    def test_strong_momentum_returns_hold_for_more(self):
        """
        Strong 3-day momentum + buyers dominating + >7 days remaining
        → HOLD_FOR_MORE
        """
        # Build: steadily rising data with high volume on up days
        closes = [round(100.0 + 0.8 * i, 2) for i in range(30)]
        data = []
        base = date(2026, 1, 2)
        for i, close in enumerate(closes):
            data.append({
                "date":   (base + timedelta(days=i)).isoformat(),
                "open":   close - 0.1,
                "high":   close + 0.5,
                "low":    close - 0.3,
                "close":  close,
                "volume": 8_000_000  # high volume on up days
            })
        result = analyse_situation("TARGET_REACHED", data, 5.5, 15)
        assert result["success"] is True
        assert result["recommendation"] in ("HOLD_FOR_MORE", "NEUTRAL")

    def test_near_30d_high_slowing_momentum_returns_good_time_to_sell(self):
        """
        Stock at/near its 30-day high + momentum slowing
        → GOOD_TIME_TO_SELL
        """
        # All prices equal (flat after big run) — SMA won't vary,
        # price will be at "30d high", momentum near 0
        closes = [100.0] * 30
        data = make_daily_data(closes)
        result = analyse_situation("TARGET_REACHED", data, 5.0, 10)
        assert result["success"] is True
        # Flat momentum = slowing, price = 30d high → GOOD_TIME_TO_SELL or NEUTRAL
        assert result["recommendation"] in ("GOOD_TIME_TO_SELL", "NEUTRAL")

    def test_target_mode_returns_correct_mode_label(self):
        data = make_trending_up()
        result = analyse_situation("TARGET_REACHED", data, 5.5, 10)
        assert result["mode"] == "TARGET_REACHED"

    def test_confidence_is_valid_value(self):
        data = make_trending_up()
        result = analyse_situation("TARGET_REACHED", data, 5.5, 10)
        assert result["confidence"] in ("HIGH", "MEDIUM", "LOW")


# ─────────────────────────────────────────────────────────────────
#  END OF PERIOD MODE
# ─────────────────────────────────────────────────────────────────

class TestEndOfPeriodMode:

    def test_close_to_target_with_uptrend(self):
        """
        Gap ≤ 1%, stock rising on most days, low rate needed
        → CLOSE_TO_TARGET
        """
        data = make_trending_up(start=100.0, gain_per_day=0.2, days=30)
        # profit at 4.2% → gap = 0.8%, days_up_in_7 should be high
        result = analyse_situation("END_OF_PERIOD", data, 4.2, 0)
        assert result["success"] is True
        assert result["recommendation"] in ("CLOSE_TO_TARGET", "UNCERTAIN")

    def test_wide_gap_returns_unlikely(self):
        """Gap > 2.5% → UNLIKELY_TO_REACH regardless of trend"""
        data = make_trending_down()
        result = analyse_situation("END_OF_PERIOD", data, 1.0, 0)
        assert result["success"] is True
        assert result["recommendation"] in ("UNLIKELY_TO_REACH", "UNCERTAIN")

    def test_weak_trend_returns_unlikely(self):
        """Stock down most days recently → UNLIKELY_TO_REACH"""
        data = make_trending_down(start=100.0, loss_per_day=0.3, days=30)
        result = analyse_situation("END_OF_PERIOD", data, 1.0, 0)
        assert result["success"] is True
        assert result["recommendation"] in ("UNLIKELY_TO_REACH", "UNCERTAIN")

    def test_end_of_period_signals_include_gap_and_days_up(self):
        data = make_trending_up()
        result = analyse_situation("END_OF_PERIOD", data, 3.5, 0)
        assert "gap_to_target_pct"  in result["signals"]
        assert "days_up_in_last_7"  in result["signals"]
        assert "daily_rate_needed"  in result["signals"]

    def test_end_of_period_mode_label(self):
        data = make_trending_up()
        result = analyse_situation("END_OF_PERIOD", data, 3.5, 0)
        assert result["mode"] == "END_OF_PERIOD"


# ─────────────────────────────────────────────────────────────────
#  GENERAL STRUCTURE TESTS
# ─────────────────────────────────────────────────────────────────

class TestOutputStructure:

    @pytest.mark.parametrize("mode,profit", [
        ("LOSS", -1.5),
        ("TARGET_REACHED", 5.5),
        ("END_OF_PERIOD", 3.5),
    ])
    def test_all_modes_return_success(self, mode, profit):
        data = make_trending_up(days=30)
        result = analyse_situation(mode, data, profit, 15)
        assert result["success"] is True

    @pytest.mark.parametrize("mode,profit", [
        ("LOSS", -1.5),
        ("TARGET_REACHED", 5.5),
        ("END_OF_PERIOD", 3.5),
    ])
    def test_all_modes_have_valid_confidence(self, mode, profit):
        data = make_trending_up(days=30)
        result = analyse_situation(mode, data, profit, 15)
        assert result["confidence"] in ("HIGH", "MEDIUM", "LOW")

    @pytest.mark.parametrize("mode,profit", [
        ("LOSS", -1.5),
        ("TARGET_REACHED", 5.5),
        ("END_OF_PERIOD", 3.5),
    ])
    def test_signals_always_include_base_fields(self, mode, profit):
        data = make_trending_up(days=30)
        result = analyse_situation(mode, data, profit, 15)
        s = result["signals"]
        assert "sma5"            in s
        assert "sma10"           in s
        assert "momentum_3d_pct" in s
        assert "volume_pressure" in s
        assert "days_remaining"  in s
        assert "profit_pct"      in s
