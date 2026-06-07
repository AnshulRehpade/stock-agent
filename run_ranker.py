# run_ranker.py
#
# Fetches the full S&P 500 stock list dynamically from Wikipedia,
# loads 90 days of historical data for each stock in parallel,
# then ranks them all and prints the Top 5.
#
# Run: python run_ranker.py
#
# Expected runtime: 2–4 minutes (parallel fetching)

import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from agents.data_fetcher import get_historical_data
from agents.stock_ranker import rank_stocks


# ── Step 1: Fetch the S&P 500 ticker list from Wikipedia ─────────

def get_sp500_tickers() -> list:
    """
    Scrapes the current S&P 500 component list from Wikipedia.
    Returns a list of ticker symbols.
    """
    import requests
    from bs4 import BeautifulSoup

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        # The S&P 500 table has id="constituents"
        table = soup.find("table", {"id": "constituents"})
        if not table:
            # Fallback: find first wikitable with a Symbol column
            for t in soup.find_all("table", {"class": "wikitable"}):
                headers_row = t.find("tr")
                if headers_row and "Symbol" in headers_row.get_text():
                    table = t
                    break

        if not table:
            print("  ❌ Could not locate the S&P 500 table on Wikipedia.")
            return []

        tickers = []
        rows = table.find_all("tr")[1:]  # skip header row
        for row in rows:
            cols = row.find_all("td")
            if cols:
                ticker = cols[0].get_text(strip=True)
                # Wikipedia uses dots (e.g. BRK.B) — yfinance needs hyphens
                ticker = ticker.replace(".", "-")
                if ticker:
                    tickers.append(ticker)

        return tickers

    except Exception as e:
        print(f"  ❌ Failed to fetch S&P 500 list: {e}")
        return []


# ── Step 2: Fetch historical data in parallel ─────────────────────

def fetch_one(ticker: str) -> dict | None:
    """Fetches 90-day history for one ticker. Returns None on failure."""
    result = get_historical_data(ticker, days=90)
    if result["success"] and result["days_returned"] >= 30:
        return {
            "ticker":        ticker,
            "current_price": result["daily_data"][-1]["close"],
            "daily_data":    result["daily_data"]
        }
    return None


def fetch_all_parallel(tickers: list, max_workers: int = 10) -> list:
    """
    Fetches all tickers in parallel using a thread pool.
    max_workers=10 means 10 stocks are fetched simultaneously.
    """
    candidates = []
    failed = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {
            executor.submit(fetch_one, t): t for t in tickers
        }
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            completed += 1
            try:
                result = future.result()
                if result:
                    candidates.append(result)
                    status = "✅"
                else:
                    failed.append(ticker)
                    status = "⚠️ "
            except Exception:
                failed.append(ticker)
                status = "❌"

            # Progress indicator every 50 stocks
            if completed % 50 == 0 or completed == len(tickers):
                pct = completed / len(tickers) * 100
                print(f"  Progress: {completed}/{len(tickers)} "
                      f"({pct:.0f}%)  — "
                      f"{len(candidates)} loaded, {len(failed)} skipped")

    return candidates, failed


# ── Main ──────────────────────────────────────────────────────────

print(f"\n{'═'*65}")
print(f"  RANK AGENT — Full S&P 500 Analysis")
print(f"  Finding the Top 5 stocks for a 5% profit target in 30 days")
print(f"{'═'*65}\n")

# Step 1 — Get the ticker list
print("  Step 1: Fetching S&P 500 component list from Wikipedia...")
tickers = get_sp500_tickers()
if not tickers:
    exit(1)
print(f"  ✅ {len(tickers)} tickers loaded from S&P 500 index.\n")

# Step 2 — Fetch historical data in parallel
print(f"  Step 2: Loading 90 days of historical data for all "
      f"{len(tickers)} stocks")
print(f"  (Fetching 10 stocks simultaneously — est. 2–4 minutes)\n")

candidates, failed = fetch_all_parallel(tickers, max_workers=10)

print(f"\n  ✅ {len(candidates)} stocks loaded successfully")
if failed:
    print(f"  ⚠️  {len(failed)} stocks skipped "
          f"(delisted, insufficient data, or fetch error)")

if len(candidates) < 5:
    print("  ❌ Not enough data to rank. Check your internet connection.")
    exit(1)

# Step 3 — Run the ranker
print(f"\n{'═'*65}")
print(f"  Step 3: Scoring & ranking {len(candidates)} stocks...")
print(f"{'═'*65}\n")

result = rank_stocks(candidates)

if not result["success"]:
    print(f"  ❌ Ranking failed: {result['error']}")
    exit(1)

# ── Print results ─────────────────────────────────────────────────

print(f"  {'Rank':<5} {'Ticker':<7} {'Price':>9}  {'Score':>6}  "
      f"{'30d Mom':>9}  {'Upside Vol':>10}  {'Trend':>8}  {'Risk Adj':>9}")
print("  " + "─" * 72)

for s in result["top5"]:
    m = s["metrics"]
    print(
        f"  #{s['rank']:<4} {s['ticker']:<7} "
        f"${s['current_price']:>9.2f}  "
        f"{s['score']:>6.1f}  "
        f"{m['momentum_30d']:>+9.2f}%  "
        f"{m['upside_volatility']*100:>9.1f}%  "
        f"{m['trend_strength']:>8.4f}  "
        f"{m['risk_adjusted_return']:>9.5f}"
    )

print()
print(f"{'═'*65}")
print(f"  DETAILED BREAKDOWN — TOP 5")
print(f"{'═'*65}")

for s in result["top5"]:
    m = s["metrics"]
    print(f"""
  #{s['rank']} {s['ticker']} — ${s['current_price']:.2f}
  ──────────────────────────────────────────────
  Composite Score:      {s['score']:.1f} / 100
  30-Day Momentum:      {m['momentum_30d']:+.2f}%
  Upside Volatility:    {m['upside_volatility']*100:.1f}% of days gained >= 1%
  Avg Daily Volume:     {m['avg_daily_volume']:,}
  Trend Strength:       {m['trend_strength']:.5f}
  Risk-Adjusted Return: {m['risk_adjusted_return']:.5f}
  Rationale:            {s['rationale']}""")

print(f"\n{'═'*65}")
print(f"  Ranked {len(candidates)} S&P 500 stocks  |  "
      f"Top score: {result['top5'][0]['score']:.1f}/100  |  "
      f"Bottom of top 5: {result['top5'][-1]['score']:.1f}/100")
print(f"\n  ⚠️  Quantitative analysis based on historical price data only.")
print(f"     Not financial advice. Always do your own research.")
print(f"{'═'*65}\n")
