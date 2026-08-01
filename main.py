"""
VCP Screener + Daily Research API
Three jobs running in parallel:
  1. APScheduler: Daily 4am AEST — Claude research → IG watchlist update
  2. APScheduler: Every 30 min market hours — VCP live scan + Slack alerts
  3. FastAPI server: REST endpoints for manual watchlist sync
"""

import logging
import threading
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import Config
from screener.runner import run_scan
from screener.notifier import Notifier
from ig_client import IGClient
from daily_research import run_daily_watchlist_update

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("main")

# ── FastAPI ───────────────────────────────────────────────────
app = FastAPI(title="VCP Screener API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Claude artifacts run from claude.ai
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class WatchlistSyncRequest(BaseModel):
    tickers: list[str]
    watchlist_name: str = "VCP Setups"
    replace: bool = True


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/watchlist/sync")
def sync_watchlist(req: WatchlistSyncRequest):
    """Manually sync a list of tickers to IG watchlist."""
    if not req.tickers:
        raise HTTPException(status_code=400, detail="No tickers provided")
    log.info(f"Manual watchlist sync: {req.tickers}")
    client = IGClient()
    result = client.sync_watchlist(
        tickers=req.tickers,
        watchlist_name=req.watchlist_name,
        replace=req.replace,
    )
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@app.post("/api/daily/run")
def trigger_daily():
    """Manually trigger the daily research + watchlist update."""
    log.info("Manual daily research triggered via API")
    threading.Thread(target=run_daily_watchlist_update, daemon=True).start()
    return {"status": "started", "message": "Daily research running in background — check logs"}


@app.get("/api/watchlists")
def list_watchlists():
    client = IGClient()
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="IG authentication failed")
    return {"watchlists": client.get_watchlists()}


# ── Scheduler ────────────────────────────────────────────────
def vcp_scan_job():
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


def start_scheduler():
    cfg = Config()
    scheduler = BackgroundScheduler(timezone="Australia/Sydney")

    # ── Job 1: Daily 4am AEST Mon–Fri — research + IG watchlist update ──
    scheduler.add_job(
        run_daily_watchlist_update,
        CronTrigger(
            day_of_week="mon-fri",
            hour=4,
            minute=0,
            timezone="Australia/Sydney",
        ),
        id="daily_research",
        name="Daily Research + IG Watchlist Update",
        misfire_grace_time=300,   # allow 5-min late start
    )
    log.info("Scheduled: Daily research at 4:00am AEST Mon–Fri")

    # ── Job 2: VCP live scan every 30 min during market hours ────
    if cfg.MARKET == "ASX":
        scheduler.add_job(
            vcp_scan_job,
            CronTrigger(
                day_of_week="mon-fri",
                hour="10-15",
                minute=f"0/{cfg.SCAN_INTERVAL_MINUTES}",
                timezone="Australia/Sydney",
            ),
            id="vcp_scan",
            name="VCP Live Scan",
        )
    else:
        scheduler.add_job(
            vcp_scan_job,
            CronTrigger(
                day_of_week="mon-fri",
                hour="9-15",
                minute=f"30/{cfg.SCAN_INTERVAL_MINUTES}",
                timezone="America/New_York",
            ),
            id="vcp_scan",
            name="VCP Live Scan",
        )
    log.info(f"Scheduled: VCP scan every {cfg.SCAN_INTERVAL_MINUTES} min during market hours")

    scheduler.start()

    # Run daily research once on startup so we can verify it works
    log.info("Running daily research on startup...")
    run_daily_watchlist_update()


if __name__ == "__main__":
    # Start scheduler + initial run in background thread
    t = threading.Thread(target=start_scheduler, daemon=True)
    t.start()

    # FastAPI in main thread
    log.info("Starting API server on port 8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
