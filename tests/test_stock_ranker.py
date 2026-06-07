# Tests for the RANK Agent (stock_ranker.py)
#
# Run: pytest tests/test_stock_ranker.py -v

import pytest
from agents.stock_ranker import (
    rank_stocks,
    _momentum_30d,
    _upside_volatility,
    _avg_daily_volume,
    _trend_strength,
    _risk_adjusted_return,
    _normalise,
)


# ─────────────────────────────────────────────────────────────────
#  HELPERS — build synthetic daily_data for tests
# ─────────────────────────────────────────────────────────────────

def make_flat_data(price: float, days: int = 90, volume: int = 1_000_000) -> list:
    """All prices the same — zero momentum, zero trend."""
    from datetime import date, timedelta
    base = date(2026, 1, 2)
    return [
        {
            "date":   (base + timedelta(days=i)).isoformat(),
            "open":   price,
            "high":   price,
            "low":    price,
            "close":  price,
            "volume": volume
        }
        for i in range(days)
    ]


def make_trending_up_data(
    start: float, daily_gain: float, days: int = 90,
    volume: int = 5_000_000
) -> list:
    """Price increases by `daily_gain` each day — steady uptrend."""
    from datetime import date, timedelta
    base = date(2026, 1, 2)
    result = []
    price = start
    for i in range(days):
        result.append({
            "date":   (base + timedelta(days=i)).isoformat(),
            "open":   round(price, 4),
            "high":   round(price * 1.005, 4),
            "low":    round(price * 0.995, 4),
            "close":  round(price, 4),
            "volume": volume
        })
        price += daily_gain
    return result


def make_volatile_data(
    base_price: float, swing: float, days: int = 90,
    volume: int = 2_000_000
) -> list:
    """Price alternates up/down each day — high volatility, no trend."""
    from datetime import date, timedelta
    base_date = date(2026, 1, 2)
    result = []
    for i in range(days):
        price = base_price + swing if i % 2 == 0 else base_price - swing
        result.append({
            "date":   (base_date + timedelta(days=i)).isoformat(),
            "open":   base_price,
            "high":   base_price + swing,
            "low":    base_price - swing,
            "close":  round(price, 4),
            "volume": volume
        })
    return result


# ─────────────────────────────────────────────────────────────────
#  INDIVIDUAL METRIC TESTS
# ─────────────────────────────────────────────────────────────────

class TestMomentum:

    def test_rising_stock_positive_momentum(self):
        data = make_trending_up_data(start=100.0, daily_gain=0.5, days=90)
        result = _momentum_30d(data)
        assert result > 0

    def test_flat_stock_zero_momentum(self):
        data = make_flat_data(price=100.0, days=90)
        result = _momentum_30d(data)
        assert result == pytest.approx(0.0, abs=0.001)

    def test_falling_stock_negative_momentum(self):
        data = make_trending_up_data(start=150.0, daily_gain=-0.5, days=90)
        result = _momentum_30d(data)
        assert result < 0

    def test_short_data_does_not_crash(self):
        """If fewer than 31 days of data, use what's available."""
        data = make_trending_up_data(start=100.0, daily_gain=1.0, days=10)
        result = _momentum_30d(data)
        assert isinstance(result, float)


class TestUpsideVolatility:

    def test_all_positive_days_score_1(self):
        """Every day gains >= 1% → score should be high."""
        # Use a percentage-based helper: each day gains 1.5% of previous close
        from datetime import date, timedelta
        base_date = date(2026, 1, 2)
        price = 100.0
        data = []
        for i in range(90):
            data.append({
                "date":   (base_date + timedelta(days=i)).isoformat(),
                "open":   round(price, 4),
                "high":   round(price * 1.02, 4),
                "low":    round(price * 0.99, 4),
                "close":  round(price, 4),
                "volume": 5_000_000
            })
            price *= 1.015   # +1.5% per day
        result = _upside_volatility(data)
        assert result > 0.9  # nearly all days qualify

    def test_flat_data_scores_zero(self):
        data = make_flat_data(price=100.0, days=90)
        result = _upside_volatility(data)
        assert result == 0.0

    def test_score_is_between_0_and_1(self):
        data = make_volatile_data(base_price=100.0, swing=1.5, days=90)
        result = _upside_volatility(data)
        assert 0.0 <= result <= 1.0


class TestAvgDailyVolume:

    def test_returns_correct_average(self):
        data = make_flat_data(price=100.0, days=30, volume=10_000_000)
        result = _avg_daily_volume(data)
        assert result == pytest.approx(10_000_000, rel=0.01)

    def test_uses_last_30_days(self):
        """Volume changes halfway through — should only use last 30 days."""
        data = (
            make_flat_data(price=100.0, days=60, volume=1_000_000) +
            make_flat_data(price=100.0, days=30, volume=50_000_000)
        )
        result = _avg_daily_volume(data)
        assert result == pytest.approx(50_000_000, rel=0.01)


class TestTrendStrength:

    def test_steady_uptrend_positive_score(self):
        data = make_trending_up_data(start=100.0, daily_gain=0.5, days=90)
        result = _trend_strength(data)
        assert result > 0

    def test_flat_data_near_zero(self):
        data = make_flat_data(price=100.0, days=90)
        result = _trend_strength(data)
        assert result == pytest.approx(0.0, abs=0.01)

    def test_downtrend_negative_score(self):
        data = make_trending_up_data(start=150.0, daily_gain=-0.5, days=90)
        result = _trend_strength(data)
        assert result < 0

    def test_erratic_data_lower_than_steady(self):
        """Volatile data should score lower than a steady climb."""
        steady = make_trending_up_data(start=100.0, daily_gain=0.5, days=90)
        volatile = make_volatile_data(base_price=100.0, swing=5.0, days=90)
        assert _trend_strength(steady) > _trend_strength(volatile)


class TestRiskAdjustedReturn:

    def test_steady_gains_positive_score(self):
        data = make_trending_up_data(start=100.0, daily_gain=0.3, days=90)
        result = _risk_adjusted_return(data)
        assert result > 0

    def test_flat_data_returns_zero(self):
        data = make_flat_data(price=100.0, days=90)
        result = _risk_adjusted_return(data)
        assert result == 0.0

    def test_volatile_lower_than_steady(self):
        """A volatile stock should score lower than a steady one."""
        steady = make_trending_up_data(start=100.0, daily_gain=0.3, days=90)
        volatile = make_volatile_data(base_price=100.0, swing=5.0, days=90)
        assert _risk_adjusted_return(steady) > _risk_adjusted_return(volatile)


class TestNormalise:

    def test_min_becomes_0_max_becomes_100(self):
        result = _normalise([10.0, 20.0, 30.0])
        assert result[0] == pytest.approx(0.0)
        assert result[-1] == pytest.approx(100.0)

    def test_all_equal_returns_50(self):
        result = _normalise([5.0, 5.0, 5.0])
        assert all(v == 50.0 for v in result)

    def test_single_value_returns_50(self):
        result = _normalise([42.0])
        assert result == [50.0]

    def test_negative_values_handled(self):
        result = _normalise([-10.0, 0.0, 10.0])
        assert result[0] == pytest.approx(0.0)
        assert result[-1] == pytest.approx(100.0)


# ─────────────────────────────────────────────────────────────────
#  RANK_STOCKS INTEGRATION TESTS
# ─────────────────────────────────────────────────────────────────

class TestRankStocks:

    def _make_candidate(self, ticker, start, gain, volume=5_000_000):
        return {
            "ticker": ticker,
            "current_price": start + gain * 90,
            "daily_data": make_trending_up_data(start, gain, 90, volume)
        }

    def test_returns_success_true(self):
        candidates = [
            self._make_candidate("AAPL", 180.0, 0.5),
            self._make_candidate("MSFT", 300.0, 0.3),
        ]
        result = rank_stocks(candidates)
        assert result["success"] is True

    def test_top5_has_at_most_5_entries(self):
        candidates = [
            self._make_candidate(f"T{i}", 100.0 + i * 10, 0.1 * i)
            for i in range(1, 8)  # 7 candidates
        ]
        result = rank_stocks(candidates)
        assert len(result["top5"]) == 5

    def test_fewer_than_5_candidates_returns_all(self):
        candidates = [
            self._make_candidate("AAPL", 180.0, 0.5),
            self._make_candidate("MSFT", 300.0, 0.3),
        ]
        result = rank_stocks(candidates)
        assert len(result["top5"]) == 2

    def test_ranks_are_sequential(self):
        candidates = [
            self._make_candidate("AAPL", 180.0, 0.5),
            self._make_candidate("MSFT", 300.0, 0.3),
            self._make_candidate("TSLA", 200.0, 0.8),
        ]
        result = rank_stocks(candidates)
        ranks = [s["rank"] for s in result["top5"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_higher_momentum_ranks_better(self):
        """
        Two identical stocks except one has higher momentum.
        The higher momentum stock should rank #1.
        """
        candidates = [
            self._make_candidate("LOW_MOM", 180.0, 0.1),
            self._make_candidate("HIGH_MOM", 180.0, 1.0),
        ]
        result = rank_stocks(candidates)
        assert result["top5"][0]["ticker"] == "HIGH_MOM"

    def test_each_stock_has_required_fields(self):
        candidates = [self._make_candidate("AAPL", 180.0, 0.5)]
        result = rank_stocks(candidates)
        stock = result["top5"][0]
        assert "rank"          in stock
        assert "ticker"        in stock
        assert "current_price" in stock
        assert "score"         in stock
        assert "metrics"       in stock
        assert "rationale"     in stock

    def test_metrics_has_all_five_fields(self):
        candidates = [self._make_candidate("AAPL", 180.0, 0.5)]
        result = rank_stocks(candidates)
        metrics = result["top5"][0]["metrics"]
        assert "momentum_30d"          in metrics
        assert "upside_volatility"     in metrics
        assert "avg_daily_volume"      in metrics
        assert "trend_strength"        in metrics
        assert "risk_adjusted_return"  in metrics

    def test_score_is_between_0_and_100(self):
        candidates = [
            self._make_candidate("AAPL", 180.0, 0.5),
            self._make_candidate("MSFT", 300.0, 0.3),
            self._make_candidate("TSLA", 200.0, 0.8),
        ]
        result = rank_stocks(candidates)
        for stock in result["top5"]:
            assert 0.0 <= stock["score"] <= 100.0

    def test_scores_descending(self):
        """Top-ranked stock should have the highest score."""
        candidates = [
            self._make_candidate(f"T{i}", 100.0, 0.1 * i)
            for i in range(1, 6)
        ]
        result = rank_stocks(candidates)
        scores = [s["score"] for s in result["top5"]]
        assert scores == sorted(scores, reverse=True)

    def test_total_candidates_reported(self):
        candidates = [
            self._make_candidate("AAPL", 180.0, 0.5),
            self._make_candidate("MSFT", 300.0, 0.3),
            self._make_candidate("TSLA", 200.0, 0.8),
        ]
        result = rank_stocks(candidates)
        assert result["total_candidates"] == 3


# ─────────────────────────────────────────────────────────────────
#  ERROR / EDGE CASE TESTS
# ─────────────────────────────────────────────────────────────────

class TestRankStocksEdgeCases:

    def test_empty_candidates_returns_error(self):
        result = rank_stocks([])
        assert result["success"] is False
        assert "No candidate" in result["error"]

    def test_missing_ticker_returns_error(self):
        result = rank_stocks([{"daily_data": make_flat_data(100.0, 30)}])
        assert result["success"] is False
        assert "ticker" in result["error"]

    def test_insufficient_data_returns_error(self):
        result = rank_stocks([{
            "ticker": "AAPL",
            "daily_data": make_flat_data(100.0, days=3)  # only 3 days
        }])
        assert result["success"] is False
        assert "insufficient" in result["error"]

    def test_single_candidate_still_ranks(self):
        result = rank_stocks([{
            "ticker": "AAPL",
            "current_price": 189.50,
            "daily_data": make_trending_up_data(180.0, 0.5, 90)
        }])
        assert result["success"] is True
        assert result["top5"][0]["rank"] == 1
