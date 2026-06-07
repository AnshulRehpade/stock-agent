# Tests for the ALERT Agent (alert_decision.py)
#
# No mocking needed — pure rules engine, just call with known inputs.
#
# Run: pytest tests/test_alert_decision.py -v

import pytest
from agents.alert_decision import evaluate_alerts


# ─────────────────────────────────────────────────────────────────
#  HELPER — build a standard "all clear" input
#  Start from a safe baseline and override only what each test needs.
# ─────────────────────────────────────────────────────────────────

def make_input(**overrides) -> dict:
    """
    Returns a standard set of inputs representing:
      - Stock is at 1.5% profit (no alerts should fire)
      - Loss alert is armed (stock has been in profit)
      - Upside alert is armed (profit not yet at 3%)
      - 20 days remaining
    """
    base = {
        "profit_loss_pct":          1.5,
        "change_since_last_poll":   0.1,
        "last_alerted_profit_pct":  0.0,
        "loss_alert_armed":         True,
        "upside_alert_armed":       True,
        "days_remaining":           20,
        "is_in_loss":               False,
        "loss_exceeds_1_pct":       False,
        "reached_3_pct":            False,
        "reached_5_pct":            False,
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────
#  NO ACTION
# ─────────────────────────────────────────────────────────────────

class TestNoAction:

    def test_normal_monitoring_no_alert(self):
        """Stock is at 1.5% profit with no threshold crossed — no alert."""
        result = evaluate_alerts(**make_input())
        assert result["action"] == "NO_ACTION"
        assert result["situation_analysis_required"] is False

    def test_above_3pct_but_jump_too_small(self):
        """
        Profit is above 3% but only moved 0.10% since last alert.
        Below the 0.25% minimum jump — no new alert.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=3.10,
            last_alerted_profit_pct=3.05,   # jump = only 0.05%
            upside_alert_armed=False,        # threshold already crossed
            reached_3_pct=True,
        ))
        assert result["action"] == "NO_ACTION"

    def test_above_3pct_unchanged_since_last_poll(self):
        """
        Profit is at 3.5% but hasn't moved — no new alert needed.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=3.5,
            last_alerted_profit_pct=3.5,    # same as current
            change_since_last_poll=0.0,
            upside_alert_armed=False,
            reached_3_pct=True,
        ))
        assert result["action"] == "NO_ACTION"


# ─────────────────────────────────────────────────────────────────
#  UPSIDE ALERTS
# ─────────────────────────────────────────────────────────────────

class TestUpsideAlerts:

    def test_first_crossing_of_3pct(self):
        """
        Stock crosses 3% for the first time.
        upside_alert_armed = True means we were waiting for this.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=3.1,
            upside_alert_armed=True,   # waiting for 3% crossing
            reached_3_pct=True,
        ))
        assert result["action"] == "UPSIDE_ALERT"
        assert result["situation_analysis_required"] is False

    def test_significant_jump_above_3pct(self):
        """
        Profit already above 3%, but jumped 0.30% since last alert.
        >= 0.25% minimum jump — fire a new alert.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=3.50,
            last_alerted_profit_pct=3.20,   # jump = 0.30%
            upside_alert_armed=False,
            reached_3_pct=True,
        ))
        assert result["action"] == "UPSIDE_ALERT"

    def test_exactly_025_jump_fires_alert(self):
        """Exactly 0.25% jump — should fire (boundary condition)."""
        result = evaluate_alerts(**make_input(
            profit_loss_pct=3.25,
            last_alerted_profit_pct=3.00,   # jump = exactly 0.25%
            upside_alert_armed=False,
            reached_3_pct=True,
        ))
        assert result["action"] == "UPSIDE_ALERT"

    def test_last_alerted_updated_after_alert(self):
        """
        When an upside alert fires, last_alerted_profit_pct should be
        updated to the current profit so the next alert measures from here.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=3.50,
            last_alerted_profit_pct=3.00,
            upside_alert_armed=False,
            reached_3_pct=True,
        ))
        assert result["updated_alert_state"]["last_alerted_profit_pct"] == 3.50

    def test_threshold_resets_when_profit_drops_below_3pct(self):
        """
        If profit drops below 3%, upside_alert_armed should reset to True
        and last_alerted_profit_pct should reset to 0.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=2.8,   # dropped below 3%
            last_alerted_profit_pct=3.5,
            upside_alert_armed=False,
            reached_3_pct=False,
        ))
        assert result["action"] == "NO_ACTION"
        assert result["updated_alert_state"]["upside_alert_armed"] is True
        assert result["updated_alert_state"]["last_alerted_profit_pct"] == 0.0

    def test_alert_fires_again_after_dip_and_recovery(self):
        """
        Client chose Continue → profit dipped below 3% → recovered.
        On recovery, upside_armed = True again so alert should fire.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=3.1,
            upside_alert_armed=True,   # was reset when profit dipped
            last_alerted_profit_pct=0.0,
            reached_3_pct=True,
        ))
        assert result["action"] == "UPSIDE_ALERT"


# ─────────────────────────────────────────────────────────────────
#  TARGET REACHED
# ─────────────────────────────────────────────────────────────────

class TestTargetReached:

    def test_5pct_target_fires_target_alert(self):
        result = evaluate_alerts(**make_input(
            profit_loss_pct=5.0,
            reached_3_pct=True,
            reached_5_pct=True,
        ))
        assert result["action"] == "TARGET_REACHED"
        assert result["situation_analysis_required"] is True

    def test_above_5pct_also_fires(self):
        """6% profit should still be TARGET_REACHED, not UPSIDE_ALERT."""
        result = evaluate_alerts(**make_input(
            profit_loss_pct=6.2,
            reached_3_pct=True,
            reached_5_pct=True,
        ))
        assert result["action"] == "TARGET_REACHED"

    def test_target_takes_priority_over_upside(self):
        """
        If both reached_3_pct and reached_5_pct are True,
        TARGET_REACHED should win over UPSIDE_ALERT.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=5.3,
            reached_3_pct=True,
            reached_5_pct=True,
            upside_alert_armed=True,
        ))
        assert result["action"] == "TARGET_REACHED"


# ─────────────────────────────────────────────────────────────────
#  LOSS ALERTS
# ─────────────────────────────────────────────────────────────────

class TestLossAlerts:

    def test_minor_loss_fires_loss_alert_minor(self):
        """
        Loss < 1%: simple alert, no situation analysis needed.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=-0.5,
            is_in_loss=True,
            loss_exceeds_1_pct=False,
            loss_alert_armed=True,
        ))
        assert result["action"] == "LOSS_ALERT_MINOR"
        assert result["situation_analysis_required"] is False

    def test_major_loss_fires_loss_alert_major(self):
        """
        Loss > 1%: situation analysis required.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=-1.5,
            is_in_loss=True,
            loss_exceeds_1_pct=True,
            loss_alert_armed=True,
        ))
        assert result["action"] == "LOSS_ALERT_MAJOR"
        assert result["situation_analysis_required"] is True

    def test_loss_alert_disarms_after_firing(self):
        """
        After a loss alert fires, loss_alert_armed should become False
        so it doesn't re-fire every poll while stock stays negative.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=-0.8,
            is_in_loss=True,
            loss_exceeds_1_pct=False,
            loss_alert_armed=True,
        ))
        assert result["updated_alert_state"]["loss_alert_armed"] is False

    def test_loss_alert_does_not_fire_when_disarmed(self):
        """
        Stock is still in loss but loss_alert_armed = False (already fired).
        No new alert until stock recovers.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=-0.8,
            is_in_loss=True,
            loss_exceeds_1_pct=False,
            loss_alert_armed=False,   # already fired
        ))
        assert result["action"] == "NO_ACTION"

    def test_loss_alert_rearmed_when_stock_recovers(self):
        """
        Stock recovers from loss back to positive territory.
        loss_alert_armed should become True again.
        """
        result = evaluate_alerts(**make_input(
            profit_loss_pct=0.5,
            is_in_loss=False,
            loss_alert_armed=False,   # was disarmed after previous loss
        ))
        assert result["updated_alert_state"]["loss_alert_armed"] is True


# ─────────────────────────────────────────────────────────────────
#  DEADLINE
# ─────────────────────────────────────────────────────────────────

class TestDeadline:

    def test_day_30_fires_deadline(self):
        result = evaluate_alerts(**make_input(days_remaining=0))
        assert result["action"] == "DEADLINE_REACHED"
        assert result["situation_analysis_required"] is True

    def test_deadline_takes_priority_over_everything(self):
        """
        Even if profit >= 5%, DEADLINE_REACHED wins on day 30.
        """
        result = evaluate_alerts(**make_input(
            days_remaining=0,
            profit_loss_pct=6.0,
            reached_3_pct=True,
            reached_5_pct=True,
        ))
        assert result["action"] == "DEADLINE_REACHED"

    def test_day_1_remaining_not_deadline(self):
        result = evaluate_alerts(**make_input(days_remaining=1))
        assert result["action"] != "DEADLINE_REACHED"


# ─────────────────────────────────────────────────────────────────
#  PRIORITY ORDER
# ─────────────────────────────────────────────────────────────────

class TestPriority:

    def test_priority_order_deadline_first(self):
        """
        Deadline > Target > Loss > Upside — deadline always wins.
        """
        result = evaluate_alerts(**make_input(
            days_remaining=0,
            profit_loss_pct=-1.5,
            is_in_loss=True,
            loss_exceeds_1_pct=True,
            loss_alert_armed=True,
        ))
        assert result["action"] == "DEADLINE_REACHED"

    def test_target_beats_loss_when_both_triggered(self):
        """
        If profit >= 5% AND in loss is somehow True (shouldn't happen,
        but rules should still be consistent — TARGET_REACHED wins).
        """
        result = evaluate_alerts(**make_input(
            days_remaining=5,
            profit_loss_pct=5.5,
            reached_3_pct=True,
            reached_5_pct=True,
            is_in_loss=False,
        ))
        assert result["action"] == "TARGET_REACHED"
