"""
VCP Screener — Minervini Volatility Contraction Pattern
Entry point. Runs on a schedule during market hours.
"""

import logging
import time
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config import Config
from screener.runner import run_scan
from screener.notifier import Notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("main")


def job():
    log.info("=== Starting VCP scan ===")
    try:
        results = run_scan()
        notifier = Notifier()
        if results:
            log.info(f"Found {len(results)} VCP candidates")
            notifier.send_results(results)
        else:
            log.info("No VCP candidates found this scan")
    except Exception as e:
        log.exception(f"Scan failed: {e}")


if __name__ == "__main__":
    cfg = Config()

    # Run once immediately on startup
    job()

    # Then on schedule
    scheduler = BlockingScheduler(timezone=cfg.TIMEZONE)

    if cfg.MARKET == "ASX":
        # ASX: Mon–Fri, every 30 min between 10:00–16:00 AEST
        scheduler.add_job(
            job,
            CronTrigger(
                day_of_week="mon-fri",
                hour="10-15",
                minute=f"0/{cfg.SCAN_INTERVAL_MINUTES}",
                timezone=cfg.TIMEZONE,
            ),
        )
    else:
        # US: Mon–Fri, every 30 min between 09:30–16:00 ET
        scheduler.add_job(
            job,
            CronTrigger(
                day_of_week="mon-fri",
                hour="9-15",
                minute=f"30/{cfg.SCAN_INTERVAL_MINUTES}",
                timezone=cfg.TIMEZONE,
            ),
        )

    log.info(
        f"Scheduler started — {cfg.MARKET} market, "
        f"every {cfg.SCAN_INTERVAL_MINUTES} min during market hours"
    )
    scheduler.start()
