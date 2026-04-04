"""
Relative Strength — IBD-style percentile ranking.

Replicates the IBD RS Rating:
- Weighted performance over 4 periods: 63, 126, 189, 252 trading days
- Most recent 63-day period is double-weighted
- Result is ranked as a percentile (1–99) vs the universe

Usage:
    rs_scores = compute_rs_ratings(closes_dict)
    # closes_dict = {"AAPL": series, "MSFT": series, ...}
"""

import logging
from typing import Dict, Optional
import pandas as pd
import numpy as np

log = logging.getLogger(__name__)


def _raw_rs_score(close: pd.Series) -> Optional[float]:
    """
    Compute the raw (unranked) IBD RS score for a single ticker.
    Needs at least 252 trading days of data.
    """
    if close is None or len(close) < 252:
        return None

    def pct_return(days: int) -> float:
        if len(close) < days + 1:
            return 0.0
        return (close.iloc[-1] / close.iloc[-(days + 1)]) - 1

    r63 = pct_return(63)
    r126 = pct_return(126)
    r189 = pct_return(189)
    r252 = pct_return(252)

    # 63d is double-weighted
    return (r63 * 2 + r126 + r189 + r252) / 5


def compute_rs_ratings(closes: Dict[str, pd.Series]) -> Dict[str, int]:
    """
    Rank all tickers by RS score and return percentile rankings (1–99).

    Args:
        closes: dict of {ticker: Close price Series}

    Returns:
        dict of {ticker: rs_rating (int 1–99)}
    """
    raw = {}
    for ticker, close in closes.items():
        score = _raw_rs_score(close)
        if score is not None:
            raw[ticker] = score

    if not raw:
        return {}

    scores = pd.Series(raw)
    # percentile rank within the universe
    ranked = scores.rank(pct=True) * 99
    ranked = ranked.clip(1, 99).round().astype(int)
    return ranked.to_dict()


def rs_line(
    stock_close: pd.Series, benchmark_close: pd.Series
) -> pd.Series:
    """
    Compute the RS line: stock price / benchmark price.
    Use S&P 500 or ASX 200 as benchmark.
    """
    common_idx = stock_close.index.intersection(benchmark_close.index)
    stock = stock_close.reindex(common_idx)
    bench = benchmark_close.reindex(common_idx)
    return stock / bench


def rs_line_trending_up(
    stock_close: pd.Series,
    benchmark_close: pd.Series,
    lookback: int = 21,
) -> bool:
    """
    Returns True if the RS line has a positive slope over the last `lookback` days.
    """
    line = rs_line(stock_close, benchmark_close)
    if len(line) < lookback:
        return False
    recent = line.iloc[-lookback:]
    slope = np.polyfit(range(len(recent)), recent.values, 1)[0]
    return slope > 0


def rs_line_at_new_high(
    stock_close: pd.Series,
    benchmark_close: pd.Series,
    lookback: int = 252,
) -> bool:
    """
    Returns True if the RS line recently made a new high — a powerful
    leading signal Minervini looks for before a price breakout.
    """
    line = rs_line(stock_close, benchmark_close)
    if len(line) < 5:
        return False
    window = line.iloc[-min(lookback, len(line)):]
    return float(line.iloc[-1]) >= float(window.max()) * 0.98  # within 2% of high
