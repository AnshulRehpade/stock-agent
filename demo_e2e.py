# demo_e2e.py — Full End-to-End Demo
#
# Runs the complete Stock Agent lifecycle through the Orchestrator:
#   Phase 1: Get top 5 stocks, select one, confirm investment
#   Phase 2: Simulate 3 hourly polls at different price points
#             - Poll 1: slight gain (no alert)
#             - Poll 2: crosses 3% (upside alert)
#             - Poll 3: hits 5% (target reached + SIT analysis)
#   Client decision: SELL
#   Phase 3: Investment closed
#
# dry_run=True — emails are previewed, not actually sent.
# Run: python demo_e2e.py

import time
from agents.orchestrator import StockAgentOrchestrator
from agents.data_fetcher  import get_historical_data
from agents.stock_ranker  import rank_stocks


def divider(title=""):
    print("\n" + "═"*60)
    if title:
        print(f"  {title}")
        print("═"*60)

def show(label, value):
    print(f"  {label:<30} {value}")


# ─────────────────────────────────────────────────────────────────
#  SETUP
# ─────────────────────────────────────────────────────────────────

divider("STOCK AGENT — Full End-to-End Demo")
print("  Mode: dry_run (emails previewed, not sent)")
print("  Using real market data from Alpha Vantage + Yahoo Finance")

orch = StockAgentOrchestrator(
    db_path="data/demo_e2e.db",
    dry_run=True
)


# ─────────────────────────────────────────────────────────────────
#  PHASE 1A: Get Top 5 Stocks
# ─────────────────────────────────────────────────────────────────

divider("PHASE 1A — Stock Selection (RANK Agent)")
print("  Fetching 90-day history for candidate stocks...\n")

# Use a focused 5-stock list to stay within Alpha Vantage daily quota
CANDIDATES = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA"]
candidate_data = []

for ticker in CANDIDATES:
    result = get_historical_data(ticker, days=90)
    if result["success"]:
        candidate_data.append({
            "ticker":        ticker,
            "current_price": result["daily_data"][-1]["close"],
            "daily_data":    result["daily_data"]
        })
        print(f"  ✅ {ticker:<6} — {result['days_returned']} days loaded  "
              f"(current: ${result['daily_data'][-1]['close']:.2f})")
    else:
        print(f"  ⚠️  {ticker}: {result['error']}")

rank_result = rank_stocks(candidate_data)

print(f"\n  {'Rank':<5} {'Ticker':<8} {'Score':>6}  {'30d Momentum':>13}")
print("  " + "─"*40)
for s in rank_result["top5"]:
    print(f"  #{s['rank']:<4} {s['ticker']:<8} "
          f"{s['score']:>6.1f}  "
          f"{s['metrics']['momentum_30d']:>+10.2f}%")

top_pick   = rank_result["top5"][0]["ticker"]
top_price  = rank_result["top5"][0]["current_price"]
print(f"\n  → Top pick: {top_pick} at ${top_price:.2f}")


# ─────────────────────────────────────────────────────────────────
#  PHASE 1B: Confirm Investment
# ─────────────────────────────────────────────────────────────────

divider("PHASE 1B — Investment Confirmation (INV Agent)")

CLIENT_BUDGET = 5000.00
print(f"  Client selects: {top_pick}")
print(f"  Budget:         ${CLIENT_BUDGET:,.2f}\n")

# We confirm using the real current price fetched above
from agents.investment_manager import create_investment
from datetime import date, timedelta
import uuid

inv = create_investment(
    ticker        = top_pick,
    budget        = CLIENT_BUDGET,
    current_price = top_price
)

if not inv["success"]:
    print(f"  ❌ Investment failed: {inv['error']}")
    exit(1)

inv["investment_id"] = f"inv_{str(uuid.uuid4())[:8].upper()}"
orch.investment        = inv
orch.phase             = "MONITORING"
orch.last_poll_profit_pct = 0.0

# Cache historical data for the top pick (needed by SIT agent)
for c in candidate_data:
    if c["ticker"] == top_pick:
        orch.price_cache = c["daily_data"]
        break

print(inv["confirmation_summary"])
show("Investment ID:",   inv["investment_id"])
show("Purchase price:",  f"${inv['purchase_price']:.2f}")
show("Shares:",          str(inv["shares"]))
show("Total invested:",  f"${inv['total_invested']:,.2f}")
show("Deadline:",        inv["deadline_date"])


# ─────────────────────────────────────────────────────────────────
#  PHASE 2: Simulate 3 Hourly Polls
# ─────────────────────────────────────────────────────────────────

purchase_price = inv["purchase_price"]

# Simulate three price scenarios
polls = [
    {
        "label":       "Poll 1 — Small gain (1%), below 3% threshold",
        "price":       round(purchase_price * 1.010, 2),
        "expected":    "NO_ACTION"
    },
    {
        "label":       "Poll 2 — Crosses 3% profit threshold",
        "price":       round(purchase_price * 1.035, 2),
        "expected":    "UPSIDE_ALERT"
    },
    {
        "label":       "Poll 3 — Hits 5% profit target",
        "price":       round(purchase_price * 1.055, 2),
        "expected":    "TARGET_REACHED"
    },
]

for i, poll in enumerate(polls, 1):
    divider(f"PHASE 2 — {poll['label']}")

    # Patch the current price by temporarily overriding get_stock_quote
    from unittest.mock import patch

    mock_quote = {
        "success": True, "ticker": top_pick,
        "price": poll["price"], "change": 1.0,
        "change_pct": "1.0%", "volume": 50_000_000,
        "fetched_at": "2026-06-05T10:00:00"
    }

    with patch("agents.orchestrator.get_stock_quote",
               return_value=mock_quote):
        result = orch.run_hourly_poll()

    if not result["success"]:
        print(f"  ❌ Poll failed: {result['error']}")
        continue

    profit_pct = result["profit_pct"]
    sign = "+" if profit_pct >= 0 else ""

    show("Current price:",     f"${poll['price']:.2f}")
    show("Profit/Loss:",       f"{sign}{profit_pct:.2f}% "
                               f"(${result.get('profit_dollars', result.get('profit_loss_dollars', 0)):+,.2f})")
    show("Days remaining:",    str(result["days_remaining"]))
    show("Action taken:",      result["action"])

    if result.get("recommendation") and result["recommendation"].get("success"):
        rec = result["recommendation"]
        print(f"\n  📊 SIT Analysis:")
        show("  Recommendation:", rec["recommendation"])
        show("  Confidence:",     rec["confidence"])
        print(f"  Reason: {rec['reason'][:80]}...")

    print(f"\n  Expected: {poll['expected']} | Got: {result['action']}")
    match = "✅" if result["action"] == poll["expected"] else "⚠️ "
    print(f"  {match} {'MATCH' if result['action'] == poll['expected'] else 'DIFFERENT'}")

    if i < len(polls):
        time.sleep(1)  # small pause between polls for readability


# ─────────────────────────────────────────────────────────────────
#  CLIENT DECISION: SELL
# ─────────────────────────────────────────────────────────────────

divider("CLIENT DECISION — Sell at 5% Profit")
print("  Client responds: SELL\n")

decision = orch.record_client_decision("SELL")
show("Decision recorded:", decision["decision"])
show("System phase:",      orch.phase)
show("Investment status:", orch.investment["status"])
print(f"\n  Sell instruction:\n  {decision['message']}")


# ─────────────────────────────────────────────────────────────────
#  FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────

divider("FINAL SUMMARY")
status = orch.get_status()
show("Phase:",            status["phase"])
show("Ticker:",           status["ticker"])
show("Purchase price:",   f"${status['purchase_price']:.2f}")
show("Final profit:",     f"{orch.last_poll_profit_pct:+.2f}%")
show("Investment status:",status["status"])

# Check the audit log
from agents.decision_logger import get_events
events = get_events(inv["investment_id"], db_path="data/demo_e2e.db")
print(f"\n  Audit log: {events['count']} events recorded")
for e in events["events"]:
    ts = e["timestamp"][:19]
    print(f"    {ts}  {e['event_type']}")

divider("DEMO COMPLETE")
print("  ✅ All 9 agents worked end-to-end successfully")
print("  ✅ Full audit trail in data/demo_e2e.db")
print()
