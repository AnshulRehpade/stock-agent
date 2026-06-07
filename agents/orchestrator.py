# ORCH Agent — Orchestrator
#
# This is the brain of the entire system.
# It owns the state machine, routes every task to the correct agent,
# and drives the full investment lifecycle from stock selection to closure.
#
# It does NOT implement any business logic itself — it only coordinates.
# Every calculation, analysis, notification, and log call is delegated
# to the appropriate specialist agent.
#
# The three phases:
#   Phase 1 — PRE_INVESTMENT : Stock selection → budget → confirmation
#   Phase 2 — MONITORING     : Hourly polls, alerts, client decisions
#   Phase 3 — CLOSED         : Investment sold or expired
#
# Interview explanation:
#   "The Orchestrator is the only agent that knows about all other agents.
#   Every other agent is isolated — DATA doesn't know about ALERT,
#   PROFIT doesn't know about NOTIF. Only ORCH holds the full picture.
#   This is the Mediator pattern — it centralises communication so
#   agents stay decoupled from each other."

import uuid
from datetime import datetime

from agents.data_fetcher      import get_stock_quote, get_historical_data
from agents.stock_ranker      import rank_stocks
from agents.investment_manager import create_investment, get_days_remaining
from agents.profit_calculator  import calculate_profit
from agents.alert_decision     import evaluate_alerts
from agents.situation_analyser import analyse_situation
from agents.notification       import send_notification, preview_notification
from agents.decision_logger    import log_event, init_db, get_last_client_decision

from pathlib import Path

DEFAULT_DB_PATH = str(Path(__file__).parent.parent / "data" / "stock_agent.db")


class StockAgentOrchestrator:
    """
    The central controller for the Stock Agent system.

    Holds the global state machine:
        phase          : PRE_INVESTMENT | MONITORING | CLOSED
        investment     : The active investment record
        alert_state    : Current armed/disarmed state of all alert thresholds
        price_cache    : Last fetched OHLCV history (avoids redundant API calls)

    All public methods return a dict with:
        {"success": True/False, ...result fields...}
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH,
                 dry_run: bool = False):
        """
        Parameters:
            db_path  : Path to SQLite database
            dry_run  : If True, previews emails instead of sending them.
                       Set to True for development/testing.
        """
        self.db_path = db_path
        self.dry_run = dry_run

        # ── System state ──────────────────────────────────────────
        self.phase      = "PRE_INVESTMENT"
        self.investment = None      # set by confirm_investment()
        self.price_cache = []       # last fetched daily_data

        # Alert thresholds — managed by ALERT agent output
        self.alert_state = {
            "loss_alert_armed":        True,
            "upside_alert_armed":      True,
            "last_alerted_profit_pct": 0.0,
        }

        self.last_poll_profit_pct = 0.0

        # Initialise the database
        init_db(db_path)

    # ─────────────────────────────────────────────────────────────
    #  PHASE 1: PRE-INVESTMENT
    # ─────────────────────────────────────────────────────────────

    def get_top5_stocks(self, candidates: list = None) -> dict:
        """
        Fetches historical data for candidate stocks and returns
        the top 5 ranked by suitability for a 5%/30-day target.

        Parameters:
            candidates : List of ticker strings. If None, uses a default
                         set of well-known stocks.

        Returns top 5 ranked stocks with scores and rationale.
        """
        if candidates is None:
            candidates = [
                "AAPL", "MSFT", "GOOGL", "AMZN", "META",
                "NVDA", "TSLA", "AMD",   "ORCL", "ADBE"
            ]

        print(f"\n  Loading historical data for {len(candidates)} stocks...")
        candidate_data = []

        for ticker in candidates:
            history = get_historical_data(ticker, days=90)
            if history["success"]:
                candidate_data.append({
                    "ticker":        ticker,
                    "current_price": history["daily_data"][-1]["close"],
                    "daily_data":    history["daily_data"]
                })
            else:
                print(f"  ⚠️  {ticker}: {history['error']}")

        if not candidate_data:
            return {
                "success": False,
                "error": "No stock data could be fetched."
            }

        result = rank_stocks(candidate_data)

        if result["success"]:
            # Cache historical data for the top pick (used later by SIT agent)
            top_ticker = result["top5"][0]["ticker"]
            for c in candidate_data:
                if c["ticker"] == top_ticker:
                    self.price_cache = c["daily_data"]
                    break

        return result

    def confirm_investment(self, ticker: str, budget: float) -> dict:
        """
        Validates the investment, creates the investment record, and
        transitions the system to MONITORING phase.

        Called after the client reviews the Top 5 and selects a stock.

        Parameters:
            ticker : Stock symbol the client chose
            budget : Dollar amount the client wants to invest
        """
        if self.phase == "MONITORING":
            return {
                "success": False,
                "error": "An active investment already exists. "
                         "Close it before starting a new one."
            }

        # Get current price for the selected stock
        quote = get_stock_quote(ticker)
        if not quote["success"]:
            return {
                "success": False,
                "error": f"Could not fetch current price for {ticker}: "
                         f"{quote['error']}"
            }

        # Create the investment record
        inv_result = create_investment(
            ticker=ticker,
            budget=budget,
            current_price=quote["price"]
        )

        if not inv_result["success"]:
            return inv_result

        # Generate a unique investment ID
        inv_result["investment_id"] = f"inv_{str(uuid.uuid4())[:8].upper()}"

        # Store in orchestrator state
        self.investment        = inv_result
        self.phase             = "MONITORING"
        self.last_poll_profit_pct = 0.0
        self.alert_state = {
            "loss_alert_armed":        True,
            "upside_alert_armed":      True,
            "last_alerted_profit_pct": 0.0
        }

        # Fetch and cache the price history for this stock
        history = get_historical_data(ticker, days=90)
        if history["success"]:
            self.price_cache = history["daily_data"]

        # Log the investment creation
        self._log("INVESTMENT_CREATED", {
            "ticker":        inv_result["ticker"],
            "purchase_price":inv_result["purchase_price"],
            "shares":        inv_result["shares"],
            "total_invested":inv_result["total_invested"],
            "budget":        budget
        })

        print(inv_result["confirmation_summary"])
        return {
            "success": True,
            "investment_id": inv_result["investment_id"],
            "investment":    inv_result
        }

    # ─────────────────────────────────────────────────────────────
    #  PHASE 2: MONITORING
    # ─────────────────────────────────────────────────────────────

    def run_hourly_poll(self) -> dict:
        """
        Runs one complete hourly monitoring cycle:
            1. Fetch current price (DATA agent)
            2. Calculate profit/loss (PROFIT agent)
            3. Evaluate alert conditions (ALERT agent)
            4. If alert needed: run SIT analysis if required
            5. Send notification (NOTIF agent)
            6. Log everything (LOG agent)

        Called by the Scheduler every 60 minutes during market hours.
        Also callable manually for testing.

        Returns the action taken and current profit state.
        """
        if self.phase != "MONITORING":
            return {
                "success": False,
                "error": f"System is in '{self.phase}' phase. "
                         "No active investment to monitor."
            }

        ticker = self.investment["ticker"]

        # Step 1 — Fetch current price
        quote = get_stock_quote(ticker)
        if not quote["success"]:
            self._log("API_ERROR", {"error": quote["error"], "ticker": ticker})
            return {
                "success": False,
                "error": f"Price fetch failed: {quote['error']}"
            }

        current_price = quote["price"]

        # Step 2 — Calculate profit/loss
        profit = calculate_profit(
            current_price       = current_price,
            purchase_price      = self.investment["purchase_price"],
            shares              = self.investment["shares"],
            total_invested      = self.investment["total_invested"],
            previous_profit_pct = self.last_poll_profit_pct
        )

        if not profit["success"]:
            return {"success": False, "error": profit["error"]}

        # Step 3 — Evaluate alert conditions
        days_remaining = get_days_remaining(
            self.investment["investment_date"]
        )

        alert = evaluate_alerts(
            profit_loss_pct          = profit["profit_loss_pct"],
            change_since_last_poll   = profit["change_since_last_poll"],
            last_alerted_profit_pct  = self.alert_state["last_alerted_profit_pct"],
            loss_alert_armed         = self.alert_state["loss_alert_armed"],
            upside_alert_armed       = self.alert_state["upside_alert_armed"],
            days_remaining           = days_remaining,
            is_in_loss               = profit["is_in_loss"],
            loss_exceeds_1_pct       = profit["loss_exceeds_1_pct"],
            reached_3_pct            = profit["reached_3_pct"],
            reached_5_pct            = profit["reached_5_pct"]
        )

        # Update alert state from ALERT agent output
        self.alert_state.update(alert["updated_alert_state"])
        self.last_poll_profit_pct = profit["profit_loss_pct"]

        # Log the poll
        self._log("POLL_COMPLETED", {
            "current_price":   current_price,
            "profit_pct":      profit["profit_loss_pct"],
            "profit_dollars":  profit["profit_loss_dollars"],
            "days_remaining":  days_remaining,
            "action":          alert["action"]
        })

        # Step 4 — Handle the alert action
        action = alert["action"]

        if action == "NO_ACTION":
            return {
                "success":       True,
                "action":        "NO_ACTION",
                "profit_pct":    profit["profit_loss_pct"],
                "profit_dollars":profit["profit_loss_dollars"],
                "current_price": current_price,
                "days_remaining":days_remaining
            }

        # Get SIT analysis if needed
        recommendation = None
        if alert["situation_analysis_required"] and self.price_cache:
            # Update price cache with today's price
            updated_cache = self.price_cache.copy()
            if updated_cache and updated_cache[-1]["close"] != current_price:
                from datetime import date
                updated_cache.append({
                    "date":   date.today().isoformat(),
                    "open":   current_price,
                    "high":   current_price,
                    "low":    current_price,
                    "close":  current_price,
                    "volume": 0
                })

            mode_map = {
                "LOSS_ALERT_MAJOR": "LOSS",
                "TARGET_REACHED":   "TARGET_REACHED",
                "DEADLINE_REACHED": "END_OF_PERIOD"
            }
            sit_mode = mode_map.get(action)
            if sit_mode:
                recommendation = analyse_situation(
                    mode           = sit_mode,
                    daily_data     = updated_cache,
                    profit_pct     = profit["profit_loss_pct"],
                    days_remaining = days_remaining
                )

        # Step 5 — Map action to notification type
        notif_map = {
            "LOSS_ALERT_MINOR": "LOSS_MINOR",
            "LOSS_ALERT_MAJOR": "LOSS_MAJOR",
            "UPSIDE_ALERT":     "UPSIDE_ALERT",
            "TARGET_REACHED":   "TARGET_REACHED",
            "DEADLINE_REACHED": "DEADLINE"
        }
        notif_type = notif_map.get(action)

        notif_result = None
        if notif_type:
            notif_result = self._send(
                notif_type, profit, recommendation, days_remaining
            )
            self._log("ALERT_FIRED", {
                "action":     action,
                "notif_type": notif_type,
                "profit_pct": profit["profit_loss_pct"],
                "alert_id":   notif_result.get("alert_id", "N/A")
            })

        return {
            "success":        True,
            "action":         action,
            "profit_pct":     profit["profit_loss_pct"],
            "profit_dollars": profit["profit_loss_dollars"],
            "current_price":  current_price,
            "days_remaining": days_remaining,
            "recommendation": recommendation,
            "notification":   notif_result
        }

    def send_daily_summary(self) -> dict:
        """
        Sends the daily P&L summary email to the client.
        Called by the Scheduler once per day at market close.
        """
        if self.phase != "MONITORING":
            return {"success": False,
                    "error": "No active investment to summarise."}

        ticker = self.investment["ticker"]
        quote  = get_stock_quote(ticker)

        if not quote["success"]:
            return {"success": False, "error": quote["error"]}

        profit = calculate_profit(
            current_price       = quote["price"],
            purchase_price      = self.investment["purchase_price"],
            shares              = self.investment["shares"],
            total_invested      = self.investment["total_invested"],
            previous_profit_pct = self.last_poll_profit_pct
        )

        days_remaining = get_days_remaining(self.investment["investment_date"])
        result = self._send("DAILY_SUMMARY", profit, None, days_remaining)
        self._log("DAILY_SUMMARY_SENT", {
            "profit_pct":  profit["profit_loss_pct"],
            "days_remaining": days_remaining
        })
        return result

    def send_daily_summary(self) -> dict:
        """
        Sends the daily P&L summary email to the client.
        Called by the Scheduler once per day at market close.
        """
        if self.phase != "MONITORING":
            return {"success": False,
                    "error": "No active investment to summarise."}

        ticker = self.investment["ticker"]
        quote  = get_stock_quote(ticker)

        if not quote["success"]:
            return {"success": False, "error": quote["error"]}

        profit = calculate_profit(
            current_price       = quote["price"],
            purchase_price      = self.investment["purchase_price"],
            shares              = self.investment["shares"],
            total_invested      = self.investment["total_invested"],
            previous_profit_pct = self.last_poll_profit_pct
        )

        days_remaining = get_days_remaining(self.investment["investment_date"])
        result = self._send("DAILY_SUMMARY", profit, None, days_remaining)
        self._log("DAILY_SUMMARY_SENT", {
            "profit_pct":     profit["profit_loss_pct"],
            "days_remaining": days_remaining
        })
        return result

    def record_client_decision(self, decision: str) -> dict:
        """
        Records the client's response to an alert (CONTINUE, SELL, EXTEND).

        Parameters:
            decision : "CONTINUE" | "SELL" | "EXTEND"

        If SELL: closes the investment and transitions to CLOSED phase.
        If CONTINUE: resets alert thresholds and resumes monitoring.
        If EXTEND: opens a new 30-day window from today.
        """
        if self.phase != "MONITORING":
            return {"success": False,
                    "error": "No active investment to decide on."}

        decision = decision.upper().strip()
        valid = {"CONTINUE", "SELL", "EXTEND"}
        if decision not in valid:
            return {
                "success": False,
                "error": f"Decision must be one of {valid}. Got: '{decision}'"
            }

        self._log("CLIENT_DECISION", {
            "decision":   decision,
            "profit_pct": self.last_poll_profit_pct
        })

        if decision == "SELL":
            self.investment["status"] = "SOLD"
            self.phase = "CLOSED"
            self._log("MONITORING_CLOSED", {
                "reason": "CLIENT_SOLD",
                "profit_pct": self.last_poll_profit_pct
            })
            return {
                "success":    True,
                "decision":   "SELL",
                "message":    (
                    f"Please log into your brokerage and sell "
                    f"{self.investment['shares']} shares of "
                    f"{self.investment['ticker']}. "
                    f"Monitoring has stopped."
                ),
                "profit_pct": self.last_poll_profit_pct
            }

        elif decision == "CONTINUE":
            # Reset alert thresholds so the next crossings fire fresh
            self.alert_state = {
                "loss_alert_armed":        not self.investment.get("is_in_loss", False),
                "upside_alert_armed":      self.last_poll_profit_pct < 3.0,
                "last_alerted_profit_pct": 0.0
            }
            return {
                "success":  True,
                "decision": "CONTINUE",
                "message":  "Monitoring resumed. Alert thresholds reset."
            }

        elif decision == "EXTEND":
            # Open a new 30-day window from today
            from datetime import date, timedelta
            new_investment_date = date.today()
            new_deadline        = new_investment_date + timedelta(days=30)
            self.investment["investment_date"] = new_investment_date.isoformat()
            self.investment["deadline_date"]   = new_deadline.isoformat()
            self.alert_state = {
                "loss_alert_armed":        True,
                "upside_alert_armed":      True,
                "last_alerted_profit_pct": 0.0
            }
            return {
                "success":     True,
                "decision":    "EXTEND",
                "message":     f"New 30-day window opened. "
                               f"New deadline: {new_deadline.isoformat()}",
                "new_deadline":new_deadline.isoformat()
            }

    def get_status(self) -> dict:
        """
        Returns the current system state — useful for debugging
        and for the client to check their investment status.
        """
        if self.phase == "PRE_INVESTMENT":
            return {
                "phase": "PRE_INVESTMENT",
                "message": "No active investment. Call get_top5_stocks() "
                           "then confirm_investment()."
            }

        days_remaining = get_days_remaining(
            self.investment["investment_date"]
        ) if self.investment else 0

        return {
            "phase":             self.phase,
            "ticker":            self.investment.get("ticker"),
            "investment_date":   self.investment.get("investment_date"),
            "deadline_date":     self.investment.get("deadline_date"),
            "days_remaining":    days_remaining,
            "purchase_price":    self.investment.get("purchase_price"),
            "shares":            self.investment.get("shares"),
            "total_invested":    self.investment.get("total_invested"),
            "last_profit_pct":   self.last_poll_profit_pct,
            "status":            self.investment.get("status"),
            "alert_state":       self.alert_state
        }

    # ─────────────────────────────────────────────────────────────
    #  PRIVATE HELPERS
    # ─────────────────────────────────────────────────────────────

    def _log(self, event_type: str, data: dict) -> None:
        """Logs an event via the Decision Logger agent."""
        investment_id = (
            self.investment.get("investment_id", "PRE_INVESTMENT")
            if self.investment else "PRE_INVESTMENT"
        )
        log_event(
            investment_id = investment_id,
            event_type    = event_type,
            data          = data,
            db_path       = self.db_path
        )

    def _send(self, notif_type: str, profit: dict,
              recommendation: dict, days_remaining: int) -> dict:
        """
        Sends or previews a notification via the NOTIF agent.
        In dry_run mode, prints the email instead of sending it.
        """
        if self.dry_run:
            result = preview_notification(
                notification_type = notif_type,
                investment        = self.investment,
                profit_data       = profit,
                recommendation    = recommendation,
                days_remaining    = days_remaining
            )
            if result["success"]:
                print(f"\n  📧 [DRY RUN] {result['subject']}")
                print(result["body"])
            return result
        else:
            return send_notification(
                notification_type = notif_type,
                investment        = self.investment,
                profit_data       = profit,
                recommendation    = recommendation,
                days_remaining    = days_remaining
            )
