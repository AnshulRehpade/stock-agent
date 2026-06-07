# main.py — Production Entry Point (Flask API + Scheduler)
#
# Exposes a simple REST API so the user can:
#   GET  /top5          → see the top 5 ranked stocks right now
#   POST /invest        → confirm investment { "ticker": "AMD", "budget": 5000 }
#   GET  /status        → check current investment status
#   POST /decide        → client decision { "decision": "SELL" }
#   POST /stop          → stop monitoring
#
# Railway runs this as a web service (not a worker).
# No environment variables needed for ticker or budget —
# the user provides them via the API.

import os
import sys
import signal
import logging
import threading
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s  %(levelname)s  %(message)s"
)
logger = logging.getLogger("main")

app = Flask(__name__)

# Global orchestrator and scheduler — shared across requests
orch      = None
scheduler = None

DEFAULT_CANDIDATES = [
    "AAPL", "MSFT", "NVDA", "AMD",  "TSLA",
    "GOOGL","AMZN", "META", "ORCL", "ADBE",
    "JPM",  "V",    "MA",   "UNH",  "LLY",
    "CAT",  "GS",   "WMT",  "COST", "CRM"
]


# ─────────────────────────────────────────────────────────────────
#  API ENDPOINTS
# ─────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def home():
    """Health check — confirms the service is running."""
    return jsonify({
        "service": "Stock Agent",
        "status":  "running",
        "endpoints": {
            "GET  /top5":       "Get top 5 ranked stocks",
            "POST /invest":     "Start monitoring { ticker, budget }",
            "GET  /status":     "Check investment status",
            "POST /decide":     "Submit decision { decision: SELL|CONTINUE|EXTEND }",
            "POST /stop":       "Stop monitoring"
        }
    })


@app.route("/top5", methods=["GET"])
def top5():
    """
    Ranks the candidate pool and returns the top 5 stocks.
    The client uses this to decide which stock to invest in.

    Optional query param: ?tickers=AMD,NVDA,AAPL
    """
    from agents.data_fetcher import get_historical_data
    from agents.stock_ranker import rank_stocks

    tickers_param = request.args.get("tickers", "")
    if tickers_param:
        candidates = [t.strip().upper() for t in tickers_param.split(",")
                      if t.strip()]
    else:
        candidates = DEFAULT_CANDIDATES

    logger.info(f"/top5 requested — loading {len(candidates)} stocks...")
    candidate_data = []

    for ticker in candidates:
        result = get_historical_data(ticker, days=90)
        if result["success"]:
            candidate_data.append({
                "ticker":        ticker,
                "current_price": result["daily_data"][-1]["close"],
                "daily_data":    result["daily_data"]
            })

    if not candidate_data:
        return jsonify({"success": False,
                        "error": "No stock data could be fetched."}), 500

    rank_result = rank_stocks(candidate_data)
    if not rank_result["success"]:
        return jsonify({"success": False,
                        "error": rank_result["error"]}), 500

    # Return top 5 without daily_data (too large for API response)
    top5_clean = []
    for s in rank_result["top5"]:
        top5_clean.append({
            "rank":          s["rank"],
            "ticker":        s["ticker"],
            "current_price": s["current_price"],
            "score":         s["score"],
            "rationale":     s["rationale"],
            "metrics": {
                "momentum_30d":         s["metrics"]["momentum_30d"],
                "upside_volatility":    s["metrics"]["upside_volatility"],
                "avg_daily_volume":     s["metrics"]["avg_daily_volume"],
                "trend_strength":       s["metrics"]["trend_strength"],
                "risk_adjusted_return": s["metrics"]["risk_adjusted_return"]
            }
        })

    return jsonify({"success": True, "top5": top5_clean})


@app.route("/invest", methods=["POST"])
def invest():
    """
    Confirms an investment and starts autonomous monitoring.

    Request body (JSON):
        { "ticker": "AMD", "budget": 5000 }

    Returns the investment record and confirmation summary.
    """
    global orch, scheduler

    data = request.get_json()
    if not data:
        return jsonify({"success": False,
                        "error": "Request body must be JSON."}), 400

    ticker = data.get("ticker", "").upper().strip()
    budget = data.get("budget")

    if not ticker:
        return jsonify({"success": False,
                        "error": "ticker is required."}), 400

    if not budget:
        return jsonify({"success": False,
                        "error": "budget is required."}), 400

    try:
        budget = float(budget)
    except (ValueError, TypeError):
        return jsonify({"success": False,
                        "error": "budget must be a number."}), 400

    if orch and orch.phase == "MONITORING":
        return jsonify({
            "success": False,
            "error":   f"Already monitoring {orch.investment['ticker']}. "
                       "POST /stop first to close the current investment."
        }), 409

    # Create orchestrator
    from agents.orchestrator import StockAgentOrchestrator
    orch = StockAgentOrchestrator(
        db_path = "data/production.db",
        dry_run = False
    )

    # Pre-load price history for the selected ticker
    from agents.data_fetcher import get_historical_data
    history = get_historical_data(ticker, days=90)
    if history["success"]:
        orch.price_cache = history["daily_data"]

    # Confirm the investment
    result = orch.confirm_investment(ticker, budget)
    if not result["success"]:
        return jsonify({"success": False, "error": result["error"]}), 400

    # Start the scheduler
    from agents.scheduler import create_scheduler
    scheduler = create_scheduler(orch)
    scheduler.start()

    inv = result["investment"]
    logger.info(f"Investment confirmed: {ticker} — "
                f"{inv['shares']} shares @ ${inv['purchase_price']:.2f}")
    logger.info(f"Scheduler started. Monitoring {ticker} until {inv['deadline_date']}")

    return jsonify({
        "success":      True,
        "message":      f"Monitoring started for {ticker}. "
                        f"Alerts will be sent to "
                        f"{os.getenv('CLIENT_EMAIL', 'configured email')}.",
        "investment": {
            "investment_id":  inv.get("investment_id"),
            "ticker":         inv["ticker"],
            "purchase_price": inv["purchase_price"],
            "shares":         inv["shares"],
            "total_invested": inv["total_invested"],
            "remaining_cash": inv["remaining_cash"],
            "investment_date":inv["investment_date"],
            "deadline_date":  inv["deadline_date"],
            "profit_target":  "5.00%"
        }
    })


@app.route("/status", methods=["GET"])
def status():
    """Returns current investment status and profit/loss."""
    if not orch:
        return jsonify({"phase": "PRE_INVESTMENT",
                        "message": "No investment yet. POST /invest to start."})

    status_data = orch.get_status()

    # Add live P&L if monitoring
    if orch.phase == "MONITORING" and orch.investment:
        from agents.data_fetcher import get_stock_quote
        quote = get_stock_quote(orch.investment["ticker"])
        if quote["success"]:
            from agents.profit_calculator import calculate_profit
            profit = calculate_profit(
                current_price       = quote["price"],
                purchase_price      = orch.investment["purchase_price"],
                shares              = orch.investment["shares"],
                total_invested      = orch.investment["total_invested"],
                previous_profit_pct = orch.last_poll_profit_pct
            )
            if profit["success"]:
                status_data["live_price"]      = quote["price"]
                status_data["live_profit_pct"] = profit["profit_loss_pct"]
                status_data["live_profit_usd"] = profit["profit_loss_dollars"]

    return jsonify(status_data)


@app.route("/decide", methods=["POST"])
def decide():
    """
    Records the client's decision.

    Request body: { "decision": "SELL" | "CONTINUE" | "EXTEND" }
    """
    if not orch or orch.phase != "MONITORING":
        return jsonify({"success": False,
                        "error": "No active investment to decide on."}), 400

    data = request.get_json()
    decision = data.get("decision", "").upper().strip() if data else ""

    if not decision:
        return jsonify({"success": False,
                        "error": "decision is required "
                                 "(SELL, CONTINUE, or EXTEND)."}), 400

    result = orch.record_client_decision(decision)
    return jsonify(result)


@app.route("/stop", methods=["POST"])
def stop_monitoring():
    """Stops monitoring and closes the investment."""
    global scheduler

    if not orch or orch.phase != "MONITORING":
        return jsonify({"success": False,
                        "error": "No active investment to stop."}), 400

    orch.phase = "CLOSED"
    orch.investment["status"] = "STOPPED"

    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)

    return jsonify({"success": True,
                    "message": "Monitoring stopped."})


# ─────────────────────────────────────────────────────────────────
#  START SERVER
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info(f"Stock Agent API starting on port {port}")
    app.run(host="0.0.0.0", port=port)
