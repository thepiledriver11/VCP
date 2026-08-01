"""
Seeds today's VCP setups directly into the IG watchlist.
Runs once on startup — bypasses the Anthropic API call so it works
even before ANTHROPIC_API_KEY is configured.
"""

import logging
log = logging.getLogger(__name__)

# ── Today's setups from the August 1, 2026 research run ──────
TODAY_SETUPS = {
    "watchlist_name": "VCP Setups",
    "tickers": [
        # ASX — Stage 2 uptrends with pivot approaching
        "MQG.AX",   # Macquarie — financials leader, all-time highs
        "CDA.AX",   # Codan — 50-day retest, FY results Aug 20
        "LYL.AX",   # Lycopodium — within 1.4% of 52-wk high pivot
        "MIN.AX",   # Mineral Resources — just reclaimed 200-day
        # US — financials + industrials breakouts
        "JPM",      # JPMorgan — within 2% of all-time high pivot
        "BAC",      # Bank of America — near 52-wk high
        "GE",       # GE Aerospace — 5% below ATH, post-earnings base
        "HON",      # Honeywell — approaching pivot $261
    ]
}


def seed_todays_watchlist():
    """Push today's researched tickers to IG on startup."""
    from ig_client import IGClient

    tickers = TODAY_SETUPS["tickers"]
    name    = TODAY_SETUPS["watchlist_name"]

    log.info(f"Seeding today's watchlist with {len(tickers)} tickers: {tickers}")

    client = IGClient()
    result = client.sync_watchlist(tickers=tickers, watchlist_name=name, replace=True)

    if "error" in result:
        log.error(f"Seed failed: {result['error']}")
        return

    log.info(
        f"Seed complete — added={result.get('added', 0)} "
        f"failed={result.get('failed', 0)} "
        f"watchlist='{result.get('watchlist', name)}'"
    )
    for d in result.get("details", []):
        icon = "✓" if d.get("status") == "added" else "✗"
        log.info(f"  {icon} {d.get('ticker'):12s} → {d.get('epic', 'no epic')} ({d.get('status')})")
