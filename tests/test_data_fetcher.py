# Tests for the DATA Agent (data_fetcher.py)
#
# We test two ways:
#   1. With mocks — fake Alpha Vantage responses so we don't use API calls
#   2. A live smoke test — actually calls Alpha Vantage (needs real API key)
#
# Run all tests:        pytest tests/test_data_fetcher.py -v
# Run only mock tests:  pytest tests/test_data_fetcher.py -v -k "not live"

import pytest
from unittest.mock import patch, MagicMock
from agents.data_fetcher import get_stock_quote, get_historical_data


# ─────────────────────────────────────────────
#  MOCK TESTS — no real API calls, runs offline
# ─────────────────────────────────────────────

class TestGetStockQuote:

    def test_returns_price_on_success(self, mocker):
        """
        Happy path: Alpha Vantage returns a valid quote.
        We mock the HTTP response so no real call is made.
        """
        # This is what Alpha Vantage actually returns for a valid ticker
        fake_response = {
            "Global Quote": {
                "01. symbol": "AAPL",
                "05. price": "189.50",
                "06. volume": "52000000",
                "09. change": "1.20",
                "10. change percent": "0.6382%"
            }
        }

        # mocker.patch replaces requests.get with a fake version
        # that returns our fake_response instead of hitting the internet
        mock_get = mocker.patch("agents.data_fetcher.requests.get")
        mocker.patch("agents.data_fetcher.API_KEY", "fake-test-key")
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = fake_response

        result = get_stock_quote("AAPL")

        assert result["success"] is True
        assert result["ticker"] == "AAPL"
        assert result["price"] == 189.50
        assert result["change"] == 1.20
        assert result["change_pct"] == "0.6382%"
        assert result["volume"] == 52000000
        assert "fetched_at" in result

    def test_invalid_ticker_returns_error(self, mocker):
        """
        When Alpha Vantage returns an empty Global Quote,
        the agent should return success=False with a clear message.
        """
        mock_get = mocker.patch("agents.data_fetcher.requests.get")
        mocker.patch("agents.data_fetcher.API_KEY", "fake-test-key")
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"Global Quote": {}}

        result = get_stock_quote("INVALIDTICKER")

        assert result["success"] is False
        assert "No data returned" in result["error"]

    def test_api_limit_reached(self, mocker):
        """
        Alpha Vantage returns a "Note" field when the daily limit is hit.
        """
        mock_get = mocker.patch("agents.data_fetcher.requests.get")
        mocker.patch("agents.data_fetcher.API_KEY", "fake-test-key")
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "Note": "Thank you for using Alpha Vantage! "
                    "Our standard API call frequency is 25 requests per day."
        }

        result = get_stock_quote("AAPL")

        assert result["success"] is False
        assert "daily API limit" in result["error"]

    def test_http_error_returns_failure(self, mocker):
        """
        If Alpha Vantage returns a non-200 status code, fail gracefully.
        """
        mock_get = mocker.patch("agents.data_fetcher.requests.get")
        mocker.patch("agents.data_fetcher.API_KEY", "fake-test-key")
        mock_get.return_value.status_code = 500

        result = get_stock_quote("AAPL")

        assert result["success"] is False
        assert "HTTP 500" in result["error"]

    def test_no_api_key_returns_error(self, mocker):
        """
        If the API key is missing from .env, fail immediately
        without making any HTTP request.
        """
        # Temporarily set the API key to None
        mocker.patch("agents.data_fetcher.API_KEY", None)

        result = get_stock_quote("AAPL")

        assert result["success"] is False
        assert "ALPHA_VANTAGE_API_KEY not found" in result["error"]

    def test_connection_error_returns_failure(self, mocker):
        """
        If there's no internet, the agent should return a clear message
        rather than crashing with an unhandled exception.
        """
        import requests as req
        mock_get = mocker.patch("agents.data_fetcher.requests.get")
        mocker.patch("agents.data_fetcher.API_KEY", "fake-test-key")
        mock_get.side_effect = req.exceptions.ConnectionError()

        result = get_stock_quote("AAPL")

        assert result["success"] is False
        assert "No internet" in result["error"]

    def test_ticker_is_uppercased(self, mocker):
        """
        The agent should uppercase the ticker regardless of input.
        "aapl" should behave the same as "AAPL".
        """
        fake_response = {
            "Global Quote": {
                "01. symbol": "AAPL",
                "05. price": "189.50",
                "06. volume": "52000000",
                "09. change": "1.20",
                "10. change percent": "0.6382%"
            }
        }
        mock_get = mocker.patch("agents.data_fetcher.requests.get")
        mocker.patch("agents.data_fetcher.API_KEY", "fake-test-key")
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = fake_response

        result = get_stock_quote("aapl")

        assert result["ticker"] == "AAPL"


class TestGetHistoricalData:

    def _make_mock_df(self, num_days: int):
        """Helper: returns a fake pandas DataFrame like yfinance would."""
        import pandas as pd
        from datetime import date, timedelta
        dates = [date(2026, 1, 2) + timedelta(days=i) for i in range(num_days)]
        data = {
            "Open":   [180.0] * num_days,
            "High":   [185.0] * num_days,
            "Low":    [179.0] * num_days,
            "Close":  [183.0] * num_days,
            "Volume": [50000000] * num_days,
        }
        df = pd.DataFrame(data, index=pd.DatetimeIndex(dates))
        return df

    def test_returns_correct_number_of_days(self, mocker):
        """Asking for 90 days should return exactly 90 records."""
        mock_ticker = mocker.patch("agents.data_fetcher.yf.Ticker")
        mock_ticker.return_value.history.return_value = self._make_mock_df(120)

        result = get_historical_data("AAPL", days=90)

        assert result["success"] is True
        assert result["days_returned"] == 90
        assert len(result["daily_data"]) == 90

    def test_daily_record_has_correct_fields(self, mocker):
        """Each record should have: date, open, high, low, close, volume."""
        mock_ticker = mocker.patch("agents.data_fetcher.yf.Ticker")
        mock_ticker.return_value.history.return_value = self._make_mock_df(10)

        result = get_historical_data("AAPL", days=5)

        assert result["success"] is True
        record = result["daily_data"][0]
        assert "date"   in record
        assert "open"   in record
        assert "high"   in record
        assert "low"    in record
        assert "close"  in record
        assert "volume" in record

    def test_data_is_sorted_oldest_first(self, mocker):
        """Returned list should go from oldest date to newest."""
        mock_ticker = mocker.patch("agents.data_fetcher.yf.Ticker")
        mock_ticker.return_value.history.return_value = self._make_mock_df(10)

        result = get_historical_data("AAPL", days=10)

        dates = [r["date"] for r in result["daily_data"]]
        assert dates == sorted(dates)

    def test_empty_dataframe_returns_error(self, mocker):
        """If yfinance returns empty data the agent fails gracefully."""
        import pandas as pd
        mock_ticker = mocker.patch("agents.data_fetcher.yf.Ticker")
        mock_ticker.return_value.history.return_value = pd.DataFrame()

        result = get_historical_data("INVALIDTICKER")

        assert result["success"] is False
        assert "No historical data" in result["error"]

    def test_yfinance_exception_returns_error(self, mocker):
        """If yfinance throws, the agent returns success=False."""
        mock_ticker = mocker.patch("agents.data_fetcher.yf.Ticker")
        mock_ticker.return_value.history.side_effect = Exception("Network error")

        result = get_historical_data("AAPL")

        assert result["success"] is False
        assert "Failed to fetch" in result["error"]

    def test_no_api_key_does_not_affect_historical(self, mocker):
        """
        get_historical_data uses yfinance — it doesn't need an API key.
        This test confirms it works even without ALPHA_VANTAGE_API_KEY set.
        """
        mocker.patch("agents.data_fetcher.API_KEY", None)
        mock_ticker = mocker.patch("agents.data_fetcher.yf.Ticker")
        mock_ticker.return_value.history.return_value = self._make_mock_df(10)

        result = get_historical_data("AAPL", days=5)

        # Should succeed — no API key needed for yfinance
        assert result["success"] is True


# ─────────────────────────────────────────────
#  LIVE SMOKE TEST — makes a real API call
#  Only runs if you have a valid API key in .env
#  Skip with: pytest -k "not live"
# ─────────────────────────────────────────────

@pytest.mark.live
def test_live_get_stock_quote():
    """
    Real call to Alpha Vantage. Requires ALPHA_VANTAGE_API_KEY in .env.
    Run with: pytest tests/test_data_fetcher.py -v -m live
    """
    result = get_stock_quote("AAPL")
    print("\nLive quote result:", result)

    # We don't assert a specific price — markets move.
    # We just verify the structure came back correctly.
    assert result["success"] is True
    assert result["ticker"] == "AAPL"
    assert isinstance(result["price"], float)
    assert result["price"] > 0


@pytest.mark.live
def test_live_get_historical_data():
    """
    Real call to Alpha Vantage for 90 days of history.
    Run with: pytest tests/test_data_fetcher.py -v -m live

    NOTE: Alpha Vantage free tier allows 5 calls/minute.
    Run this test on its own to avoid hitting the rate limit:
        pytest tests/test_data_fetcher.py -v -k "live_get_historical"
    """
    import time
    # Wait 15 seconds to avoid the 5-calls/minute rate limit
    # when this test runs right after the quote test
    time.sleep(15)

    result = get_historical_data("AAPL", days=90)
    print(f"\nLive history: {result}")

    assert result["success"] is True, f"Failed: {result.get('error')}"
    assert result["days_returned"] >= 60  # allow for weekends/holidays
    assert len(result["daily_data"]) > 0
    assert result["daily_data"][0]["close"] > 0
