# DATA Agent — Data Fetcher
#
# This agent has ONE job: get stock price data from Alpha Vantage.
# It knows nothing about profit, alerts, or investments.
# It just fetches data and returns it cleanly.
#
# Two functions:
#   1. get_stock_quote(ticker)     → current price of a stock
#   2. get_historical_data(ticker) → last 90 days of daily prices

import os
import requests
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv

# load_dotenv() reads your .env file and makes the keys available
# via os.getenv(). This must run before we try to read the API key.
load_dotenv()

# The API key is read from .env — never hardcoded in the source file.
API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

# All Alpha Vantage requests go to this base URL.
# We attach different parameters to get different data.
BASE_URL = "https://www.alphavantage.co/query"


def get_stock_quote(ticker: str) -> dict:
    """
    Gets the CURRENT price of a stock right now.

    Example: get_stock_quote("AAPL")
    Asks Alpha Vantage: "What is Apple trading at this moment?"

    Returns a dict with success=True and the price,
    or success=False and an error message if something went wrong.
    """

    # Guard: if the API key was not found in .env, fail immediately
    # with a clear message rather than a confusing HTTP 403 error.
    if not API_KEY:
        return {
            "success": False,
            "error": "ALPHA_VANTAGE_API_KEY not found. "
                     "Check your .env file."
        }

    # These are the query parameters Alpha Vantage expects.
    # "function" tells it what kind of data we want.
    # "symbol" is the stock ticker (e.g. AAPL, MSFT, TSLA).
    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": ticker.upper(),
        "apikey": API_KEY
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=10)
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": "No internet connection or Alpha Vantage is unreachable."
        }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "Request timed out. Alpha Vantage took too long to respond."
        }

    # HTTP 200 means the request reached the server successfully.
    # Anything else (403, 429, 500) means something went wrong.
    if response.status_code != 200:
        return {
            "success": False,
            "error": f"HTTP {response.status_code} from Alpha Vantage."
        }

    data = response.json()

    # Alpha Vantage returns {"Global Quote": {}} (empty) when the
    # ticker doesn't exist or the free tier hits its daily limit.
    quote = data.get("Global Quote", {})
    if not quote:
        # Check if Alpha Vantage returned a rate limit message
        if "Note" in data:
            return {
                "success": False,
                "error": "Alpha Vantage daily API limit reached. "
                         "Try again tomorrow."
            }
        return {
            "success": False,
            "error": f"No data returned for ticker '{ticker}'. "
                     "Check the ticker symbol."
        }

    # Alpha Vantage field names have numbers like "05. price".
    # We parse them into clean, readable keys.
    return {
        "success": True,
        "ticker": ticker.upper(),
        "price": float(quote["05. price"]),
        "change": float(quote["09. change"]),
        "change_pct": quote["10. change percent"],  # e.g. "0.6382%"
        "volume": int(quote["06. volume"]),
        "fetched_at": datetime.now().isoformat()
    }


def get_historical_data(ticker: str, days: int = 90) -> dict:
    """
    Gets the last N days of daily closing prices and volumes.

    Example: get_historical_data("AAPL", days=90)
    Asks Yahoo Finance: "Show me Apple's price every market day
    for the last 90 days."

    Uses yfinance (Yahoo Finance) — free, no API key needed.
    Alpha Vantage's daily history endpoints are premium-only on the free tier.

    Returns a dict with success=True and a list of daily records,
    or success=False and an error message.
    """
    try:
        ticker_obj = yf.Ticker(ticker.upper())
        # period="6mo" gives ~125 trading days — enough to always cover 90
        df = ticker_obj.history(period="6mo")
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to fetch historical data: {str(e)}"
        }

    if df is None or df.empty:
        return {
            "success": False,
            "error": f"No historical data returned for ticker '{ticker}'. "
                     "Check the ticker symbol."
        }

    # yfinance returns a DataFrame with DatetimeIndex.
    # We convert it to a list of plain dicts, oldest first.
    df = df.sort_index()          # ensure oldest → newest order
    df = df.tail(days)            # keep only the last `days` rows

    daily_data = []
    for timestamp, row in df.iterrows():
        daily_data.append({
            "date":   timestamp.strftime("%Y-%m-%d"),
            "open":   round(float(row["Open"]),   4),
            "high":   round(float(row["High"]),   4),
            "low":    round(float(row["Low"]),    4),
            "close":  round(float(row["Close"]),  4),
            "volume": int(row["Volume"])
        })

    return {
        "success": True,
        "ticker": ticker.upper(),
        "days_requested": days,
        "days_returned": len(daily_data),
        "daily_data": daily_data
    }
