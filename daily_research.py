"""
Daily 4am AEST research job.

Flow:
  1. Call Anthropic API — ask for today's top VCP/momentum setups on ASX + US
  2. Parse the structured JSON response to extract tickers + metadata
  3. Call IGClient.sync_watchlist() to update "VCP Setups" watchlist
  4. Log a clean summary to Railway logs
"""

import json
import logging
import os
from datetime import datetime

import requests

log = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL   = "claude-sonnet-4-6"


def _call_anthropic(prompt: str) -> str:
    """Call Anthropic API and return the text response."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY env var not set")

    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"]


def get_daily_setups() -> dict:
    """
    Ask Claude to identify today's top VCP/momentum setups.
    Returns parsed JSON with tickers and metadata.
    """
    today = datetime.now().strftime("%A %d %B %Y")

    prompt = f"""You are a senior momentum trader using Mark Minervini's VCP (Volatility Contraction Pattern) 
and SEPA methodology. Today is {today} AEST (Sydney time).

Research the current ASX and US equity markets and identify stocks that are:
1. In a confirmed Stage 2 uptrend (price above rising 50, 150, 200-day SMAs)
2. Forming a VCP base — 2-4 progressively tighter contractions, volume drying up
3. Either approaching a pivot breakout point OR showing an undercut-and-rally pattern
4. Relative Strength 70+ vs their respective index
5. Liquid — ASX: $200K+ avg daily volume; US: $500K+ avg daily volume

Consider: ASX gold/resources/mining-services, US financials/industrials/energy, any sector 
showing genuine Stage 2 leadership right now. Avoid: ASX tech (WTC, XRO), ASX healthcare (CSL, COH),
any stock below its 200-day SMA.

Return ONLY valid JSON, no markdown fences, no preamble:

{{
  "date": "{today}",
  "market_regime": "BULL | CAUTION | BEAR",
  "regime_note": "One sentence on overall market health",
  "setups": [
    {{
      "ticker": "BHP.AX",
      "name": "BHP Group",
      "exchange": "ASX",
      "action": "BREAKOUT | UNDERCUT | WATCH",
      "current_price": 45.20,
      "pivot": 46.50,
      "stop": 43.00,
      "risk_pct": 7.5,
      "thesis": "One sentence — why this setup is valid now",
      "catalyst": "Any upcoming event or null",
      "quality": 85
    }}
  ],
  "avoid": ["WTC.AX", "XRO.AX"],
  "summary": "2-3 sentence exec summary of today's opportunity set"
}}

Include 5-10 genuine setups only. Do not manufacture setups that don't exist.
If the market is hostile (bear regime, few setups), say so clearly and return fewer tickers."""

    log.info("Calling Anthropic API for daily setups...")
    raw = _call_anthropic(prompt)

    # Strip any accidental markdown fences
    clean = raw.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    clean = clean.strip()

    return json.loads(clean)


def run_daily_watchlist_update():
    """
    Main entry point — called by APScheduler at 4am AEST Mon–Fri.
    Gets setups from Claude, filters to actionable ones, syncs to IG.
    """
    log.info("=" * 60)
    log.info("DAILY WATCHLIST UPDATE — starting")
    log.info("=" * 60)

    # ── 1. Get setups from Claude ─────────────────────────────────
    try:
        data = get_daily_setups()
    except Exception as e:
        log.error(f"Failed to get daily setups: {e}")
        return

    regime    = data.get("market_regime", "UNKNOWN")
    setups    = data.get("setups", [])
    summary   = data.get("summary", "")
    avoid     = data.get("avoid", [])

    log.info(f"Market regime: {regime}")
    log.info(f"Summary: {summary}")
    log.info(f"Setups identified: {len(setups)}")

    if not setups:
        log.warning("No setups identified today — watchlist not updated")
        return

    # ── 2. Extract tickers (BREAKOUT + UNDERCUT only, skip WATCH-only) ──
    actionable = [
        s for s in setups
        if s.get("action") in ("BREAKOUT", "UNDERCUT") and s.get("quality", 0) >= 50
    ]

    if not actionable:
        log.info("No actionable setups (all WATCH-only or quality < 50) — using all setups")
        actionable = setups

    tickers = [s["ticker"] for s in actionable]

    log.info(f"Actionable tickers for IG watchlist: {tickers}")
    for s in actionable:
        log.info(
            f"  {s['ticker']:12s} {s['action']:10s} "
            f"price={s.get('current_price','?')} "
            f"pivot={s.get('pivot','?')} "
            f"stop={s.get('stop','?')} "
            f"risk={s.get('risk_pct','?')}% "
            f"q={s.get('quality','?')} — {s.get('thesis','')}"
        )

    # ── 3. Sync to IG watchlist ───────────────────────────────────
    try:
        from ig_client import IGClient
        client = IGClient()
        result = client.sync_watchlist(
            tickers=tickers,
            watchlist_name="VCP Setups",
            replace=True,      # Fresh list every morning
        )
        log.info(
            f"IG watchlist sync: added={result.get('added',0)} "
            f"failed={result.get('failed',0)} "
            f"watchlist='{result.get('watchlist','VCP Setups')}'"
        )
        for detail in result.get("details", []):
            status = detail.get("status")
            epic   = detail.get("epic", "no epic")
            ticker = detail.get("ticker")
            icon   = "✓" if status == "added" else "✗"
            log.info(f"  {icon} {ticker} → {epic} ({status})")

    except Exception as e:
        log.error(f"IG watchlist sync failed: {e}")

    log.info("DAILY WATCHLIST UPDATE — complete")
    log.info("=" * 60)
