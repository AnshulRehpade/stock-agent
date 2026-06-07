# Tests for the NOTIF Agent (notification.py)
#
# Two test types:
#   1. Template tests  — use preview_notification(), no email sent, no mocking
#   2. Send path tests — mock smtplib to test send logic without real email
#
# Run: pytest tests/test_notification.py -v

import pytest
from agents.notification import send_notification, preview_notification


# ─────────────────────────────────────────────────────────────────
#  TEST DATA HELPERS
# ─────────────────────────────────────────────────────────────────

def make_investment(ticker="AAPL", purchase_price=180.0, shares=10):
    return {
        "ticker":         ticker,
        "purchase_price": purchase_price,
        "shares":         shares,
        "total_invested": round(shares * purchase_price, 2),
        "investment_date":"2026-06-05",
        "deadline_date":  "2026-07-05",
        "status":         "ACTIVE"
    }

def make_profit(profit_pct=3.5, current_price=186.30):
    total_invested = 1800.0
    current_value  = round(current_price * 10, 2)
    profit_dollars = round(current_value - total_invested, 2)
    return {
        "current_price":         current_price,
        "current_value":         current_value,
        "total_invested":        total_invested,
        "profit_loss_pct":       profit_pct,
        "profit_loss_dollars":   profit_dollars,
        "change_since_last_poll":0.30,
        "is_in_profit":          profit_pct > 0,
        "is_in_loss":            profit_pct < 0,
    }

def make_recommendation(rec="HOLD", reason="Test reason", confidence="MEDIUM"):
    return {
        "success":        True,
        "recommendation": rec,
        "reason":         reason,
        "confidence":     confidence,
        "signals": {
            "sma_signal":      "ABOVE",
            "momentum_3d_pct": 0.5,
            "volume_pressure": "LOW",
            "gap_to_target_pct": 1.5,
            "days_up_in_last_7": 5,
            "daily_rate_needed": 0.3
        }
    }


# ─────────────────────────────────────────────────────────────────
#  TEMPLATE TESTS (preview_notification — no email sent)
# ─────────────────────────────────────────────────────────────────

class TestDailySummaryTemplate:

    def test_subject_contains_ticker_and_pnl(self):
        result = preview_notification(
            "DAILY_SUMMARY", make_investment(), make_profit(), days_remaining=20
        )
        assert result["success"] is True
        assert "AAPL" in result["subject"]
        assert "3.50" in result["subject"]

    def test_body_contains_current_price(self):
        result = preview_notification(
            "DAILY_SUMMARY", make_investment(), make_profit(current_price=186.30),
            days_remaining=20
        )
        assert "186.30" in result["body"]

    def test_body_contains_days_remaining(self):
        result = preview_notification(
            "DAILY_SUMMARY", make_investment(), make_profit(), days_remaining=15
        )
        assert "15" in result["body"]

    def test_body_contains_purchase_price(self):
        result = preview_notification(
            "DAILY_SUMMARY", make_investment(purchase_price=180.0), make_profit(),
            days_remaining=20
        )
        assert "180.00" in result["body"]


class TestLossMinorTemplate:

    def test_subject_contains_loss_indicator(self):
        result = preview_notification(
            "LOSS_MINOR", make_investment(),
            make_profit(profit_pct=-0.5, current_price=179.0),
            days_remaining=18
        )
        assert result["success"] is True
        assert "Loss" in result["subject"] or "loss" in result["subject"].lower()

    def test_body_contains_hold_and_sell_options(self):
        result = preview_notification(
            "LOSS_MINOR", make_investment(),
            make_profit(profit_pct=-0.5, current_price=179.0),
            days_remaining=18
        )
        assert "HOLD" in result["body"]
        assert "SELL" in result["body"]

    def test_body_contains_alert_id(self):
        result = preview_notification(
            "LOSS_MINOR", make_investment(),
            make_profit(profit_pct=-0.5, current_price=179.0),
            days_remaining=18
        )
        assert "PREVIEW" in result["body"]


class TestLossMajorTemplate:

    def test_subject_indicates_action_required(self):
        result = preview_notification(
            "LOSS_MAJOR", make_investment(),
            make_profit(profit_pct=-1.5, current_price=177.3),
            recommendation=make_recommendation("HOLD"),
            days_remaining=15
        )
        assert result["success"] is True
        assert "1%" in result["subject"] or "Action" in result["subject"]

    def test_body_contains_recommendation(self):
        result = preview_notification(
            "LOSS_MAJOR", make_investment(),
            make_profit(profit_pct=-1.5, current_price=177.3),
            recommendation=make_recommendation("CONSIDER_SELLING"),
            days_remaining=15
        )
        assert "CONSIDER_SELLING" in result["body"]

    def test_body_contains_system_analysis_section(self):
        result = preview_notification(
            "LOSS_MAJOR", make_investment(),
            make_profit(profit_pct=-1.5, current_price=177.3),
            recommendation=make_recommendation(),
            days_remaining=15
        )
        assert "System Analysis" in result["body"]

    def test_body_works_without_recommendation(self):
        """Should not crash if no SIT analysis provided"""
        result = preview_notification(
            "LOSS_MAJOR", make_investment(),
            make_profit(profit_pct=-1.5, current_price=177.3),
            recommendation=None,
            days_remaining=15
        )
        assert result["success"] is True


class TestUpsideAlertTemplate:

    def test_subject_shows_profit_and_upward_direction(self):
        result = preview_notification(
            "UPSIDE_ALERT", make_investment(),
            make_profit(profit_pct=3.5, current_price=186.30),
            days_remaining=20
        )
        assert result["success"] is True
        assert "3.50" in result["subject"]

    def test_body_shows_continue_and_sell_options(self):
        result = preview_notification(
            "UPSIDE_ALERT", make_investment(),
            make_profit(profit_pct=3.5, current_price=186.30),
            days_remaining=20
        )
        assert "CONTINUE" in result["body"]
        assert "SELL" in result["body"]

    def test_body_shows_5pct_target(self):
        result = preview_notification(
            "UPSIDE_ALERT", make_investment(),
            make_profit(profit_pct=3.5, current_price=186.30),
            days_remaining=20
        )
        assert "5.00" in result["body"]

    def test_body_shows_change_since_last_alert(self):
        profit = make_profit(profit_pct=3.5, current_price=186.30)
        profit["change_since_last_poll"] = 0.25
        result = preview_notification(
            "UPSIDE_ALERT", make_investment(), profit, days_remaining=20
        )
        assert "0.25" in result["body"]


class TestTargetReachedTemplate:

    def test_subject_shows_target_reached(self):
        result = preview_notification(
            "TARGET_REACHED", make_investment(),
            make_profit(profit_pct=5.3, current_price=189.54),
            recommendation=make_recommendation("GOOD_TIME_TO_SELL"),
            days_remaining=10
        )
        assert result["success"] is True
        assert "5.30" in result["subject"] or "Target" in result["subject"]

    def test_body_contains_recommendation(self):
        result = preview_notification(
            "TARGET_REACHED", make_investment(),
            make_profit(profit_pct=5.3, current_price=189.54),
            recommendation=make_recommendation("HOLD_FOR_MORE"),
            days_remaining=10
        )
        assert "HOLD_FOR_MORE" in result["body"]

    def test_body_shows_sell_and_continue_options(self):
        result = preview_notification(
            "TARGET_REACHED", make_investment(),
            make_profit(profit_pct=5.3, current_price=189.54),
            recommendation=make_recommendation(),
            days_remaining=10
        )
        assert "SELL" in result["body"]
        assert "CONTINUE" in result["body"]


class TestDeadlineTemplate:

    def test_subject_shows_window_closed(self):
        result = preview_notification(
            "DEADLINE", make_investment(),
            make_profit(profit_pct=3.2, current_price=185.76),
            recommendation=make_recommendation("UNLIKELY_TO_REACH"),
            days_remaining=0
        )
        assert result["success"] is True
        assert "Closed" in result["subject"] or "Window" in result["subject"]

    def test_body_contains_sell_and_extend_options(self):
        result = preview_notification(
            "DEADLINE", make_investment(),
            make_profit(profit_pct=3.2, current_price=185.76),
            recommendation=make_recommendation("CLOSE_TO_TARGET"),
            days_remaining=0
        )
        assert "SELL"   in result["body"]
        assert "EXTEND" in result["body"]

    def test_body_contains_end_of_period_analysis(self):
        result = preview_notification(
            "DEADLINE", make_investment(),
            make_profit(profit_pct=3.2, current_price=185.76),
            recommendation=make_recommendation("CLOSE_TO_TARGET"),
            days_remaining=0
        )
        assert "End-of-Period" in result["body"]

    def test_body_contains_deadline_date(self):
        inv = make_investment()
        result = preview_notification(
            "DEADLINE", inv,
            make_profit(profit_pct=3.2, current_price=185.76),
            recommendation=make_recommendation(),
            days_remaining=0
        )
        assert "2026-07-05" in result["body"]


# ─────────────────────────────────────────────────────────────────
#  VALIDATION TESTS
# ─────────────────────────────────────────────────────────────────

class TestValidation:

    def test_invalid_notification_type_fails(self):
        result = preview_notification(
            "INVALID_TYPE", make_investment(), make_profit()
        )
        assert result["success"] is False
        assert "Unknown" in result["error"]

    def test_all_valid_types_preview_successfully(self):
        types = [
            "DAILY_SUMMARY", "LOSS_MINOR", "LOSS_MAJOR",
            "UPSIDE_ALERT", "TARGET_REACHED", "DEADLINE"
        ]
        for t in types:
            result = preview_notification(
                t, make_investment(), make_profit(),
                recommendation=make_recommendation(), days_remaining=15
            )
            assert result["success"] is True, f"Failed for type: {t}"

    def test_missing_email_credentials_returns_error(self, mocker):
        """send_notification should fail gracefully if .env not configured"""
        mocker.patch("agents.notification.SMTP_USER", "")
        mocker.patch("agents.notification.SMTP_PASSWORD", "")
        result = send_notification(
            "DAILY_SUMMARY", make_investment(), make_profit(),
            days_remaining=15
        )
        assert result["success"] is False
        assert "credentials" in result["error"].lower()

    def test_missing_client_email_returns_error(self, mocker):
        mocker.patch("agents.notification.SMTP_USER", "sender@gmail.com")
        mocker.patch("agents.notification.SMTP_PASSWORD", "password")
        mocker.patch("agents.notification.CLIENT_EMAIL", "")
        result = send_notification(
            "DAILY_SUMMARY", make_investment(), make_profit(),
            days_remaining=15
        )
        assert result["success"] is False
        assert "CLIENT_EMAIL" in result["error"]


# ─────────────────────────────────────────────────────────────────
#  SEND PATH TEST (mocked SMTP — no real email)
# ─────────────────────────────────────────────────────────────────

class TestSendPath:

    def test_successful_send_returns_alert_id(self, mocker):
        """Mock SMTP so no real email is sent — verify the return structure"""
        mocker.patch("agents.notification.SMTP_USER",    "sender@gmail.com")
        mocker.patch("agents.notification.SMTP_PASSWORD","testpassword")
        mocker.patch("agents.notification.CLIENT_EMAIL", "client@example.com")

        # Mock the entire SMTP connection
        mock_smtp = mocker.MagicMock()
        mocker.patch("agents.notification.smtplib.SMTP",
                     return_value=mock_smtp.__enter__.return_value)

        result = send_notification(
            "DAILY_SUMMARY", make_investment(), make_profit(),
            days_remaining=15
        )

        assert result["success"] is True
        assert "alert_id"  in result
        assert "sent_at"   in result
        assert "subject"   in result
        assert "recipient" in result
        assert result["recipient"] == "client@example.com"

    def test_smtp_auth_error_returns_friendly_message(self, mocker):
        """SMTP auth failure should return a clear error, not a crash"""
        import smtplib
        mocker.patch("agents.notification.SMTP_USER",    "sender@gmail.com")
        mocker.patch("agents.notification.SMTP_PASSWORD","wrongpassword")
        mocker.patch("agents.notification.CLIENT_EMAIL", "client@example.com")
        mocker.patch("agents.notification.smtplib.SMTP",
                     side_effect=smtplib.SMTPAuthenticationError(535, "Bad credentials"))

        result = send_notification(
            "DAILY_SUMMARY", make_investment(), make_profit(),
            days_remaining=15
        )

        assert result["success"] is False
        assert "authentication" in result["error"].lower()
