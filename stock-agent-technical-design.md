# Stock Agent — Technical Design Document

**Version:** 1.3  
**Date:** June 5, 2026  
**Status:** Draft

---

## 1. System Overview

The Stock Agent is a two-phase intelligent assistant:

1. **Pre-Investment Phase** — Analyses historical stock data to surface the top 5 performing stocks, helping the client make an informed investment decision.
2. **Post-Investment Phase** — Monitors the selected stock continuously, sends daily updates, fires threshold-based alerts, and surfaces sell/continue decisions to the client.

The system is scoped to support **a single active investment** at any given time.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Stock Agent                          │
│                                                             │
│  ┌──────────────┐    ┌───────────────┐    ┌─────────────┐  │
│  │  Stock        │    │  Investment   │    │  Monitoring │  │
│  │  Analyser    │───▶│  Manager      │───▶│  Engine     │  │
│  │  (Pre-Buy)   │    │  (Budget +    │    │  (Post-Buy) │  │
│  └──────────────┘    │   Selection)  │    └─────────────┘  │
│                      └───────────────┘           │         │
│                                                  ▼         │
│                                       ┌─────────────────┐  │
│                                       │  Notification   │  │
│                                       │  Service        │  │
│                                       └─────────────────┘  │
└─────────────────────────────────────────────────────────────┘
         │                                        │
         ▼                                        ▼
  Market Data Provider                    Client (Email/SMS/Push)
```

---

## 3. Phase 1 — Stock Selection & Analysis

### 3.1 Top 5 Stock Recommendation Engine

Before the client commits to an investment, the agent shall analyse historical market data and present a ranked list of the top 5 stocks most suitable for a short-term 5% profit target within 30 days.

#### 3.1.1 Data Source — Alpha Vantage (Free Tier)

All market data is fetched from the **Alpha Vantage API (free tier)**.

| Constraint | Detail |
|------------|--------|
| API call limit | 25 calls/day |
| Usage for Top 5 analysis | 1 call per stock (end-of-day daily adjusted data) = 5 calls for Top 5 |
| Usage for monitoring | 1 call/hour per active stock during market hours |
| Endpoint used (analysis) | `TIME_SERIES_DAILY_ADJUSTED` |
| Endpoint used (monitoring) | `GLOBAL_QUOTE` (latest price, 1 call per poll) |

> **Note:** With 25 calls/day total and monitoring polling every 60 minutes across ~7 market hours, the monitoring phase consumes ~7 calls/day, leaving sufficient quota for the daily Top 5 refresh and analysis.

#### 3.1.2 Data Inputs

| Input | Description |
|-------|-------------|
| Historical price data | Minimum 90 days of end-of-day OHLCV (Open, High, Low, Close, Volume) data |
| Market sector data | Sector classification for each stock |
| Volatility metrics | Standard deviation of daily returns |

#### 3.1.3 Ranking Criteria

The goal is a **5% profit within 30 days** — a short-term, momentum-driven target. The ranking criteria are specifically chosen to favour stocks that have recently shown strong upward movement, are easy to trade (liquid), and carry manageable risk. Here is each metric, how it is calculated, and why it was selected:

---

**1. 30-Day Momentum (Weight: 30%)**

_What it measures:_ How much the stock's price has risen over the past 30 days.

_How it is calculated:_
```
Momentum = ((Close_today - Close_30d_ago) / Close_30d_ago) × 100
```

_Why it was chosen:_ This is the most direct predictor for a 30-day profit goal. A stock already moving upward over the past month is more likely to continue that trend in the near term. It directly answers "has this stock been doing well recently?" — which is the first thing a short-term investor needs to know.

---

**2. Upside Volatility (Weight: 25%)**

_What it measures:_ How often the stock registers a daily gain of ≥ 1% — i.e., frequency of meaningful positive moves.

_How it is calculated:_
```
Upside Volatility Score = (Count of days with daily return ≥ 1% over last 30 days) / 30
```

_Why it was chosen:_ Raw volatility (standard deviation) penalises both upward and downward swings equally — that would hurt good candidates. By measuring only upside volatility, the system rewards stocks that frequently make meaningful positive moves without penalising them for their overall price range. For a 5% target in 30 days, the stock needs regular positive days, not just one big spike.

---

**3. Average Daily Volume (Weight: 20%)**

_What it measures:_ The average number of shares traded per day over the last 30 days.

_How it is calculated:_
```
Avg Daily Volume = Sum of daily volumes over last 30 days / 30
```
_Normalised_ across the candidate set to a 0–100 score (highest volume = 100).

_Why it was chosen:_ High volume means the stock is liquid — the client can sell at any point without difficulty finding a buyer. Low-volume stocks can be hard to exit at a fair price, which is a risk when the client needs to sell quickly upon hitting their profit target. Volume also confirms that price moves are backed by genuine market activity rather than thin trading.

---

**4. 90-Day Trend Strength (Weight: 15%)**

_What it measures:_ How consistently the stock has been trending upward over the past 90 days, using a linear regression slope on daily closing prices.

_How it is calculated:_
```
Fit a linear regression line to the last 90 days of closing prices.
Trend Strength = slope of the regression line (normalised)
R² (coefficient of determination) is used to confirm the trend is steady, not erratic.
```

_Why it was chosen:_ A stock can have a good 30-day run that is actually a recovery spike after a big drop. The 90-day trend shows whether the upward movement is part of a sustained, longer-term pattern or just a short-term bounce. Stocks with a steady 90-day upward trend are more reliable candidates for continued gains.

---

**5. Risk-Adjusted Return (Weight: 10%)**

_What it measures:_ How much return the stock has delivered per unit of risk taken, over the last 90 days — a simplified Sharpe ratio.

_How it is calculated:_
```
Daily Returns = (Close_day_n - Close_day_n-1) / Close_day_n-1
Avg Daily Return = Mean of daily returns over 90 days
Daily StdDev = Standard deviation of daily returns over 90 days
Risk-Adjusted Score = Avg Daily Return / Daily StdDev
```

_Why it was chosen:_ Two stocks might have the same 30-day momentum, but one achieved it with wild day-to-day swings while the other climbed steadily. The risk-adjusted score separates them — it identifies stocks where gains come with lower variance. This protects the client from selecting a stock that "looks good on average" but could drop sharply on any given day.

---

**Composite Score Formula:**
```
Score = (Momentum × 0.30)
      + (Upside Volatility × 0.25)
      + (Avg Daily Volume Score × 0.20)
      + (Trend Strength × 0.15)
      + (Risk-Adjusted Return × 0.10)
```

All metrics are normalised to a 0–100 scale before weighting so that no single metric dominates due to its unit of measurement.

#### 3.1.4 Output — Top 5 Stock Report

For each of the top 5 stocks, the agent shall present:

```
┌─────────────────────────────────────────────────┐
│  Rank #1 — TICKER (Company Name)                │
│                                                 │
│  Current Price:        $XXX.XX                  │
│  30-Day Return:        +X.X%                    │
│  Avg Daily Volume:     X,XXX,XXX                │
│  Volatility (30d):     X.X%                     │
│  Analyst Summary:      [Brief rationale]        │
│                                                 │
│  ► Select this stock                            │
└─────────────────────────────────────────────────┘
```

---

### 3.2 Stock Selection & Budget Entry

After reviewing the top 5 report, the client shall:

1. **Select one stock** from the ranked list (or optionally enter a custom ticker)
2. **Enter an investment budget** in their local currency

#### 3.2.1 Budget Validation Rules

- Budget must be a positive numeric value
- Budget must be sufficient to purchase at least 1 share of the selected stock
- System shall calculate the **number of shares** the budget can buy:

```
Shares = floor(Budget / Current Market Price)
Remaining Cash = Budget - (Shares × Current Market Price)
```

- The client is shown a confirmation summary before the investment is recorded:

```
You are investing $X,XXX in TICKER
  Shares to purchase: XX
  Purchase price per share: $XXX.XX
  Total invested: $X,XXX.XX
  Remaining uninvested cash: $XX.XX

[Confirm Investment]  [Cancel]
```

---

## 4. Phase 2 — Investment Monitoring

Once the client confirms the investment, the Monitoring Engine activates.

### 4.1 Investment Record

The system stores the following at investment time:

| Field | Description |
|-------|-------------|
| `ticker` | Stock symbol |
| `purchase_price` | Price per share at time of investment |
| `shares` | Number of shares purchased |
| `total_invested` | `shares × purchase_price` |
| `investment_date` | Date of investment (Day 0) |
| `deadline_date` | `investment_date + 30 days` |
| `status` | `ACTIVE` / `SOLD` / `EXPIRED` |

### 4.2 Profit Calculation

```
Current Value   = shares × current_price
Profit/Loss ($) = Current Value - total_invested
Profit/Loss (%) = ((Current Value - total_invested) / total_invested) × 100
```

---

### 4.3 Daily Price Fluctuation Reminders

- A scheduled job runs **once per day** (configurable time, default: 4:00 PM local market close)
- The notification includes:

```
📈 Daily Stock Update — Day X of 30

  Stock:              TICKER
  Today's Close:      $XXX.XX
  Day Change:         +X.XX%
  Your Profit/Loss:   +X.XX% ($XXX.XX)
  Days Remaining:     XX days

  [View Full Report]
```

- Delivered via the client's configured notification channel (Email / SMS / Push)

---

### 4.4 Downside Alert — Stock Price Drop

The system shall monitor for negative price movement from the purchase price and alert the client in two distinct scenarios.

#### 4.4.1 Trigger Conditions

| Condition | Behaviour |
|-----------|-----------|
| Profit/Loss drops **below 0%** (any loss from purchase price) | Loss alert fired immediately |
| Loss is **less than 1%** from purchase price | Alert fired with loss details; client decides — no automatic recommendation |
| Loss is **more than 1%** from purchase price | System runs a situation analysis and includes a hold/sell recommendation |

#### 4.4.2 Situation Analysis (Loss > 1%)

When the loss is less than 1%, the system analyses recent price behaviour before sending the alert. This is a lightweight technical analysis using already-fetched data — no additional API calls are needed.

**Signals evaluated:**

| Signal | Method | Interpretation |
|--------|--------|----------------|
| Short-term trend | 5-day vs 10-day simple moving average (SMA) | SMA5 > SMA10 = recovering; SMA5 < SMA10 = continuing to fall |
| Recent momentum | Price change over last 3 days | Positive = stock stabilising or recovering |
| Volume on down days | Average volume on loss days vs gain days | Higher volume on losses = stronger selling pressure (bad sign) |
| Days remaining | Days left in 30-day window | More days remaining = more time to recover |

**Recommendation logic:**

```
IF (SMA5 > SMA10) AND (3-day momentum > 0) AND (days_remaining > 10):
    Recommendation = "HOLD — Short-term indicators suggest recovery. 
                      The dip appears minor and the trend may reverse."

ELSE IF (volume_on_down_days > volume_on_up_days) AND (days_remaining < 10):
    Recommendation = "CONSIDER SELLING — Selling pressure is elevated 
                      and limited time remains to recover."

ELSE:
    Recommendation = "MONITOR CLOSELY — Mixed signals. Watch the next 
                      1-2 days before deciding."
```

#### 4.4.3 Alert Content — Loss > 1% (with recommendation)

```
📉 Loss Alert — Action May Be Required

  Your investment in TICKER is currently at a loss.

  Purchase Price:     $XXX.XX
  Current Price:      $XXX.XX
  Current Loss:       -X.XX% (-$XX.XX)
  Days Remaining:     XX days

  📊 System Analysis:
  ─────────────────────────────────────────────
  5-Day Trend:        Recovering / Declining
  3-Day Momentum:     Positive / Negative
  Selling Pressure:   Low / High
  ─────────────────────────────────────────────
  Recommendation:     [HOLD / CONSIDER SELLING / MONITOR CLOSELY]
  Reason:             [Brief rationale from analysis]

  [I'll Hold]  [I Want to Sell]

  Note: This is a system analysis, not financial advice.
```

#### 4.4.4 Alert Content — Loss < 1% (without recommendation)

```
⚠️ Loss Alert

  Your investment in TICKER has dropped slightly below your purchase price.

  Purchase Price:     $XXX.XX
  Current Price:      $XXX.XX
  Current Loss:       -X.XX% (-$XX.XX)
  Days Remaining:     XX days

  [I'll Hold]  [I Want to Sell]
```

#### 4.4.5 Alert Frequency for Loss Alerts

- The loss alert fires **once per downward crossing** of 0% from positive territory
- It does **not** fire repeatedly on each poll while the stock remains in loss territory
- It re-fires if the stock recovers above 0% and then drops below 0% again (new downward crossing)

---

### 4.5 3% Profit Threshold Alert

#### 4.5.1 Trigger Logic

The system shall fire the 3% alert under the following conditions:

| Condition | Behaviour |
|-----------|-----------|
| Profit crosses **above 3%** for the first time | Alert fired |
| Profit is above 3% and has **increased by ≥ 0.25% since the last alert** (moving toward 5%) | Alert fired to keep client informed of meaningful upward progress |
| Profit is above 3% but increase since last alert is **less than 0.25%** | No alert sent — change too small to warrant notification |
| Profit is above 3% but **unchanged from the previous poll** | No alert sent |
| Client responds **"Continue"** → profit dips below 3% → profit rises back to ≥ 3% | Alert fired again (0.25% jump rule resets) |
| Client does **not respond** to the alert | Monitoring continues; alert re-fires the next time profit rises by ≥ 0.25% above the last alerted level |
| Client responds **"Sell Now"** | Trade Notifier sends manual sell instruction; monitoring stops |

**0.25% minimum jump rule explained:**
The last alerted profit % is stored after each alert fires. A new alert only fires when:
```
current_profit % - last_alerted_profit % >= 0.25%
```
This prevents the system from repeatedly alerting on noise like +3.01%, +3.03%, +3.06% — the client only hears from the system when there is a meaningful step forward toward the 5% target.

#### 4.5.2 Alert State Machine

```
         ┌──────────────────────────────────────┐
         │           MONITORING                 │
         │     (profit < 3%, watching)          │
         └──────────────┬───────────────────────┘
                        │ profit crosses ≥ 3%
                        ▼
         ┌──────────────────────────────────────┐
         │         ALERT SENT                   │
         │   (last_alerted_profit % stored)     │
         └──────┬───────────────────┬───────────┘
                │                   │
          "Sell Now"      "Continue", No Response,
                │          or profit still rising
                ▼                   │
         ┌──────────┐               ▼
         │  TRADE   │  ┌────────────────────────────────────┐
         │ NOTIFIER │  │  MONITORING RESUMED                │
         └──────────┘  │                                    │
                       │  Profit rises ≥ 0.25% above        │
                       │  last_alerted_profit % →           │
                       │  new alert sent, value updated      │
                       │                                    │
                       │  Profit rises < 0.25% → no alert   │
                       │                                    │
                       │  Profit unchanged → no alert       │
                       │                                    │
                       │  Profit dips below 3% →            │
                       │  threshold resets,                 │
                       │  last_alerted_profit % cleared     │
                       └────────────────────────────────────┘
```

#### 4.5.3 Alert Content

```
⚠️ Profit Update — TICKER is moving toward your 5% target!

  Current Price:      $XXX.XX
  Your Profit:        +X.XX% ($XXX.XX)
  Change since last:  +X.XX% ↑
  Days Remaining:     XX days
  Target:             5.00%

  What would you like to do?

  [✅ Continue — Keep holding towards 5% target]
  [💰 Sell Now — Lock in X.XX% profit today]

  Note: If you don't respond, monitoring will continue automatically.
```

---

### 4.6 5% Target Achievement Notification & Post-Target Recommendation

When profit reaches or exceeds 5%, the system does two things simultaneously:
1. Sends the achievement notification to the client
2. Runs a post-target analysis to recommend whether selling now is optimal or if the stock shows potential for further gains in the near term

#### 4.6.1 Post-Target Analysis

The system analyses the following signals using already-fetched price data:

| Signal | Method | Interpretation |
|--------|--------|----------------|
| Current momentum | Profit % gain over the last 3 days | Still accelerating = may go higher |
| Distance to recent high | Compare current price to 30-day high | If close to the high, upside may be limited |
| Volume trend | Compare today's volume to 5-day average | Higher volume on up days = strong buying interest |
| Days remaining | Days left in 30-day window | More remaining time = more opportunity |
| Rate of approach to target | How quickly the stock moved from 3% to 5% | Fast move = possible correction ahead |

**Recommendation logic:**

```
IF (3-day momentum > 1%) AND (volume_today > avg_volume_5d) AND (days_remaining > 7):
    Recommendation = "HOLD FOR MORE — The stock is still gaining momentum 
                      with strong volume. It may deliver better returns 
                      in the coming days."

ELSE IF (current_price >= 0.98 × high_30d) AND (3-day momentum < 0.5%):
    Recommendation = "GOOD TIME TO SELL — The stock is near its 30-day 
                      high and momentum is slowing. Locking in profit 
                      now may be optimal."

ELSE:
    Recommendation = "NEUTRAL — The stock has hit your target. Selling 
                      now locks in a solid gain. Holding carries some 
                      risk but may yield more if momentum continues."
```

#### 4.6.2 Notification Content

```
🎯 Profit Target Reached!

  TICKER has hit your 5% profit goal!

  Current Price:      $XXX.XX
  Your Profit:        +X.XX% ($XXX.XX)
  Days Remaining:     XX days

  📊 Should you sell now or hold for more?
  ─────────────────────────────────────────────
  3-Day Momentum:     +X.XX% (Strong / Slowing)
  Volume Today:       Above / Below 5-day avg
  Distance to 30d High: X.XX% away
  ─────────────────────────────────────────────
  Recommendation:     [HOLD FOR MORE / GOOD TIME TO SELL / NEUTRAL]
  Reason:             [Brief rationale]

  [Sell Now — Lock in X.XX% profit]  [Continue Holding]

  Note: This is a system analysis, not financial advice.
```

- If client selects **Sell Now** → Trade Notifier sends manual sell instruction; monitoring ends
- If client selects **Continue Holding** → monitoring continues; 3% upward alert remains active

---

### 4.7 30-Day Deadline Handling & End-of-Period Analysis

On Day 30, regardless of whether the 5% target was reached, the system runs an end-of-period stock analysis and includes a recommendation in the deadline notification.

#### 4.7.1 End-of-Period Analysis (5% Goal Not Achieved)

When the 30-day window closes without reaching 5%, the system evaluates whether the goal is still achievable in the near term or whether the stock has stalled.

**Signals evaluated:**

| Signal | Method | Interpretation |
|--------|--------|----------------|
| Current profit % | Distance from 5% target | e.g., at 4.2% = close; at 1.5% = far |
| Recent trajectory | Profit change over last 7 days | Trending up = recovery possible |
| Rate needed | % gain per day needed to hit 5% in next 5–7 days | If unrealistically high = unlikely |
| Recent volume | Volume trend over last 5 days | Rising volume on up days = positive signal |
| 30-day price behaviour | Number of days stock gained vs lost | Gives sense of overall momentum character |

**Recommendation logic:**

```
remaining_gap = 5.0% - current_profit_%
days_of_trend = count of last 7 days where price increased
rate_needed_per_day = remaining_gap / 5  (assuming 5-day outlook)

IF (remaining_gap <= 1.0%) AND (days_of_trend >= 4) AND (rate_needed_per_day <= 0.3%):
    Recommendation = "CLOSE TO TARGET — You are X.XX% away from your goal. 
                      The stock has been trending upward and may reach 5% 
                      within the next 3–5 days. Consider extending monitoring."

ELSE IF (remaining_gap > 2.5%) OR (days_of_trend <= 2):
    Recommendation = "UNLIKELY TO REACH TARGET SOON — The stock is X.XX% 
                      away and recent momentum is weak. Selling now locks in 
                      your current gain of X.XX%."

ELSE:
    Recommendation = "UNCERTAIN — Mixed signals. Extending monitoring carries 
                      moderate risk. Current gain is X.XX%."
```

#### 4.7.2 Notification Content

```
⏰ 30-Day Investment Window Closed

  Your monitoring window for TICKER has ended.

  Purchase Price:     $XXX.XX
  Current Price:      $XXX.XX
  Current Profit:     +X.XX% ($XX.XX)
  Target:             5.00%
  Remaining Gap:      X.XX%

  📊 End-of-Period Analysis:
  ─────────────────────────────────────────────
  7-Day Trend:        X of 7 days positive
  Daily Rate Needed:  X.XX%/day (to hit 5% in 5 days)
  Volume Trend:       Rising / Flat / Declining
  ─────────────────────────────────────────────
  Recommendation:     [CLOSE TO TARGET / UNLIKELY / UNCERTAIN]
  Reason:             [Brief rationale]

  [Sell Now — Lock in X.XX%]  [Extend Monitoring (new 30-day window)]

  Note: This is a system analysis, not financial advice.
```

- **Sell Now** → Trade Notifier sends manual sell instruction; investment record closed
- **Extend Monitoring** → A new 30-day window is opened from today; end-of-period analysis restarts at Day 30

---

## 5. System Components

### 5.1 Component Breakdown

| Component | Responsibility |
|-----------|----------------|
| **Stock Analyser** | Fetches 90-day end-of-day historical data via Alpha Vantage, computes ranking metrics, generates top-5 report |
| **Investment Manager** | Records investment details, validates budget, computes share quantity |
| **Price Feed** | Polls Alpha Vantage `GLOBAL_QUOTE` endpoint every **60 minutes** during market hours (conserves free-tier quota) |
| **Profit Calculator** | Computes current profit % from latest polled price and stored investment record |
| **Alert Engine** | Manages threshold state machines for 3% upside alerts and 0% downside alerts; tracks price change between polls |
| **Situation Analyser** | Runs lightweight technical analysis (SMA, momentum, volume) to generate hold/sell recommendations for: loss < 1% alerts, 5% target notifications, and end-of-period reports |
| **Scheduler** | Runs daily summary job at configured time (default: 4:00 PM market close) |
| **Notification Service** | Dispatches alerts via Email / SMS / Push; records delivery status |
| **Decision Logger** | Logs every client response (Continue / Sell Notification / No Response) with timestamp |
| **Trade Notifier** | Sends the client a formatted sell instruction with current price; client executes manually via their broker |

---

### 5.2 Data Flow

```
Market Data Provider (Alpha Vantage Free Tier)
        │
        │  GLOBAL_QUOTE — every 60 min during market hours
        │  TIME_SERIES_DAILY_ADJUSTED — 1 call/stock for analysis
        ▼
   Price Feed ──────────────────────────────────────┐
        │                                            │
        ▼                                            ▼
Profit Calculator                            Stock Analyser
        │                                            │
        ▼                                            ▼
  Alert Engine                          Top 5 Report (pre-investment)
        │
        ├──► Profit unchanged since last poll ──► No alert (silent)
        │
        ├──► Daily Scheduler ──► Notification Service ──► Client
        │
        ├──► Profit < 0% (loss crossing) ──► Situation Analyser
        │         │                                  │
        │         │  (if loss < 1%)                  │
        │         │◄─── hold/sell recommendation ────┘
        │         ▼
        │    Notification Service ──► Client (loss alert + recommendation)
        │
        ├──► Profit ≥ 3% AND increased since last poll
        │         ▼
        │    Notification Service ──► Client (3% upward alert)
        │
        ├──► Profit ≥ 5% ──► Situation Analyser
        │         │                    │
        │         │◄─── hold/sell recommendation ────┘
        │         ▼
        │    Notification Service ──► Client (5% target + recommendation)
        │
        └──► Day 30 reached ──► Situation Analyser
                  │                      │
                  │◄─── end-of-period analysis ──────┘
                  ▼
             Notification Service ──► Client (deadline + analysis)
                        │
                        ▼
                 Decision Logger
                        │
              ┌─────────┴──────────┐
              │                    │
      "Sell Notification"    "Continue" /
              │               No Response
              ▼                    ▼
      Trade Notifier          Alert Engine
  (sends sell instruction    (threshold reset)
   to client; client
   executes manually)
```

---

## 6. Alpha Vantage API Integration

### 6.1 Endpoints Used

| Purpose | Endpoint | Calls/Day |
|---------|----------|-----------|
| Top 5 stock historical analysis | `TIME_SERIES_DAILY_ADJUSTED` | 1 per stock = 5 total |
| Monitoring — hourly price poll | `GLOBAL_QUOTE` | ~7 (1/hr × 7 market hours) |
| **Total daily usage** | | **~12 calls/day** (well within 25 limit) |

### 6.2 Sample API Calls

**Fetch historical daily data for analysis:**
```
GET https://www.alphavantage.co/query
  ?function=TIME_SERIES_DAILY_ADJUSTED
  &symbol=AAPL
  &outputsize=full
  &apikey=YOUR_API_KEY
```

**Fetch current price for monitoring:**
```
GET https://www.alphavantage.co/query
  ?function=GLOBAL_QUOTE
  &symbol=AAPL
  &apikey=YOUR_API_KEY

Response:
{
  "Global Quote": {
    "01. symbol": "AAPL",
    "05. price": "189.50",
    "09. change": "1.20",
    "10. change percent": "0.6382%"
  }
}
```

### 6.3 API Call Budget Management

| Scenario | Calls Used |
|----------|-----------|
| Morning Top 5 refresh (1 call × 5 stocks) | 5 |
| Monitoring polls (1 call/hr × 7 hrs) | 7 |
| Buffer for retries / errors | 13 |
| **Daily total limit** | **25** |

The system shall implement a **call counter** that tracks daily usage and pauses non-critical polling if the limit approaches 25 to prevent service interruption.

---

## 7. Internal API Design

### 7.1 Stock Analysis API

```
GET /api/stocks/top5
Response:
{
  "generated_at": "2026-06-05T16:00:00Z",
  "stocks": [
    {
      "rank": 1,
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "current_price": 189.50,
      "return_30d_pct": 6.2,
      "avg_daily_volume": 58000000,
      "volatility_30d_pct": 1.4,
      "score": 87.3,
      "rationale": "Strong momentum with consistent daily gains"
    },
    ...
  ]
}
```

### 7.2 Investment API

```
POST /api/investment
Body:
{
  "ticker": "AAPL",
  "budget": 5000.00,
  "purchase_price": 189.50
}

Response:
{
  "investment_id": "inv_abc123",
  "ticker": "AAPL",
  "shares": 26,
  "total_invested": 4927.00,
  "remaining_cash": 73.00,
  "investment_date": "2026-06-05",
  "deadline_date": "2026-07-05",
  "status": "ACTIVE"
}
```

### 7.3 Decision Callback API

```
POST /api/investment/{investment_id}/decision
Body:
{
  "alert_id": "alert_xyz789",
  "decision": "CONTINUE" | "SELL"
}

Response (CONTINUE):
{
  "decision": "CONTINUE",
  "acknowledged_at": "2026-06-10T14:32:00Z",
  "monitoring_status": "ACTIVE",
  "threshold_reset": true
}

Response (SELL):
{
  "decision": "SELL",
  "acknowledged_at": "2026-06-10T14:32:00Z",
  "sell_price_at_decision": 197.20,
  "profit_pct": 4.07,
  "profit_amount": 200.46,
  "instruction": "Please log into your brokerage and sell XX shares of TICKER at market price (~$197.20)."
}
```

> **Note:** The system does **not** place the sell order automatically. When the client selects "Sell Now", the system sends a formatted notification with the current price and share count. The client executes the sale manually through their brokerage. This is the confirmed v1 behaviour.

---

## 8. Notification Templates

### 8.1 Channels

| Channel | Trigger Types |
|---------|--------------|
| Email | Daily summary, threshold alerts, deadline alerts |
| SMS | Threshold alerts only (brief format) |
| Push Notification | All alerts (if mobile app is available) |

### 8.2 Non-Response Behaviour

If the client does not respond to a 3% alert:
- The system logs a `NO_RESPONSE` event with a timestamp
- Monitoring continues uninterrupted
- The alert threshold is **reset** — the next alert fires when profit dips below 3% and re-crosses upward again

---

## 9. Non-Functional Requirements

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-1 | Threshold alert delivery latency | ≤ 5 minutes after price poll detects crossing |
| NFR-2 | Stock price data polling interval | Every 60 minutes during market hours (Alpha Vantage free tier) |
| NFR-3 | Historical data coverage for analysis | Minimum 90 days (end-of-day) |
| NFR-4 | System uptime during market hours | 99.9% |
| NFR-5 | All client decisions logged | Timestamped, immutable audit log |
| NFR-6 | Alpha Vantage API calls/day | ≤ 25 (free tier limit) |
| NFR-7 | Top 5 report generation time | ≤ 10 seconds |
| NFR-8 | Sell execution | Manual by client via their brokerage (no automated orders) |

---

## 10. Constraints & Assumptions

- The client invests in **one stock at a time** (single active investment per session)
- The system does **not** place buy or sell orders autonomously — all trade execution is manual by the client via their brokerage
- Stock price data is sourced exclusively from **Alpha Vantage free tier** (25 API calls/day limit)
- Price polling frequency is **every 60 minutes** during market hours to stay within the free-tier limit
- Historical data for the Top 5 analysis uses **end-of-day data** (1 call per stock)
- Tax, regulatory, or compliance checks are **out of scope** for v1
- The client is assumed to have an active brokerage account for executing trades

---

## 11. Out of Scope (v1)

- Multi-stock portfolio management
- Automated buy or sell order placement via brokerage API
- Tax computation
- Financial advisory or investment recommendations beyond historical performance analysis
- Stop-loss automation
- Real-time (sub-minute) price streaming
- Options, ETFs, or derivative instruments

---

## 12. Full End-to-End Flow

```
1. Client requests top 5 stock recommendations
         │
         ▼
2. Stock Analyser fetches 90-day end-of-day data from Alpha Vantage
   (5 API calls — 1 per stock)
   → Ranks stocks by momentum, volume, volatility
   → Returns Top 5 report to client
         │
         ▼
3. Client reviews Top 5, selects a stock, enters budget
         │
         ▼
4. System calculates shares, shows confirmation summary
         │
         ▼
5. Client confirms → Investment record created (Day 0)
         │
         ▼
6. Monitoring Engine activates
   ├── Daily scheduler: sends daily P&L summary at market close
   └── Price Feed: polls Alpha Vantage GLOBAL_QUOTE every 60 minutes
         │
         ▼
7. Profit Calculator checks profit % after each poll
   ├── Profit < 3%  → continue monitoring (no alert)
   ├── Profit ≥ 3%  → 3% Alert fired
   │       ├── Client: "Sell Now" → Trade Notifier sends sell instruction → Client sells manually → DONE
   │       ├── Client: "Continue" → threshold reset, monitoring resumed
   │       └── No Response       → threshold reset, monitoring resumed
   ├── Profit ≥ 5%  → 5% Goal Alert fired
   │       ├── Client: "Sell Now" → Trade Notifier sends sell instruction → Client sells manually → DONE
   │       └── Client: "Continue" → monitoring continues
   └── Day 30 reached → Deadline Alert fired
           ├── Client: "Sell Now"  → Trade Notifier sends sell instruction → Client sells manually → DONE
           └── Client: "Extend"   → new 30-day window opened
```

---

## 13. Glossary

| Term | Definition |
|------|------------|
| OHLCV | Open, High, Low, Close, Volume — standard stock data format |
| Alpha Vantage | Market data provider used for all price data (free tier, 25 calls/day) |
| Momentum | Rate of price appreciation over a defined period |
| Volatility | Standard deviation of daily returns; indicates price variability |
| Purchase Price | Price per share at the time the client confirms the investment |
| Profit % | `((Current Value - Total Invested) / Total Invested) × 100` |
| 3% Threshold | Intermediate profit level; triggers a hold/sell decision prompt |
| 5% Target | The client's defined profit goal |
| 30-Day Window | Maximum monitoring duration from investment date |
| Threshold Reset | Rearming the 3% alert after a "Continue" response, no-response, or dip below 3% |
| Upward Crossing | When profit moves from below 3% to at or above 3% |
| Last Alerted Profit % | The profit % at which the most recent 3% zone alert was fired; used to enforce the 0.25% minimum jump rule |
| 0.25% Minimum Jump | The minimum increase in profit % above the last alerted level required to fire a new upward alert; prevents noise alerts on trivial gains |
| Situation Analyser | Component that performs lightweight technical analysis (SMA, momentum, volume) to generate hold/sell/monitor recommendations |
| Trade Notifier | System component that sends the client a manual sell instruction (no automated brokerage integration in v1) |
| GLOBAL_QUOTE | Alpha Vantage endpoint that returns the latest price for a single stock |
| TIME_SERIES_DAILY_ADJUSTED | Alpha Vantage endpoint that returns end-of-day historical OHLCV data |
| SMA | Simple Moving Average — average closing price over N days; used in situation analysis |
