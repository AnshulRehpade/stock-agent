# RANK Agent — Stock Ranker
#
# This agent has ONE job: take 90 days of historical price data for a
# list of candidate stocks and return them ranked from best to worst
# for a short-term 5% profit target within 30 days.
#
# It uses 5 scoring metrics (from the technical design):
#   1. 30-day momentum       (30%) — has it been rising recently?
#   2. Upside volatility     (25%) — does it make meaningful positive moves?
#   3. Average daily volume  (20%) — is it liquid enough to exit easily?
#   4. 90-day trend strength (15%) — is the upward trend sustained?
#   5. Risk-adjusted return  (10%) — are the gains consistent vs erratic?
#
# Each metric is normalised to 0–100 before weighting,
# so no single metric dominates just because of its unit size.

import numpy as np
from scipy import stats


# ─────────────────────────────────────────────────────────────────
#  INDIVIDUAL METRIC CALCULATORS
#  Each function takes the daily_data list and returns a raw score.
#  Raw scores are normalised later in rank_stocks().
# ─────────────────────────────────────────────────────────────────

def _momentum_30d(daily_data: list) -> float:
    """
    How much has the price risen over the last 30 days?

    Formula: ((close_today - close_30d_ago) / close_30d_ago) × 100

    A stock at +8% over the last month scores higher than one at +2%.
    This is the most direct indicator for a 30-day profit goal.

    Returns the % gain as a float. Can be negative.
    """
    closes = [d["close"] for d in daily_data]
    if len(closes) < 31:
        # Not enough data — use whatever we have
        return ((closes[-1] - closes[0]) / closes[0]) * 100
    return ((closes[-1] - closes[-31]) / closes[-31]) * 100


def _upside_volatility(daily_data: list) -> float:
    """
    How often does the stock make a meaningful positive move (>= 1% in a day)?

    Formula: count of days with daily return >= 1% over last 30 days, / 30

    A stock that gains >= 1% on 12 out of 30 days scores 0.40.
    One that does it 20 out of 30 days scores 0.67.

    Why not just raw volatility (std dev)?
    Raw volatility penalises big drops AND big gains equally.
    We only care about UPWARD moves for a profit target — so we
    measure only those.

    Returns a ratio between 0.0 and 1.0.
    """
    closes = [d["close"] for d in daily_data]
    # Need at least 2 prices to compute a return
    if len(closes) < 2:
        return 0.0

    # Use the last 30 daily returns (requires last 31 prices)
    window = closes[-31:] if len(closes) >= 31 else closes
    daily_returns = [
        (window[i] - window[i - 1]) / window[i - 1]
        for i in range(1, len(window))
        if window[i - 1] > 0   # guard against zero division
    ]
    if not daily_returns:
        return 0.0

    positive_days = sum(1 for r in daily_returns if r >= 0.01)
    return positive_days / len(daily_returns)


def _avg_daily_volume(daily_data: list) -> float:
    """
    What is the average number of shares traded per day over 30 days?

    A higher average means the stock is liquid — easy to buy and sell
    without moving the price. Low-volume stocks are risky because
    you might not find a buyer when you want to exit.

    Returns the average volume as a float (can be very large, e.g. 50,000,000).
    It will be normalised relative to other candidates in rank_stocks().
    """
    volumes = [d["volume"] for d in daily_data]
    window = volumes[-30:]
    return sum(window) / len(window)


def _trend_strength(daily_data: list) -> float:
    """
    How consistently has the price been trending upward over 90 days?

    Method: linear regression on the last 90 closing prices.
    - slope: direction and steepness of the trend
    - R² (r_value²): how well the prices fit a straight line (0=random, 1=perfect)

    Score = slope × R²

    This penalises erratic stocks that have a high slope but a messy path.
    A steady, consistent climb scores higher than a volatile one with the
    same start and end point.

    Why 90 days? 30-day momentum can be a short-term spike or recovery.
    90-day trend shows whether the upward movement is a real pattern.

    Returns the trend score (slope × r²). Can be negative for downtrends.
    """
    closes = [d["close"] for d in daily_data]
    window = closes[-90:] if len(closes) >= 90 else closes
    x = np.arange(len(window))
    # If all values are identical, slope and r_value are both 0
    if len(set(window)) == 1:
        return 0.0
    slope, _, r_value, _, _ = stats.linregress(x, window)
    r_squared = r_value ** 2
    return slope * r_squared


def _risk_adjusted_return(daily_data: list) -> float:
    """
    How much return does this stock deliver per unit of risk (volatility)?

    This is a simplified Sharpe ratio over 90 days.

    Formula:
        daily_returns = % change each day
        score = mean(daily_returns) / std(daily_returns)

    A stock with a steady +0.2%/day scores higher than one that swings
    wildly between +3% and -2.5% days even if the average is similar.

    This protects against picking a stock that "looks good on average"
    but could drop sharply on any given day.

    Returns the ratio. Higher = better. Returns 0.0 if std is zero
    (flat price = no risk, but also no movement).
    """
    closes = [d["close"] for d in daily_data]
    window = closes[-91:] if len(closes) >= 91 else closes
    if len(window) < 2:
        return 0.0
    daily_returns = np.array([
        (window[i] - window[i - 1]) / window[i - 1]
        for i in range(1, len(window))
    ])
    std = np.std(daily_returns)
    if std == 0:
        return 0.0
    return float(np.mean(daily_returns) / std)


# ─────────────────────────────────────────────────────────────────
#  NORMALISATION
#  Converts a list of raw scores to 0–100 range.
#  This ensures a volume of 50,000,000 doesn't dwarf a momentum of 6.0.
# ─────────────────────────────────────────────────────────────────

def _normalise(values: list) -> list:
    """
    Scales a list of values to the range [0, 100].

    min value → 0
    max value → 100
    everything else → proportional in between

    If all values are equal (e.g. only one stock), returns [50.0, ...].
    """
    min_val = min(values)
    max_val = max(values)
    if max_val == min_val:
        # All stocks scored the same on this metric — give everyone 50
        return [50.0] * len(values)
    return [
        ((v - min_val) / (max_val - min_val)) * 100
        for v in values
    ]


# ─────────────────────────────────────────────────────────────────
#  MAIN FUNCTION
# ─────────────────────────────────────────────────────────────────

def rank_stocks(candidates: list) -> dict:
    """
    Scores and ranks a list of stocks by suitability for a 5%/30-day target.

    Input:
        candidates: list of dicts, each with:
            {
                "ticker": "AAPL",
                "current_price": 189.50,
                "daily_data": [
                    {"date": "...", "open": ..., "high": ...,
                     "low": ..., "close": ..., "volume": ...},
                    ...  # at least 30 days, ideally 90
                ]
            }

    Output:
        {
            "success": True,
            "top5": [
                {
                    "rank": 1,
                    "ticker": "AAPL",
                    "current_price": 189.50,
                    "score": 87.3,          # composite 0–100
                    "metrics": {
                        "momentum_30d":        6.2,
                        "upside_volatility":   0.53,
                        "avg_daily_volume":    58000000,
                        "trend_strength":      0.81,
                        "risk_adjusted_return":0.74
                    },
                    "rationale": "Strong 30-day momentum with consistent
                                  positive days and high liquidity."
                },
                ...  # up to 5 entries
            ]
        }
    """

    # ── Validate input ─────────────────────────────────────────────
    if not candidates:
        return {
            "success": False,
            "error": "No candidate stocks provided."
        }

    for c in candidates:
        if "ticker" not in c:
            return {"success": False, "error": "Each candidate must have a 'ticker' field."}
        if "daily_data" not in c or len(c["daily_data"]) < 5:
            return {
                "success": False,
                "error": f"Ticker '{c.get('ticker')}' has insufficient historical data "
                         f"(need at least 5 days, got {len(c.get('daily_data', []))})."
            }

    # ── Step 1: Compute raw metrics for every candidate ────────────
    raw_metrics = []
    for c in candidates:
        d = c["daily_data"]
        raw_metrics.append({
            "ticker":                c["ticker"],
            "current_price":         c.get("current_price", d[-1]["close"]),
            "momentum":              _momentum_30d(d),
            "upside_vol":            _upside_volatility(d),
            "avg_volume":            _avg_daily_volume(d),
            "trend_strength":        _trend_strength(d),
            "risk_adj":              _risk_adjusted_return(d),
        })

    # ── Step 2: Normalise each metric across all candidates ────────
    # Extract each metric as a list, normalise, then put back.
    keys = ["momentum", "upside_vol", "avg_volume", "trend_strength", "risk_adj"]
    normalised = {
        k: _normalise([m[k] for m in raw_metrics])
        for k in keys
    }

    # ── Step 3: Compute weighted composite score ───────────────────
    # Weights from the technical design:
    weights = {
        "momentum":      0.30,
        "upside_vol":    0.25,
        "avg_volume":    0.20,
        "trend_strength":0.15,
        "risk_adj":      0.10,
    }

    scored = []
    for i, m in enumerate(raw_metrics):
        composite = sum(
            normalised[k][i] * weights[k]
            for k in keys
        )
        scored.append({
            "ticker":        m["ticker"],
            "current_price": m["current_price"],
            "score":         round(composite, 2),
            "metrics": {
                "momentum_30d":         round(m["momentum"], 4),
                "upside_volatility":    round(m["upside_vol"], 4),
                "avg_daily_volume":     int(m["avg_volume"]),
                "trend_strength":       round(m["trend_strength"], 6),
                "risk_adjusted_return": round(m["risk_adj"], 6),
            }
        })

    # ── Step 4: Sort by score descending, take top 5 ───────────────
    scored.sort(key=lambda x: x["score"], reverse=True)
    top5 = scored[:5]

    # ── Step 5: Add rank and rationale ────────────────────────────
    for rank, stock in enumerate(top5, start=1):
        stock["rank"] = rank
        stock["rationale"] = _build_rationale(stock["metrics"], stock["score"])

    return {
        "success": True,
        "total_candidates": len(candidates),
        "top5": top5
    }


def _build_rationale(metrics: dict, score: float) -> str:
    """
    Builds a plain-English one-line summary of why this stock ranked well.
    Uses rule-based text — no LLM needed for v1.
    """
    parts = []

    if metrics["momentum_30d"] >= 5.0:
        parts.append(f"strong 30-day momentum (+{metrics['momentum_30d']:.1f}%)")
    elif metrics["momentum_30d"] >= 2.0:
        parts.append(f"moderate 30-day momentum (+{metrics['momentum_30d']:.1f}%)")

    if metrics["upside_volatility"] >= 0.5:
        pct = int(metrics["upside_volatility"] * 100)
        parts.append(f"positive moves on {pct}% of days")

    if metrics["avg_daily_volume"] >= 10_000_000:
        parts.append("high liquidity")
    elif metrics["avg_daily_volume"] >= 1_000_000:
        parts.append("moderate liquidity")

    if metrics["trend_strength"] > 0:
        parts.append("sustained upward trend")

    if metrics["risk_adjusted_return"] > 0.05:
        parts.append("consistent risk-adjusted returns")

    if not parts:
        return f"Composite score: {score:.1f}/100."

    return ", ".join(parts).capitalize() + f". Score: {score:.1f}/100."
