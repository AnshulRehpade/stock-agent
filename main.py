# main.py — Production Entry Point
#
# This is what Railway runs on the cloud server.
# It starts the Orchestrator and the Scheduler, then keeps running forever.
#
# The system is interactive via environment variables:
#   TICKER  — stock to monitor (e.g. "AMD")
#   BUDGET  — investment budget in USD (e.g. "5000")
#
# Set these as environment variables in Railway dashboard.
#
# Flow:
#   1. Read TICKER and BUDGET from environment
#   2. Confirm the investment
#   3. Start the scheduler (hourly polls + daily summary)
#   4. Keep running until the investment is closed

import os
import sys
import time
import signal
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s  %(levelname)s  %(message)s"
)
logger = logging.getLogger("main")


def main():
    # ── Read config from environment ──────────────────────────────
    ticker = os.getenv("TICKER", "").upper().strip()
    budget_str = os.getenv("BUDGET", "").strip()

    if not ticker:
        logger.error("TICKER environment variable not set. "
                     "Set it in Railway dashboard (e.g. TICKER=AMD)")
        sys.exit(1)

    if not budget_str:
        logger.error("BUDGET environment variable not set. "
                     "Set it in Railway dashboard (e.g. BUDGET=5000)")
        sys.exit(1)

    try:
        budget = float(budget_str.replace(",", "").replace("$", ""))
    except ValueError:
        logger.error(f"BUDGET must be a number. Got: '{budget_str}'")
        sys.exit(1)

    logger.info("=" * 55)
    logger.info("  STOCK AGENT — Production Mode")
    logger.info(f"  Ticker: {ticker}  |  Budget: ${budget:,.2f}")
    logger.info("=" * 55)

    # ── Start Orchestrator ────────────────────────────────────────
    from agents.orchestrator import StockAgentOrchestrator
    orch = StockAgentOrchestrator(
        db_path  = "data/production.db",
        dry_run  = False   # send real emails
    )

    # ── Confirm investment ────────────────────────────────────────
    logger.info(f"Fetching current price for {ticker}...")
    result = orch.confirm_investment(ticker, budget)

    if not result["success"]:
        logger.error(f"Investment failed: {result['error']}")
        sys.exit(1)

    inv = result["investment"]
    logger.info(f"Investment confirmed:")
    logger.info(f"  {ticker} — {inv['shares']} shares @ "
                f"${inv['purchase_price']:.2f}")
    logger.info(f"  Total invested: ${inv['total_invested']:,.2f}")
    logger.info(f"  Deadline: {inv['deadline_date']}")

    # ── Start Scheduler ───────────────────────────────────────────
    from agents.scheduler import create_scheduler
    scheduler = create_scheduler(orch)
    scheduler.start()
    logger.info("Scheduler started.")
    logger.info("Hourly polls: Mon–Fri 10 AM – 3 PM ET")
    logger.info("Daily summary: Mon–Fri 4:05 PM ET")

    # ── Graceful shutdown on SIGTERM (Railway sends this to stop) ─
    def shutdown(signum, frame):
        logger.info("Shutdown signal received. Stopping scheduler...")
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped. Goodbye.")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # ── Keep running ──────────────────────────────────────────────
    logger.info("System running. Waiting for scheduled polls...")
    while True:
        if orch.phase == "CLOSED":
            logger.info("Investment closed. System shutting down.")
            scheduler.shutdown(wait=False)
            sys.exit(0)
        time.sleep(30)


if __name__ == "__main__":
    main()
