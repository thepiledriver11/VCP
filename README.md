# VCP Screener — Minervini Volatility Contraction Pattern

Automated screener that detects Minervini VCP setups and fires alerts
when a stock is approaching or breaking its pivot price.

## How it works

4-stage funnel runs every 30 minutes during market hours:

1. **Trend Template** — Minervini's 8-point Stage 2 filter (SMA stack, 52wk range)
2. **Fundamental filter** — EPS growth, min price, min average volume
3. **VCP detection** — progressive contraction math, half-rule, higher lows, volume dry-up
4. **Breakout check** — pivot cross + volume confirmation; fires urgent alert

---

## Setup

### 1. Clone and deploy to Railway

```bash
git clone <your-repo>
cd vcp_screener
# push to Railway — it auto-detects railway.toml
```

### 2. Set environment variables in Railway dashboard

**Required — at least one notification channel:**
```
SLACK_TOKEN          your-slack-bot-token
SLACK_CHANNEL        #vcp-alerts
```
OR
```
WEBHOOK_URL          https://your-railway-service.up.railway.app/webhook
```

**Market config:**
```
MARKET               ASX          # ASX or US
TIMEZONE             Australia/Sydney  # or America/New_York
```

**Watchlist (optional — defaults to ASX 200 or S&P 500 if not set):**
```
WATCHLIST_TICKERS    BHP.AX,CBA.AX,CSL.AX,...
```
OR upload a `watchlist.csv` with a `ticker` column.

**Screener thresholds (all have sensible defaults):**
```
RS_MIN               70       # minimum IBD RS Rating (1–99)
MIN_PRICE            1.0      # minimum stock price
MIN_AVG_VOLUME       400000   # minimum average daily volume
MIN_EPS_GROWTH       20       # minimum quarterly EPS growth %
MIN_CONTRACTIONS     2        # minimum number of VCP contractions
MAX_CONTRACTIONS     5        # maximum
MAX_BASE_DEPTH_PCT   35       # maximum base correction depth %
MIN_BASE_WEEKS       3
MAX_BASE_WEEKS       65
BREAKOUT_VOLUME_MULTIPLIER  1.40  # breakout needs 40%+ above avg volume
MAX_CHASE_PCT        5.0      # don't enter if > 5% above pivot
SCAN_INTERVAL_MINUTES  30
```

**No API key needed for yfinance (the default data source).**

**Optional — Polygon.io for better US data:**
```
POLYGON_API_KEY      your-polygon-key
```

---

## What you DON'T need to provide

- IG Markets credentials are NOT required for the screener.
  The screener only detects opportunities and alerts you.
  Your existing Railway service handles order execution.
  Connect the two by setting `WEBHOOK_URL` to your existing service's `/webhook` endpoint.

---

## Alert format

**Slack scan summary (every 30 min):**
```
📊 VCP Scan — 3 candidates

*BHP.AX* 🎯 APPROACHING PIVOT
  Pivot: 45.20 | Stop: 43.80 | RS: 87
  Quality: 85/100 | Contractions: 3 | Base depth: 18.2%
```

**Breakout alert (immediate):**
```
🚨 VCP BREAKOUT: CBA.AX
Entry: 98.50 | Stop: 95.20 | Risk: 3.4%
Volume: 187% of 50d avg
RS Rating: 92
```

---

## File structure

```
vcp_screener/
├── main.py                  # Entry point + scheduler
├── config.py                # All env var config
├── requirements.txt
├── railway.toml
├── watchlist.csv            # Your ticker list
└── screener/
    ├── runner.py            # 4-stage scan orchestration
    ├── data_fetcher.py      # yfinance wrapper + caching
    ├── trend_template.py    # Minervini 8-point filter
    ├── vcp_detector.py      # Contraction pattern detection
    ├── relative_strength.py # IBD-style RS ranking
    ├── breakout.py          # Pivot + volume breakout check
    └── notifier.py          # Slack + webhook alerts
```

---

## Connecting to your existing IG Markets bot

Set `WEBHOOK_URL` to your existing Railway service's webhook endpoint.
When a breakout is detected, the screener POSTs:

```json
{
  "event": "vcp_scan",
  "results": [{
    "type": "breakout",
    "ticker": "BHP.AX",
    "entry_price": 45.25,
    "stop_price": 43.80,
    "risk_pct": 3.2,
    "vol_ratio": 1.87,
    "rs_rating": 87,
    "vcp_quality": 85
  }]
}
```

Your existing bot receives this, looks up the IG epic for the ticker,
and places the order.
