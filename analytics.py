"""
Performance analytics and report generator.
Produces both a text summary and a multi-panel chart.
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch
import warnings
warnings.filterwarnings("ignore")


def compute_metrics(trades: list, equity: pd.Series, benchmark: pd.DataFrame, initial_equity: float) -> dict:
    closed = [t for t in trades if t.status == "CLOSED"]
    if not closed:
        return {}

    # R multiples
    r_multiples = []
    for t in closed:
        if t.exit_price and t.r_size > 0:
            r = (t.exit_price - t.entry_price) / t.r_size
            # Adjust for partials
            r_multiples.append(r)

    # P&L
    pnl_list = []
    for t in closed:
        if t.exit_price:
            pnl = (t.exit_price - t.entry_price) * t.shares
            pnl_list.append(pnl)

    winners = [p for p in pnl_list if p > 0]
    losers  = [p for p in pnl_list if p <= 0]

    win_rate = len(winners) / len(pnl_list) if pnl_list else 0
    avg_win  = np.mean(winners) if winners else 0
    avg_loss = np.mean(losers)  if losers  else 0
    profit_factor = abs(sum(winners) / sum(losers)) if losers else float("inf")

    # Expectancy in R
    r_wins  = [r for r in r_multiples if r > 0]
    r_loss  = [r for r in r_multiples if r <= 0]
    expectancy_r = (win_rate * np.mean(r_wins) if r_wins else 0) + \
                   ((1 - win_rate) * np.mean(r_loss) if r_loss else 0)

    # Equity metrics
    eq = equity.dropna()
    total_return = (eq.iloc[-1] - initial_equity) / initial_equity

    # Annualised return
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr  = (eq.iloc[-1] / initial_equity) ** (1 / years) - 1 if years > 0 else 0

    # Drawdown
    running_max = eq.cummax()
    dd = (eq - running_max) / running_max
    max_dd = dd.min()

    # Sharpe (daily returns, annualised, rf=0)
    daily_ret = eq.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else 0

    # Calmar
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0

    # Benchmark comparison
    bench_close = benchmark["Close"].reindex(eq.index, method="ffill").dropna()
    if len(bench_close) >= 2:
        bench_ret = (bench_close.iloc[-1] - bench_close.iloc[0]) / bench_close.iloc[0]
        bench_daily = bench_close.pct_change().dropna()
        bench_sharpe = (bench_daily.mean() / bench_daily.std()) * np.sqrt(252) if bench_daily.std() > 0 else 0
    else:
        bench_ret = 0
        bench_sharpe = 0

    # Exit reason breakdown
    reason_counts = {}
    for t in closed:
        reason_counts[t.exit_reason] = reason_counts.get(t.exit_reason, 0) + 1

    # Avg holding period
    hold_days = []
    for t in closed:
        if t.entry_date and t.exit_date:
            d = (pd.Timestamp(t.exit_date) - pd.Timestamp(t.entry_date)).days
            hold_days.append(d)

    return {
        "n_trades":         len(closed),
        "win_rate":         win_rate,
        "avg_win_$":        avg_win,
        "avg_loss_$":       avg_loss,
        "profit_factor":    profit_factor,
        "expectancy_r":     expectancy_r,
        "avg_r_winner":     np.mean(r_wins) if r_wins else 0,
        "avg_r_loser":      np.mean(r_loss) if r_loss else 0,
        "max_r_winner":     max(r_multiples) if r_multiples else 0,
        "total_return":     total_return,
        "cagr":             cagr,
        "max_drawdown":     max_dd,
        "sharpe":           sharpe,
        "calmar":           calmar,
        "benchmark_return": bench_ret,
        "benchmark_sharpe": bench_sharpe,
        "alpha":            total_return - bench_ret,
        "years":            years,
        "avg_hold_days":    np.mean(hold_days) if hold_days else 0,
        "exit_reasons":     reason_counts,
        "r_multiples":      r_multiples,
        "dd_series":        dd,
        "equity":           eq,
        "bench_close":      bench_close,
        "initial_equity":   initial_equity,
    }


def print_report(m: dict):
    print("\n" + "═" * 60)
    print("  MINERVINI VCP STRATEGY — BACKTEST RESULTS")
    print("═" * 60)
    print(f"  Period:          {m['years']:.1f} years")
    print(f"  Total trades:    {m['n_trades']}")
    print()
    print("  ── Returns ─────────────────────────────────────────")
    print(f"  Total return:    {m['total_return']:+.1%}")
    print(f"  CAGR:            {m['cagr']:+.1%}")
    print(f"  Benchmark:       {m['benchmark_return']:+.1%} (buy & hold)")
    print(f"  Alpha:           {m['alpha']:+.1%}")
    print()
    print("  ── Risk ─────────────────────────────────────────────")
    print(f"  Max drawdown:    {m['max_drawdown']:.1%}")
    print(f"  Sharpe ratio:    {m['sharpe']:.2f}")
    print(f"  Calmar ratio:    {m['calmar']:.2f}")
    print(f"  Bench Sharpe:    {m['benchmark_sharpe']:.2f}")
    print()
    print("  ── Trade statistics ─────────────────────────────────")
    print(f"  Win rate:        {m['win_rate']:.1%}")
    print(f"  Profit factor:   {m['profit_factor']:.2f}")
    print(f"  Expectancy:      {m['expectancy_r']:+.2f}R per trade")
    print(f"  Avg winner:      +{m['avg_r_winner']:.2f}R  (${m['avg_win_$']:,.0f})")
    print(f"  Avg loser:       {m['avg_r_loser']:.2f}R  (${m['avg_loss_$']:,.0f})")
    print(f"  Best trade:      +{m['max_r_winner']:.1f}R")
    print(f"  Avg hold:        {m['avg_hold_days']:.0f} days")
    print()
    print("  ── Exit reasons ─────────────────────────────────────")
    for reason, count in sorted(m["exit_reasons"].items(), key=lambda x: -x[1]):
        pct = count / m["n_trades"] * 100
        print(f"  {reason:<20} {count:3d}  ({pct:.0f}%)")
    print("═" * 60)


def build_chart(m: dict, out_path: str = "/mnt/user-data/outputs/vcp_backtest.png"):
    eq    = m["equity"]
    dd    = m["dd_series"]
    bench = m["bench_close"]
    r_mult = m["r_multiples"]
    init  = m["initial_equity"]

    # Normalise for comparison
    eq_norm    = eq / init * 100
    bench_norm = bench / bench.iloc[0] * 100

    fig = plt.figure(figsize=(16, 20), facecolor="#1a1a2e")
    fig.suptitle(
        "Minervini VCP Strategy — Backtest Results",
        fontsize=18, fontweight="bold", color="white", y=0.98
    )

    gs = gridspec.GridSpec(
        4, 2,
        figure=fig,
        hspace=0.45,
        wspace=0.35,
        top=0.94, bottom=0.05,
        left=0.08, right=0.95,
    )

    ACCENT  = "#00d4aa"
    RED     = "#ff4757"
    YELLOW  = "#ffd700"
    GRAY    = "#8892b0"
    BG      = "#16213e"
    PANEL   = "#0f3460"

    def style_ax(ax, title):
        ax.set_facecolor(BG)
        ax.tick_params(colors=GRAY, labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#2d3561")
        ax.set_title(title, color="white", fontsize=11, fontweight="bold", pad=8)
        ax.yaxis.label.set_color(GRAY)
        ax.xaxis.label.set_color(GRAY)

    # ── 1. Equity curve vs benchmark ────────────────────────────
    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(eq_norm.index,    eq_norm.values,    color=ACCENT,  lw=2,   label="VCP Strategy",  zorder=3)
    ax1.plot(bench_norm.index, bench_norm.values, color=YELLOW,  lw=1.5, label="Benchmark",     zorder=2, alpha=0.8)
    ax1.fill_between(eq_norm.index, 100, eq_norm.values,
                     where=eq_norm.values >= 100, alpha=0.08, color=ACCENT)
    ax1.fill_between(eq_norm.index, 100, eq_norm.values,
                     where=eq_norm.values < 100, alpha=0.08, color=RED)
    ax1.axhline(100, color=GRAY, lw=0.8, linestyle="--", alpha=0.5)
    ax1.legend(loc="upper left", fontsize=9, facecolor=PANEL, labelcolor="white", edgecolor="none")
    ax1.set_ylabel("Indexed (start = 100)")
    style_ax(ax1, "Equity Curve vs Benchmark")

    # ── 2. Drawdown ──────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.fill_between(dd.index, dd.values * 100, 0, color=RED, alpha=0.7)
    ax2.plot(dd.index, dd.values * 100, color=RED, lw=1)
    ax2.set_ylabel("Drawdown (%)")
    style_ax(ax2, f"Drawdown  (Max: {m['max_drawdown']:.1%})")

    # ── 3. R multiple distribution ───────────────────────────────
    ax3 = fig.add_subplot(gs[1, 1])
    wins  = [r for r in r_mult if r > 0]
    losses = [r for r in r_mult if r <= 0]
    bins = np.linspace(min(r_mult) - 0.5, max(r_mult) + 0.5, 25) if r_mult else 20
    ax3.hist(wins,   bins=bins, color=ACCENT, alpha=0.8, label=f"Winners ({len(wins)})")
    ax3.hist(losses, bins=bins, color=RED,    alpha=0.8, label=f"Losers ({len(losses)})")
    ax3.axvline(0, color="white", lw=1, linestyle="--")
    ax3.axvline(m["expectancy_r"], color=YELLOW, lw=1.5, linestyle=":", label=f"Expectancy: {m['expectancy_r']:+.2f}R")
    ax3.legend(fontsize=8, facecolor=PANEL, labelcolor="white", edgecolor="none")
    ax3.set_xlabel("R Multiple")
    ax3.set_ylabel("# Trades")
    style_ax(ax3, "R Multiple Distribution")

    # ── 4. Rolling win rate ──────────────────────────────────────
    ax4 = fig.add_subplot(gs[2, 0])
    if len(r_mult) >= 10:
        r_series = pd.Series([1 if r > 0 else 0 for r in r_mult])
        rolling_wr = r_series.rolling(10).mean() * 100
        ax4.plot(rolling_wr.values, color=ACCENT, lw=1.5)
        ax4.axhline(50, color=GRAY, lw=0.8, linestyle="--", alpha=0.6)
        ax4.axhline(m["win_rate"] * 100, color=YELLOW, lw=1.2, linestyle=":", label=f"Overall: {m['win_rate']:.0%}")
        ax4.set_ylim(0, 100)
        ax4.legend(fontsize=8, facecolor=PANEL, labelcolor="white", edgecolor="none")
    ax4.set_ylabel("Win Rate (%)")
    ax4.set_xlabel("Trade #")
    style_ax(ax4, "Rolling Win Rate (10-trade window)")

    # ── 5. Cumulative R ──────────────────────────────────────────
    ax5 = fig.add_subplot(gs[2, 1])
    if r_mult:
        cum_r = np.cumsum(r_mult)
        colors_r = [ACCENT if r > 0 else RED for r in r_mult]
        ax5.plot(cum_r, color=ACCENT, lw=2)
        ax5.fill_between(range(len(cum_r)), 0, cum_r,
                         where=np.array(cum_r) >= 0, color=ACCENT, alpha=0.12)
        ax5.fill_between(range(len(cum_r)), 0, cum_r,
                         where=np.array(cum_r) < 0, color=RED, alpha=0.12)
        ax5.axhline(0, color=GRAY, lw=0.8, linestyle="--")
        ax5.set_xlabel("Trade #")
        ax5.set_ylabel("Cumulative R")
    style_ax(ax5, "Cumulative R Multiple")

    # ── 6. Key metrics scoreboard ────────────────────────────────
    ax6 = fig.add_subplot(gs[3, :])
    ax6.set_facecolor(BG)
    ax6.set_xlim(0, 1)
    ax6.set_ylim(0, 1)
    ax6.axis("off")
    style_ax(ax6, "Performance Summary")

    metrics_display = [
        ("CAGR",           f"{m['cagr']:+.1%}",           ACCENT),
        ("Total Return",   f"{m['total_return']:+.1%}",    ACCENT),
        ("Alpha",          f"{m['alpha']:+.1%}",           ACCENT if m['alpha'] > 0 else RED),
        ("Max Drawdown",   f"{m['max_drawdown']:.1%}",     RED),
        ("Sharpe",         f"{m['sharpe']:.2f}",           YELLOW),
        ("Calmar",         f"{m['calmar']:.2f}",           YELLOW),
        ("Win Rate",       f"{m['win_rate']:.0%}",         ACCENT if m['win_rate'] >= 0.45 else RED),
        ("Profit Factor",  f"{m['profit_factor']:.2f}",    ACCENT),
        ("Expectancy",     f"{m['expectancy_r']:+.2f}R",   ACCENT if m['expectancy_r'] > 0 else RED),
        ("Trades",         f"{m['n_trades']}",             "white"),
        ("Avg Hold",       f"{m['avg_hold_days']:.0f}d",   GRAY),
        ("Best Trade",     f"+{m['max_r_winner']:.1f}R",   YELLOW),
    ]

    cols = 6
    for i, (label, value, color) in enumerate(metrics_display):
        col = i % cols
        row = i // cols
        x = 0.02 + col * 0.165
        y = 0.72 - row * 0.45

        box = FancyBboxPatch((x - 0.01, y - 0.28), 0.155, 0.38,
                              boxstyle="round,pad=0.01",
                              facecolor=PANEL, edgecolor="#2d3561", lw=1)
        ax6.add_patch(box)
        ax6.text(x + 0.065, y + 0.04, value, ha="center", va="center",
                 color=color, fontsize=14, fontweight="bold")
        ax6.text(x + 0.065, y - 0.14, label, ha="center", va="center",
                 color=GRAY, fontsize=8)

    plt.savefig(out_path, dpi=140, bbox_inches="tight", facecolor="#1a1a2e")
    plt.close()
    return out_path
