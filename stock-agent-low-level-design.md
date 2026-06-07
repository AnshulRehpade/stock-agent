# Stock Agent — Low Level Design & Agent Orchestration

**Version:** 1.0  
**Date:** June 5, 2026  
**Status:** Draft

---

## 1. Overview

This document defines the low-level design of the Stock Agent system as a **chain of orchestrated AI agents**. Each agent has a single, well-defined responsibility. They are wired together by an **Orchestrator Agent** that manages state, routes messages, and decides which agent to invoke next based on the current context.

The system is built as a **multi-agent pipeline** — not a monolith. This keeps each agent focused, testable, and replaceable independently.

---

## 2. Agent Roster

| Agent ID | Agent Name | Role |
|----------|------------|------|
| `ORCH` | Orchestrator Agent | Central controller — routes tasks, manages state, decides next agent |
| `DATA` | Data Fetcher Agent | Calls Alpha Vantage API, respects rate limits, returns raw price data |
| `RANK` | Stock Ranker Agent | Scores and ranks candidate stocks, produces Top 5 report |
| `INV` | Investment Manager Agent | Validates budget, calculates shares, records investment |
| `PROFIT` | Profit Calculator Agent | Computes current profit/loss % from latest price |
| `ALERT` | Alert Decision Agent | Evaluates alert conditions, decides which alert type to fire |
| `SIT` | Situation Analyser Agent | Runs technical analysis (SMA, momentum, volume) for recommendations |
| `NOTIF` | Notification Agent | Formats and dispatches notifications via Email/SMS/Push |
| `LOG` | Decision Logger Agent | Persists all client decisions and system events with timestamps |
| `SCHED` | Scheduler Agent | Triggers daily summary job and hourly poll cycle |

---

## 3. Agent Definitions

### 3.1 Orchestrator Agent (`ORCH`)

**Purpose:** The brain of the system. Maintains the global state machine, receives events, and delegates work to the correct agent in the correct order.

**Inputs:**
- User actions (stock selection, budget confirmation, continue/sell decisions)
- Agent outputs from all other agents
- Timer events from the Scheduler Agent

**State it manages:**

```python
system_state = {
    "phase": "PRE_INVESTMENT" | "MONITORING" | "CLOSED",
    "investment": {
        "ticker": str,
        "purchase_price": float,
        "shares": int,
        "total_invested": float,
        "investment_date": date,
        "deadline_date": date,
        "status": "ACTIVE" | "SOLD" | "EXPIRED"
    },
    "alert_state": {
        "loss_alert_armed": bool,        # True when profit > 0% (arms loss crossing)
        "upside_alert_armed": bool,      # True when profit < 3% (arms 3% crossing)
        "last_alerted_profit_pct": float # Last profit % at which 3% zone alert fired
    },
    "api_call_count": int,               # Daily Alpha Vantage call counter
    "last_poll_profit_pct": float        # Profit % at last poll
}
```

**Routing logic (pseudocode):**

```
ON timer_event("HOURLY_POLL"):
    IF api_call_count < 23:
        invoke DATA → get current price
        invoke PROFIT → compute profit %
        invoke ALERT → evaluate all alert conditions
        IF alert needed:
            IF situation_analysis_required:
                invoke SIT → get recommendation
            invoke NOTIF → send alert
            invoke LOG → record event
    ELSE:
        invoke LOG → record "API_LIMIT_APPROACHING, poll skipped"

ON timer_event("DAILY_SUMMARY"):
    invoke DATA → get latest price (if not already polled today)
    invoke NOTIF → send daily summary
    invoke LOG → record daily summary sent

ON user_event("STOCK_SELECTED", ticker):
    invoke DATA → fetch 90-day history for top 5 candidates
    invoke RANK → score and rank stocks
    invoke NOTIF → present Top 5 report to client

ON user_event("INVESTMENT_CONFIRMED", ticker, budget):
    invoke INV → validate budget, calculate shares, record investment
    transition phase → "MONITORING"

ON user_event("CLIENT_DECISION", decision):
    invoke LOG → record decision
    IF decision == "SELL":
        invoke NOTIF → send sell instruction
        transition status → "SOLD", phase → "CLOSED"
    IF decision == "CONTINUE":
        reset alert thresholds in alert_state

ON timer_event("DAY_30"):
    invoke SIT → run end-of-period analysis
    invoke NOTIF → send deadline notification with analysis
    invoke LOG → record deadline event
```

---

### 3.2 Data Fetcher Agent (`DATA`)

**Purpose:** All Alpha Vantage API calls go through this agent. It enforces the 25 calls/day limit and handles retries.

**Inputs:**
```python
{
    "mode": "HISTORY" | "QUOTE",
    "ticker": str,
    "api_call_count": int   # passed from ORCH to enforce limit
}
```

**Outputs:**
```python
# HISTORY mode
{
    "ticker": str,
    "daily_data": [
        {"date": "2026-05-01", "open": 180.0, "high": 185.0,
         "low": 179.5, "close": 183.2, "volume": 52000000},
        ...  # 90+ days
    ]
}

# QUOTE mode
{
    "ticker": str,
    "price": float,
    "change": float,
    "change_pct": float,
    "fetched_at": datetime
}
```

**Internal logic:**
```
IF api_call_count >= 25:
    return error: "DAILY_LIMIT_REACHED"

call Alpha Vantage endpoint
IF success:
    increment api_call_count
    return parsed data
IF rate_limit_error (HTTP 429):
    wait 60 seconds, retry once
    IF still fails: return error "RATE_LIMITED"
IF other error:
    return error with message
```

**Endpoints used:**
- `TIME_SERIES_DAILY_ADJUSTED` for HISTORY mode
- `GLOBAL_QUOTE` for QUOTE mode

---

### 3.3 Stock Ranker Agent (`RANK`)

**Purpose:** Takes 90-day historical data for a set of candidate stocks and returns a ranked Top 5 list with scores and rationale.

**Inputs:**
```python
{
    "candidates": [
        {"ticker": "AAPL", "daily_data": [...]},
        {"ticker": "MSFT", "daily_data": [...]},
        ...
    ]
}
```

**Outputs:**
```python
{
    "top5": [
        {
            "rank": 1,
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "current_price": 189.50,
            "score": 87.3,
            "metrics": {
                "momentum_30d": 6.2,
                "upside_volatility": 0.53,
                "avg_daily_volume": 58000000,
                "trend_strength": 0.81,
                "risk_adjusted_return": 0.74
            },
            "rationale": "Strong 30-day momentum with consistent positive days and high liquidity."
        },
        ...
    ]
}
```

**Internal scoring logic:**

```python
def score_stock(daily_data):
    closes = [d["close"] for d in daily_data]
    volumes = [d["volume"] for d in daily_data]

    # 1. 30-day momentum
    momentum = (closes[-1] - closes[-31]) / closes[-31] * 100

    # 2. Upside volatility — frequency of daily gains >= 1%
    daily_returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    upside_vol = sum(1 for r in daily_returns[-30:] if r >= 0.01) / 30

    # 3. Average daily volume (last 30 days)
    avg_volume = sum(volumes[-30:]) / 30

    # 4. Trend strength — linear regression slope on last 90 closes
    slope, r_squared = linear_regression(closes[-90:])

    # 5. Risk-adjusted return — simplified Sharpe over 90 days
    avg_return = sum(daily_returns[-90:]) / 90
    std_return = std_dev(daily_returns[-90:])
    risk_adj = avg_return / std_return if std_return != 0 else 0

    return {
        "momentum": momentum,
        "upside_vol": upside_vol,
        "avg_volume": avg_volume,
        "trend_strength": slope * r_squared,  # penalise erratic trends
        "risk_adj": risk_adj
    }

def composite_score(metrics, volume_max):
    # Normalise all to 0-100 before weighting
    return (
        normalise(metrics["momentum"])        * 0.30 +
        normalise(metrics["upside_vol"])      * 0.25 +
        (metrics["avg_volume"] / volume_max)  * 0.20 +  # already relative
        normalise(metrics["trend_strength"])  * 0.15 +
        normalise(metrics["risk_adj"])        * 0.10
    )
```

---

### 3.4 Investment Manager Agent (`INV`)

**Purpose:** Validates the client's budget, calculates shares, and creates the investment record.

**Inputs:**
```python
{
    "ticker": str,
    "budget": float,
    "current_price": float
}
```

**Outputs:**
```python
{
    "valid": bool,
    "error": str | None,   # e.g. "Budget too low to buy 1 share"
    "investment": {
        "ticker": str,
        "purchase_price": float,
        "shares": int,
        "total_invested": float,
        "remaining_cash": float,
        "investment_date": date,
        "deadline_date": date,   # investment_date + 30 days
        "status": "ACTIVE"
    }
}
```

**Validation logic:**
```python
shares = floor(budget / current_price)
if shares < 1:
    return error("Budget too low to purchase even 1 share at $X.XX")

total_invested = shares * current_price
remaining_cash = budget - total_invested
```

---

### 3.5 Profit Calculator Agent (`PROFIT`)

**Purpose:** Single-purpose agent. Takes current price and investment record, returns current profit/loss figures.

**Inputs:**
```python
{
    "current_price": float,
    "investment": { "purchase_price": float, "shares": int, "total_invested": float }
}
```

**Outputs:**
```python
{
    "current_value": float,
    "profit_loss_dollars": float,
    "profit_loss_pct": float,
    "previous_profit_pct": float,   # from last poll — for change calculation
    "change_since_last_poll": float
}
```

---

### 3.6 Alert Decision Agent (`ALERT`)

**Purpose:** The rules engine. Evaluates the current profit/loss figures against all alert conditions and tells the Orchestrator what action (if any) to take.

**Inputs:**
```python
{
    "profit_loss_pct": float,
    "change_since_last_poll": float,
    "last_alerted_profit_pct": float,
    "loss_alert_armed": bool,
    "upside_alert_armed": bool,
    "days_remaining": int,
    "investment_date": date,
    "deadline_date": date
}
```

**Outputs:**
```python
{
    "action": "NO_ACTION"
            | "LOSS_ALERT_MINOR"        # loss < 1%, no analysis
            | "LOSS_ALERT_MAJOR"        # loss > 1%, run SIT agent
            | "UPSIDE_ALERT"            # profit >= 3%, >= 0.25% jump
            | "TARGET_REACHED"          # profit >= 5%, run SIT agent
            | "DEADLINE_REACHED",       # Day 30, run SIT agent
    "situation_analysis_required": bool,
    "updated_alert_state": {
        "loss_alert_armed": bool,
        "upside_alert_armed": bool,
        "last_alerted_profit_pct": float
    }
}
```

**Decision logic:**

```python
def evaluate_alerts(profit_pct, change, last_alerted, loss_armed,
                    upside_armed, days_remaining):

    # --- Deadline check (highest priority) ---
    if days_remaining == 0:
        return Action.DEADLINE_REACHED, situation_required=True

    # --- Target reached ---
    if profit_pct >= 5.0:
        return Action.TARGET_REACHED, situation_required=True

    # --- Loss alert ---
    if profit_pct < 0 and loss_armed:
        loss_armed = False   # disarm until stock recovers above 0%
        if abs(profit_pct) > 1.0:
            return Action.LOSS_ALERT_MAJOR, situation_required=True
        else:
            return Action.LOSS_ALERT_MINOR, situation_required=False

    if profit_pct >= 0:
        loss_armed = True   # re-arm when stock is back above 0%

    # --- Upside alert (3% zone) ---
    if profit_pct >= 3.0:
        upside_armed = False
        jump = profit_pct - last_alerted
        if jump >= 0.25:
            return Action.UPSIDE_ALERT, situation_required=False
    else:
        upside_armed = True
        last_alerted = 0.0   # reset when below 3%

    return Action.NO_ACTION
```

---

### 3.7 Situation Analyser Agent (`SIT`)

**Purpose:** Runs lightweight technical analysis on the stored price history to generate a hold/sell recommendation. Called by `ORCH` when `ALERT` flags `situation_analysis_required=True`.

**Inputs:**
```python
{
    "mode": "LOSS" | "TARGET_REACHED" | "END_OF_PERIOD",
    "daily_data": [...],   # last 30 days of close prices + volumes
    "profit_pct": float,
    "days_remaining": int
}
```

**Outputs:**
```python
{
    "recommendation": "HOLD" | "CONSIDER_SELLING" | "MONITOR_CLOSELY"
                    | "HOLD_FOR_MORE" | "GOOD_TIME_TO_SELL" | "NEUTRAL"
                    | "CLOSE_TO_TARGET" | "UNLIKELY_TO_REACH" | "UNCERTAIN",
    "signals": {
        "sma5_vs_sma10": "ABOVE" | "BELOW",
        "momentum_3d": float,
        "volume_pressure": "LOW" | "HIGH",
        "days_remaining": int
    },
    "reason": str   # one sentence explanation
}
```

**Internal analysis — LOSS mode:**
```python
sma5  = avg(closes[-5:])
sma10 = avg(closes[-10:])
momentum_3d = (closes[-1] - closes[-4]) / closes[-4] * 100
vol_down_days = avg volume on days where close < prev_close (last 10d)
vol_up_days   = avg volume on days where close > prev_close (last 10d)

if sma5 > sma10 and momentum_3d > 0 and days_remaining > 10:
    recommendation = "HOLD"
elif vol_down_days > vol_up_days and days_remaining < 10:
    recommendation = "CONSIDER_SELLING"
else:
    recommendation = "MONITOR_CLOSELY"
```

**Internal analysis — TARGET_REACHED mode:**
```python
momentum_3d = (closes[-1] - closes[-4]) / closes[-4] * 100
vol_today   = volumes[-1]
avg_vol_5d  = avg(volumes[-5:])
high_30d    = max(closes[-30:])

if momentum_3d > 1.0 and vol_today > avg_vol_5d and days_remaining > 7:
    recommendation = "HOLD_FOR_MORE"
elif closes[-1] >= 0.98 * high_30d and momentum_3d < 0.5:
    recommendation = "GOOD_TIME_TO_SELL"
else:
    recommendation = "NEUTRAL"
```

**Internal analysis — END_OF_PERIOD mode:**
```python
gap = 5.0 - profit_pct
days_up_in_7 = count of last 7 days where close > prev_close
rate_needed  = gap / 5   # per day over 5-day outlook

if gap <= 1.0 and days_up_in_7 >= 4 and rate_needed <= 0.3:
    recommendation = "CLOSE_TO_TARGET"
elif gap > 2.5 or days_up_in_7 <= 2:
    recommendation = "UNLIKELY_TO_REACH"
else:
    recommendation = "UNCERTAIN"
```

---

### 3.8 Notification Agent (`NOTIF`)

**Purpose:** Formats and dispatches all outbound messages to the client. Never makes business decisions — only renders and sends.

**Inputs:**
```python
{
    "type": "DAILY_SUMMARY"
          | "LOSS_MINOR" | "LOSS_MAJOR"
          | "UPSIDE_ALERT" | "TARGET_REACHED" | "DEADLINE",
    "investment": {...},
    "profit_data": {...},
    "recommendation": {...} | None,
    "channel": "EMAIL" | "SMS" | "PUSH"
}
```

**Outputs:**
```python
{
    "dispatched": bool,
    "alert_id": str,   # UUID for this specific alert — used in decision callback
    "channel": str,
    "sent_at": datetime
}
```

Each notification template is a pre-defined format (as specified in the technical design). The agent selects the template based on `type`, injects the data, and sends via the configured channel.

---

### 3.9 Decision Logger Agent (`LOG`)

**Purpose:** Immutable audit log. Every event — alert fired, client decision, no-response, poll result, API error — is persisted here.

**Inputs:**
```python
{
    "event_type": "ALERT_FIRED" | "CLIENT_DECISION" | "NO_RESPONSE"
                | "POLL_COMPLETED" | "API_ERROR" | "DAILY_SUMMARY_SENT"
                | "INVESTMENT_CREATED" | "MONITORING_CLOSED",
    "investment_id": str,
    "data": dict,   # event-specific payload
    "timestamp": datetime
}
```

**Outputs:**
```python
{ "logged": bool, "log_id": str }
```

All log entries are append-only. No log entry is ever modified or deleted.

---

### 3.10 Scheduler Agent (`SCHED`)

**Purpose:** Time-based trigger agent. Fires events at the correct times to drive the monitoring loop.

**Scheduled jobs:**

| Job | Trigger | Event fired to ORCH |
|-----|---------|---------------------|
| Hourly poll | Every 60 min, market hours only (9:30 AM – 4:00 PM) | `HOURLY_POLL` |
| Daily summary | Once per day at 4:00 PM (configurable) | `DAILY_SUMMARY` |
| Deadline check | On `deadline_date` at market open | `DAY_30` |

**Market hours check:**
```python
MARKET_OPEN  = time(9, 30)   # 9:30 AM
MARKET_CLOSE = time(16, 0)   # 4:00 PM
MARKET_DAYS  = [MON, TUE, WED, THU, FRI]

def should_poll():
    now = datetime.now()
    return (now.weekday() in MARKET_DAYS and
            MARKET_OPEN <= now.time() <= MARKET_CLOSE)
```

---

## 4. Data Store

All persistent data is stored in a simple structured format (JSON file, SQLite, or equivalent).

### 4.1 Tables / Collections

**`investments`**
```
investment_id, ticker, purchase_price, shares, total_invested,
remaining_cash, investment_date, deadline_date, status
```

**`alert_state`**
```
investment_id, loss_alert_armed, upside_alert_armed,
last_alerted_profit_pct, last_poll_profit_pct, api_call_count,
api_call_reset_date
```

**`event_log`**
```
log_id, investment_id, event_type, data (JSON), timestamp
```

**`price_cache`**
```
ticker, date, close, volume   # stores fetched OHLCV for SIT agent re-use
```

---

## 5. Agent Orchestration Flowchart

```
╔══════════════════════════════════════════════════════════════════╗
║                     STOCK AGENT SYSTEM                          ║
║                   Agent Orchestration Flow                      ║
╚══════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━ PHASE 1: PRE-INVESTMENT ━━━━━━━━━━━━━━━━━━━━━

[CLIENT]
  │  "Show me top stocks"
  ▼
[ORCH] ──► [DATA] ── fetch 90d history × 5 tickers ──► raw OHLCV data
  │◄─────────────────────────────────────────────────┘
  │
  ├──► [RANK] ── score & rank candidates ──► Top 5 ranked list
  │◄──────────────────────────────────────┘
  │
  ├──► [NOTIF] ── present Top 5 report ──► [CLIENT]
  │
  │  Client selects stock + enters budget
  ▼
[ORCH] ──► [INV] ── validate budget, calc shares ──► investment record
  │◄───────────────────────────────────────────────┘
  │
  ├──► [NOTIF] ── show confirmation screen ──► [CLIENT]
  │
  │  Client confirms investment
  ▼
[ORCH] ── stores investment, arms alert state
  │
  │  transition: phase = MONITORING
  ▼

━━━━━━━━━━━━━━━━━━━━━ PHASE 2: MONITORING LOOP ━━━━━━━━━━━━━━━━━━━━

[SCHED] ── fires HOURLY_POLL every 60 min (market hours)
  │
  ▼
[ORCH] ──► [DATA] ── GLOBAL_QUOTE ──► current price
  │◄─────────────────────────────────┘
  │
  ├──► [PROFIT] ── calc profit % ──► {profit_pct, change_since_last}
  │◄────────────────────────────────┘
  │
  ├──► [ALERT] ── evaluate all conditions ──► {action, sit_required}
  │◄─────────────────────────────────────────┘
  │
  │  ┌─────────────────────────────────────────────────────────┐
  │  │              ALERT ROUTING DECISION                     │
  │  │                                                         │
  │  │  action == NO_ACTION ────────────────────► (loop end)   │
  │  │                                                         │
  │  │  action == LOSS_ALERT_MINOR                             │
  │  │      └──► [NOTIF] ──► [CLIENT] ──► [LOG]               │
  │  │                                                         │
  │  │  action == LOSS_ALERT_MAJOR                             │
  │  │      └──► [SIT] mode=LOSS ──► recommendation           │
  │  │               └──► [NOTIF] ──► [CLIENT] ──► [LOG]      │
  │  │                                                         │
  │  │  action == UPSIDE_ALERT (profit ≥ 3%, jump ≥ 0.25%)    │
  │  │      └──► [NOTIF] ──► [CLIENT] ──► [LOG]               │
  │  │                                                         │
  │  │  action == TARGET_REACHED (profit ≥ 5%)                 │
  │  │      └──► [SIT] mode=TARGET_REACHED ──► recommendation  │
  │  │               └──► [NOTIF] ──► [CLIENT] ──► [LOG]      │
  │  │                                                         │
  │  │  action == DEADLINE_REACHED (Day 30)                    │
  │  │      └──► [SIT] mode=END_OF_PERIOD ──► analysis        │
  │  │               └──► [NOTIF] ──► [CLIENT] ──► [LOG]      │
  │  └─────────────────────────────────────────────────────────┘
  │
  │  [CLIENT] responds to alert
  ▼
[ORCH] ──► [LOG] ── record client decision
  │
  │  ┌────────────────────────────────────────────────────────┐
  │  │           CLIENT DECISION ROUTING                      │
  │  │                                                        │
  │  │  decision == SELL                                      │
  │  │      └──► [NOTIF] ── send sell instruction             │
  │  │               └──► [LOG] ── status = SOLD             │
  │  │               └──► ORCH transitions phase = CLOSED    │
  │  │                         ── STOP ──                    │
  │  │                                                        │
  │  │  decision == CONTINUE (or no response)                 │
  │  │      └──► [ORCH] resets alert thresholds               │
  │  │               └──► return to MONITORING LOOP           │
  │  └────────────────────────────────────────────────────────┘
  │
  ▼
[SCHED] ── fires DAILY_SUMMARY at 4:00 PM
  │
  ▼
[ORCH] ──► [NOTIF] ── send daily P&L summary ──► [CLIENT]
  │
  └──► [LOG] ── record daily summary sent
       │
       └──► return to MONITORING LOOP (next hourly poll)

━━━━━━━━━━━━━━━━━━━━━ PHASE 3: CLOSED ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Investment status = SOLD or EXPIRED
All scheduled jobs cancelled for this investment
Final log entry written
System idles until client starts a new investment (returns to Phase 1)
```

---

## 6. MCP (Model Context Protocol) Integration

The system uses MCP to decouple all agents from external services. Agents never call Alpha Vantage, email providers, or the database directly — they call MCP tools. The MCP servers own all integration logic, retry handling, and credentials.

### 6.1 MCP Server Overview

| MCP Server | Used By | Tools Exposed |
|------------|---------|---------------|
| `market-data-mcp` | `DATA` agent | `get_stock_quote`, `get_historical_data` |
| `notifications-mcp` | `NOTIF` agent | `send_email`, `send_sms`, `send_push` |
| `persistence-mcp` | `LOG`, `INV`, `ORCH` agents | `log_event`, `create_investment`, `get_investment`, `update_investment_status`, `get_alert_state`, `update_alert_state`, `get_price_cache`, `set_price_cache` |

---

### 6.2 Market Data MCP Server (`market-data-mcp`)

**Owns:** All Alpha Vantage communication, API key management, 25 calls/day counter, retry logic.

**Tools:**

```python
tool: get_stock_quote(ticker: str) -> {
    "ticker": str,
    "price": float,
    "change": float,
    "change_pct": float,
    "fetched_at": datetime
}
# Calls: TIME_SERIES_GLOBAL_QUOTE
# Enforces: daily call counter, rejects if >= 25 calls used today
# Retry: once after 60s on HTTP 429

tool: get_historical_data(ticker: str, days: int = 90) -> {
    "ticker": str,
    "daily_data": [
        {"date": str, "open": float, "high": float,
         "low": float, "close": float, "volume": int}
    ]
}
# Calls: TIME_SERIES_DAILY_ADJUSTED
# Enforces: daily call counter
```

**What this removes from the DATA agent:**
- No HTTP client code in the agent
- No API key stored in agent config
- No retry logic in the agent
- No call counter passed around between agents — the MCP server owns it internally

**Swap benefit:** When upgrading from Alpha Vantage free tier to Polygon.io, only this MCP server changes. All 10 agents are untouched.

---

### 6.3 Notifications MCP Server (`notifications-mcp`)

**Owns:** Email (SendGrid/SMTP), SMS (Twilio), Push (Firebase) integrations and credentials.

**Tools:**

```python
tool: send_email(to: str, subject: str, body_html: str) -> {
    "delivered": bool,
    "message_id": str
}

tool: send_sms(phone: str, message: str) -> {
    "delivered": bool,
    "message_id": str
}

tool: send_push(device_id: str, title: str, body: str) -> {
    "delivered": bool,
    "message_id": str
}
```

**What this removes from the NOTIF agent:**
- No provider SDK imports in the agent
- No credentials in agent config
- Retry logic for delivery failures lives in the MCP server

**Extensibility benefit:** Adding WhatsApp or Telegram later = add one tool to this MCP server. The NOTIF agent gains the capability without any code change.

---

### 6.4 Persistence MCP Server (`persistence-mcp`)

**Owns:** SQLite database (or any other storage), all schema knowledge, query logic.

**Tools:**

```python
tool: create_investment(
    ticker, purchase_price, shares, total_invested,
    remaining_cash, investment_date, deadline_date
) -> { "investment_id": str }

tool: get_investment(investment_id: str) -> { ...investment record... }

tool: update_investment_status(investment_id: str, status: str) -> { "updated": bool }

tool: get_alert_state(investment_id: str) -> {
    "loss_alert_armed": bool,
    "upside_alert_armed": bool,
    "last_alerted_profit_pct": float,
    "last_poll_profit_pct": float,
    "api_call_count": int,
    "api_call_reset_date": date
}

tool: update_alert_state(investment_id: str, state: dict) -> { "updated": bool }

tool: log_event(
    investment_id: str, event_type: str,
    data: dict, timestamp: datetime
) -> { "log_id": str }

tool: get_price_cache(ticker: str, days: int) -> { "daily_data": [...] }

tool: set_price_cache(ticker: str, daily_data: list) -> { "cached": bool }
```

**What this removes from agents:**
- No SQL or file I/O in any agent
- Schema changes (e.g. adding a field) only require updating this MCP server
- Storage backend (SQLite → Postgres) can be swapped with zero agent changes

---

### 6.5 Updated Architecture with MCP Layer

```
┌────────────────────────────────────────────────────────────────┐
│                        AGENT LAYER                             │
│                                                                │
│   ORCH  ◄──► DATA  ◄──► RANK  ◄──► INV  ◄──► PROFIT          │
│   ALERT ◄──► SIT   ◄──► NOTIF ◄──► LOG  ◄──► SCHED           │
│                                                                │
│   (agents call MCP tools — no direct external calls)          │
└──────────────────────┬─────────────────────────────┬──────────┘
                       │ MCP tool calls               │
          ┌────────────▼──────────┐    ┌──────────────▼──────────┐
          │   market-data-mcp     │    │   notifications-mcp      │
          │                       │    │                          │
          │  get_stock_quote()    │    │  send_email()            │
          │  get_historical_data()│    │  send_sms()              │
          └────────────┬──────────┘    │  send_push()             │
                       │               └──────────────┬───────────┘
                       ▼                              ▼
               Alpha Vantage API          Email / SMS / Push providers
          ┌──────────────────────────────────────────────────────┐
          │                  persistence-mcp                     │
          │                                                      │
          │  create_investment()   get_investment()              │
          │  update_alert_state()  log_event()                   │
          │  get_price_cache()     set_price_cache()             │
          └──────────────────────────┬───────────────────────────┘
                                     ▼
                               SQLite / Database
```

---

### 6.6 How MCP Changes the DATA Agent

**Before MCP** — DATA agent owns all integration complexity:
```python
# DATA agent had to manage all of this:
import requests
api_key = os.getenv("ALPHA_VANTAGE_KEY")
call_count = load_from_db("api_call_count")
if call_count >= 25:
    raise LimitError()
response = requests.get(f"https://...&apikey={api_key}")
if response.status_code == 429:
    time.sleep(60)
    response = requests.get(...)  # retry
increment_call_count()
parse_response(response.json())
```

**After MCP** — DATA agent is pure business logic:
```python
# DATA agent now simply calls:
result = mcp.call("market-data-mcp", "get_stock_quote", {"ticker": "AAPL"})
if result.status == "ERROR" and result.error == "DAILY_LIMIT_REACHED":
    orch.log_and_skip_poll()
```

All the complexity moved into the MCP server where it belongs.

---

## 7. Agent Communication Protocol

All agents communicate via **structured message passing** through the Orchestrator. No agent calls another agent directly.

```
[Agent A] ──► output payload ──► [ORCH] ──► input payload ──► [Agent B]
```

Each message has a standard envelope:

```python
{
    "from_agent": "DATA",
    "to_agent": "ORCH",
    "investment_id": "inv_abc123",
    "timestamp": "2026-06-10T14:32:00Z",
    "status": "SUCCESS" | "ERROR",
    "error_message": str | None,
    "payload": { ... }   # agent-specific data
}
```

This means:
- Every agent is stateless — state lives in `ORCH` and the data store
- Any agent can be retried or replaced without affecting others
- All inter-agent traffic is observable and loggable

---

## 8. Error Handling

| Scenario | Handling |
|----------|----------|
| Alpha Vantage returns error | `DATA` retries once after 60s; if still fails, `ORCH` skips poll and logs `API_ERROR` |
| Daily API limit reached | `ORCH` skips hourly polls for rest of day; daily summary still sent using cached price |
| Notification delivery fails | `NOTIF` retries up to 3 times with exponential backoff; logs failure |
| Client never responds to alert | `ORCH` treats as `CONTINUE` after 4 hours; logs `NO_RESPONSE` |
| `SIT` agent fails | `ORCH` sends alert without recommendation, notes "Analysis unavailable" in message |
| `INV` validation fails | `ORCH` returns error to client and re-prompts for budget |

---

## 9. Implementation Sequence

Build and test agents in this order — each one is independently testable before the next is added:

```
Step 1:  DATA agent          — API integration, rate limit logic, retry
Step 2:  PROFIT agent        — Pure math, no dependencies, easy to unit test
Step 3:  RANK agent          — Scoring logic, test against known stock data
Step 4:  INV agent           — Budget validation, share calculation
Step 5:  ALERT agent         — Rules engine, test all alert conditions with mock data
Step 6:  SIT agent           — Technical analysis, test each mode independently
Step 7:  NOTIF agent         — Template rendering, channel dispatch (mock channels first)
Step 8:  LOG agent           — Persistence, append-only guarantees
Step 9:  SCHED agent         — Timer logic, market hours check
Step 10: ORCH agent          — Wire everything together, integration tests
Step 11: End-to-end test     — Full simulation with mock API + mock client decisions
```

---

## 10. Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Agent communication | All through Orchestrator | Keeps agents stateless and independently testable |
| External service integration | Via MCP servers | Decouples agents from APIs; swap providers without touching agent code |
| API call tracking | Owned by `market-data-mcp` server | Single source of truth; no counter passed between agents |
| State storage | Centralised in ORCH + `persistence-mcp` | Avoids distributed state synchronisation complexity |
| Price history for SIT | Cached via `persistence-mcp` `set_price_cache` | Avoids extra API calls when SIT needs historical data |
| No-response timeout | 4 hours | Balances responsiveness with not spamming the client |
| Alert minimum jump | 0.25% above `last_alerted_profit_pct` | Prevents noise from micro-movements above 3% |
