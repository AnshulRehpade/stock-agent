# PROFIT Agent — Profit Calculator
#
# This agent has ONE job: given the current stock price and the
# original investment details, calculate how much profit or loss
# the client currently has.
#
# No API calls. No external libraries. Pure arithmetic.
#
# Why it exists as a separate agent:
#   Every other agent (ALERT, SIT, NOTIF) needs profit figures.
#   Instead of each one recalculating it themselves (badly, possibly
#   inconsistently), this agent is the single source of truth for
#   all profit/loss numbers in the system.


def calculate_profit(
    current_price: float,
    purchase_price: float,
    shares: int,
    total_invested: float,
    previous_profit_pct: float = 0.0
) -> dict:
    """
    Calculates the current profit or loss on an investment.

    Think of it like this:
        - You bought 10 shares of AAPL at $180 each → total invested = $1800
        - AAPL is now at $189 → current value = $1890
        - Profit = $90, which is 5% of $1800

    Parameters:
        current_price       : Latest market price per share
        purchase_price      : Price per share when the client bought in
        shares              : Number of shares held
        total_invested      : Exact amount paid (shares × purchase_price)
        previous_profit_pct : Profit % from the last poll cycle
                              (used to calculate change since last check)

    Returns a dict with success=True and all profit figures,
    or success=False with an error message if inputs are invalid.
    """

    # ── Input validation ───────────────────────────────────────────
    # An agent should never silently produce wrong numbers.
    # If inputs don't make sense, fail loudly with a clear message.

    if current_price <= 0:
        return {
            "success": False,
            "error": f"current_price must be > 0, got {current_price}"
        }

    if purchase_price <= 0:
        return {
            "success": False,
            "error": f"purchase_price must be > 0, got {purchase_price}"
        }

    if shares <= 0:
        return {
            "success": False,
            "error": f"shares must be > 0, got {shares}"
        }

    if total_invested <= 0:
        return {
            "success": False,
            "error": f"total_invested must be > 0, got {total_invested}"
        }

    # ── Core calculations ──────────────────────────────────────────

    # What are all the shares worth right now?
    current_value = round(shares * current_price, 2)

    # How much money has been made or lost in dollars?
    profit_loss_dollars = round(current_value - total_invested, 2)

    # What percentage gain/loss is that relative to what was invested?
    # Formula: ((current - original) / original) × 100
    profit_loss_pct = round(
        ((current_value - total_invested) / total_invested) * 100, 4
    )

    # How much has the profit % moved since the last time we checked?
    # This is used by the ALERT agent to decide if a new alert is needed.
    change_since_last_poll = round(profit_loss_pct - previous_profit_pct, 4)

    # ── Build a clean, readable result ────────────────────────────
    return {
        "success": True,

        # Core figures
        "current_price":        current_price,
        "current_value":        current_value,
        "total_invested":       total_invested,

        # Profit/loss
        "profit_loss_dollars":  profit_loss_dollars,
        "profit_loss_pct":      profit_loss_pct,

        # Change since the previous hourly poll
        # Positive = stock went up since last check
        # Negative = stock went down since last check
        # Zero     = no movement
        "previous_profit_pct":      previous_profit_pct,
        "change_since_last_poll":   change_since_last_poll,

        # Convenience flags used by the ALERT agent
        # These avoid magic number comparisons scattered across the codebase
        "is_in_profit":         profit_loss_pct > 0,
        "is_in_loss":           profit_loss_pct < 0,
        "reached_3_pct":        profit_loss_pct >= 3.0,
        "reached_5_pct":        profit_loss_pct >= 5.0,
        "loss_exceeds_1_pct":   profit_loss_pct <= -1.0,
    }
