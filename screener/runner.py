"""
Scan runner — orchestrates the 4-stage VCP screening funnel.

Stage 1: Trend template filter  (eliminates ~95% of universe)
Stage 2: Fundamental filter     (eliminates ~60-70% of remaining)
Stage 3: VCP pattern detection  (scores remaining candidates)
Stage 4: Breakout check         (flags imminent and active breakouts)
"""

import csv
import logging
import os
from typing import List, Optional

import pandas as pd

from config import Config
from screener.data_fetcher import get_ohlcv, get_fundamentals, get_universe, clear_cache
from screener.trend_template import check_trend_template
from screener.vcp_detector import detect_vcp
from screener.relative_strength import compute_rs_ratings, rs_line_trending_up, rs_line_at_new_high
from screener.breakout import check_breakout
from screener.notifier import Notifier

log = logging.getLogger(__name__)


def _load_watchlist(cfg: Config) -> List[str]:
    """Load tickers from env var, CSV file, or auto-universe."""
    # Priority 1: env var
    if cfg.WATCHLIST_TICKERS:
        tickers = [t.strip() for t in cfg.WATCHLIST_TICKERS.split(",") if t.strip()]
        log.info(f"Watchlist from env var: {len(tickers)} tickers")
        return tickers

    # Priority 2: CSV file
    if os.path.exists(cfg.WATCHLIST_FILE):
        tickers = []
        with open(cfg.WATCHLIST_FILE) as f:
            reader = csv.DictReader(f)
            for row in reader:
                t = row.get("ticker", "").strip()
                if t:
                    tickers.append(t)
        log.info(f"Watchlist from {cfg.WATCHLIST_FILE}: {len(tickers)} tickers")
        return tickers

    # Priority 3: auto-universe
    log.info(f"No watchlist configured — using {cfg.MARKET} universe")
    return get_universe(cfg.MARKET)


def run_scan() -> List[dict]:
    """
    Run the full 4-stage VCP scan.
    Returns a list of candidate dicts ready for notification/alerting.
    """
    cfg = Config()
    clear_cache()

    tickers = _load_watchlist(cfg)
    if not tickers:
        log.warning("Empty watchlist — nothing to scan")
        return []

    log.info(f"Scanning {len(tickers)} tickers...")

    # Determine benchmark for RS calculation
    benchmark_ticker = "^AXJO" if cfg.MARKET == "ASX" else "^GSPC"
    benchmark_df = get_ohlcv(benchmark_ticker, period_days=400)
    benchmark_close = benchmark_df["Close"] if benchmark_df is not None else None

    # ── Fetch all close series upfront (for RS ranking) ──────────
    closes = {}
    all_data = {}
    for ticker in tickers:
        df = get_ohlcv(ticker, period_days=400)
        if df is not None and not df.empty:
            closes[ticker] = df["Close"]
            all_data[ticker] = df

    if not closes:
        log.error("No data fetched")
        return []

    # ── Compute RS ratings for the whole universe ─────────────────
    rs_ratings = compute_rs_ratings(closes)
    log.info(f"RS ratings computed for {len(rs_ratings)} tickers")

    candidates = []

    for ticker in tickers:
        df = all_data.get(ticker)
        if df is None:
            continue

        # ── Stage 1: Trend template ───────────────────────────────
        tt = check_trend_template(df, ticker)
        if not tt.passes:
            log.debug(f"{ticker} failed trend template: {tt.reasons}")
            continue

        # ── RS rating check ───────────────────────────────────────
        rs_rating = rs_ratings.get(ticker, 0)
        if rs_rating < cfg.RS_MIN:
            log.debug(f"{ticker} RS {rs_rating} < {cfg.RS_MIN}")
            continue

        # ── Stage 2: Basic fundamental filters ───────────────────
        # Note: yfinance fundamentals are quarterly delayed.
        # Skip if not available rather than reject.
        fundamentals = get_fundamentals(ticker)
        price = fundamentals.get("price") or tt.current_price or 0
        avg_vol = fundamentals.get("avg_volume") or 0

        if price < cfg.MIN_PRICE:
            log.debug(f"{ticker} price {price} < min {cfg.MIN_PRICE}")
            continue

        if avg_vol > 0 and avg_vol < cfg.MIN_AVG_VOLUME:
            log.debug(f"{ticker} avg vol {avg_vol:,.0f} < min {cfg.MIN_AVG_VOLUME:,}")
            continue

        eps_growth = fundamentals.get("eps_growth_yoy")
        if eps_growth is not None and eps_growth < (cfg.MIN_EPS_GROWTH / 100):
            log.debug(f"{ticker} EPS growth {eps_growth:.0%} below threshold")
            continue

        # ── Stage 3: VCP pattern detection ───────────────────────
        vcp = detect_vcp(
            df,
            ticker,
            min_contractions=cfg.MIN_CONTRACTIONS,
            max_contractions=cfg.MAX_CONTRACTIONS,
            max_base_depth=cfg.MAX_BASE_DEPTH_PCT,
            min_base_weeks=cfg.MIN_BASE_WEEKS,
            max_base_weeks=cfg.MAX_BASE_WEEKS,
        )

        if not vcp.passes:
            log.debug(f"{ticker} VCP failed: {vcp.reasons}")
            continue

        # ── RS line quality signals ───────────────────────────────
        rs_trending = False
        rs_new_high = False
        if benchmark_close is not None:
            rs_trending = rs_line_trending_up(df["Close"], benchmark_close)
            rs_new_high = rs_line_at_new_high(df["Close"], benchmark_close)

        # ── Stage 4: Breakout check ───────────────────────────────
        breakout = None
        if vcp.pivot_price and vcp.stop_price:
            bo = check_breakout(
                df,
                ticker,
                pivot=vcp.pivot_price,
                stop=vcp.stop_price,
                volume_multiplier=cfg.BREAKOUT_VOLUME_MULTIPLIER,
                max_chase_pct=cfg.MAX_CHASE_PCT,
            )
            if bo.is_valid_breakout:
                breakout = bo
                notifier = Notifier()
                notifier.send_breakout_alert(ticker, bo, vcp, rs_rating)

        # ── Build result dict ─────────────────────────────────────
        candidate = {
            "ticker": ticker,
            "name": fundamentals.get("name", ticker),
            "sector": fundamentals.get("sector", ""),
            "current_price": tt.current_price,
            "pivot_price": vcp.pivot_price,
            "stop_price": vcp.stop_price,
            "risk_pct": vcp.risk_pct,
            "quality_score": vcp.quality_score,
            "rs_rating": rs_rating,
            "rs_trending_up": rs_trending,
            "rs_at_new_high": rs_new_high,
            "contraction_count": vcp.contraction_count,
            "base_depth_pct": vcp.base_depth_pct,
            "base_weeks": vcp.base_weeks,
            "volume_declining": vcp.volume_declining,
            "final_contraction_dry": vcp.final_contraction_dry,
            "approaching_pivot": vcp.approaching_pivot,
            "broke_pivot": vcp.broke_pivot,
            "valid_breakout": breakout is not None,
            "trend_score": tt.score,
            "sma50": tt.sma50,
            "sma150": tt.sma150,
            "sma200": tt.sma200,
            "week52_high": tt.week52_high,
            "week52_low": tt.week52_low,
            "eps_growth": eps_growth,
            "avg_volume": avg_vol,
        }

        candidates.append(candidate)
        log.info(
            f"✓ {ticker} | RS={rs_rating} | Quality={vcp.quality_score:.0f}/100 | "
            f"Pivot={vcp.pivot_price} | Contractions={vcp.contraction_count}"
        )

    log.info(f"=== Scan complete: {len(candidates)} VCP candidates ===")
    return candidates
