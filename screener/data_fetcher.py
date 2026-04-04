"""
Data fetcher — yfinance wrapper.
Fetches OHLCV + fundamental data.
Polygon.io can be swapped in for real-time US data if POLYGON_API_KEY is set.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

# Simple in-process cache to avoid re-fetching within a scan run
_cache: Dict[str, Tuple[datetime, pd.DataFrame]] = {}
CACHE_TTL_SECONDS = 300  # 5 min


def get_ohlcv(ticker: str, period_days: int = 365) -> Optional[pd.DataFrame]:
    """
    Return a DataFrame with columns: Open, High, Low, Close, Volume
    indexed by date. Returns None if fetch fails.
    """
    cache_key = f"{ticker}_{period_days}"
    cached = _cache.get(cache_key)
    if cached:
        ts, df = cached
        if (datetime.utcnow() - ts).total_seconds() < CACHE_TTL_SECONDS:
            return df

    try:
        t = yf.Ticker(ticker)
        # Download enough history for all our calculations
        df = t.history(period=f"{period_days}d", auto_adjust=True)
        if df.empty:
            log.warning(f"No data returned for {ticker}")
            return None

        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        _cache[cache_key] = (datetime.utcnow(), df)
        return df
    except Exception as e:
        log.error(f"Failed to fetch OHLCV for {ticker}: {e}")
        return None


def get_fundamentals(ticker: str) -> dict:
    """
    Return a dict with key fundamental metrics.
    Empty dict on failure.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        return {
            "eps_growth_yoy": info.get("earningsGrowth"),       # e.g. 0.42 = 42%
            "revenue_growth_yoy": info.get("revenueGrowth"),    # e.g. 0.25 = 25%
            "roe": info.get("returnOnEquity"),                   # e.g. 0.17 = 17%
            "market_cap": info.get("marketCap"),
            "avg_volume": info.get("averageVolume"),
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "sector": info.get("sector", ""),
            "name": info.get("shortName", ticker),
        }
    except Exception as e:
        log.warning(f"Failed to fetch fundamentals for {ticker}: {e}")
        return {}


def get_universe(market: str) -> list[str]:
    """
    Return a default universe of tickers when no watchlist is configured.
    ASX 200 or S&P 500.
    """
    if market == "ASX":
        return _asx200_tickers()
    return _sp500_tickers()


def _sp500_tickers() -> list[str]:
    try:
        table = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        )[0]
        return table["Symbol"].tolist()
    except Exception as e:
        log.error(f"Failed to scrape S&P 500 tickers: {e}")
        return []


def _asx200_tickers() -> list[str]:
    try:
        table = pd.read_html(
            "https://en.wikipedia.org/wiki/S%26P/ASX_200"
        )[1]
        tickers = table["Code"].tolist()
        return [f"{t}.AX" for t in tickers]
    except Exception as e:
        log.error(f"Failed to scrape ASX 200 tickers: {e}")
        return []


def clear_cache():
    _cache.clear()
