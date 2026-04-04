"""
VCP Strategy Backtest — Real Data Runner
Fetches live historical data via yfinance, runs the full backtest,
and outputs results + charts.

Usage:
    python run_real_backtest.py [--market ASX|US|BOTH] [--start 2020-01-01] [--end 2024-12-31]
"""

import sys
import os
import argparse
import logging

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

from real_data  import download_universe, get_asx200_tickers, get_sp500_tickers, download_ticker
from backtester import VCPBacktester
from analytics  import compute_metrics, print_report, build_chart
import pandas as pd


def build_benchmark(market: str, start: str, end: str) -> pd.DataFrame:
    ticker = "^AXJO" if market == "ASX" else "^GSPC"
    log.info(f"Downloading benchmark {ticker}...")
    df = download_ticker(ticker, start, end)
    if df is None:
        log.warning("Benchmark download failed — using flat line")
        import pandas as pd
        from real_data import _cache_path
        dates = pd.bdate_range(start, end)
        return pd.DataFrame({"Close": [3000.0] * len(dates)}, index=dates)
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="BOTH", choices=["ASX", "US", "BOTH"])
    parser.add_argument("--start",  default="2020-01-01")
    parser.add_argument("--end",    default="2024-12-31")
    parser.add_argument("--equity", default=40000, type=float)
    parser.add_argument("--risk",   default=0.01,  type=float, help="Risk per trade (0.01 = 1%%)")
    parser.add_argument("--rs-min", default=70,    type=int)
    parser.add_argument("--quality-min", default=40.0, type=float)
    args = parser.parse_args()

    log.info(f"Backtest: {args.market} | {args.start} → {args.end} | equity=${args.equity:,.0f}")

    # ── Build ticker universe ─────────────────────────────────────
    tickers = []
    if args.market in ("ASX", "BOTH"):
        asx = get_asx200_tickers()
        log.info(f"ASX universe: {len(asx)} tickers")
        tickers += asx
    if args.market in ("US", "BOTH"):
        us = get_sp500_tickers()
        log.info(f"US universe: {len(us)} tickers")
        tickers += us

    log.info(f"Total tickers to download: {len(tickers)}")

    # ── Download OHLCV ────────────────────────────────────────────
    stocks = download_universe(
        tickers,
        start=args.start,
        end=args.end,
        batch_size=10,
        inter_batch_delay=2.0,
    )
    log.info(f"Stocks with sufficient data: {len(stocks)}")

    if len(stocks) < 5:
        log.error("Not enough data downloaded. Check network / yfinance.")
        sys.exit(1)

    # ── Benchmark ─────────────────────────────────────────────────
    primary_market = "ASX" if args.market == "ASX" else "US"
    benchmark = build_benchmark(primary_market, args.start, args.end)

    # ── Run backtest ──────────────────────────────────────────────
    log.info("Starting backtest engine...")
    bt = VCPBacktester(
        stocks=stocks,
        benchmark=benchmark,
        initial_equity=args.equity,
        risk_pct=args.risk,
        max_heat_pct=0.06,
        max_positions=6,
        rs_min=args.rs_min,
        quality_min=args.quality_min,
        entry_offset_pct=0.01,
        volume_multiplier=1.40,
        max_chase_pct=0.05,
        slippage_pct=0.002,
        commission_per_trade=10.0,
        warmup_days=260,
    )

    result = bt.run()

    n_trades = len([t for t in result.trades if t.status == "CLOSED"])
    log.info(f"Signals: {len(result.scan_log)} | Trades executed: {n_trades}")

    if n_trades < 5:
        log.warning(
            f"Only {n_trades} trades — consider lowering --rs-min or --quality-min. "
            "A statistically meaningful backtest needs 30+ trades."
        )

    # ── Performance analytics ─────────────────────────────────────
    metrics = compute_metrics(
        trades=result.trades,
        equity=result.equity_curve,
        benchmark=benchmark,
        initial_equity=args.equity,
    )

    if not metrics:
        log.error("No metrics computed — no closed trades.")
        sys.exit(1)

    print_report(metrics)

    # ── Save outputs ──────────────────────────────────────────────
    out_dir = os.getenv("OUTPUT_DIR", "/tmp/backtest_output")
    os.makedirs(out_dir, exist_ok=True)

    chart_path = os.path.join(out_dir, "vcp_backtest.png")
    build_chart(metrics, out_path=chart_path)
    log.info(f"Chart saved: {chart_path}")

    # Trade log CSV
    rows = []
    for t in result.trades:
        if t.status == "CLOSED" and t.exit_price:
            rows.append({
                "ticker":      t.ticker,
                "entry_date":  t.entry_date,
                "exit_date":   t.exit_date,
                "entry_price": round(t.entry_price, 4),
                "exit_price":  round(t.exit_price, 4),
                "shares":      round(t.shares, 2),
                "r_multiple":  round(t.final_r(), 2),
                "realised_pnl": round(t.realised_pnl, 2),
                "exit_reason": t.exit_reason,
                "quality":     t.quality,
                "rs_rating":   t.rs_rating,
                "hold_days":   t.days_in_trade,
            })
    if rows:
        csv_path = os.path.join(out_dir, "vcp_trade_log.csv")
        pd.DataFrame(rows).sort_values("entry_date").to_csv(csv_path, index=False)
        log.info(f"Trade log: {csv_path}")

    # Equity curve CSV
    eq_path = os.path.join(out_dir, "equity_curve.csv")
    result.equity_curve.to_csv(eq_path, header=["equity"])
    log.info(f"Equity curve: {eq_path}")

    return metrics


if __name__ == "__main__":
    main()
