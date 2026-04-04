"""
Trend Template — Minervini's 8-point Stage 2 filter.

Every criterion must pass. A single failure disqualifies the stock.
Returns a TrendTemplateResult with pass/fail per criterion and an
overall score for ranking candidates.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class TrendTemplateResult:
    ticker: str
    passes: bool = False
    score: int = 0           # 0–8; useful for ranking even if all 8 pass
    reasons: list = field(default_factory=list)

    # Individual criterion results
    price_above_sma50: bool = False
    price_above_sma150: bool = False
    price_above_sma200: bool = False
    sma50_above_sma150: bool = False
    sma50_above_sma200: bool = False
    sma150_above_sma200: bool = False
    sma200_rising: bool = False
    price_above_52wk_low_threshold: bool = False
    price_near_52wk_high: bool = False

    # Values for debugging / alert messages
    current_price: Optional[float] = None
    sma50: Optional[float] = None
    sma150: Optional[float] = None
    sma200: Optional[float] = None
    sma200_22d_ago: Optional[float] = None
    week52_low: Optional[float] = None
    week52_high: Optional[float] = None


def check_trend_template(
    df: pd.DataFrame,
    ticker: str,
    low_pct_threshold: float = 0.25,   # price must be ≥25% above 52wk low
    high_pct_threshold: float = 0.25,  # price must be within 25% of 52wk high
) -> TrendTemplateResult:
    """
    Apply Minervini's 8-point trend template to a price DataFrame.

    Args:
        df: OHLCV DataFrame indexed by date, at least 260 trading days
        ticker: for logging
        low_pct_threshold: minimum % above 52wk low (default 25%)
        high_pct_threshold: maximum % below 52wk high (default 25%)

    Returns:
        TrendTemplateResult
    """
    result = TrendTemplateResult(ticker=ticker)

    if df is None or len(df) < 260:
        result.reasons.append("Insufficient data (need 260+ trading days)")
        return result

    close = df["Close"]
    current = close.iloc[-1]
    result.current_price = round(current, 4)

    # ── Moving averages ──────────────────────────────────────────
    sma50 = close.rolling(50).mean().iloc[-1]
    sma150 = close.rolling(150).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    sma200_22d = close.rolling(200).mean().iloc[-23]  # 22 trading days ago

    result.sma50 = round(sma50, 4)
    result.sma150 = round(sma150, 4)
    result.sma200 = round(sma200, 4)
    result.sma200_22d_ago = round(sma200_22d, 4)

    # ── 52-week range ────────────────────────────────────────────
    week52 = close.iloc[-252:]
    w52_low = week52.min()
    w52_high = week52.max()
    result.week52_low = round(w52_low, 4)
    result.week52_high = round(w52_high, 4)

    # ── Criterion 1: Price > SMA50 ───────────────────────────────
    result.price_above_sma50 = bool(current > sma50)
    if not result.price_above_sma50:
        result.reasons.append(f"Price {current:.2f} < SMA50 {sma50:.2f}")

    # ── Criterion 2: Price > SMA150 ──────────────────────────────
    result.price_above_sma150 = bool(current > sma150)
    if not result.price_above_sma150:
        result.reasons.append(f"Price {current:.2f} < SMA150 {sma150:.2f}")

    # ── Criterion 3: Price > SMA200 ──────────────────────────────
    result.price_above_sma200 = bool(current > sma200)
    if not result.price_above_sma200:
        result.reasons.append(f"Price {current:.2f} < SMA200 {sma200:.2f}")

    # ── Criterion 4: SMA50 > SMA150 ──────────────────────────────
    result.sma50_above_sma150 = bool(sma50 > sma150)
    if not result.sma50_above_sma150:
        result.reasons.append(f"SMA50 {sma50:.2f} < SMA150 {sma150:.2f}")

    # ── Criterion 5: SMA50 > SMA200 ──────────────────────────────
    result.sma50_above_sma200 = bool(sma50 > sma200)
    if not result.sma50_above_sma200:
        result.reasons.append(f"SMA50 {sma50:.2f} < SMA200 {sma200:.2f}")

    # ── Criterion 6: SMA150 > SMA200 ─────────────────────────────
    result.sma150_above_sma200 = bool(sma150 > sma200)
    if not result.sma150_above_sma200:
        result.reasons.append(f"SMA150 {sma150:.2f} < SMA200 {sma200:.2f}")

    # ── Criterion 7: SMA200 rising (today > 22 trading days ago) ─
    result.sma200_rising = bool(sma200 > sma200_22d)
    if not result.sma200_rising:
        result.reasons.append(
            f"SMA200 not rising: {sma200:.2f} vs {sma200_22d:.2f} (22d ago)"
        )

    # ── Criterion 8a: Price ≥ 52wk_low * (1 + threshold) ────────
    result.price_above_52wk_low_threshold = bool(
        current >= w52_low * (1 + low_pct_threshold)
    )
    if not result.price_above_52wk_low_threshold:
        result.reasons.append(
            f"Price {current:.2f} not ≥{low_pct_threshold*100:.0f}% above 52wk low {w52_low:.2f}"
        )

    # ── Criterion 8b: Price within 25% of 52wk high ──────────────
    result.price_near_52wk_high = bool(
        current >= w52_high * (1 - high_pct_threshold)
    )
    if not result.price_near_52wk_high:
        result.reasons.append(
            f"Price {current:.2f} more than {high_pct_threshold*100:.0f}% below 52wk high {w52_high:.2f}"
        )

    # ── Tally ─────────────────────────────────────────────────────
    criteria = [
        result.price_above_sma50,
        result.price_above_sma150,
        result.price_above_sma200,
        result.sma50_above_sma150,
        result.sma50_above_sma200,
        result.sma150_above_sma200,
        result.sma200_rising,
        result.price_above_52wk_low_threshold,
        result.price_near_52wk_high,
    ]
    result.score = sum(criteria)
    result.passes = all(criteria)

    return result
