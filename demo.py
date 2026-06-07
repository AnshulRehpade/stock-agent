# demo.py — See all 4 agents working in real time
#
# This script walks through a realistic scenario:
#   1. DATA agent   — fetches real stock data from the internet
#   2. RANK agent   — scores and ranks the stocks
#   3. PROFIT agent — calculates profit/loss on a simulated investment
#   4. ALERT agent  — decides what alert (if any) to fire
#
# Run: python demo.py

import json
from agents.data_fetcher import get_stock_quote, get_historical_data
from agents.stock_ranker import rank_stocks
from agents.profit_calculator import calculate_profit
from agents.alert_decision import evaluate_alerts

# ─────────────────────────────────────────────────────────────────
#  DISPLAY HELPERS
# ─────────────────────────────────────────────────────────────────

def section(title):
    print("\n" + "═" * 60)
    print(f"  {title}")
    print("═" * 60)

def step(label, value):
    print(f"  {label:<32} {value}")

def ok(msg):
    print(f"  ✅ {msg}")

def warn(msg):
    print(f"  ⚠️  {msg}")

def err(msg):
    print(f"  ❌ {msg}")


# ─────────────────────────────────────────────────────────────────
#  AGENT 1: DATA — Fetch current price + 90 days of history
# ─────────────────────────────────────────────────────────────────

# These are the 5 candidate stocks we will rank
CANDIDATES = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

section("AGENT 1: DATA FETCHER")
print("  Fetching current prices from Alpha Vantage...")
print("  Fetching 90-day history from Yahoo Finance...\n")

candidate_data = []

for i, ticker in enumerate(CANDIDATES):
    # Alpha Vantage free tier: 5 calls/min = 1 call every 12s
    # Add a small delay between calls to avoid hitting the rate limit
    if i > 0:
        import time
        time.sleep(13)

    # Fetch current price
    quote = get_stock_quote(ticker)
    # Fetch 90-day history (uses yfinance — no rate limit)
    history = get_historical_data(ticker, days=90)

    if quote["success"] and history["success"]:
        ok(f"{ticker:6} — current price: ${quote['price']:.2f}  "
           f"| {history['days_returned']} days of history loaded")
        candidate_data.append({
            "ticker":        ticker,
            "current_price": quote["price"],
            "daily_data":    history["daily_data"]
        })
    else:
        error_msg = quote.get("error") or history.get("error")
        warn(f"{ticker:6} — skipped ({error_msg})")

if not candidate_data:
    err("No stock data could be fetched. Check your API key in .env")
    exit(1)

print(f"\n  {len(candidate_data)} of {len(CANDIDATES)} stocks loaded successfully.")


# ─────────────────────────────────────────────────────────────────
#  AGENT 2: RANK — Score and rank the candidates
# ─────────────────────────────────────────────────────────────────

section("AGENT 2: STOCK RANKER")
print("  Scoring stocks across 5 metrics:")
print("    30-day momentum (30%) | Upside volatility (25%)")
print("    Avg daily volume (20%) | 90-day trend (15%) | Risk-adjusted return (10%)\n")

rank_result = rank_stocks(candidate_data)

if not rank_result["success"]:
    err(f"Ranking failed: {rank_result['error']}")
    exit(1)

# Build a lookup so we can get daily_data back from the top pick
candidate_lookup = {c["ticker"]: c for c in candidate_data}

print(f"  {'Rank':<6} {'Ticker':<8} {'Score':>7}  {'30d Momentum':>14}  {'Rationale'}")
print("  " + "-" * 85)

for stock in rank_result["top5"]:
    m = stock["metrics"]
    print(
        f"  #{stock['rank']:<5} {stock['ticker']:<8} "
        f"{stock['score']:>6.1f}   "
        f"{m['momentum_30d']:>+10.2f}%    "
        f"{stock['rationale']}"
    )

top_pick = rank_result["top5"][0]
print(f"\n  Top pick: {top_pick['ticker']} at ${top_pick['current_price']:.2f}")

# ─────────────────────────────────────────────────────────────────
#  AGENT 3: PROFIT — Simulate an investment and calculate P&L
# ─────────────────────────────────────────────────────────────────

section("AGENT 3: PROFIT CALCULATOR")

# Simulate: client invested $5,000 in the top pick 30 days ago
# We use the price from 30 days ago as the purchase price
top_history = candidate_lookup[top_pick["ticker"]]["daily_data"]
purchase_price = top_history[-30]["close"]  # price 30 days ago
current_price  = top_pick["current_price"]
budget         = 5000.0
shares         = int(budget // purchase_price)
total_invested = round(shares * purchase_price, 2)

print(f"  Simulating investment in {top_pick['ticker']}:\n")
step("Budget:",            f"${budget:,.2f}")
step("Purchase price:",    f"${purchase_price:.2f}  (30 days ago)")
step("Shares purchased:",  f"{shares}")
step("Total invested:",    f"${total_invested:,.2f}")
step("Current price:",     f"${current_price:.2f}")

profit_result = calculate_profit(
    current_price=current_price,
    purchase_price=purchase_price,
    shares=shares,
    total_invested=total_invested,
    previous_profit_pct=0.0   # first poll
)

if not profit_result["success"]:
    err(f"Profit calculation failed: {profit_result['error']}")
    exit(1)

print()
step("Current value:",     f"${profit_result['current_value']:,.2f}")
step("Profit / Loss ($):", f"${profit_result['profit_loss_dollars']:+,.2f}")
step("Profit / Loss (%):", f"{profit_result['profit_loss_pct']:+.2f}%")
print()

if profit_result["is_in_profit"]:
    ok(f"Investment is in profit (+{profit_result['profit_loss_pct']:.2f}%)")
else:
    warn(f"Investment is at a loss ({profit_result['profit_loss_pct']:.2f}%)")

if profit_result["reached_5_pct"]:
    ok("5% profit target REACHED!")
elif profit_result["reached_3_pct"]:
    ok("3% threshold crossed — monitoring for 5% target")


# ─────────────────────────────────────────────────────────────────
#  AGENT 4: ALERT — Decide what action to take
# ─────────────────────────────────────────────────────────────────

section("AGENT 4: ALERT DECISION ENGINE")
print("  Evaluating alert conditions...\n")

# Simulate: this is day 15 of the 30-day window
days_remaining = 15

alert_result = evaluate_alerts(
    profit_loss_pct          = profit_result["profit_loss_pct"],
    change_since_last_poll   = profit_result["change_since_last_poll"],
    last_alerted_profit_pct  = 0.0,    # no previous alert
    loss_alert_armed         = True,   # stock was in profit at start
    upside_alert_armed       = True,   # waiting for 3% crossing
    days_remaining           = days_remaining,
    is_in_loss               = profit_result["is_in_loss"],
    loss_exceeds_1_pct       = profit_result["loss_exceeds_1_pct"],
    reached_3_pct            = profit_result["reached_3_pct"],
    reached_5_pct            = profit_result["reached_5_pct"],
)

step("Profit %:",          f"{profit_result['profit_loss_pct']:+.2f}%")
step("Days remaining:",    f"{days_remaining}")
step("Action decided:",    alert_result["action"])
step("Needs SIT agent?",   str(alert_result["situation_analysis_required"]))

print()

ACTION_MESSAGES = {
    "NO_ACTION":         "📊 No alert needed. Continue monitoring silently.",
    "UPSIDE_ALERT":      "📈 Send upside alert — profit is growing toward 5% target.",
    "TARGET_REACHED":    "🎯 Send target alert — 5% goal achieved! Run post-target analysis.",
    "LOSS_ALERT_MINOR":  "📉 Send minor loss alert — stock dipped slightly below purchase price.",
    "LOSS_ALERT_MAJOR":  "🚨 Send major loss alert — loss > 1%. Run Situation Analyser.",
    "DEADLINE_REACHED":  "⏰ Send deadline alert — 30-day window closed. Run end-of-period analysis.",
}

print(f"  → {ACTION_MESSAGES.get(alert_result['action'], alert_result['action'])}")
print(f"\n  Updated alert state for next poll:")
state = alert_result["updated_alert_state"]
step("  loss_alert_armed:",       str(state["loss_alert_armed"]))
step("  upside_alert_armed:",     str(state["upside_alert_armed"]))
step("  last_alerted_profit_pct:", f"{state['last_alerted_profit_pct']}%")


# ─────────────────────────────────────────────────────────────────
#  SUMMARY
# ─────────────────────────────────────────────────────────────────

section("SUMMARY")
print(f"  Stock selected:  {top_pick['ticker']}")
print(f"  Ranking score:   {top_pick['score']:.1f}/100")
print(f"  Investment:      ${total_invested:,.2f}  ({shares} shares @ ${purchase_price:.2f})")
print(f"  Current P&L:     {profit_result['profit_loss_pct']:+.2f}% "
      f"(${profit_result['profit_loss_dollars']:+,.2f})")
print(f"  Alert action:    {alert_result['action']}")
print()
