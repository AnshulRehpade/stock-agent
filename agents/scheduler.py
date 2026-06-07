# SCHED Agent — Scheduler
#
# Drives the monitoring loop automatically using APScheduler.
# Runs two recurring jobs:
#   1. Hourly poll  — every 60 minutes, Mon–Fri, 9:30 AM – 4:00 PM ET
#   2. Daily summary — once per day at 4:00 PM ET (market close)
#
# In production this runs as a background process on the cloud server.
# It fires ORCH.run_hourly_poll() and ORCH.send_daily_summary() on schedule.
#
# Why APScheduler over cron:
#   APScheduler runs inside the Python process — no separate cron setup
#   needed. It handles timezone-aware scheduling, market hours checks,
#   and integrates directly with the Orchestrator object in memory.

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import logging

# Market timezone — US Eastern (NYSE/NASDAQ)
MARKET_TZ = pytz.timezone("America/New_York")

# Set up logging so scheduler events appear in cloud logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s"
)
logger = logging.getLogger("scheduler")


def create_scheduler(orchestrator) -> BackgroundScheduler:
    """
    Creates and returns a configured APScheduler instance.

    Parameters:
        orchestrator : A running StockAgentOrchestrator instance.
                       The scheduler calls methods on this object.

    Returns the scheduler (not yet started — call scheduler.start()).

    Interview explanation:
        "The scheduler is decoupled from the Orchestrator — it just
        holds a reference to it and calls its methods on a timer.
        This means I can test the Orchestrator independently without
        needing a real scheduler running. The scheduler is the last
        piece that makes the system autonomous."
    """
    scheduler = BackgroundScheduler(timezone=MARKET_TZ)

    # ── Job 1: Hourly price poll ──────────────────────────────────
    # Runs every hour on the hour, Mon–Fri, during market hours.
    # 9:30 AM open — but we start at 10:00 AM to let the market settle.
    # Last poll at 3:00 PM (market closes 4:00 PM, data lags slightly).
    scheduler.add_job(
        func     = _run_poll,
        args     = [orchestrator],
        trigger  = CronTrigger(
            day_of_week = "mon-fri",
            hour        = "10,11,12,13,14,15",   # 10 AM – 3 PM ET
            minute      = 0,
            timezone    = MARKET_TZ
        ),
        id       = "hourly_poll",
        name     = "Hourly Price Poll",
        replace_existing = True
    )

    # ── Job 2: Daily summary email ────────────────────────────────
    # Sent once per day at 4:05 PM ET (just after market close).
    scheduler.add_job(
        func     = _send_daily_summary,
        args     = [orchestrator],
        trigger  = CronTrigger(
            day_of_week = "mon-fri",
            hour        = 16,
            minute      = 5,
            timezone    = MARKET_TZ
        ),
        id       = "daily_summary",
        name     = "Daily Summary Email",
        replace_existing = True
    )

    return scheduler


def _run_poll(orchestrator) -> None:
    """
    Runs one hourly poll cycle via the Orchestrator.
    Called automatically by the scheduler.
    Errors are caught and logged — never crash the scheduler.
    """
    if orchestrator.phase != "MONITORING":
        logger.info("Poll skipped — no active investment.")
        return

    try:
        result = orchestrator.run_hourly_poll()
        if result["success"]:
            sign = "+" if result["profit_pct"] >= 0 else ""
            logger.info(
                f"Poll completed | {orchestrator.investment['ticker']} "
                f"${result['current_price']:.2f} | "
                f"P&L: {sign}{result['profit_pct']:.2f}% | "
                f"Action: {result['action']}"
            )
        else:
            logger.warning(f"Poll failed: {result['error']}")
    except Exception as e:
        logger.error(f"Unexpected error in hourly poll: {e}", exc_info=True)


def _send_daily_summary(orchestrator) -> None:
    """
    Sends the daily summary email via the Orchestrator.
    Called automatically by the scheduler at 4:05 PM ET.
    """
    if orchestrator.phase != "MONITORING":
        logger.info("Daily summary skipped — no active investment.")
        return

    try:
        result = orchestrator.send_daily_summary()
        if result.get("success"):
            logger.info(
                f"Daily summary sent to {result.get('recipient', 'client')}"
            )
        else:
            logger.warning(f"Daily summary failed: {result.get('error')}")
    except Exception as e:
        logger.error(f"Unexpected error in daily summary: {e}", exc_info=True)
