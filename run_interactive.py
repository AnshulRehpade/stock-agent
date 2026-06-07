# run_interactive.py — Interactive Real-Time Stock Agent
#
# This is the full system running interactively.
# It asks you questions, fetches real data, and sends real emails.
#
# Run: python run_interactive.py
#
# What it does:
#   1. Fetches top 5 stocks from a candidate pool
#   2. You pick one and enter a budget
#   3. The system monitors live prices every 60 seconds (demo speed)
#      (In production this would be every 60 minutes)
#   4. When thresholds are crossed, real emails are sent
#   5. You type your decisions (CONTINUE / SELL / EXTEND) in the terminal

import time
import os
import sys
from datetime import datetime
from unittest.mock import patch

from agents.orchestrator    import StockAgentOrchestrator
from agents.data_fetcher    import get_historical_data, get_stock_quote
from agents.stock_ranker    import rank_stocks
from agents.investment_manager import create_investment
from dotenv import load_dotenv
import uuid

load_dotenv()


# ─────────────────────────────────────────────────────────────────
#  DISPLAY HELPERS
# ─────────────────────────────────────────────────────────────────

def divider(title=""):
    print("\n" + "═"*60)
    if title:
        print(f"  {title}")
        print("═"*60)

def info(msg):  print(f"  {msg}")
def ok(msg):    print(f"  ✅ {msg}")
def warn(msg):  print(f"  ⚠️  {msg}")
def err(msg):   print(f"  ❌ {msg}")

def ask(prompt, options=None):
    """Prompt the user for input with optional validation."""
    while True:
        try:
            if options:
                opts = " / ".join(options)
                val = input(f"\n  → {prompt} [{opts}]: ").strip().upper()
                if val in [o.upper() for o in options]:
                    return val
                print(f"  Please enter one of: {opts}")
            else:
                val = input(f"\n  → {prompt}: ").strip()
                if val:
                    return val
                print("  Input cannot be empty.")
        except (KeyboardInterrupt, EOFError):
            print("\n\n  Exiting Stock Agent. Goodbye!")
            sys.exit(0)


# ─────────────────────────────────────────────────────────────────
#  CHECK EMAIL CONFIG
# ─────────────────────────────────────────────────────────────────

def check_email_config():
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    client_email = os.getenv("CLIENT_EMAIL", "")

    if not smtp_user or smtp_user == "your_gmail@gmail.com":
        warn("Email not configured. Running in DRY RUN mode (no emails sent).")
        warn("To enable real emails, fill in SMTP_USER, SMTP_PASSWORD,")
        warn("and CLIENT_EMAIL in your .env file.")
        return True  # dry_run = True
    if not smtp_pass or smtp_pass == "your_16_char_app_password":
        warn("SMTP_PASSWORD not set. Running in DRY RUN mode.")
        return True
    ok(f"Email configured → {client_email}")
    return False  # dry_run = False


# ─────────────────────────────────────────────────────────────────
#  MAIN INTERACTIVE FLOW
# ─────────────────────────────────────────────────────────────────

divider("STOCK AGENT — Interactive Real-Time Demo")
info("Welcome! This system will help you pick a stock and monitor it.")
info("Press Ctrl+C at any time to exit.\n")

dry_run = check_email_config()
mode_label = "DRY RUN (preview only)" if dry_run else "LIVE (real emails)"
info(f"Mode: {mode_label}")


# ─────────────────────────────────────────────────────────────────
#  PHASE 1A: STOCK SELECTION
# ─────────────────────────────────────────────────────────────────

divider("STEP 1 — Finding Top 5 Stocks")

CANDIDATES = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA",
              "GOOGL", "AMZN", "META", "ORCL", "ADBE"]

info(f"Fetching 90-day history for {len(CANDIDATES)} candidate stocks...\n")

candidate_data = []
for ticker in CANDIDATES:
    result = get_historical_data(ticker, days=90)
    if result["success"]:
        candidate_data.append({
            "ticker":        ticker,
            "current_price": result["daily_data"][-1]["close"],
            "daily_data":    result["daily_data"]
        })
        print(f"  ✅ {ticker:<6} ${result['daily_data'][-1]['close']:>9.2f}")
    else:
        warn(f"{ticker}: {result['error']}")

if not candidate_data:
    err("Could not load any stock data. Check your internet connection.")
    sys.exit(1)

rank_result = rank_stocks(candidate_data)
if not rank_result["success"]:
    err(f"Ranking failed: {rank_result['error']}")
    sys.exit(1)

print(f"\n  {'Rank':<5} {'Ticker':<8} {'Score':>6}  "
      f"{'30d Momentum':>13}  {'Current Price':>13}")
print("  " + "─"*55)
for s in rank_result["top5"]:
    print(f"  #{s['rank']:<4} {s['ticker']:<8} "
          f"{s['score']:>6.1f}  "
          f"{s['metrics']['momentum_30d']:>+10.2f}%  "
          f"${s['current_price']:>12.2f}")

# Build lookup for history
candidate_lookup = {c["ticker"]: c for c in candidate_data}


# ─────────────────────────────────────────────────────────────────
#  PHASE 1B: CLIENT PICKS A STOCK
# ─────────────────────────────────────────────────────────────────

divider("STEP 2 — Select Your Stock & Budget")

top_tickers = [s["ticker"] for s in rank_result["top5"]]
info("Choose one of the top 5 stocks above, or type any ticker symbol.")

selected = ask("Enter ticker to invest in", top_tickers).upper()

# If custom ticker not in our list, fetch it
if selected not in candidate_lookup:
    info(f"Fetching data for {selected}...")
    r = get_historical_data(selected, days=90)
    if not r["success"]:
        err(f"Could not load {selected}: {r['error']}")
        sys.exit(1)
    candidate_lookup[selected] = {
        "ticker":        selected,
        "current_price": r["daily_data"][-1]["close"],
        "daily_data":    r["daily_data"]
    }

current_price = candidate_lookup[selected]["current_price"]
info(f"Current price of {selected}: ${current_price:.2f}")
info(f"Minimum budget to buy 1 share: ${current_price:.2f}")

while True:
    try:
        budget_str = ask("Enter your investment budget in USD (e.g. 5000)")
        budget = float(budget_str.replace(",", "").replace("$", ""))
        if budget <= 0:
            warn("Budget must be greater than zero.")
            continue
        if budget < current_price:
            warn(f"Budget ${budget:,.2f} is less than 1 share "
                 f"(${current_price:.2f}). Please enter a higher amount.")
            continue
        break
    except ValueError:
        warn("Please enter a valid number.")

shares = int(budget // current_price)
total  = round(shares * current_price, 2)
info(f"\n  You will buy {shares} shares of {selected}")
info(f"  Total invested: ${total:,.2f}  |  Remaining: ${budget - total:,.2f}")

confirm = ask("Confirm investment?", ["YES", "NO"])
if confirm != "YES":
    info("Investment cancelled.")
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────
#  SETUP ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────

orch = StockAgentOrchestrator(
    db_path="data/interactive_session.db",
    dry_run=dry_run
)

inv = create_investment(
    ticker        = selected,
    budget        = budget,
    current_price = current_price
)
inv["investment_id"] = f"inv_{str(uuid.uuid4())[:8].upper()}"
orch.investment       = inv
orch.phase            = "MONITORING"
orch.price_cache      = candidate_lookup[selected]["daily_data"]

from agents.decision_logger import log_event
log_event(
    investment_id = inv["investment_id"],
    event_type    = "INVESTMENT_CREATED",
    data          = {"ticker": selected, "shares": shares,
                     "total_invested": total, "purchase_price": current_price},
    db_path       = orch.db_path
)

divider("INVESTMENT CONFIRMED")
print(inv["confirmation_summary"])
ok(f"Investment ID: {inv['investment_id']}")
ok("Monitoring started. Real-time polls every 60 seconds.")
info("(In production this runs every 60 minutes during market hours)\n")


# ─────────────────────────────────────────────────────────────────
#  PHASE 2: LIVE MONITORING LOOP
# ─────────────────────────────────────────────────────────────────

divider("PHASE 2 — Live Monitoring")
info("Polling live prices. Type your decision when prompted.")
info("Press Ctrl+C to stop monitoring.\n")

POLL_INTERVAL = 60   # seconds between polls (demo speed)
poll_count = 0

while orch.phase == "MONITORING":
    try:
        poll_count += 1
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n  [{ts}] Poll #{poll_count} — fetching {selected} price...")

        result = orch.run_hourly_poll()

        if not result["success"]:
            warn(f"Poll failed: {result['error']}")
            info(f"Retrying in {POLL_INTERVAL} seconds...")
            time.sleep(POLL_INTERVAL)
            continue

        sign = "+" if result["profit_pct"] >= 0 else ""
        print(f"  Price: ${result['current_price']:.2f}  |  "
              f"P&L: {sign}{result['profit_pct']:.2f}%  |  "
              f"Days left: {result['days_remaining']}  |  "
              f"Action: {result['action']}")

        # If an alert was fired, ask client for decision
        if result["action"] in ("UPSIDE_ALERT", "TARGET_REACHED",
                                 "LOSS_ALERT_MINOR", "LOSS_ALERT_MAJOR",
                                 "DEADLINE_REACHED"):

            print()
            if result["action"] == "DEADLINE_REACHED":
                decision = ask(
                    "30-day window closed. What would you like to do?",
                    ["SELL", "EXTEND"]
                )
            elif result["action"] == "TARGET_REACHED":
                decision = ask(
                    f"🎯 Target reached at {sign}{result['profit_pct']:.2f}%! "
                    f"What would you like to do?",
                    ["SELL", "CONTINUE"]
                )
            elif result["action"] in ("LOSS_ALERT_MINOR", "LOSS_ALERT_MAJOR"):
                decision = ask(
                    f"📉 Loss alert ({result['profit_pct']:.2f}%). "
                    f"What would you like to do?",
                    ["HOLD", "SELL"]
                )
                # Map HOLD → CONTINUE for the orchestrator
                decision = "CONTINUE" if decision == "HOLD" else "SELL"
            else:
                decision = ask(
                    f"📈 Profit at {sign}{result['profit_pct']:.2f}%. "
                    f"What would you like to do?",
                    ["CONTINUE", "SELL"]
                )

            dec_result = orch.record_client_decision(decision)
            ok(dec_result.get("message", f"Decision recorded: {decision}"))

            if orch.phase == "CLOSED":
                break

        if orch.phase == "MONITORING":
            info(f"Next poll in {POLL_INTERVAL} seconds... "
                 f"(Press Ctrl+C to stop)")
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n")
        divider("Monitoring Stopped")
        stop = ask("Stop monitoring?", ["YES", "NO"])
        if stop == "YES":
            orch.phase = "CLOSED"
            break
        else:
            info("Resuming monitoring...")
            continue


# ─────────────────────────────────────────────────────────────────
#  FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────

divider("SESSION SUMMARY")
status = orch.get_status()
info(f"Ticker:          {status['ticker']}")
info(f"Purchase price:  ${status['purchase_price']:.2f}")
info(f"Final P&L:       {orch.last_poll_profit_pct:+.2f}%")
info(f"Status:          {status['status']}")
info(f"Total polls:     {poll_count}")

from agents.decision_logger import get_events
events = get_events(inv["investment_id"], db_path=orch.db_path)
info(f"\nAudit log: {events['count']} events recorded")
for e in events["events"]:
    print(f"    {e['timestamp'][:19]}  {e['event_type']}")

print()
ok("Session complete. All events saved to data/interactive_session.db")
print()
