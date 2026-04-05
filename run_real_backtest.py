"""
VCP Backtest — Real Data Runner with inline diagnostics
"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

from real_data import download_ticker, download_universe, get_asx200_tickers, get_sp500_tickers
from backtester import VCPBacktester
from analytics import compute_metrics, print_report, build_chart
from signals import trend_template_score, detect_vcp_signal, compute_rs
import pandas as pd
import numpy as np


def diagnose(stocks):
    """
    Print exactly what the VCP detector sees on each stock —
    pivot timing, price at detection, and what price does next 30 days.
    This tells us if the problem is detection, entry, or exits.
    """
    print("\n" + "═"*60)
    print("  DIAGNOSTIC: VCP DETECTION AUDIT")
    print("═"*60)

    all_signals = []

    for ticker, df in stocks.items():
        close = df["Close"]
        # Scan every 20 trading days after warmup
        for i in range(260, len(df) - 30, 20):
            slice_df = df.iloc[:i]
            tt = trend_template_score(slice_df["Close"])
            if tt < 9:
                continue
            sig = detect_vcp_signal(slice_df)
            if not sig["passes"]:
                continue

            pivot = sig["pivot"]
            current_price = float(close.iloc[i])
            stop = sig["stop"]
            r_size = pivot - stop

            # What did price do in the 30 days after this detection?
            future = close.iloc[i:i+31]
            if len(future) < 5:
                continue

            # Did price actually break above pivot?
            broke_pivot = any(future > pivot * 1.01)
            if broke_pivot:
                # Find entry bar
                entry_bar = next((j for j, p in enumerate(future) if p > pivot * 1.01), None)
                if entry_bar is not None:
                    entry_price = float(future.iloc[entry_bar])
                    # Best price in next 20 bars after entry
                    post_entry = future.iloc[entry_bar:entry_bar+21]
                    best_price = float(post_entry.max()) if len(post_entry) > 0 else entry_price
                    worst_price = float(post_entry.min()) if len(post_entry) > 0 else entry_price
                    max_r = (best_price - entry_price) / r_size if r_size > 0 else 0
                    max_loss_r = (worst_price - entry_price) / r_size if r_size > 0 else 0
                    all_signals.append({
                        "ticker": ticker,
                        "date": str(df.index[i].date()),
                        "pivot": round(pivot, 2),
                        "entry": round(entry_price, 2),
                        "stop": round(stop, 2),
                        "r_size_pct": round(r_size/pivot*100, 1),
                        "max_r_possible": round(max_r, 2),
                        "max_loss_r": round(max_loss_r, 2),
                        "quality": sig["quality"],
                        "n_cont": sig["n_contractions"],
                    })

    if not all_signals:
        print("  No signals found with TT=9 that triggered entry.")
        print("  Check: are any stocks passing the full Trend Template?")
        # Show TT scores
        for ticker, df in list(stocks.items())[:5]:
            score = trend_template_score(df["Close"])
            print(f"  {ticker}: TT score = {score}/9, last price = {df['Close'].iloc[-1]:.2f}")
        return

    df_sig = pd.DataFrame(all_signals)
    print(f"\n  Total triggerable signals: {len(df_sig)}")
    print(f"  Avg max R possible:        {df_sig['max_r_possible'].mean():+.2f}R")
    print(f"  Median max R possible:     {df_sig['max_r_possible'].median():+.2f}R")
    print(f"  % that reached 1R:         {(df_sig['max_r_possible']>=1.0).mean():.0%}")
    print(f"  % that reached 2R:         {(df_sig['max_r_possible']>=2.0).mean():.0%}")
    print(f"  % that reached 3R+:        {(df_sig['max_r_possible']>=3.0).mean():.0%}")
    print(f"  Avg stop distance:         {df_sig['r_size_pct'].mean():.1f}% of price")
    print(f"  Avg max adverse R:         {df_sig['max_loss_r'].mean():.2f}R")
    print()
    print("  Top 10 signals by max R achievable:")
    top = df_sig.nlargest(10, "max_r_possible")[
        ["ticker","date","pivot","entry","r_size_pct","max_r_possible","quality"]
    ]
    print(top.to_string(index=False))
    print()
    print("  Bottom 10 (worst entries):")
    bot = df_sig.nsmallest(10, "max_r_possible")[
        ["ticker","date","pivot","entry","r_size_pct","max_r_possible","quality"]
    ]
    print(bot.to_string(index=False))
    print("═"*60 + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="BOTH", choices=["ASX","US","BOTH"])
    parser.add_argument("--start",  default="2020-01-01")
    parser.add_argument("--end",    default="2024-12-31")
    parser.add_argument("--equity", default=40000, type=float)
    parser.add_argument("--risk",   default=0.01,  type=float)
    parser.add_argument("--rs-min", default=70,    type=int)
    parser.add_argument("--quality-min", default=40.0, type=float)
    args = parser.parse_args()

    tickers = []
    if args.market in ("ASX","BOTH"): tickers += get_asx200_tickers()
    if args.market in ("US","BOTH"):  tickers += get_sp500_tickers()
    log.info(f"Downloading {len(tickers)} tickers...")

    stocks = download_universe(tickers, start=args.start, end=args.end,
                               batch_size=10, inter_batch_delay=2.0)
    log.info(f"Loaded {len(stocks)} stocks")

    if len(stocks) < 5:
        log.error("Not enough data."); sys.exit(1)

    # ── Run diagnostic first ──────────────────────────────────────
    diagnose(stocks)

    # ── Benchmark ─────────────────────────────────────────────────
    bench_ticker = "^AXJO" if args.market == "ASX" else "^GSPC"
    benchmark = download_ticker(bench_ticker, args.start, args.end)
    if benchmark is None:
        dates = pd.bdate_range(args.start, args.end)
        benchmark = pd.DataFrame({"Close": [3000.0]*len(dates)}, index=dates)

    # ── Backtest ──────────────────────────────────────────────────
    bt = VCPBacktester(
        stocks=stocks, benchmark=benchmark,
        initial_equity=args.equity, risk_pct=args.risk,
        max_heat_pct=0.06, max_positions=6,
        rs_min=args.rs_min, quality_min=args.quality_min,
        entry_offset_pct=0.01, volume_multiplier=1.40,
        max_chase_pct=0.05, slippage_pct=0.002,
        commission_per_trade=10.0, warmup_days=260,
    )
    result = bt.run()
    n = len([t for t in result.trades if t.status=="CLOSED"])
    log.info(f"Signals: {len(result.scan_log)} | Trades: {n}")

    metrics = compute_metrics(result.trades, result.equity_curve, benchmark, args.equity)
    if not metrics: log.error("No metrics."); sys.exit(1)
    print_report(metrics)

    out_dir = os.getenv("OUTPUT_DIR", "/tmp/backtest_output")
    os.makedirs(out_dir, exist_ok=True)
    build_chart(metrics, os.path.join(out_dir, "vcp_backtest.png"))

    rows = []
    for t in result.trades:
        if t.status=="CLOSED" and t.exit_price:
            rows.append({"ticker":t.ticker,"entry_date":t.entry_date,"exit_date":t.exit_date,
                         "entry":round(t.entry_price,4),"exit":round(t.exit_price,4),
                         "r_multiple":round(t.final_r(),2),"pnl":round(t.realised_pnl,2),
                         "reason":t.exit_reason,"quality":t.quality,"rs":t.rs_rating,"days":t.days_in_trade})
    if rows:
        pd.DataFrame(rows).sort_values("entry_date").to_csv(
            os.path.join(out_dir,"vcp_trade_log.csv"), index=False)
    pd.DataFrame({"equity": result.equity_curve}).to_csv(
        os.path.join(out_dir,"equity_curve.csv"))
    log.info(f"Outputs saved to {out_dir}")


if __name__ == "__main__":
    main()
