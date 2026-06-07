# SIT Agent — Situation Analyser
#
# This agent has ONE job: look at recent price history and produce
# a plain-English hold/sell recommendation with supporting signals.
#
# It is called by the Orchestrator in three situations:
#   1. LOSS mode       — stock dropped > 1% below purchase price
#   2. TARGET mode     — stock hit the 5% profit goal
#   3. END_OF_PERIOD   — 30-day window has closed without hitting 5%
#
# Each mode asks a different question:
#   LOSS         → "Is this a temporary dip or a real problem?"
#   TARGET       → "Should the client sell now or hold for more?"
#   END_OF_PERIOD→ "Is 5% still achievable or should they cut losses/take gains?"
#
# All analysis uses already-fetched price data — no extra API calls.
# This keeps the system within the Alpha Vantage 25 calls/day limit.
#
# Tools used: numpy only (already installed for the RANK agent).
# No external API calls. Pure historical data analysis.

import numpy as np


# ─────────────────────────────────────────────────────────────────
#  SIGNAL CALCULATORS
#  Each function computes one technical indicator.
#  Used internally by analyse_situation().
# ─────────────────────────────────────────────────────────────────

def _sma(closes: list, window: int) -> float:
    """
    Simple Moving Average over the last `window` closing prices.

    SMA is the average closing price over N days.
    Comparing SMA5 vs SMA10:
      SMA5 > SMA10 → recent prices are ABOVE the 10-day average → recovering
      SMA5 < SMA10 → recent prices are BELOW the 10-day average → still falling

    This is one of the oldest and most widely used technical indicators.
    It smooths out day-to-day noise and shows the underlying trend direction.
    """
    if len(closes) < window:
        # Not enough data — use whatever we have
        return sum(closes) / len(closes)
    return sum(closes[-window:]) / window


def _momentum_nd(closes: list, n: int) -> float:
    """
    Price change over the last N days as a percentage.

    Measures recent short-term direction.
    Positive = stock has been rising over N days
    Negative = stock has been falling over N days

    We use 3-day and 7-day windows depending on the mode.
    """
    if len(closes) < n + 1:
        n = len(closes) - 1
    if n <= 0 or closes[-n - 1] == 0:
        return 0.0
    return ((closes[-1] - closes[-n - 1]) / closes[-n - 1]) * 100


def _volume_pressure(daily_data: list, window: int = 10) -> str:
    """
    Compares average volume on down days vs up days over the last `window` days.

    HIGH  → more volume on falling days = sellers dominating = bearish signal
    LOW   → more volume on rising days  = buyers dominating = bullish signal
    NEUTRAL → roughly equal

    Why this matters:
        Volume confirms price moves. A price drop on high volume means
        many investors are selling — the move is "real". A drop on low
        volume may just be a quiet day with no strong conviction.
    """
    recent = daily_data[-window:]
    if len(recent) < 2:
        return "NEUTRAL"

    up_vols   = [d["volume"] for i, d in enumerate(recent[1:], 1)
                 if d["close"] >= recent[i-1]["close"]]
    down_vols = [d["volume"] for i, d in enumerate(recent[1:], 1)
                 if d["close"] <  recent[i-1]["close"]]

    avg_up   = sum(up_vols)   / len(up_vols)   if up_vols   else 0
    avg_down = sum(down_vols) / len(down_vols) if down_vols else 0

    if avg_down > avg_up * 1.2:
        return "HIGH"     # sellers dominating
    elif avg_up > avg_down * 1.2:
        return "LOW"      # buyers dominating (counter-intuitive name — low selling pressure)
    return "NEUTRAL"


def _days_up_in_window(closes: list, window: int = 7) -> int:
    """
    Counts how many of the last `window` days had a positive price move.
    Used in END_OF_PERIOD mode to assess recent momentum.
    """
    recent = closes[-window - 1:] if len(closes) >= window + 1 else closes
    count = 0
    for i in range(1, len(recent)):
        if recent[i] > recent[i - 1]:
            count += 1
    return count


# ─────────────────────────────────────────────────────────────────
#  MAIN FUNCTION
# ─────────────────────────────────────────────────────────────────

def analyse_situation(
    mode: str,
    daily_data: list,
    profit_pct: float,
    days_remaining: int
) -> dict:
    """
    Analyses the current stock situation and returns a recommendation.

    Parameters:
        mode           : "LOSS" | "TARGET_REACHED" | "END_OF_PERIOD"
        daily_data     : List of daily OHLCV dicts (oldest first)
                         Each dict: {"date", "open", "high", "low",
                                     "close", "volume"}
        profit_pct     : Current profit/loss % from purchase price
        days_remaining : Days left in the 30-day monitoring window

    Returns:
        {
            "success": True,
            "mode": str,
            "recommendation": str,   # the action word
            "signals": {...},        # the raw signal values
            "reason": str,           # plain English explanation
            "confidence": str        # "HIGH" | "MEDIUM" | "LOW"
        }

    Interview explanation:
        "The SIT agent is called only when something meaningful happens.
        It never runs on a normal poll — only on a loss > 1%, a 5% target
        hit, or the 30-day deadline. This keeps it efficient. It uses only
        data already fetched by the DATA agent, so no additional API calls
        are consumed."
    """

    # ── Validate inputs ───────────────────────────────────────────
    valid_modes = {"LOSS", "TARGET_REACHED", "END_OF_PERIOD"}
    if mode not in valid_modes:
        return {
            "success": False,
            "error": f"Invalid mode '{mode}'. Must be one of {valid_modes}"
        }

    if not daily_data or len(daily_data) < 5:
        return {
            "success": False,
            "error": "Insufficient price history (need at least 5 days)."
        }

    closes = [d["close"] for d in daily_data]

    # ── Compute signals (same for all modes) ─────────────────────
    sma5       = _sma(closes, 5)
    sma10      = _sma(closes, 10)
    momentum3d = _momentum_nd(closes, 3)
    momentum7d = _momentum_nd(closes, 7)
    vol_press  = _volume_pressure(daily_data, window=10)

    sma_signal = "ABOVE" if sma5 > sma10 else "BELOW"

    signals = {
        "sma5":            round(sma5, 4),
        "sma10":           round(sma10, 4),
        "sma_signal":      sma_signal,      # "ABOVE" = recovering
        "momentum_3d_pct": round(momentum3d, 4),
        "momentum_7d_pct": round(momentum7d, 4),
        "volume_pressure": vol_press,       # "HIGH" = sellers dominating
        "days_remaining":  days_remaining,
        "profit_pct":      round(profit_pct, 4),
    }

    # ── Route to the correct analysis mode ───────────────────────
    if mode == "LOSS":
        return _analyse_loss(signals, daily_data)
    elif mode == "TARGET_REACHED":
        return _analyse_target_reached(signals, closes)
    else:
        return _analyse_end_of_period(signals, closes, profit_pct)


# ─────────────────────────────────────────────────────────────────
#  MODE 1: LOSS
#  Question: Is this a temporary dip or a real problem?
# ─────────────────────────────────────────────────────────────────

def _analyse_loss(signals: dict, daily_data: list) -> dict:
    """
    Triggered when loss exceeds 1% below purchase price.

    Logic:
      HOLD            → SMA5 > SMA10 (recovering trend) AND
                        3-day momentum is positive AND
                        days remaining > 10 (time to recover)

      CONSIDER_SELLING→ Sellers dominating (high volume on down days) AND
                        days remaining < 10 (little time left)

      MONITOR_CLOSELY → Mixed signals — neither clearly recovering
                        nor clearly deteriorating
    """
    sma_recovering   = signals["sma_signal"] == "ABOVE"
    momentum_positive = signals["momentum_3d_pct"] > 0
    enough_time      = signals["days_remaining"] > 10
    high_selling_pressure = signals["volume_pressure"] == "HIGH"
    low_time         = signals["days_remaining"] < 10

    if sma_recovering and momentum_positive and enough_time:
        recommendation = "HOLD"
        reason = (
            f"The 5-day moving average is above the 10-day average, "
            f"suggesting the dip may be reversing. "
            f"3-day momentum is positive (+{signals['momentum_3d_pct']:.2f}%) "
            f"and {signals['days_remaining']} days remain for recovery."
        )
        confidence = "HIGH" if signals["momentum_3d_pct"] > 0.5 else "MEDIUM"

    elif high_selling_pressure and low_time:
        recommendation = "CONSIDER_SELLING"
        reason = (
            f"Heavy selling pressure detected (high volume on down days) "
            f"with only {signals['days_remaining']} days remaining. "
            f"Recovery is unlikely within the monitoring window."
        )
        confidence = "HIGH"

    else:
        recommendation = "MONITOR_CLOSELY"
        reason = (
            f"Mixed signals — SMA trend is {'recovering' if sma_recovering else 'declining'}, "
            f"3-day momentum is {signals['momentum_3d_pct']:+.2f}%. "
            f"Watch the next 1–2 days before deciding."
        )
        confidence = "LOW"

    return {
        "success":        True,
        "mode":           "LOSS",
        "recommendation": recommendation,
        "signals":        signals,
        "reason":         reason,
        "confidence":     confidence
    }


# ─────────────────────────────────────────────────────────────────
#  MODE 2: TARGET_REACHED
#  Question: Sell now or hold for potentially more gains?
# ─────────────────────────────────────────────────────────────────

def _analyse_target_reached(signals: dict, closes: list) -> dict:
    """
    Triggered when profit reaches or exceeds 5%.

    Logic:
      HOLD_FOR_MORE   → Strong recent momentum AND volume on up days AND
                        days remaining > 7 (room to run further)

      GOOD_TIME_TO_SELL → Stock near 30-day high AND momentum slowing
                          (classic sign of a near-term peak)

      NEUTRAL         → Neither clearly overextended nor clearly accelerating
    """
    high_30d      = max(closes[-30:]) if len(closes) >= 30 else max(closes)
    current_price = closes[-1]
    near_high     = current_price >= 0.98 * high_30d  # within 2% of 30d high

    momentum_strong  = signals["momentum_3d_pct"] > 1.0
    momentum_slowing = signals["momentum_3d_pct"] < 0.5
    buyers_dominant  = signals["volume_pressure"] == "LOW"  # low selling pressure
    enough_time      = signals["days_remaining"] > 7

    if momentum_strong and buyers_dominant and enough_time:
        recommendation = "HOLD_FOR_MORE"
        reason = (
            f"Strong 3-day momentum (+{signals['momentum_3d_pct']:.2f}%) "
            f"with buyers dominating volume. "
            f"{signals['days_remaining']} days remain — the stock may push higher."
        )
        confidence = "MEDIUM"

    elif near_high and momentum_slowing:
        recommendation = "GOOD_TIME_TO_SELL"
        reason = (
            f"The stock is near its 30-day high (${high_30d:.2f}) "
            f"and 3-day momentum is slowing ({signals['momentum_3d_pct']:+.2f}%). "
            f"This may be a near-term peak — locking in profit now looks optimal."
        )
        confidence = "HIGH"

    else:
        recommendation = "NEUTRAL"
        reason = (
            f"The stock has hit the 5% target. "
            f"Selling now locks in a solid gain. "
            f"Holding carries some risk but momentum ({signals['momentum_3d_pct']:+.2f}%) "
            f"{'suggests continued gains' if signals['momentum_3d_pct'] > 0 else 'is weakening'}."
        )
        confidence = "LOW"

    return {
        "success":        True,
        "mode":           "TARGET_REACHED",
        "recommendation": recommendation,
        "signals":        signals,
        "reason":         reason,
        "confidence":     confidence
    }


# ─────────────────────────────────────────────────────────────────
#  MODE 3: END_OF_PERIOD
#  Question: Is 5% still achievable or should they exit?
# ─────────────────────────────────────────────────────────────────

def _analyse_end_of_period(
    signals: dict,
    closes: list,
    profit_pct: float
) -> dict:
    """
    Triggered on Day 30 when the 5% target hasn't been reached.

    Logic:
      CLOSE_TO_TARGET → Gap to 5% is small (≤ 1%), recent trend is up,
                        and the required daily gain is achievable (≤ 0.3%/day)

      UNLIKELY        → Gap is large (> 2.5%) OR very few positive days
                        recently — recovery is not realistic in near term

      UNCERTAIN       → In between — mixed signals
    """
    gap           = 5.0 - profit_pct
    days_up_in_7  = _days_up_in_window(closes, window=7)
    # How much % per day is needed to close the gap in 5 days?
    rate_needed   = gap / 5

    signals["gap_to_target_pct"]   = round(gap, 4)
    signals["days_up_in_last_7"]   = days_up_in_7
    signals["daily_rate_needed"]   = round(rate_needed, 4)

    close_gap   = gap <= 1.0
    trending_up = days_up_in_7 >= 4
    achievable  = rate_needed <= 0.3

    wide_gap       = gap > 2.5
    weak_trend     = days_up_in_7 <= 2

    if close_gap and trending_up and achievable:
        recommendation = "CLOSE_TO_TARGET"
        reason = (
            f"You are {gap:.2f}% away from the 5% goal. "
            f"The stock gained on {days_up_in_7}/7 recent days "
            f"and only needs +{rate_needed:.2f}%/day over 5 days to reach target. "
            f"Consider extending monitoring."
        )
        confidence = "MEDIUM"

    elif wide_gap or weak_trend:
        recommendation = "UNLIKELY_TO_REACH"
        reason = (
            f"The stock is {gap:.2f}% below the 5% target "
            f"and gained on only {days_up_in_7}/7 recent days. "
            f"Reaching 5% in the near term is unlikely. "
            f"Current {'gain' if profit_pct >= 0 else 'loss'}: {profit_pct:+.2f}%."
        )
        confidence = "HIGH"

    else:
        recommendation = "UNCERTAIN"
        reason = (
            f"Mixed signals — {gap:.2f}% gap to target, "
            f"{days_up_in_7}/7 positive days recently. "
            f"Extending monitoring carries moderate risk."
        )
        confidence = "LOW"

    return {
        "success":        True,
        "mode":           "END_OF_PERIOD",
        "recommendation": recommendation,
        "signals":        signals,
        "reason":         reason,
        "confidence":     confidence
    }
