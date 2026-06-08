# ORCH Agent — LangGraph Orchestrator with GPT-4o-mini
#
# This is NOT a script calling functions in order.
# It's a LangGraph StateGraph where:
#   - Each existing agent is wrapped as a graph NODE
#   - An LLM sits at the decision point and REASONS about what to do
#   - State flows through the graph, each node reads/writes specific fields
#   - The LLM can OVERRIDE the rules engine if the alert is noise
#
# Graph:
#   fetch_price → calc_profit → eval_alert → llm_decision → notify → log
#                                  ↓ (no alert)
#                                log → END

import os
import json
import uuid
import logging
from typing import TypedDict, Optional
from datetime import date, timedelta

from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from agents.data_fetcher       import get_stock_quote, get_historical_data
from agents.stock_ranker       import rank_stocks
from agents.investment_manager import create_investment, get_days_remaining
from agents.profit_calculator  import calculate_profit
from agents.alert_decision     import evaluate_alerts
from agents.situation_analyser import analyse_situation
from agents.notification       import send_notification, preview_notification
from agents.decision_logger    import log_event, init_db

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("orchestrator")


# ─────────────────────────────────────────────────────────────────
#  LLM SETUP
# ─────────────────────────────────────────────────────────────────

llm = ChatGoogleGenerativeAI(
    model             = "gemini-2.5-flash",
    temperature       = 0.3,
    google_api_key    = os.getenv("GOOGLE_API_KEY", "")
)

SYSTEM_PROMPT = """You are an AI investment advisor agent managing a client's stock portfolio.
Your goal: help the client achieve 5% profit on a single stock within 30 days.

When a rules-based alert is triggered, YOU decide:
1. Is this alert worth sending to the client right now, or is it noise?
2. What recommendation should you give (natural language, 1-2 sentences)?
3. How confident are you? (HIGH/MEDIUM/LOW)

Consider momentum, trend, time remaining, and whether the move is meaningful.
Be concise, honest, and actionable. If signals are mixed, say so.

Respond ONLY in this JSON format:
{"send_alert": true/false, "recommendation": "your advice", "confidence": "HIGH/MEDIUM/LOW", "reasoning": "brief explanation"}
"""


# ─────────────────────────────────────────────────────────────────
#  STATE — flows through the graph, each node reads/writes fields
# ─────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # Identity
    ticker: str
    investment_id: str

    # Investment record (locked at investment time)
    purchase_price: float
    shares: int
    total_invested: float
    investment_date: str
    deadline_date: str
    budget: float

    # Live data (updated each poll)
    ohlcv_data: Optional[list]
    current_price: Optional[float]
    profit_pct: Optional[float]
    profit_dollars: Optional[float]
    change_since_last_poll: Optional[float]

    # Alert state (persists between polls)
    loss_alert_armed: bool
    upside_alert_armed: bool
    last_alerted_profit_pct: float
    last_poll_profit_pct: float
    days_remaining: int

    # Convenience flags from PROFIT agent
    is_in_loss: Optional[bool]
    loss_exceeds_1_pct: Optional[bool]
    reached_3_pct: Optional[bool]
    reached_5_pct: Optional[bool]

    # Decision flow (filled during graph execution)
    alert_action: Optional[str]
    should_alert: Optional[bool]
    recommendation: Optional[str]
    confidence: Optional[str]
    llm_reasoning: Optional[str]
    alert_sent: Optional[bool]
    logged: Optional[bool]

    # Client response
    decision: Optional[str]

    # System config
    phase: str
    db_path: str
    dry_run: bool


# ─────────────────────────────────────────────────────────────────
#  NODE 1: DATA AGENT — fetch current price
# ─────────────────────────────────────────────────────────────────

def fetch_price_node(state: AgentState) -> dict:
    """Wraps the DATA agent as a graph node."""
    quote = get_stock_quote(state["ticker"])

    if not quote["success"]:
        log_event(state["investment_id"], "API_ERROR",
                  {"error": quote["error"]}, db_path=state["db_path"])
        return {"current_price": None, "alert_action": "API_ERROR"}

    return {"current_price": quote["price"]}


# ─────────────────────────────────────────────────────────────────
#  NODE 2: PROFIT AGENT — calculate P&L
# ─────────────────────────────────────────────────────────────────

def calc_profit_node(state: AgentState) -> dict:
    """Wraps the PROFIT agent as a graph node."""
    if not state.get("current_price"):
        return {}

    result = calculate_profit(
        current_price       = state["current_price"],
        purchase_price      = state["purchase_price"],
        shares              = state["shares"],
        total_invested      = state["total_invested"],
        previous_profit_pct = state["last_poll_profit_pct"]
    )

    if not result["success"]:
        return {}

    days = get_days_remaining(state["investment_date"])

    return {
        "profit_pct":           result["profit_loss_pct"],
        "profit_dollars":       result["profit_loss_dollars"],
        "change_since_last_poll": result["change_since_last_poll"],
        "days_remaining":       days,
        "is_in_loss":           result["is_in_loss"],
        "loss_exceeds_1_pct":   result["loss_exceeds_1_pct"],
        "reached_3_pct":        result["reached_3_pct"],
        "reached_5_pct":        result["reached_5_pct"],
    }


# ─────────────────────────────────────────────────────────────────
#  NODE 3: ALERT AGENT — evaluate rules
# ─────────────────────────────────────────────────────────────────

def eval_alert_node(state: AgentState) -> dict:
    """Wraps the ALERT agent as a graph node."""
    if state.get("profit_pct") is None:
        return {"alert_action": "NO_ACTION"}

    alert = evaluate_alerts(
        profit_loss_pct          = state["profit_pct"],
        change_since_last_poll   = state["change_since_last_poll"],
        last_alerted_profit_pct  = state["last_alerted_profit_pct"],
        loss_alert_armed         = state["loss_alert_armed"],
        upside_alert_armed       = state["upside_alert_armed"],
        days_remaining           = state["days_remaining"],
        is_in_loss               = state["is_in_loss"],
        loss_exceeds_1_pct       = state["loss_exceeds_1_pct"],
        reached_3_pct            = state["reached_3_pct"],
        reached_5_pct            = state["reached_5_pct"]
    )

    updated = alert["updated_alert_state"]
    return {
        "alert_action":          alert["action"],
        "loss_alert_armed":      updated["loss_alert_armed"],
        "upside_alert_armed":    updated["upside_alert_armed"],
        "last_alerted_profit_pct": updated["last_alerted_profit_pct"],
        "last_poll_profit_pct":  state["profit_pct"],
    }


# ─────────────────────────────────────────────────────────────────
#  NODE 4: LLM DECISION — the intelligent part
# ─────────────────────────────────────────────────────────────────

def llm_decision_node(state: AgentState) -> dict:
    """
    GPT-4o-mini reasons about whether to send the alert.
    Can override the rules engine if the alert is noise.
    Generates natural language recommendation.
    """
    action = state.get("alert_action", "NO_ACTION")

    context = f"""
Stock: {state['ticker']}
Purchase: ${state['purchase_price']:.2f} | Current: ${state['current_price']:.2f}
P&L: {state['profit_pct']:+.2f}% (${state['profit_dollars']:+.2f})
Change since last check: {state['change_since_last_poll']:+.2f}%
Days remaining: {state['days_remaining']}/30
Alert triggered by rules engine: {action}

Should this alert be sent to the client right now?
"""

    try:
        response = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=context)
        ])

        text = response.content.strip()
        # Strip markdown code fences if present
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        output = json.loads(text)

        send = output.get("send_alert", True)
        return {
            "should_alert":   send,
            "recommendation": output.get("recommendation", ""),
            "confidence":     output.get("confidence", "MEDIUM"),
            "llm_reasoning":  output.get("reasoning", ""),
            # Override alert_action if LLM says don't send
            "alert_action":   state["alert_action"] if send else "NO_ACTION"
        }

    except Exception as e:
        logger.warning(f"LLM failed ({e}), falling back to deterministic.")
        # Fallback: honour the rules engine as-is
        return {
            "should_alert":   True,
            "recommendation": f"Alert triggered: {action}. Check your investment.",
            "confidence":     "MEDIUM",
            "llm_reasoning":  f"LLM unavailable ({e}), using rules engine."
        }


# ─────────────────────────────────────────────────────────────────
#  NODE 5: NOTIFICATION AGENT — send the alert
# ─────────────────────────────────────────────────────────────────

def notify_node(state: AgentState) -> dict:
    """Wraps the NOTIF agent as a graph node."""
    action = state.get("alert_action", "NO_ACTION")
    if action == "NO_ACTION" or action == "API_ERROR":
        return {"alert_sent": False}

    notif_map = {
        "LOSS_ALERT_MINOR": "LOSS_MINOR",
        "LOSS_ALERT_MAJOR": "LOSS_MAJOR",
        "UPSIDE_ALERT":     "UPSIDE_ALERT",
        "TARGET_REACHED":   "TARGET_REACHED",
        "DEADLINE_REACHED": "DEADLINE"
    }
    notif_type = notif_map.get(action)
    if not notif_type:
        return {"alert_sent": False}

    # Build investment dict for the NOTIF agent
    investment = {
        "ticker":         state["ticker"],
        "purchase_price": state["purchase_price"],
        "shares":         state["shares"],
        "total_invested": state["total_invested"],
        "investment_date":state["investment_date"],
        "deadline_date":  state["deadline_date"],
        "status":         "ACTIVE"
    }

    # Build profit dict
    profit_data = {
        "current_price":       state["current_price"],
        "current_value":       state["shares"] * state["current_price"],
        "total_invested":      state["total_invested"],
        "profit_loss_pct":     state["profit_pct"],
        "profit_loss_dollars": state["profit_dollars"],
        "change_since_last_poll": state["change_since_last_poll"],
        "is_in_profit":        state["profit_pct"] > 0,
        "is_in_loss":          state["profit_pct"] < 0,
    }

    # Build recommendation from LLM output
    recommendation = {
        "success":        True,
        "recommendation": state.get("recommendation", ""),
        "confidence":     state.get("confidence", "MEDIUM"),
        "reason":         state.get("llm_reasoning", ""),
        "signals": {
            "sma_signal":      "LLM-based",
            "momentum_3d_pct": state["change_since_last_poll"],
            "volume_pressure": "LLM-based",
            "days_remaining":  state["days_remaining"],
            "profit_pct":      state["profit_pct"]
        }
    }

    send_fn = preview_notification if state["dry_run"] else send_notification
    result = send_fn(
        notification_type = notif_type,
        investment        = investment,
        profit_data       = profit_data,
        recommendation    = recommendation,
        days_remaining    = state["days_remaining"]
    )

    return {"alert_sent": result.get("success", False)}


# ─────────────────────────────────────────────────────────────────
#  NODE 6: LOGGER AGENT — record everything
# ─────────────────────────────────────────────────────────────────

def log_poll_node(state: AgentState) -> dict:
    """Wraps the LOG agent as a graph node."""
    log_event(
        investment_id = state["investment_id"],
        event_type    = "POLL_COMPLETED",
        data = {
            "current_price":  state.get("current_price"),
            "profit_pct":     state.get("profit_pct"),
            "alert_action":   state.get("alert_action"),
            "should_alert":   state.get("should_alert"),
            "llm_reasoning":  (state.get("llm_reasoning") or "")[:100],
            "alert_sent":     state.get("alert_sent")
        },
        db_path = state["db_path"]
    )
    return {"logged": True}


# ─────────────────────────────────────────────────────────────────
#  ROUTING — conditional edges in the graph
# ─────────────────────────────────────────────────────────────────

def route_after_alert_eval(state: AgentState) -> str:
    """After rules engine: go to LLM if alert triggered, else skip to log."""
    action = state.get("alert_action", "NO_ACTION")
    if action in ("NO_ACTION", "API_ERROR"):
        return "log_poll"
    return "llm_decision"


def route_after_llm(state: AgentState) -> str:
    """After LLM: send notification if should_alert=True, else skip to log."""
    if state.get("should_alert"):
        return "notify"
    return "log_poll"


# ─────────────────────────────────────────────────────────────────
#  BUILD THE GRAPH
# ─────────────────────────────────────────────────────────────────

def build_monitoring_graph():
    """
    Constructs the LangGraph state machine:

        fetch_price → calc_profit → eval_alert
                                        │
                         ┌──────────────┼──────────────┐
                         ↓ (NO_ACTION)  ↓ (alert)      │
                      log_poll      llm_decision       │
                         ↑              │               │
                         │    ┌─────────┼─────────┐    │
                         │    ↓ (skip)  ↓ (send)  │    │
                         │  log_poll   notify     │    │
                         │              ↓          │    │
                         │           log_poll      │    │
                         └────────── END ──────────┘────┘
    """
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("fetch_price",   fetch_price_node)
    graph.add_node("calc_profit",   calc_profit_node)
    graph.add_node("eval_alert",    eval_alert_node)
    graph.add_node("llm_decision",  llm_decision_node)
    graph.add_node("notify",        notify_node)
    graph.add_node("log_poll",      log_poll_node)

    # Linear edges
    graph.set_entry_point("fetch_price")
    graph.add_edge("fetch_price", "calc_profit")
    graph.add_edge("calc_profit", "eval_alert")

    # Conditional: after alert evaluation
    graph.add_conditional_edges(
        "eval_alert",
        route_after_alert_eval,
        {"llm_decision": "llm_decision", "log_poll": "log_poll"}
    )

    # Conditional: after LLM decides
    graph.add_conditional_edges(
        "llm_decision",
        route_after_llm,
        {"notify": "notify", "log_poll": "log_poll"}
    )

    # After notify, always log
    graph.add_edge("notify", "log_poll")

    # Log is the final node
    graph.add_edge("log_poll", END)

    return graph.compile()


# ─────────────────────────────────────────────────────────────────
#  ORCHESTRATOR CLASS — wraps graph for the Flask API
# ─────────────────────────────────────────────────────────────────

class StockAgentOrchestrator:
    """
    AI-powered orchestrator using LangGraph + GPT-4o-mini.

    The LLM:
    - Can override alert rules if it decides the signal is noise
    - Generates natural language recommendations
    - Provides auditable reasoning for every decision
    """

    def __init__(self, db_path="data/stock_agent.db", dry_run=False):
        self.db_path   = db_path
        self.dry_run   = dry_run
        self.phase     = "PRE_INVESTMENT"
        self.investment = None
        self.price_cache = []
        self.alert_state = {
            "loss_alert_armed": True,
            "upside_alert_armed": True,
            "last_alerted_profit_pct": 0.0
        }
        self.last_poll_profit_pct = 0.0
        self.graph = build_monitoring_graph()
        init_db(db_path)

    def confirm_investment(self, ticker: str, budget: float) -> dict:
        """Validates and creates the investment record."""
        if self.phase == "MONITORING":
            return {"success": False,
                    "error": "An active investment already exists."}

        quote = get_stock_quote(ticker)
        if not quote["success"]:
            return {"success": False, "error": quote["error"]}

        inv = create_investment(ticker=ticker, budget=budget,
                               current_price=quote["price"])
        if not inv["success"]:
            return inv

        inv["investment_id"] = f"inv_{str(uuid.uuid4())[:8].upper()}"
        self.investment = inv
        self.phase = "MONITORING"
        self.last_poll_profit_pct = 0.0
        self.alert_state = {
            "loss_alert_armed": True,
            "upside_alert_armed": True,
            "last_alerted_profit_pct": 0.0
        }

        history = get_historical_data(ticker, days=90)
        if history["success"]:
            self.price_cache = history["daily_data"]

        log_event(inv["investment_id"], "INVESTMENT_CREATED", {
            "ticker": ticker, "shares": inv["shares"],
            "purchase_price": inv["purchase_price"]
        }, db_path=self.db_path)

        return {"success": True, "investment_id": inv["investment_id"],
                "investment": inv}

    def run_hourly_poll(self) -> dict:
        """
        Executes one monitoring cycle through the LangGraph state machine.
        The LLM decides at the decision node whether to alert or suppress.
        """
        if self.phase != "MONITORING":
            return {"success": False, "error": f"Phase: {self.phase}"}

        # Build initial state for graph execution
        initial_state: AgentState = {
            "ticker":               self.investment["ticker"],
            "investment_id":        self.investment["investment_id"],
            "purchase_price":       self.investment["purchase_price"],
            "shares":               self.investment["shares"],
            "total_invested":       self.investment["total_invested"],
            "investment_date":      self.investment["investment_date"],
            "deadline_date":        self.investment["deadline_date"],
            "budget":               self.investment["budget"],
            "ohlcv_data":           self.price_cache,
            "current_price":        None,
            "profit_pct":           None,
            "profit_dollars":       None,
            "change_since_last_poll": None,
            "loss_alert_armed":     self.alert_state["loss_alert_armed"],
            "upside_alert_armed":   self.alert_state["upside_alert_armed"],
            "last_alerted_profit_pct": self.alert_state["last_alerted_profit_pct"],
            "last_poll_profit_pct": self.last_poll_profit_pct,
            "days_remaining":       0,
            "is_in_loss":           None,
            "loss_exceeds_1_pct":   None,
            "reached_3_pct":        None,
            "reached_5_pct":        None,
            "alert_action":         None,
            "should_alert":         None,
            "recommendation":       None,
            "confidence":           None,
            "llm_reasoning":        None,
            "alert_sent":           None,
            "logged":               None,
            "decision":             None,
            "phase":                self.phase,
            "db_path":              self.db_path,
            "dry_run":              self.dry_run,
        }

        # Execute the graph
        final_state = self.graph.invoke(initial_state)

        # Sync state back to orchestrator (persists between polls)
        self.alert_state = {
            "loss_alert_armed":      final_state["loss_alert_armed"],
            "upside_alert_armed":    final_state["upside_alert_armed"],
            "last_alerted_profit_pct": final_state["last_alerted_profit_pct"],
        }
        self.last_poll_profit_pct = final_state.get("last_poll_profit_pct",
                                                     self.last_poll_profit_pct)

        return {
            "success":        True,
            "action":         final_state.get("alert_action") or "NO_ACTION",
            "profit_pct":     final_state.get("profit_pct") or 0.0,
            "profit_dollars": final_state.get("profit_dollars") or 0.0,
            "current_price":  final_state.get("current_price") or 0.0,
            "days_remaining": final_state.get("days_remaining") or 0,
            "should_alert":   final_state.get("should_alert"),
            "llm_reasoning":  final_state.get("llm_reasoning") or "",
            "recommendation": final_state.get("recommendation"),
            "confidence":     final_state.get("confidence"),
            "alert_sent":     final_state.get("alert_sent", False)
        }

    def send_daily_summary(self) -> dict:
        """Sends daily P&L summary email."""
        if self.phase != "MONITORING":
            return {"success": False, "error": "No active investment."}

        quote = get_stock_quote(self.investment["ticker"])
        if not quote["success"]:
            return {"success": False, "error": quote["error"]}

        profit = calculate_profit(
            current_price       = quote["price"],
            purchase_price      = self.investment["purchase_price"],
            shares              = self.investment["shares"],
            total_invested      = self.investment["total_invested"],
            previous_profit_pct = self.last_poll_profit_pct
        )
        days = get_days_remaining(self.investment["investment_date"])
        send_fn = preview_notification if self.dry_run else send_notification
        result = send_fn("DAILY_SUMMARY", self.investment, profit,
                         days_remaining=days)
        log_event(self.investment["investment_id"], "DAILY_SUMMARY_SENT",
                  {"profit_pct": profit["profit_loss_pct"]},
                  db_path=self.db_path)
        return result

    def record_client_decision(self, decision: str) -> dict:
        """Records client response: CONTINUE, SELL, or EXTEND."""
        if self.phase != "MONITORING":
            return {"success": False, "error": "No active investment."}

        decision = decision.upper().strip()
        valid = {"CONTINUE", "SELL", "EXTEND"}
        if decision not in valid:
            return {"success": False,
                    "error": f"Decision must be one of {valid}."}

        log_event(self.investment["investment_id"], "CLIENT_DECISION",
                  {"decision": decision}, db_path=self.db_path)

        if decision == "SELL":
            self.investment["status"] = "SOLD"
            self.phase = "CLOSED"
            log_event(self.investment["investment_id"], "MONITORING_CLOSED",
                      {"reason": "CLIENT_SOLD"}, db_path=self.db_path)
            return {
                "success": True, "decision": "SELL",
                "message": f"Sell {self.investment['shares']} shares of "
                           f"{self.investment['ticker']}. Monitoring stopped.",
                "profit_pct": self.last_poll_profit_pct
            }
        elif decision == "CONTINUE":
            self.alert_state = {
                "loss_alert_armed": True,
                "upside_alert_armed": self.last_poll_profit_pct < 3.0,
                "last_alerted_profit_pct": 0.0
            }
            return {"success": True, "decision": "CONTINUE",
                    "message": "Monitoring resumed. Thresholds reset."}
        elif decision == "EXTEND":
            new_dl = (date.today() + timedelta(days=30)).isoformat()
            self.investment["investment_date"] = date.today().isoformat()
            self.investment["deadline_date"] = new_dl
            self.alert_state = {
                "loss_alert_armed": True,
                "upside_alert_armed": True,
                "last_alerted_profit_pct": 0.0
            }
            return {"success": True, "decision": "EXTEND",
                    "message": f"New 30-day window. Deadline: {new_dl}",
                    "new_deadline": new_dl}

    def get_status(self) -> dict:
        """Returns current system state."""
        if self.phase == "PRE_INVESTMENT":
            return {"phase": "PRE_INVESTMENT",
                    "message": "No investment yet."}
        return {
            "phase":           self.phase,
            "ticker":          self.investment.get("ticker"),
            "investment_date": self.investment.get("investment_date"),
            "deadline_date":   self.investment.get("deadline_date"),
            "days_remaining":  get_days_remaining(
                self.investment["investment_date"]),
            "purchase_price":  self.investment.get("purchase_price"),
            "shares":          self.investment.get("shares"),
            "total_invested":  self.investment.get("total_invested"),
            "last_profit_pct": self.last_poll_profit_pct,
            "status":          self.investment.get("status"),
            "alert_state":     self.alert_state
        }
