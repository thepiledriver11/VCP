"""
Daily VCP Research Cron Job
Runs once, exits cleanly. Railway calls this on schedule.

Flow:
  1. Call Claude API → get today's top ASX + US VCP setups
  2. Find or create "VCP Setups" watchlist in IG Markets
  3. Clear it and populate with today's tickers
  4. Print summary and exit
"""

import json
import logging
import os
import sys
import requests
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
TODAY = datetime.now().strftime("%A %d %B %Y")


def get_vcp_setups() -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set")
        sys.exit(1)

    prompt = f"""You are a senior momentum trader using Minervini VCP/SEPA methodology. Today is {TODAY} AEST.

Research current ASX and US equity markets. Find stocks that are:
1. Stage 2 uptrend — price above rising 50, 150, 200-day SMAs
2. VCP base forming — 2-4 progressively tighter contractions, volume drying up
3. Within 5% of a pivot breakout OR showing an undercut-and-rally setup
4. RS 70+ vs their index. ASX: $200K+ avg daily volume. US: $500K+ avg daily volume.

Focus on: ASX gold/resources/mining-services/quality-financials, US financials/industrials.
Avoid: ASX tech (WTC, XRO), ASX healthcare (CSL, COH), anything below its 200-day SMA.

Return ONLY valid JSON — no markdown, no explanation:
{{
  "date": "{TODAY}",
  "market_regime": "BULL | CAUTION | BEAR",
  "regime_note": "one sentence",
  "setups": [
    {{
      "ticker": "MQG.AX",
      "name": "Macquarie Group",
      "exchange": "ASX",
      "action": "BREAKOUT | UNDERCUT | WATCH",
      "current_price": 250.00,
      "pivot": 255.00,
      "stop": 238.00,
      "risk_pct": 6.7,
      "thesis": "one sentence — why valid now",
      "quality": 80
    }}
  ],
  "summary": "2-3 sentence exec summary of today's opportunity set"
}}

Return 5-10 genuine setups only. If the market is hostile return fewer — do not manufacture setups."""

    log.info("Calling Claude API for today's VCP setups...")
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"]
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(clean)
    except Exception as e:
        log.error(f"Claude API call failed: {e}")
        sys.exit(1)


def sync_to_ig(tickers: list[str]) -> dict:
    ig_api_key  = os.getenv("IG_API_KEY")
    ig_username = os.getenv("IG_USERNAME")
    ig_password = os.getenv("IG_PASSWORD")
    ig_demo     = os.getenv("IG_DEMO", "false").lower() == "true"
    base_url    = "https://demo-api.ig.com/gateway/deal" if ig_demo else "https://api.ig.com/gateway/deal"

    if not all([ig_api_key, ig_username, ig_password]):
        log.error("IG credentials not set — add IG_API_KEY, IG_USERNAME, IG_PASSWORD to Railway Variables")
        return {"error": "missing credentials"}

    session = requests.Session()

    # ── Authenticate ─────────────────────────────────────────────
    log.info(f"Authenticating with IG Markets ({'demo' if ig_demo else 'live'})...")
    try:
        auth = session.post(
            f"{base_url}/session",
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "Accept": "application/json; charset=UTF-8",
                "X-IG-API-KEY": ig_api_key,
                "Version": "2",
            },
            json={"identifier": ig_username, "password": ig_password, "encryptedPassword": False},
            timeout=15,
        )
        auth.raise_for_status()
        cst   = auth.headers.get("CST")
        token = auth.headers.get("X-SECURITY-TOKEN")
        if not cst or not token:
            log.error("Auth succeeded but no session tokens returned")
            return {"error": "no session tokens"}
        log.info("IG authentication successful")
    except Exception as e:
        log.error(f"IG authentication failed: {e}")
        return {"error": str(e)}

    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
        "X-IG-API-KEY": ig_api_key,
        "CST": cst,
        "X-SECURITY-TOKEN": token,
        "Version": "1",
    }

    # ── Find or create "VCP Setups" watchlist ────────────────────
    log.info("Looking for VCP Setups watchlist...")
    watchlist_id = None
    try:
        wl_resp = session.get(f"{base_url}/watchlists", headers=headers, timeout=15)
        wl_resp.raise_for_status()
        for wl in wl_resp.json().get("watchlists", []):
            if wl.get("name", "").lower() == "vcp setups":
                watchlist_id = wl["id"]
                log.info(f"Found existing watchlist — id={watchlist_id}")
                break
    except Exception as e:
        log.error(f"Failed to fetch watchlists: {e}")

    if not watchlist_id:
        log.info("Creating VCP Setups watchlist...")
        try:
            create = session.post(
                f"{base_url}/watchlists",
                headers=headers,
                json={"name": "VCP Setups", "epics": []},
                timeout=15,
            )
            create.raise_for_status()
            watchlist_id = create.json().get("watchlistId")
            log.info(f"Created watchlist — id={watchlist_id}")
        except Exception as e:
            log.error(f"Failed to create watchlist: {e}")
            return {"error": str(e)}

    # ── Clear existing instruments ───────────────────────────────
    log.info("Clearing existing watchlist instruments...")
    try:
        existing = session.get(f"{base_url}/watchlists/{watchlist_id}", headers=headers, timeout=15)
        existing.raise_for_status()
        for market in existing.json().get("markets", []):
            epic = market.get("epic")
            if epic:
                session.delete(f"{base_url}/watchlists/{watchlist_id}/{epic}", headers=headers, timeout=10)
    except Exception as e:
        log.warning(f"Could not clear watchlist (continuing): {e}")

    # ── Add tickers ──────────────────────────────────────────────
    added, failed = 0, 0
    for ticker in tickers:
        search_term = ticker.replace(".AX", "").replace("-", ".")
        try:
            # Search for epic
            search = session.get(
                f"{base_url}/markets",
                headers=headers,
                params={"searchTerm": search_term},
                timeout=15,
            )
            search.raise_for_status()
            markets = search.json().get("markets", [])

            if not markets:
                log.warning(f"  ✗ {ticker} — no IG market found")
                failed += 1
                continue

            # Pick best match — don't filter on epic string format (varies by IG region)
            epic = None
            for m in markets:
                m_name = m.get("instrumentName", "").upper()
                m_type = m.get("instrumentType", "")
                if search_term.upper() in m_name and m_type == "SHARES":
                    epic = m.get("epic"); break
            if not epic:
                # Fallback: first SHARES instrument
                for m in markets:
                    if m.get("instrumentType") == "SHARES":
                        epic = m.get("epic"); break
            if not epic:
                # Last resort: first result
                epic = markets[0].get("epic")
            log.info(f"  → {ticker} matched: {markets[0].get('instrumentName','')} epic={epic}")

            # Add to watchlist
            add = session.put(
                f"{base_url}/watchlists/{watchlist_id}",
                headers=headers,
                json={"epic": epic},
                timeout=15,
            )
            if add.status_code in (200, 208):
                log.info(f"  ✓ {ticker:12s} → {epic}")
                added += 1
            else:
                log.warning(f"  ✗ {ticker} — add returned {add.status_code}")
                failed += 1

        except Exception as e:
            log.error(f"  ✗ {ticker} — {e}")
            failed += 1

    return {"added": added, "failed": failed, "watchlist_id": watchlist_id}


def main():
    log.info("=" * 60)
    log.info(f"VCP DAILY RESEARCH — {TODAY}")
    log.info("=" * 60)

    # ── 1. Get setups from Claude ────────────────────────────────
    data    = get_vcp_setups()
    regime  = data.get("market_regime", "UNKNOWN")
    summary = data.get("summary", "")
    setups  = data.get("setups", [])

    log.info(f"Regime:  {regime}")
    log.info(f"Summary: {summary}")
    log.info(f"Setups:  {len(setups)} found")

    for s in setups:
        icon = "🟢" if s["action"] == "BREAKOUT" else "🟡" if s["action"] == "UNDERCUT" else "👁"
        log.info(
            f"  {icon} {s['ticker']:12s} {s['action']:10s} "
            f"price={s.get('current_price','?'):8}  "
            f"pivot={s.get('pivot','?'):8}  "
            f"stop={s.get('stop','?'):8}  "
            f"risk={s.get('risk_pct','?')}%"
        )
        log.info(f"       {s.get('thesis','')}")

    # ── 2. Filter to actionable tickers ─────────────────────────
    tickers = [
        s["ticker"] for s in setups
        if s.get("action") in ("BREAKOUT", "UNDERCUT") and s.get("quality", 0) >= 50
    ]
    if not tickers:
        tickers = [s["ticker"] for s in setups]  # fallback: use all

    log.info(f"\nSyncing to IG: {tickers}")

    # ── 3. Update IG watchlist ───────────────────────────────────
    result = sync_to_ig(tickers)

    if "error" in result:
        log.error(f"IG sync failed: {result['error']}")
        sys.exit(1)

    log.info(f"\n{'='*60}")
    log.info(f"DONE — added={result['added']} failed={result['failed']}")
    log.info(f"VCP Setups watchlist updated in IG Markets")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    main()
