# NOTIF Agent — Notification Agent
#
# This agent has ONE job: format and send email notifications to the client.
# It never makes business decisions — it only renders messages and dispatches.
#
# Why it exists as a separate agent:
#   Five different alert types exist in the system (loss, upside, target,
#   deadline, daily summary). If each agent that triggers an alert also
#   formatted and sent the email, the email logic would be scattered
#   across 5 files. This agent centralises all formatting and delivery
#   in one place. Changing the email template or switching providers
#   requires touching only this file.
#
# Email delivery uses Python's built-in smtplib — no external libraries.
# Credentials are read from .env (SMTP_HOST, SMTP_PORT, SMTP_USER,
# SMTP_PASSWORD, CLIENT_EMAIL).
#
# For Gmail: generate an App Password at
#   https://myaccount.google.com/apppasswords
# (Your regular Gmail password won't work with SMTP)

import os
import smtplib
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── Email config from .env ────────────────────────────────────────
SMTP_HOST    = os.getenv("SMTP_HOST",    "smtp.gmail.com")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER    = os.getenv("SMTP_USER",    "")
SMTP_PASSWORD= os.getenv("SMTP_PASSWORD","")
CLIENT_EMAIL = os.getenv("CLIENT_EMAIL", "")
SENDER_NAME  = os.getenv("SENDER_NAME",  "Stock Agent")


# ─────────────────────────────────────────────────────────────────
#  MAIN FUNCTION
# ─────────────────────────────────────────────────────────────────

def send_notification(
    notification_type: str,
    investment: dict,
    profit_data: dict,
    recommendation: dict = None,
    days_remaining: int = None
) -> dict:
    """
    Formats and sends an email notification to the client.

    Parameters:
        notification_type : One of:
                            "DAILY_SUMMARY"
                            "LOSS_MINOR"
                            "LOSS_MAJOR"
                            "UPSIDE_ALERT"
                            "TARGET_REACHED"
                            "DEADLINE"
        investment        : The investment record from INV agent
        profit_data       : Output from PROFIT agent
        recommendation    : Output from SIT agent (optional — for LOSS_MAJOR,
                            TARGET_REACHED, DEADLINE only)
        days_remaining    : Days left in 30-day window

    Returns:
        {
            "success":    bool,
            "alert_id":   str,   # unique ID for this notification
            "sent_at":    str,
            "recipient":  str,
            "subject":    str
        }
    """

    # ── Validate config ───────────────────────────────────────────
    if not SMTP_USER or not SMTP_PASSWORD:
        return {
            "success": False,
            "error": "Email credentials not configured. "
                     "Set SMTP_USER and SMTP_PASSWORD in .env"
        }

    if not CLIENT_EMAIL:
        return {
            "success": False,
            "error": "CLIENT_EMAIL not set in .env"
        }

    valid_types = {
        "DAILY_SUMMARY", "LOSS_MINOR", "LOSS_MAJOR",
        "UPSIDE_ALERT", "TARGET_REACHED", "DEADLINE"
    }
    if notification_type not in valid_types:
        return {
            "success": False,
            "error": f"Unknown notification_type '{notification_type}'. "
                     f"Must be one of {valid_types}"
        }

    # ── Build the email ───────────────────────────────────────────
    alert_id = str(uuid.uuid4())[:8].upper()   # short readable ID e.g. "A3F7B2C1"
    subject, body = _build_email(
        notification_type, investment, profit_data,
        recommendation, days_remaining, alert_id
    )

    # ── Send via SMTP ─────────────────────────────────────────────
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{SENDER_NAME} <{SMTP_USER}>"
        msg["To"]      = CLIENT_EMAIL

        # Plain text version (fallback for email clients that don't render HTML)
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()          # encrypt the connection
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, CLIENT_EMAIL, msg.as_string())

        return {
            "success":   True,
            "alert_id":  alert_id,
            "sent_at":   datetime.now().isoformat(),
            "recipient": CLIENT_EMAIL,
            "subject":   subject
        }

    except smtplib.SMTPAuthenticationError:
        return {
            "success": False,
            "error": "SMTP authentication failed. "
                     "Check SMTP_USER and SMTP_PASSWORD in .env. "
                     "For Gmail, use an App Password."
        }
    except smtplib.SMTPException as e:
        return {
            "success": False,
            "error": f"SMTP error: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to send email: {str(e)}"
        }


# ─────────────────────────────────────────────────────────────────
#  EMAIL BUILDER — routes to the correct template
# ─────────────────────────────────────────────────────────────────

def _build_email(
    notification_type, investment, profit_data,
    recommendation, days_remaining, alert_id
) -> tuple:
    """Returns (subject, body) for the given notification type."""

    ticker         = investment.get("ticker", "UNKNOWN")
    purchase_price = investment.get("purchase_price", 0)
    shares         = investment.get("shares", 0)
    total_invested = investment.get("total_invested", 0)
    current_price  = profit_data.get("current_price", 0)
    profit_pct     = profit_data.get("profit_loss_pct", 0)
    profit_dollars = profit_data.get("profit_loss_dollars", 0)
    days_left      = days_remaining if days_remaining is not None else "?"

    sign = "+" if profit_pct >= 0 else ""

    if notification_type == "DAILY_SUMMARY":
        return _daily_summary(
            ticker, current_price, purchase_price,
            profit_pct, profit_dollars, days_left, sign,
            profit_data.get("change_since_last_poll", 0)
        )

    elif notification_type == "LOSS_MINOR":
        return _loss_minor(
            ticker, current_price, purchase_price,
            profit_pct, profit_dollars, days_left, alert_id
        )

    elif notification_type == "LOSS_MAJOR":
        return _loss_major(
            ticker, current_price, purchase_price,
            profit_pct, profit_dollars, days_left,
            recommendation, alert_id
        )

    elif notification_type == "UPSIDE_ALERT":
        return _upside_alert(
            ticker, current_price, profit_pct, profit_dollars,
            days_left, sign, alert_id,
            profit_data.get("change_since_last_poll", 0)
        )

    elif notification_type == "TARGET_REACHED":
        return _target_reached(
            ticker, current_price, profit_pct, profit_dollars,
            days_left, sign, recommendation, alert_id
        )

    elif notification_type == "DEADLINE":
        return _deadline(
            ticker, current_price, purchase_price,
            profit_pct, profit_dollars, recommendation, alert_id,
            investment.get("deadline_date", "")
        )


# ─────────────────────────────────────────────────────────────────
#  EMAIL TEMPLATES
# ─────────────────────────────────────────────────────────────────

def _daily_summary(ticker, current_price, purchase_price,
                   profit_pct, profit_dollars, days_left,
                   sign, change_today) -> tuple:
    subject = f"📈 Daily Update: {ticker} | {sign}{profit_pct:.2f}% P&L"
    body = f"""Stock Agent — Daily Summary
{'='*45}

  Stock:              {ticker}
  Current Price:      ${current_price:,.2f}
  Purchase Price:     ${purchase_price:,.2f}
  Today's Change:     {'+' if change_today >= 0 else ''}{change_today:.2f}%

  Your Profit/Loss:   {sign}{profit_pct:.2f}% (${profit_dollars:+,.2f})
  Days Remaining:     {days_left} days
  Target:             5.00%

{'='*45}
This is an automated daily summary from your Stock Agent.
"""
    return subject, body


def _loss_minor(ticker, current_price, purchase_price,
                profit_pct, profit_dollars, days_left, alert_id) -> tuple:
    subject = f"⚠️ Loss Alert: {ticker} is below your purchase price"
    body = f"""Stock Agent — Loss Alert  [ID: {alert_id}]
{'='*45}

  Your investment in {ticker} has dropped below your purchase price.

  Purchase Price:     ${purchase_price:,.2f}
  Current Price:      ${current_price:,.2f}
  Current Loss:       {profit_pct:.2f}% (${profit_dollars:,.2f})
  Days Remaining:     {days_left} days

  What would you like to do?
    → Reply "HOLD"  to continue monitoring
    → Reply "SELL"  to exit the position

  If you don't respond, monitoring will continue automatically.

{'='*45}
Alert ID: {alert_id} | Stock Agent
"""
    return subject, body


def _loss_major(ticker, current_price, purchase_price,
                profit_pct, profit_dollars, days_left,
                recommendation, alert_id) -> tuple:
    subject = f"🚨 Action Required: {ticker} loss exceeds 1%"

    rec_text = ""
    if recommendation and recommendation.get("success"):
        rec = recommendation.get("recommendation", "MONITOR_CLOSELY")
        reason = recommendation.get("reason", "")
        confidence = recommendation.get("confidence", "LOW")
        signals = recommendation.get("signals", {})
        rec_text = f"""
  System Analysis:
  ─────────────────────────────────────────
  SMA Trend:          {'Recovering' if signals.get('sma_signal') == 'ABOVE' else 'Declining'}
  3-Day Momentum:     {signals.get('momentum_3d_pct', 0):+.2f}%
  Selling Pressure:   {signals.get('volume_pressure', 'N/A')}
  ─────────────────────────────────────────
  Recommendation:     {rec}
  Confidence:         {confidence}
  Reason:             {reason}

  Note: This is a system analysis, not financial advice.
"""

    body = f"""Stock Agent — Major Loss Alert  [ID: {alert_id}]
{'='*45}

  Your investment in {ticker} has dropped more than 1%.

  Purchase Price:     ${purchase_price:,.2f}
  Current Price:      ${current_price:,.2f}
  Current Loss:       {profit_pct:.2f}% (${profit_dollars:,.2f})
  Days Remaining:     {days_left} days
{rec_text}
  What would you like to do?
    → Reply "HOLD"  to continue monitoring
    → Reply "SELL"  to exit the position

{'='*45}
Alert ID: {alert_id} | Stock Agent
"""
    return subject, body


def _upside_alert(ticker, current_price, profit_pct, profit_dollars,
                  days_left, sign, alert_id, change_since_last) -> tuple:
    subject = f"📈 Profit Update: {ticker} at {sign}{profit_pct:.2f}% — moving toward 5%"
    body = f"""Stock Agent — Profit Alert  [ID: {alert_id}]
{'='*45}

  {ticker} is moving toward your 5% target!

  Current Price:      ${current_price:,.2f}
  Your Profit:        {sign}{profit_pct:.2f}% (${profit_dollars:+,.2f})
  Change since last:  {'+' if change_since_last >= 0 else ''}{change_since_last:.2f}% ↑
  Days Remaining:     {days_left} days
  Target:             5.00%

  What would you like to do?
    → Reply "CONTINUE"  to keep holding toward 5%
    → Reply "SELL"      to lock in {sign}{profit_pct:.2f}% profit now

  If you don't respond, monitoring will continue automatically.

{'='*45}
Alert ID: {alert_id} | Stock Agent
"""
    return subject, body


def _target_reached(ticker, current_price, profit_pct, profit_dollars,
                    days_left, sign, recommendation, alert_id) -> tuple:
    subject = f"🎯 Target Reached! {ticker} hit {sign}{profit_pct:.2f}% profit"

    rec_text = ""
    if recommendation and recommendation.get("success"):
        rec = recommendation.get("recommendation", "NEUTRAL")
        reason = recommendation.get("reason", "")
        confidence = recommendation.get("confidence", "LOW")
        signals = recommendation.get("signals", {})
        rec_text = f"""
  Should you sell now or hold for more?
  ─────────────────────────────────────────
  3-Day Momentum:     {signals.get('momentum_3d_pct', 0):+.2f}%
  Volume Trend:       {'Buyers dominant' if signals.get('volume_pressure') == 'LOW' else 'Sellers active'}
  ─────────────────────────────────────────
  Recommendation:     {rec}
  Confidence:         {confidence}
  Reason:             {reason}

  Note: This is a system analysis, not financial advice.
"""

    body = f"""Stock Agent — Profit Target Reached!  [ID: {alert_id}]
{'='*45}

  🎯 {ticker} has hit your 5% profit goal!

  Current Price:      ${current_price:,.2f}
  Your Profit:        {sign}{profit_pct:.2f}% (${profit_dollars:+,.2f})
  Days Remaining:     {days_left} days
{rec_text}
  What would you like to do?
    → Reply "SELL"      to lock in profit (recommended)
    → Reply "CONTINUE"  to keep holding for potentially more gains

{'='*45}
Alert ID: {alert_id} | Stock Agent
"""
    return subject, body


def _deadline(ticker, current_price, purchase_price,
              profit_pct, profit_dollars, recommendation,
              alert_id, deadline_date) -> tuple:
    sign = "+" if profit_pct >= 0 else ""
    subject = f"⏰ 30-Day Window Closed: {ticker} at {sign}{profit_pct:.2f}%"

    rec_text = ""
    if recommendation and recommendation.get("success"):
        rec = recommendation.get("recommendation", "UNCERTAIN")
        reason = recommendation.get("reason", "")
        signals = recommendation.get("signals", {})
        gap = signals.get("gap_to_target_pct", 5.0 - profit_pct)
        days_up = signals.get("days_up_in_last_7", "?")
        rate = signals.get("daily_rate_needed", "?")
        rec_text = f"""
  End-of-Period Analysis:
  ─────────────────────────────────────────
  Gap to 5% target:   {gap:.2f}%
  7-Day trend:        {days_up}/7 days positive
  Daily rate needed:  {rate:.2f}%/day (to hit 5% in 5 days)
  ─────────────────────────────────────────
  Recommendation:     {rec}
  Reason:             {reason}

  Note: This is a system analysis, not financial advice.
"""

    body = f"""Stock Agent — 30-Day Window Closed  [ID: {alert_id}]
{'='*45}

  Your 30-day monitoring window for {ticker} has ended.

  Purchase Price:     ${purchase_price:,.2f}
  Current Price:      ${current_price:,.2f}
  Final P&L:          {sign}{profit_pct:.2f}% (${profit_dollars:+,.2f})
  Target:             5.00%
  Deadline:           {deadline_date}
{rec_text}
  What would you like to do?
    → Reply "SELL"    to close the position now
    → Reply "EXTEND"  to open a new 30-day monitoring window

{'='*45}
Alert ID: {alert_id} | Stock Agent
"""
    return subject, body


# ─────────────────────────────────────────────────────────────────
#  PREVIEW FUNCTION (no email sent — for testing templates)
# ─────────────────────────────────────────────────────────────────

def preview_notification(
    notification_type: str,
    investment: dict,
    profit_data: dict,
    recommendation: dict = None,
    days_remaining: int = None
) -> dict:
    """
    Returns the formatted email subject and body WITHOUT sending it.
    Use this to test templates in development without email credentials.
    """
    valid_types = {
        "DAILY_SUMMARY", "LOSS_MINOR", "LOSS_MAJOR",
        "UPSIDE_ALERT", "TARGET_REACHED", "DEADLINE"
    }
    if notification_type not in valid_types:
        return {
            "success": False,
            "error": f"Unknown notification_type '{notification_type}'"
        }

    alert_id = "PREVIEW"
    subject, body = _build_email(
        notification_type, investment, profit_data,
        recommendation, days_remaining, alert_id
    )
    return {
        "success": True,
        "subject": subject,
        "body":    body
    }
