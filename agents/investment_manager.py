# INV Agent — Investment Manager
#
# This agent has ONE job: take the client's stock selection and budget,
# validate everything, calculate how many shares they can buy, and
# create the investment record that the Monitoring Engine will use.
#
# It runs ONCE — at the moment the client confirms their investment.
# After that, the PROFIT and ALERT agents take over.
#
# Why it exists as a separate agent:
#   The investment record is the foundation of the entire monitoring
#   phase. Getting it right — correct purchase price, correct share
#   count, correct deadline date — is critical. Isolating this logic
#   here means it's easy to test and impossible for monitoring logic
#   to accidentally corrupt it.

from datetime import date, timedelta
from math import floor


def create_investment(
    ticker: str,
    budget: float,
    current_price: float,
    investment_date: date = None
) -> dict:
    """
    Validates the client's investment parameters and creates the
    investment record used by all monitoring agents.

    Parameters:
        ticker         : Stock symbol the client wants to buy (e.g. "AAPL")
        budget         : Amount the client wants to invest in dollars
        current_price  : Current market price per share at time of investment
        investment_date: Date of investment (defaults to today if not provided)

    Returns a dict with success=True and the full investment record,
    or success=False and a clear error message if inputs are invalid.

    Interview explanation:
        "The investment record is immutable once created. The purchase
        price, share count, and deadline are locked in at this moment.
        All profit calculations throughout the 30-day window are relative
        to this record. This is why validation is strict — a wrong
        purchase price would corrupt every P&L calculation downstream."
    """

    # ── Input validation ──────────────────────────────────────────

    if not ticker or not isinstance(ticker, str):
        return {
            "success": False,
            "error": "Ticker must be a non-empty string."
        }

    ticker = ticker.upper().strip()

    if budget <= 0:
        return {
            "success": False,
            "error": f"Budget must be greater than zero. Got: ${budget}"
        }

    if current_price <= 0:
        return {
            "success": False,
            "error": f"Current price must be greater than zero. Got: ${current_price}"
        }

    # ── Share calculation ─────────────────────────────────────────
    #
    # How many whole shares can the client buy with their budget?
    # We use floor() — you can't buy 0.7 of a share.
    #
    # Example:
    #   Budget: $5,000  |  Price: $189.50 per share
    #   Shares: floor(5000 / 189.50) = floor(26.38) = 26 shares
    #   Total invested: 26 × $189.50 = $4,927.00
    #   Remaining cash: $5,000 - $4,927 = $73.00 (stays in their account)

    shares = floor(budget / current_price)

    # Can the client afford at least 1 share?
    if shares < 1:
        return {
            "success": False,
            "error": (
                f"Budget of ${budget:,.2f} is insufficient to buy even 1 share "
                f"of {ticker} at ${current_price:,.2f} per share. "
                f"Minimum required: ${current_price:,.2f}"
            )
        }

    # ── Calculate investment amounts ──────────────────────────────

    total_invested = round(shares * current_price, 2)
    remaining_cash = round(budget - total_invested, 2)

    # ── Set investment dates ──────────────────────────────────────
    #
    # investment_date = Day 0 (today by default)
    # deadline_date   = Day 30 = investment_date + 30 calendar days
    #
    # Note: We use calendar days, not trading days.
    # 30 calendar days captures roughly 21 trading days.

    if investment_date is None:
        investment_date = date.today()

    deadline_date = investment_date + timedelta(days=30)

    # ── Build the investment record ───────────────────────────────
    #
    # This record is the ground truth for the entire monitoring phase.
    # Every profit calculation uses purchase_price and total_invested
    # from this record — never the current price at time of calculation.

    return {
        "success": True,

        # Core identification
        "ticker":            ticker,
        "status":            "ACTIVE",   # ACTIVE / SOLD / EXPIRED

        # Price and quantity — locked in at investment time
        "purchase_price":    current_price,
        "shares":            shares,
        "total_invested":    total_invested,
        "remaining_cash":    remaining_cash,
        "budget":            budget,

        # Timeline
        "investment_date":   investment_date.isoformat(),
        "deadline_date":     deadline_date.isoformat(),
        "days_total":        30,

        # Confirmation summary shown to client before they confirm
        "confirmation_summary": _build_confirmation(
            ticker, shares, current_price,
            total_invested, remaining_cash,
            investment_date, deadline_date
        )
    }


def get_days_remaining(
    investment_date_str: str,
    as_of_date: date = None
) -> int:
    """
    Calculates how many days remain in the 30-day monitoring window.

    Used by the ALERT agent and daily scheduler to track the deadline.

    Parameters:
        investment_date_str : Investment date as ISO string (e.g. "2026-06-05")
        as_of_date          : Date to calculate from (defaults to today)

    Returns the number of days remaining (0 if deadline has passed).

    Interview explanation:
        "I separated this into its own function because the days-remaining
        calculation is needed in multiple places — the Alert agent checks
        it every poll, the Notification agent includes it in every message.
        Having one function avoids off-by-one errors from multiple
        implementations of the same date arithmetic."
    """
    if as_of_date is None:
        as_of_date = date.today()

    investment_date = date.fromisoformat(investment_date_str)
    deadline_date   = investment_date + timedelta(days=30)
    remaining       = (deadline_date - as_of_date).days

    # Never return negative — once the window has passed, it's 0
    return max(0, remaining)


def _build_confirmation(
    ticker, shares, price,
    total_invested, remaining_cash,
    investment_date, deadline_date
) -> str:
    """
    Builds the confirmation screen text shown to the client
    before they finalise the investment.
    """
    return (
        f"\n  You are investing in {ticker}\n"
        f"  ─────────────────────────────────────\n"
        f"  Shares to purchase:    {shares}\n"
        f"  Price per share:       ${price:,.2f}\n"
        f"  Total invested:        ${total_invested:,.2f}\n"
        f"  Remaining cash:        ${remaining_cash:,.2f}\n"
        f"  ─────────────────────────────────────\n"
        f"  Monitoring starts:     {investment_date.strftime('%B %d, %Y')}\n"
        f"  30-day deadline:       {deadline_date.strftime('%B %d, %Y')}\n"
        f"  Profit target:         5.00%\n"
    )
