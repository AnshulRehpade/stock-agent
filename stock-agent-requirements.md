# Stock Agent - Functional Requirements Document

## 1. Overview

The Stock Agent is an automated assistant designed to help clients achieve a defined profit target on stock investments within a specified time window. It monitors stock performance, sends timely alerts, and supports client decision-making when key profit thresholds are reached.

---

## 2. Business Objective

The client wants to achieve a **minimum 5% profit** on a stock investment within **30 days** of the initial purchase date.

---

## 3. Functional Requirements

### 3.1 Investment Tracking

- The system shall record the stock purchase date and purchase price at the time of investment.
- The system shall continuously track the current market price of the invested stock.
- The system shall calculate the real-time profit/loss percentage based on the purchase price.

**Formula:**
```
Profit % = ((Current Price - Purchase Price) / Purchase Price) × 100
```

---

### 3.2 Daily Price Fluctuation Reminders

- The system shall send the client a **daily notification** summarizing:
  - Current stock price
  - Percentage change from the previous day's closing price
  - Current profit/loss percentage since the date of investment
  - Number of days remaining within the 30-day window

- Notifications shall be delivered at a consistent time each day (e.g., market close or a user-configured time).

---

### 3.3 Profit Threshold Alert (3% Trigger)

- The system shall monitor the stock price in real time (or at defined polling intervals).
- **When the profit crosses 3% at any point**, the system shall immediately send an alert to the client with the following:
  - Current profit percentage
  - Current stock price
  - Days remaining in the 30-day window
  - A prompt asking the client to choose one of the following options:

    | Option | Description |
    |--------|-------------|
    | **Continue** | Hold the stock and continue targeting the 5% profit goal |
    | **Sell Now** | Sell the stock immediately at the current profit percentage |

- The alert shall await or record the client's response before taking any action.
- If the client selects **Sell Now**, the system shall trigger the sell order at the current market price.
- If the client selects **Continue**, the system shall resume monitoring and continue sending daily updates.

---

### 3.4 Goal Achievement Notification (5% Target)

- When the stock profit reaches or exceeds **5%**, the system shall notify the client immediately with:
  - Achieved profit percentage
  - Current stock price
  - A prompt to confirm selling the stock or continuing to hold

---

### 3.5 30-Day Deadline Handling

- If the 30-day window expires and the **5% target has not been reached**, the system shall:
  - Notify the client that the investment window has closed
  - Display the current profit/loss percentage
  - Prompt the client to decide whether to sell or extend monitoring

---

## 4. Non-Functional Requirements

| # | Requirement |
|---|-------------|
| NFR-1 | Notifications must be delivered within 5 minutes of a threshold being crossed |
| NFR-2 | The system must support real-time or near-real-time stock price data (max 15-min delay) |
| NFR-3 | All client decisions (continue/sell) must be logged with a timestamp |
| NFR-4 | The system must be available 24/7 during market hours |
| NFR-5 | Notification channels shall be configurable (email, SMS, push notification) |

---

## 5. User Interaction Flow

```
Client invests in a stock
        │
        ▼
System records purchase price & date
        │
        ▼
Daily reminders sent (price, P&L %, days remaining)
        │
        ▼
Has profit crossed 3%? ──── No ──► Continue monitoring
        │
       Yes
        │
        ▼
Send 3% threshold alert with Continue / Sell Now options
        │
   ┌────┴─────┐
   │          │
Sell Now   Continue
   │          │
   ▼          ▼
Execute    Monitor until 5% goal or 30-day deadline
sell order
```

---

## 6. Assumptions

- The client has already placed the initial stock purchase before the agent begins monitoring.
- Stock price data is sourced from a reliable market data provider.
- The client has pre-configured their preferred notification channel.
- "Sell" actions initiated by the agent are subject to the client's brokerage platform capabilities.

---

## 7. Out of Scope

- Automatic selling without explicit client confirmation
- Portfolio management across multiple stocks simultaneously (v1)
- Tax calculation or financial advisory services
- Stock selection or buy recommendations

---

## 8. Glossary

| Term | Definition |
|------|------------|
| Purchase Price | The price at which the client bought the stock |
| Profit % | Percentage gain relative to the purchase price |
| 3% Threshold | The intermediate profit level that triggers a hold/sell decision prompt |
| 5% Target | The client's desired profit goal |
| 30-Day Window | The maximum duration for achieving the profit target |
| Daily Reminder | A scheduled notification sent each day with stock performance data |
