# Stock Agent — AI-Powered Investment Monitoring System

A multi-agent stock monitoring system built with **LangGraph** and **Google Gemini** that helps achieve a 5% profit target within 30 days on a single stock investment.

The system ranks S&P 500 stocks, recommends the best investment, monitors prices autonomously, and sends intelligent email alerts with AI-generated recommendations.

## Architecture

```
User (API calls)
     │
     ▼
Flask REST API ──────────────────────────────────────────────────────
     │
     ▼
┌────────────────────────────────────────────────────────────────────┐
│                    LangGraph State Machine                         │
│                                                                    │
│  [DATA Agent]  →  [PROFIT Agent]  →  [ALERT Agent]                │
│  fetch price      calc P&L            rules engine                 │
│                                            │                       │
│                           ┌── NO_ACTION ───┤── alert triggered ──┐ │
│                           ↓                                      ↓ │
│                      [LOG Agent]                       [LLM Decision] │
│                           │                          Gemini 2.5 Flash │
│                          END                               │       │
│                                         ┌── suppress ──────┤       │
│                                         ↓                  ↓       │
│                                    [LOG Agent]      [NOTIF Agent]   │
│                                         │           send email      │
│                                        END               │         │
│                                                     [LOG Agent]    │
│                                                          │         │
│                                                         END        │
└────────────────────────────────────────────────────────────────────┘
```

## What Makes This an AI Agent (Not Just Automation)

The **Gemini LLM sits at the decision node** and:
- **Overrides** the rules engine when an alert is noise (e.g., trivial price movement)
- **Generates natural language** recommendations (not template if/else)
- **Reasons about context** — considers momentum, time remaining, and whether the move is meaningful
- **Provides auditable reasoning** for every decision (logged to database)

Example LLM output:
> *"The stock has made a significant move (+3.80%) towards the 5% target very early in the 30-day period, indicating strong positive momentum. Consider monitoring closely for profit-taking."*

## Agents

| Agent | Responsibility |
|-------|----------------|
| **DATA** | Fetches live prices (Alpha Vantage) and 90-day history (yfinance) |
| **RANK** | Scores and ranks stocks using 5 weighted metrics (momentum, volatility, volume, trend, risk-adjusted return) |
| **PROFIT** | Calculates real-time P&L with change-since-last-poll tracking |
| **ALERT** | Rules engine with state machine (3% upside, 5% target, loss alerts, 0.25% minimum jump rule) |
| **SIT** | Technical analysis (SMA, momentum, volume pressure) for hold/sell recommendations |
| **INV** | Budget validation, share calculation, investment record creation |
| **NOTIF** | Email formatting and delivery via Gmail SMTP |
| **LOG** | Append-only SQLite audit trail for all events and decisions |
| **ORCH** | LangGraph state machine + Gemini LLM at decision points |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| GET | `/top5` | Rank stocks and return top 5 recommendations |
| POST | `/invest` | Start monitoring `{"ticker": "AMD", "budget": 5000}` |
| GET | `/status` | Current investment status with live P&L |
| POST | `/decide` | Client decision `{"decision": "SELL"}` |
| POST | `/stop` | Stop monitoring |

## Tech Stack

| Layer | Technology |
|-------|------------|
| Agent Framework | LangGraph (state machine with conditional edges) |
| LLM | Google Gemini 2.5 Flash (free tier) |
| API | Flask |
| Data Sources | Alpha Vantage (live quotes) + yfinance (90-day OHLCV) |
| Notifications | Gmail SMTP (Python built-in smtplib) |
| Database | SQLite (append-only event log) |
| Deployment | Railway.app (cloud, 24/7) |
| Testing | pytest (222 tests, 0 failures) |

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/anshulrehpade/Stock_Analysis.git
cd Stock_Analysis
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your keys:
#   ALPHA_VANTAGE_API_KEY — from alphavantage.co
#   GOOGLE_API_KEY — from aistudio.google.com/apikey (free)
#   SMTP_USER, SMTP_PASSWORD — Gmail App Password
#   CLIENT_EMAIL — where alerts are sent
```

### 3. Run locally

```bash
python main.py
# API runs on http://localhost:5000
```

### 4. Test

```bash
# Get top 5 stocks
curl http://localhost:5000/top5

# Start monitoring
curl -X POST http://localhost:5000/invest -H "Content-Type: application/json" -d '{"ticker": "AMD", "budget": 5000}'

# Check status
curl http://localhost:5000/status
```

### 5. Run tests

```bash
pytest tests/ -k "not live" -v
# 222 tests, all passing
```

## Deploy to Railway

1. Push to GitHub
2. Connect repo in [railway.app](https://railway.app)
3. Add environment variables in Railway dashboard
4. Deploy — service runs 24/7 with automatic hourly monitoring

## Project Structure

```
Stock_Analysis/
├── agents/
│   ├── data_fetcher.py       — DATA agent (Alpha Vantage + yfinance)
│   ├── stock_ranker.py       — RANK agent (5-metric scoring)
│   ├── profit_calculator.py  — PROFIT agent (P&L calculation)
│   ├── alert_decision.py     — ALERT agent (rules engine)
│   ├── situation_analyser.py — SIT agent (technical analysis)
│   ├── investment_manager.py — INV agent (budget/shares)
│   ├── notification.py       — NOTIF agent (email delivery)
│   ├── decision_logger.py    — LOG agent (SQLite audit trail)
│   ├── orchestrator.py       — ORCH agent (LangGraph + Gemini)
│   └── scheduler.py          — SCHED agent (APScheduler)
├── tests/                    — 222 tests across all agents
├── data/                     — SQLite database files
├── main.py                   — Flask API entry point
├── requirements.txt
├── Procfile                  — Railway deployment config
├── nixpacks.toml             — Railway build config
└── .env.example              — Environment variable template
```

## Design Documents

- `stock-agent-requirements.md` — Functional requirements
- `stock-agent-technical-design.md` — High-level technical design
- `stock-agent-low-level-design.md` — Low-level design with agent orchestration

## How the Ranking Works

Stocks are scored on 5 weighted metrics optimised for a 30-day, 5% profit target:

| Metric | Weight | Why |
|--------|--------|-----|
| 30-day momentum | 30% | Direct predictor of near-term performance |
| Upside volatility | 25% | Frequency of meaningful positive daily moves |
| Avg daily volume | 20% | Liquidity — ability to exit at fair price |
| 90-day trend strength | 15% | Sustained uptrend vs short-term spike |
| Risk-adjusted return | 10% | Consistent gains vs erratic swings |

## Alert Rules

- **3% threshold** — fires when profit crosses 3%, then every +0.25% step toward 5%
- **5% target** — fires with AI recommendation (sell now vs hold for more)
- **Loss > 1%** — fires with AI situation analysis and hold/sell recommendation
- **30-day deadline** — fires with end-of-period analysis
- **LLM override** — Gemini can suppress noise alerts the rules engine would fire

## License

MIT
