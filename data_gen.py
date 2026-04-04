"""
Synthetic OHLCV generator.
Creates realistic momentum stock price series using GBM with
regime-switching (Stage 1/2/3/4) and embedded VCP formations.
"""

import numpy as np
import pandas as pd
from datetime import date, timedelta


def _trading_days(start: str, end: str) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, end=end)


def make_stock(
    ticker: str,
    start: str = "2020-01-01",
    end: str = "2024-12-31",
    seed: int = 42,
    stage2_start_frac: float = 0.20,   # fraction of total period where Stage 2 starts
    stage2_end_frac: float = 0.85,
    annual_drift_stage2: float = 0.55,  # strong uptrend in stage 2
    annual_vol: float = 0.35,
    base_price: float = 20.0,
    embed_vcps: int = 3,               # number of VCP bases to embed
) -> pd.DataFrame:
    """
    Generate a synthetic OHLCV series for one stock.
    The series goes through Stage 1 (base), Stage 2 (uptrend with VCPs),
    then Stage 3/4 (top/downtrend).
    """
    rng = np.random.default_rng(seed)
    dates = _trading_days(start, end)
    n = len(dates)
    dt = 1 / 252

    stage2_start = int(n * stage2_start_frac)
    stage2_end = int(n * stage2_end_frac)

    # Build daily log returns
    log_returns = np.zeros(n)

    for i in range(1, n):
        if i < stage2_start:
            # Stage 1: sideways drift, low vol
            mu = 0.03
            vol = annual_vol * 0.5
        elif i <= stage2_end:
            # Stage 2: strong uptrend
            progress = (i - stage2_start) / (stage2_end - stage2_start)
            mu = annual_drift_stage2 * (1 - progress * 0.3)
            vol = annual_vol
        else:
            # Stage 3/4: distribution/downtrend
            mu = -0.45
            vol = annual_vol * 1.4
        log_returns[i] = (mu - 0.5 * vol ** 2) * dt + vol * np.sqrt(dt) * rng.standard_normal()

    # Cumulative price series (close)
    close = base_price * np.exp(np.cumsum(log_returns))

    # Embed VCP contractions inside Stage 2
    if embed_vcps > 0:
        stage2_len = stage2_end - stage2_start
        vcp_positions = np.linspace(0.05, 0.75, embed_vcps)
        for frac in vcp_positions:
            vcp_start = stage2_start + int(stage2_len * frac)
            # First contraction: 15–25% pullback over 15–25 days
            d1 = rng.integers(15, 26)
            pullback1 = rng.uniform(0.12, 0.22)
            for j in range(vcp_start, min(vcp_start + d1, n)):
                close[j] *= (1 - pullback1 * (j - vcp_start) / d1)

            # Recovery
            rec1 = rng.integers(10, 20)
            for j in range(vcp_start + d1, min(vcp_start + d1 + rec1, n)):
                close[j] = close[vcp_start + d1] * (1 + 0.15 * (j - (vcp_start + d1)) / rec1)

            # Second contraction: ~half of first
            c2_start = vcp_start + d1 + rec1
            d2 = rng.integers(8, 15)
            pullback2 = pullback1 * rng.uniform(0.40, 0.60)
            for j in range(c2_start, min(c2_start + d2, n)):
                close[j] = close[c2_start] * (1 - pullback2 * (j - c2_start) / d2)

            # Recovery to new high
            rec2 = rng.integers(8, 15)
            for j in range(c2_start + d2, min(c2_start + d2 + rec2, n)):
                close[j] = close[c2_start + d2] * (1 + 0.18 * (j - (c2_start + d2)) / rec2)

            # Third contraction: even tighter
            c3_start = c2_start + d2 + rec2
            d3 = rng.integers(5, 10)
            pullback3 = pullback2 * rng.uniform(0.35, 0.55)
            for j in range(c3_start, min(c3_start + d3, n)):
                close[j] = close[c3_start] * (1 - pullback3 * (j - c3_start) / d3)

    # Ensure no negative prices
    close = np.maximum(close, 0.01)

    # Build OHLCV from close
    daily_vol_factor = annual_vol / np.sqrt(252)
    high = close * (1 + np.abs(rng.normal(0, daily_vol_factor * 0.6, n)))
    low  = close * (1 - np.abs(rng.normal(0, daily_vol_factor * 0.6, n)))
    open_ = close * (1 + rng.normal(0, daily_vol_factor * 0.3, n))
    open_ = np.clip(open_, low, high)

    # Volume: higher on breakout days, lower on contraction lows
    base_vol = rng.lognormal(mean=np.log(500_000), sigma=0.5, size=n)
    price_change = np.abs(np.diff(close, prepend=close[0]))
    vol_scaling = 1 + 2 * (price_change / (close + 1e-9))
    volume = (base_vol * vol_scaling).astype(int)

    df = pd.DataFrame({
        "Open":   open_,
        "High":   high,
        "Low":    low,
        "Close":  close,
        "Volume": volume,
    }, index=dates)
    df.index.name = "Date"
    return df


def make_benchmark(
    start: str = "2020-01-01",
    end: str = "2024-12-31",
    seed: int = 0,
    annual_return: float = 0.12,
    annual_vol: float = 0.18,
    base: float = 3000.0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = _trading_days(start, end)
    n = len(dates)
    dt = 1 / 252
    lr = (annual_return - 0.5 * annual_vol**2) * dt + annual_vol * np.sqrt(dt) * rng.standard_normal(n)
    lr[0] = 0
    close = base * np.exp(np.cumsum(lr))
    return pd.DataFrame({"Close": close}, index=dates)


def generate_universe(
    n_stocks: int = 30,
    start: str = "2020-01-01",
    end: str = "2024-12-31",
) -> dict:
    """Generate a universe of n_stocks synthetic stocks."""
    stocks = {}
    np.random.seed(99)
    for i in range(n_stocks):
        ticker = f"SYN{i+1:02d}"
        seed = i * 17 + 3
        drift = np.random.uniform(0.25, 0.80)
        vol   = np.random.uniform(0.25, 0.55)
        base  = np.random.uniform(10.0, 150.0)
        s2s   = np.random.uniform(0.10, 0.30)
        s2e   = np.random.uniform(0.65, 0.90)
        vcps  = np.random.randint(2, 5)
        stocks[ticker] = make_stock(
            ticker, start=start, end=end, seed=seed,
            annual_drift_stage2=drift,
            annual_vol=vol,
            base_price=base,
            stage2_start_frac=s2s,
            stage2_end_frac=s2e,
            embed_vcps=vcps,
        )
    return stocks


def make_momentum_stock(
    seed: int,
    start: str = "2019-01-01",
    end: str = "2024-12-31",
    base_price: float = 30.0,
) -> pd.DataFrame:
    """
    GBM with momentum persistence — much closer to real momentum stock behaviour.
    Trending regimes have autocorrelated returns so breakouts actually run.
    """
    rng = np.random.default_rng(seed)
    dates = _trading_days(start, end)
    n = len(dates)
    dt = 1 / 252

    stage2_start = int(n * rng.uniform(0.12, 0.28))
    stage2_end   = int(n * rng.uniform(0.65, 0.90))

    close    = np.zeros(n)
    close[0] = base_price
    momentum = 0.0

    for i in range(1, n):
        if i < stage2_start:
            mu = 0.02; vol = 0.20; mom_decay = 0.0
        elif i <= stage2_end:
            progress = (i - stage2_start) / (stage2_end - stage2_start)
            mu = rng.uniform(0.35, 0.80) * (1 - progress * 0.25)
            vol = rng.uniform(0.28, 0.55)
            mom_decay = rng.uniform(0.75, 0.92)
        else:
            mu = -0.55; vol = 0.50; mom_decay = 0.5

        shock    = rng.standard_normal()
        momentum = mom_decay * momentum + (1 - mom_decay) * shock
        log_r    = (mu - 0.5 * vol ** 2) * dt + vol * np.sqrt(dt) * momentum
        close[i] = max(close[i - 1] * np.exp(log_r), 0.01)

    daily_vol_f = 0.38 / np.sqrt(252)
    high  = close * (1 + np.abs(rng.normal(0, daily_vol_f * 0.7, n)))
    low   = close * (1 - np.abs(rng.normal(0, daily_vol_f * 0.7, n)))
    open_ = np.clip(close * (1 + rng.normal(0, daily_vol_f * 0.4, n)), low, high)
    base_vol = rng.lognormal(np.log(600_000), 0.5, n)
    price_ch = np.abs(np.diff(close, prepend=close[0]))
    volume   = (base_vol * (1 + 3 * (price_ch / (close + 1e-9)))).astype(int)

    df = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )
    df.index.name = "Date"
    return df


def generate_momentum_universe(
    n_stocks: int = 100,
    start: str = "2019-01-01",
    end: str = "2024-12-31",
) -> dict:
    """Generate a universe using momentum-persistent stock model."""
    np.random.seed(42)
    stocks = {}
    for i in range(n_stocks):
        base = np.random.uniform(10.0, 200.0)
        stocks[f"MOM{i+1:03d}"] = make_momentum_stock(
            seed=i * 13 + 7, start=start, end=end, base_price=base
        )
    return stocks
