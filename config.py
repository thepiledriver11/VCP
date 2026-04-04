"""
Config — all values loaded from environment variables.
Never hardcode secrets. Set these in Railway dashboard.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    # ── Market ──────────────────────────────────────────────────
    MARKET: str = field(default_factory=lambda: os.getenv("MARKET", "ASX"))
    # ASX  → tickers like "BHP.AX"
    # US   → tickers like "AAPL"
    TIMEZONE: str = field(
        default_factory=lambda: os.getenv("TIMEZONE", "Australia/Sydney")
    )

    # ── Data source ─────────────────────────────────────────────
    # yfinance is used by default (free, no key needed).
    # Set POLYGON_API_KEY to use Polygon.io for US real-time data.
    POLYGON_API_KEY: Optional[str] = field(
        default_factory=lambda: os.getenv("POLYGON_API_KEY")
    )

    # ── Watchlist ────────────────────────────────────────────────
    # Comma-separated tickers in env var OR path to a CSV file
    # with a "ticker" column.
    # Example: WATCHLIST_TICKERS="BHP.AX,CBA.AX,CSL.AX"
    WATCHLIST_TICKERS: str = field(
        default_factory=lambda: os.getenv("WATCHLIST_TICKERS", "")
    )
    WATCHLIST_FILE: str = field(
        default_factory=lambda: os.getenv("WATCHLIST_FILE", "watchlist.csv")
    )
    # If both empty, screener will auto-load ASX 200 or S&P 500 tickers

    # ── IG Markets ───────────────────────────────────────────────
    IG_API_KEY: Optional[str] = field(
        default_factory=lambda: os.getenv("IG_API_KEY")
    )
    IG_USERNAME: Optional[str] = field(
        default_factory=lambda: os.getenv("IG_USERNAME")
    )
    IG_PASSWORD: Optional[str] = field(
        default_factory=lambda: os.getenv("IG_PASSWORD")
    )
    IG_ACCOUNT_ID: Optional[str] = field(
        default_factory=lambda: os.getenv("IG_ACCOUNT_ID")
    )
    IG_DEMO: bool = field(
        default_factory=lambda: os.getenv("IG_DEMO", "true").lower() == "true"
    )

    # ── Notifications ────────────────────────────────────────────
    # At least one of these should be set.
    SLACK_TOKEN: Optional[str] = field(
        default_factory=lambda: os.getenv("SLACK_TOKEN")
    )
    SLACK_CHANNEL: str = field(
        default_factory=lambda: os.getenv("SLACK_CHANNEL", "#vcp-alerts")
    )
    WEBHOOK_URL: Optional[str] = field(
        default_factory=lambda: os.getenv("WEBHOOK_URL")
    )  # Generic POST webhook (e.g. your existing Railway service)

    # ── Screener thresholds ──────────────────────────────────────
    RS_MIN: int = field(default_factory=lambda: int(os.getenv("RS_MIN", "70")))
    MIN_PRICE: float = field(
        default_factory=lambda: float(os.getenv("MIN_PRICE", "1.0"))
    )
    MIN_AVG_VOLUME: int = field(
        default_factory=lambda: int(os.getenv("MIN_AVG_VOLUME", "400000"))
    )
    MIN_EPS_GROWTH: float = field(
        default_factory=lambda: float(os.getenv("MIN_EPS_GROWTH", "20.0"))
    )

    # VCP pattern params
    MIN_CONTRACTIONS: int = field(
        default_factory=lambda: int(os.getenv("MIN_CONTRACTIONS", "2"))
    )
    MAX_CONTRACTIONS: int = field(
        default_factory=lambda: int(os.getenv("MAX_CONTRACTIONS", "5"))
    )
    MAX_BASE_DEPTH_PCT: float = field(
        default_factory=lambda: float(os.getenv("MAX_BASE_DEPTH_PCT", "35.0"))
    )
    MIN_BASE_WEEKS: int = field(
        default_factory=lambda: int(os.getenv("MIN_BASE_WEEKS", "3"))
    )
    MAX_BASE_WEEKS: int = field(
        default_factory=lambda: int(os.getenv("MAX_BASE_WEEKS", "65"))
    )

    # Breakout confirmation
    BREAKOUT_VOLUME_MULTIPLIER: float = field(
        default_factory=lambda: float(os.getenv("BREAKOUT_VOLUME_MULTIPLIER", "1.4"))
    )
    MAX_CHASE_PCT: float = field(
        default_factory=lambda: float(os.getenv("MAX_CHASE_PCT", "5.0"))
    )

    # Scheduling
    SCAN_INTERVAL_MINUTES: int = field(
        default_factory=lambda: int(os.getenv("SCAN_INTERVAL_MINUTES", "30"))
    )

    def ig_base_url(self) -> str:
        if self.IG_DEMO:
            return "https://demo-api.ig.com/gateway/deal"
        return "https://api.ig.com/gateway/deal"
