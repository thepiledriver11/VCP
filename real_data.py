"""
Real data loader using yfinance.
Handles rate limiting, bad tickers, and caches to disk to avoid re-downloading.
"""

import os
import time
import logging
import pandas as pd
import yfinance as yf
from pathlib import Path

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("CACHE_DIR", "/tmp/vcp_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(ticker: str, start: str, end: str) -> Path:
    key = f"{ticker}_{start}_{end}".replace("/", "-")
    return CACHE_DIR / f"{key}.parquet"


def download_ticker(
    ticker: str,
    start: str,
    end: str,
    retries: int = 3,
    delay: float = 1.5,
) -> pd.DataFrame | None:
    cache = _cache_path(ticker, start, end)
    if cache.exists():
        try:
            return pd.read_parquet(cache)
        except Exception:
            cache.unlink(missing_ok=True)

    for attempt in range(retries):
        try:
            df = yf.download(
                ticker,
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            if df.empty:
                return None
            # yfinance returns MultiIndex columns when downloading single ticker
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.index = pd.to_datetime(df.index)
            df.sort_index(inplace=True)
            df.to_parquet(cache)
            return df
        except Exception as e:
            log.warning(f"{ticker} attempt {attempt+1} failed: {e}")
            time.sleep(delay * (attempt + 1))
    return None


def download_universe(
    tickers: list[str],
    start: str,
    end: str,
    batch_size: int = 10,
    inter_batch_delay: float = 3.0,
) -> dict:
    """
    Download OHLCV for a list of tickers.
    Batches downloads with delays to respect yfinance rate limits.
    Returns {ticker: DataFrame}.
    """
    results = {}
    total = len(tickers)
    for i in range(0, total, batch_size):
        batch = tickers[i:i + batch_size]
        log.info(f"Downloading batch {i//batch_size + 1}/{(total-1)//batch_size + 1}: {batch}")
        for ticker in batch:
            df = download_ticker(ticker, start, end)
            if df is not None and len(df) >= 260:
                results[ticker] = df
            else:
                log.debug(f"Skipped {ticker} (insufficient data)")
        if i + batch_size < total:
            time.sleep(inter_batch_delay)
    log.info(f"Downloaded {len(results)}/{total} tickers successfully")
    return results


def get_asx200_tickers() -> list[str]:
    """Scrape ASX 200 tickers from Wikipedia and append .AX suffix."""
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/S%26P/ASX_200")
        for table in tables:
            for col in table.columns:
                if "code" in str(col).lower() or "ticker" in str(col).lower():
                    tickers = table[col].dropna().tolist()
                    tickers = [str(t).strip().upper() for t in tickers if len(str(t).strip()) <= 5]
                    if len(tickers) > 50:
                        return [f"{t}.AX" for t in tickers[:200]]
    except Exception as e:
        log.warning(f"Wikipedia ASX scrape failed: {e}")

    # Fallback: top 50 ASX stocks
    return [
        "BHP.AX","CBA.AX","CSL.AX","NAB.AX","WBC.AX","ANZ.AX","WES.AX","MQG.AX",
        "RIO.AX","TLS.AX","WOW.AX","FMG.AX","WDS.AX","STO.AX","NCM.AX","CPU.AX",
        "COL.AX","AMC.AX","ASX.AX","REA.AX","QBE.AX","SHL.AX","APA.AX","ALL.AX",
        "IAG.AX","ORG.AX","TWE.AX","NST.AX","EVN.AX","OZL.AX","MGR.AX","GPT.AX",
        "SGP.AX","DXS.AX","SCG.AX","GMG.AX","ALX.AX","TCL.AX","SYD.AX","AGL.AX",
        "AZJ.AX","MIN.AX","ILU.AX","NXT.AX","WTC.AX","XRO.AX","ALU.AX","APX.AX",
        "PME.AX","CAR.AX",
    ]


def get_sp500_tickers() -> list[str]:
    """Scrape S&P 500 tickers from Wikipedia."""
    try:
        table = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        return table["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception as e:
        log.warning(f"Wikipedia SP500 scrape failed: {e}")
    # Fallback: large-cap US
    return [
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","AVGO","JPM","LLY",
        "V","UNH","XOM","MA","HD","PG","COST","MRK","ABBV","ORCL","CVX","BAC",
        "KO","PEP","ADBE","TMO","ACN","CRM","MCD","NFLX","AMD","TXN","QCOM","GE",
        "NOW","UBER","INTU","AMAT","LRCX","KLAC","MRVL","SNOW","DDOG","TTD","SHOP",
        "ENPH","SMCI","CRWD","ZS","PANW","FTNT",
    ]
