# ALERT Agent — Alert Decision Engine
#
# This agent has ONE job: look at the current profit/loss figures
# and decide what action (if any) the Orchestrator should take.
#
# It does NOT send notifications. It does NOT calculate profit.
# It just evaluates rules and returns an action code.
#
# Think of it as a traffic light:
#   - It reads the current situation
#   - It tells ORCH what to do next
#   - ORCH then calls the right agent (NOTIF, SIT, etc.)
#
# Alert rules (from the technical design):
#   DEADLINE_REACHED  — Day 30 has arrived
#   TARGET_REACHED    — Profit >= 5%
#   LOSS_ALERT_MAJOR  — Loss > 1% (triggers Situation Analyser)
#   LOSS_ALERT_MINOR  — Any loss < 1% (simple alert, no analysis)
#   UPSIDE_ALERT      — Profit >= 3% AND jumped >= 0.25% since last alert
#   NO_ACTION         — None of the above conditions met

from datetime import date


# The minimum profit jump required above 3% to fire a new upside alert.
# Prevents noise alerts on tiny movements like 3.01% → 3.03% → 3.06%.
MIN_UPSIDE_JUMP_PCT = 0.25


def evaluate_alerts(
    profit_loss_pct: float,
    change_since_last_poll: float,
    last_alerted_profit_pct: float,
    loss_alert_armed: bool,
    upside_alert_armed: bool,
    days_remaining: int,
    is_in_loss: bool,
    loss_exceeds_1_pct: bool,
    reached_3_pct: bool,
    reached_5_pct: bool,
) -> dict:
    """
    Evaluates all alert conditions and returns the action ORCH should take.

    Parameters (all come from PROFIT agent output + system state):
        profit_loss_pct        : Current profit/loss as a percentage
        change_since_last_poll : How much profit % changed since last check
        last_alerted_profit_pct: The profit % at which the last upside alert fired
        loss_alert_armed       : True when stock is in profit (ready to detect a drop)
        upside_alert_armed     : True when profit is below 3% (ready to detect crossing)
        days_remaining         : Days left in the 30-day window
        is_in_loss             : True if profit_loss_pct < 0
        loss_exceeds_1_pct     : True if loss is more than 1%
        reached_3_pct          : True if profit >= 3.0%
        reached_5_pct          : True if profit >= 5.0%

    Returns:
        {
            "action": str,                    # what ORCH should do next
            "situation_analysis_required": bool,  # True = call SIT agent first
            "updated_alert_state": {
                "loss_alert_armed": bool,
                "upside_alert_armed": bool,
                "last_alerted_profit_pct": float
            }
        }
    """

    # Work with mutable copies of the alert state flags.
    # We update them as we go and return the final state.
    new_loss_armed    = loss_alert_armed
    new_upside_armed  = upside_alert_armed
    new_last_alerted  = last_alerted_profit_pct

    # ── Rule 1: Deadline (highest priority) ───────────────────────
    # Check this first — it overrides everything else.
    if days_remaining <= 0:
        return _result(
            action="DEADLINE_REACHED",
            situation_required=True,
            loss_armed=new_loss_armed,
            upside_armed=new_upside_armed,
            last_alerted=new_last_alerted
        )

    # ── Rule 2: 5% target reached ─────────────────────────────────
    if reached_5_pct:
        return _result(
            action="TARGET_REACHED",
            situation_required=True,
            loss_armed=new_loss_armed,
            upside_armed=new_upside_armed,
            last_alerted=new_last_alerted
        )

    # ── Rule 3: Loss alert ─────────────────────────────────────────
    # The loss alert fires when:
    #   - The stock drops below 0% (is_in_loss = True)
    #   - AND the loss alert is currently armed (stock was in profit before)
    #
    # Once fired, we disarm it so it doesn't re-fire every poll while
    # the stock stays negative. It re-arms when profit returns above 0%.
    if is_in_loss and loss_alert_armed:
        new_loss_armed = False   # disarm until stock recovers
        if loss_exceeds_1_pct:
            return _result(
                action="LOSS_ALERT_MAJOR",
                situation_required=True,
                loss_armed=new_loss_armed,
                upside_armed=new_upside_armed,
                last_alerted=new_last_alerted
            )
        else:
            return _result(
                action="LOSS_ALERT_MINOR",
                situation_required=False,
                loss_armed=new_loss_armed,
                upside_armed=new_upside_armed,
                last_alerted=new_last_alerted
            )

    # Re-arm the loss alert once the stock is back in positive territory
    if not is_in_loss:
        new_loss_armed = True

    # ── Rule 4: Upside alert (3% zone) ────────────────────────────
    # Fires when:
    #   a) Profit just crossed 3% for the first time (upside_armed = True)
    #      OR client chose "Continue" / didn't respond (threshold reset)
    #   b) Profit is above 3% AND has jumped >= 0.25% since last alert
    #
    # Does NOT fire if profit is above 3% but barely moved.
    if reached_3_pct:
        # Mark the upside threshold as crossed — disarm it
        new_upside_armed = False

        jump_since_last_alert = profit_loss_pct - last_alerted_profit_pct

        # First crossing (upside was armed) OR meaningful jump (>= 0.25%)
        if upside_alert_armed or jump_since_last_alert >= MIN_UPSIDE_JUMP_PCT:
            new_last_alerted = profit_loss_pct   # record where we alerted
            return _result(
                action="UPSIDE_ALERT",
                situation_required=False,
                loss_armed=new_loss_armed,
                upside_armed=new_upside_armed,
                last_alerted=new_last_alerted
            )
    else:
        # Profit dropped back below 3% — re-arm the upside threshold
        # and reset last_alerted so the next crossing fires fresh
        new_upside_armed = True
        new_last_alerted = 0.0

    # ── No alert needed ───────────────────────────────────────────
    return _result(
        action="NO_ACTION",
        situation_required=False,
        loss_armed=new_loss_armed,
        upside_armed=new_upside_armed,
        last_alerted=new_last_alerted
    )


def _result(
    action: str,
    situation_required: bool,
    loss_armed: bool,
    upside_armed: bool,
    last_alerted: float
) -> dict:
    """Builds the standard return dict for evaluate_alerts."""
    return {
        "action": action,
        "situation_analysis_required": situation_required,
        "updated_alert_state": {
            "loss_alert_armed":        loss_armed,
            "upside_alert_armed":      upside_armed,
            "last_alerted_profit_pct": round(last_alerted, 4)
        }
    }
