"""
Breakout Detector — monitors whether a stock has crossed its VCP pivot
with sufficient volume confirmation.

Used both during scans (end-of-day confirmation) and intraday
(extrapolated volume check at any point in the trading session).
"""

import logging
from dataclasses import dataclass
from typing import Optional
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class BreakoutResult:
    ticker: str
    pivot_price: float
    current_price: float
    stop_price: float

    # Volume
    vol_today: float
    vol_50d_avg: float
    vol_ratio: float           # today / 50d_avg

    # State
    broke_pivot: bool = False
    volume_confirmed: bool = False
    within_chase_limit: bool = False  # price ≤ pivot * 1.05
    is_valid_breakout: bool = False

    entry_price: Optional[float] = None    # = current_price at breakout
    risk_pct: Optional[float] = None

    reason: str = ""


def check_breakout(
    df: pd.DataFrame,
    ticker: str,
    pivot: float,
    stop: float,
    volume_multiplier: float = 1.40,   # volume must be ≥ 140% of 50d avg
    max_chase_pct: float = 5.0,         # don't buy > 5% above pivot
    intraday_bars_elapsed: Optional[int] = None,  # if provided, extrapolate volume
    total_bars_in_session: int = 390,             # full US session in minutes; 300 for ASX
) -> BreakoutResult:
    """
    Checks if today's close (or intraday price) constitutes a valid VCP breakout.

    Args:
        df: OHLCV DataFrame (last row = today's data so far)
        ticker: for logging
        pivot: the pivot price (high of final contraction)
        stop: stop-loss price (low of final contraction)
        volume_multiplier: required vol vs 50d avg
        max_chase_pct: maximum % above pivot to still enter
        intraday_bars_elapsed: if set, extrapolate daily volume
        total_bars_in_session: total 1-min bars in the market session
    """
    current = float(df["Close"].iloc[-1])
    today_volume = float(df["Volume"].iloc[-1])
    vol_50d_avg = float(df["Volume"].iloc[-51:-1].mean())

    # Extrapolate intraday volume to end-of-day estimate
    if intraday_bars_elapsed and intraday_bars_elapsed > 0:
        projected_vol = today_volume * (total_bars_in_session / intraday_bars_elapsed)
    else:
        projected_vol = today_volume

    vol_ratio = projected_vol / vol_50d_avg if vol_50d_avg > 0 else 0.0

    result = BreakoutResult(
        ticker=ticker,
        pivot_price=round(pivot, 4),
        current_price=round(current, 4),
        stop_price=round(stop, 4),
        vol_today=round(today_volume, 0),
        vol_50d_avg=round(vol_50d_avg, 0),
        vol_ratio=round(vol_ratio, 2),
    )

    # ── Did price cross the pivot? ────────────────────────────────
    result.broke_pivot = current > pivot
    if not result.broke_pivot:
        result.reason = f"Price {current:.4f} has not crossed pivot {pivot:.4f}"
        return result

    # ── Volume confirmation ───────────────────────────────────────
    result.volume_confirmed = vol_ratio >= volume_multiplier
    if not result.volume_confirmed:
        result.reason = (
            f"Volume {vol_ratio:.0%} of 50d avg — needs ≥{volume_multiplier:.0%}"
        )

    # ── Not overextended (within chase limit) ─────────────────────
    max_buy = pivot * (1 + max_chase_pct / 100)
    result.within_chase_limit = current <= max_buy
    if not result.within_chase_limit:
        result.reason = (
            f"Price {current:.4f} is >{max_chase_pct:.0f}% above pivot {pivot:.4f} — extended"
        )

    # ── Valid breakout = all three conditions ─────────────────────
    result.is_valid_breakout = (
        result.broke_pivot
        and result.volume_confirmed
        and result.within_chase_limit
    )

    if result.is_valid_breakout:
        result.entry_price = current
        risk = (current - stop) / current * 100
        result.risk_pct = round(risk, 2)
        result.reason = (
            f"✅ VALID BREAKOUT: entry={current:.4f}, "
            f"stop={stop:.4f}, risk={risk:.1f}%, "
            f"vol={vol_ratio:.0%} of 50d avg"
        )
        log.info(f"{ticker} {result.reason}")

    return result
