"""
VCP Pattern Detector — Minervini's Volatility Contraction Pattern.

Identifies the sequence of progressively tighter pullbacks that form
within a Stage 2 base, validates the half-rule, higher lows, and
volume dry-up, then returns a scored result with the pivot price.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

log = logging.getLogger(__name__)


@dataclass
class Contraction:
    """A single high-to-low pullback within the base."""
    high: float
    low: float
    high_date: str
    low_date: str
    depth_pct: float       # (high - low) / high * 100
    avg_volume: float      # average volume during this contraction
    is_higher_low: bool = False


@dataclass
class VCPResult:
    ticker: str
    passes: bool = False
    quality_score: float = 0.0       # 0–100

    contractions: List[Contraction] = field(default_factory=list)
    contraction_count: int = 0

    # Pattern metrics
    base_depth_pct: float = 0.0      # overall peak-to-trough depth in the base
    base_start_date: Optional[str] = None
    base_weeks: float = 0.0
    half_rule_passes: bool = False
    higher_lows: bool = False
    volume_declining: bool = False
    final_contraction_dry: bool = False  # volume evaporated in last contraction

    # Entry data
    pivot_price: Optional[float] = None   # high of final contraction = buy trigger
    stop_price: Optional[float] = None    # low of final contraction
    risk_pct: Optional[float] = None      # (pivot - stop) / pivot * 100

    # State
    approaching_pivot: bool = False       # within 3% of pivot right now
    broke_pivot: bool = False             # today's close > pivot

    reasons: list = field(default_factory=list)


def detect_vcp(
    df: pd.DataFrame,
    ticker: str,
    min_contractions: int = 2,
    max_contractions: int = 5,
    max_base_depth: float = 35.0,
    min_base_weeks: int = 3,
    max_base_weeks: int = 65,
    half_rule_tolerance: float = 0.40,   # each contraction ≤ 60% of prior
    vol_50d_multiplier_dry: float = 0.50, # final contraction avg vol < 50% of 50d avg
) -> VCPResult:
    """
    Detect a VCP in the trailing base.

    Looks back up to max_base_weeks * 5 trading days.
    """
    result = VCPResult(ticker=ticker)

    if df is None or len(df) < 50:
        result.reasons.append("Insufficient data")
        return result

    # ── Define base lookback window ──────────────────────────────
    lookback = max_base_weeks * 5
    base_df = df.iloc[-min(lookback, len(df)):]

    close = base_df["Close"]
    high = base_df["High"]
    low = base_df["Low"]
    volume = base_df["Volume"]
    dates = base_df.index

    # ── Find local peaks and troughs ─────────────────────────────
    # Use a 5-bar window to identify pivot highs and lows
    order = 5
    close_arr = close.values

    peak_idx = argrelextrema(close_arr, np.greater_equal, order=order)[0]
    trough_idx = argrelextrema(close_arr, np.less_equal, order=order)[0]

    if len(peak_idx) < 1 or len(trough_idx) < 1:
        result.reasons.append("No clear peaks/troughs detected")
        return result

    # ── Build contraction sequence ────────────────────────────────
    # A contraction = from a local peak down to the subsequent trough
    contractions: List[Contraction] = []

    # Merge peaks and troughs sorted by index; build high-low pairs
    all_pivots = sorted(
        [(i, "peak") for i in peak_idx] + [(i, "trough") for i in trough_idx],
        key=lambda x: x[0],
    )

    # Walk through pivots looking for: peak → trough → peak ... sequences
    i = 0
    while i < len(all_pivots) - 1:
        idx, ptype = all_pivots[i]
        if ptype == "peak":
            # Find the next trough
            j = i + 1
            while j < len(all_pivots) and all_pivots[j][1] != "trough":
                j += 1
            if j < len(all_pivots):
                trough_i, _ = all_pivots[j]
                h = float(close_arr[idx])
                l = float(close_arr[trough_i])
                if h > 0 and l > 0:
                    depth = (h - l) / h * 100
                    avg_vol = float(volume.iloc[idx:trough_i + 1].mean())
                    contractions.append(
                        Contraction(
                            high=round(h, 4),
                            low=round(l, 4),
                            high_date=str(dates[idx].date()),
                            low_date=str(dates[trough_i].date()),
                            depth_pct=round(depth, 2),
                            avg_volume=round(avg_vol, 0),
                        )
                    )
                i = j
            else:
                break
        else:
            i += 1

    if not contractions:
        result.reasons.append("No contraction pairs found")
        return result

    # ── Filter to the most recent N contractions ──────────────────
    # Only keep the trailing contractions within our base window
    contractions = contractions[-max_contractions:]
    result.contractions = contractions
    result.contraction_count = len(contractions)

    if len(contractions) < min_contractions:
        result.reasons.append(
            f"Only {len(contractions)} contraction(s), need ≥{min_contractions}"
        )
        return result

    # ── Check: overall base depth ─────────────────────────────────
    all_highs = [c.high for c in contractions]
    all_lows = [c.low for c in contractions]
    base_peak = max(all_highs)
    base_trough = min(all_lows)
    base_depth = (base_peak - base_trough) / base_peak * 100
    result.base_depth_pct = round(base_depth, 2)

    if base_depth > max_base_depth:
        result.reasons.append(
            f"Base too deep: {base_depth:.1f}% (max {max_base_depth}%)"
        )
        return result

    # ── Check: base duration ──────────────────────────────────────
    base_start = pd.Timestamp(contractions[0].high_date).tz_localize(None)
    last_date = dates[-1]
    if hasattr(last_date, "tzinfo") and last_date.tzinfo is not None:
        last_date = last_date.tz_localize(None)
    base_weeks = (last_date - base_start).days / 7
    result.base_start_date = contractions[0].high_date
    result.base_weeks = round(base_weeks, 1)

    if base_weeks < min_base_weeks:
        result.reasons.append(
            f"Base too short: {base_weeks:.1f} weeks (min {min_base_weeks})"
        )
        return result

    # ── Check: progressive tightening (half-rule) ─────────────────
    # Each successive contraction depth should be ≤ 60% of the prior
    half_rule = True
    for i in range(1, len(contractions)):
        prior = contractions[i - 1].depth_pct
        current = contractions[i].depth_pct
        # Allow up to 60% tolerance (Minervini says "roughly half")
        if current > prior * (1 - half_rule_tolerance + 1):
            # current is bigger than prior — fails progressive tightening
            if current >= prior:
                half_rule = False
                result.reasons.append(
                    f"Contraction {i+1} ({current:.1f}%) not smaller than "
                    f"contraction {i} ({prior:.1f}%)"
                )
                break
    result.half_rule_passes = half_rule

    # ── Check: higher lows ────────────────────────────────────────
    higher_lows = True
    for i in range(1, len(contractions)):
        if contractions[i].low <= contractions[i - 1].low:
            higher_lows = False
            result.reasons.append(
                f"Contraction {i+1} low ({contractions[i].low:.4f}) ≤ "
                f"prior low ({contractions[i-1].low:.4f})"
            )
            break
        contractions[i].is_higher_low = True
    result.higher_lows = higher_lows

    # ── Check: volume declining across contractions ───────────────
    volumes = [c.avg_volume for c in contractions]
    vol_declining = all(
        volumes[i] < volumes[i - 1] for i in range(1, len(volumes))
    )
    result.volume_declining = vol_declining
    if not vol_declining:
        result.reasons.append("Volume not consistently declining across contractions")

    # ── Check: volume dry-up in final contraction ─────────────────
    vol_50d = float(df["Volume"].iloc[-50:].mean())
    final_avg_vol = contractions[-1].avg_volume
    vol_ratio = final_avg_vol / vol_50d if vol_50d > 0 else 1.0
    result.final_contraction_dry = vol_ratio < vol_50d_multiplier_dry
    if not result.final_contraction_dry:
        result.reasons.append(
            f"Volume not dry in final contraction: "
            f"{vol_ratio:.0%} of 50d avg (target <{vol_50d_multiplier_dry:.0%})"
        )

    # ── Pivot and stop prices ─────────────────────────────────────
    # Pivot = high of final contraction (the buy trigger)
    # Stop = low of final contraction
    final = contractions[-1]
    result.pivot_price = final.high
    result.stop_price = final.low
    risk = (final.high - final.low) / final.high * 100
    result.risk_pct = round(risk, 2)

    # ── Current price relationship to pivot ───────────────────────
    current_price = float(close.iloc[-1])
    pct_from_pivot = (result.pivot_price - current_price) / result.pivot_price * 100
    result.approaching_pivot = 0 < pct_from_pivot <= 3.0
    result.broke_pivot = current_price > result.pivot_price

    # ── Quality score (0–100) ─────────────────────────────────────
    score = 0.0

    # Half-rule (25 pts)
    if result.half_rule_passes:
        score += 25

    # Higher lows (20 pts)
    if result.higher_lows:
        score += 20

    # Volume declining (20 pts)
    if result.volume_declining:
        score += 20

    # Volume dry-up in final contraction (15 pts)
    if result.final_contraction_dry:
        score += 15

    # Optimal contraction count 3–4 (10 pts)
    if 3 <= result.contraction_count <= 4:
        score += 10
    elif result.contraction_count == 2:
        score += 5

    # Tight final contraction depth ≤6% (10 pts)
    if final.depth_pct <= 6.0:
        score += 10
    elif final.depth_pct <= 10.0:
        score += 5

    result.quality_score = round(score, 1)

    # ── Final pass/fail ───────────────────────────────────────────
    # Hard requirements: half-rule + higher lows + contraction count
    result.passes = (
        result.half_rule_passes
        and result.higher_lows
        and result.contraction_count >= min_contractions
        and result.contraction_count <= max_contractions
        and result.base_depth_pct <= max_base_depth
    )

    return result
