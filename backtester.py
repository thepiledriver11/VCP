"""
VCPBacktester v2 — tuned exit management for realistic R capture.
Changes vs v1:
- Trail stop: 88% of price (12% below, gives room to run)
- After 3R: tighten trail to 94% 
- Time stop: 15 days at <0.3R (not 10 days at 0.5R)
- Climax threshold: 8% single-day (was 6%)
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from signals import trend_template_score, detect_vcp_signal, compute_rs, pct_above_sma


@dataclass
class Trade:
    ticker: str; entry_date: str; entry_price: float; stop_price: float
    pivot: float; shares: float; initial_risk: float; quality: float; rs_rating: int
    exit_date: Optional[str] = None; exit_price: Optional[float] = None
    status: str = "OPEN"; exit_reason: str = ""
    at_breakeven: bool = False; partial1_done: bool = False; partial2_done: bool = False
    remaining_shares: float = 0.0; days_in_trade: int = 0
    trail_stop: Optional[float] = None; realised_pnl: float = 0.0

    def __post_init__(self): self.remaining_shares = self.shares

    @property
    def r_size(self): return self.entry_price - self.stop_price

    def current_r(self, price):
        return (price - self.entry_price) / self.r_size if self.r_size > 0 else 0.0

    def final_r(self):
        if self.exit_price and self.r_size > 0:
            return (self.exit_price - self.entry_price) / self.r_size
        return 0.0


@dataclass
class BacktestResult:
    trades: list; equity_curve: pd.Series; daily_heat: pd.Series
    regime_series: pd.Series; scan_log: list


class VCPBacktester:
    def __init__(self, stocks, benchmark, initial_equity=40_000, risk_pct=0.01,
                 max_heat_pct=0.06, max_positions=6, rs_min=70, quality_min=40.0,
                 entry_offset_pct=0.01, volume_multiplier=1.40, max_chase_pct=0.05,
                 slippage_pct=0.002, commission_per_trade=10.0, warmup_days=260):
        self.stocks=stocks; self.benchmark=benchmark; self.cash=initial_equity
        self.initial_equity=initial_equity; self.risk_pct=risk_pct
        self.max_heat=max_heat_pct; self.max_pos=max_positions
        self.rs_min=rs_min; self.quality_min=quality_min
        self.entry_offset=entry_offset_pct; self.vol_mult=volume_multiplier
        self.max_chase=max_chase_pct; self.slippage=slippage_pct
        self.commission=commission_per_trade; self.warmup=warmup_days
        self.open_trades=[]; self.closed_trades=[]; self.pending_orders={}
        self.equity_history=[]; self.heat_history=[]; self.regime_history=[]; self.scan_log=[]

    def _mkt_value(self, date_str):
        val = 0.0
        for t in self.open_trades:
            df = self.stocks.get(t.ticker)
            if df is None: continue
            dt = pd.Timestamp(date_str)
            if dt in df.index: val += t.remaining_shares * float(df.loc[dt, "Close"])
        return val

    def _total_eq(self, date_str=None):
        if date_str: return self.cash + self._mkt_value(date_str)
        return self.cash + sum(t.remaining_shares * t.entry_price for t in self.open_trades)

    def _heat(self):
        eq = self._total_eq(); 
        if eq <= 0: return 0.0
        return sum(max(0,(t.entry_price-(t.trail_stop or t.stop_price))*t.remaining_shares)
                   for t in self.open_trades) / eq

    def _sell(self, trade, qty, price, reason=""):
        qty = min(qty, trade.remaining_shares)
        if qty <= 0: return
        proceeds = qty * price * (1-self.slippage) - self.commission
        trade.realised_pnl += qty*(price*(1-self.slippage)-trade.entry_price) - self.commission
        self.cash += proceeds; trade.remaining_shares -= qty

    def _close(self, trade, date_str, price, reason):
        if trade.remaining_shares > 0: self._sell(trade, trade.remaining_shares, price)
        trade.exit_date=date_str; trade.exit_price=price; trade.exit_reason=reason; trade.status="CLOSED"
        self.closed_trades.append(trade)
        if trade in self.open_trades: self.open_trades.remove(trade)

    def _manage(self, trade, row, date_str):
        price=float(row["Close"]); low=float(row["Low"]); op=float(row["Open"])
        trade.days_in_trade += 1
        r = trade.current_r(price)

        # Trail: 12% below current, tighten to 6% after 3R
        trail_pct = 0.94 if r >= 3.0 else 0.88
        if trade.trail_stop is None: trade.trail_stop = trade.stop_price
        trade.trail_stop = max(trade.trail_stop, price * trail_pct)

        # Move stop to breakeven at 1R
        if not trade.at_breakeven and r >= 1.0:
            trade.stop_price = trade.entry_price
            trade.trail_stop = max(trade.trail_stop, trade.entry_price)
            trade.at_breakeven = True

        # Partial ⅓ at 2R
        if not trade.partial1_done and r >= 2.0 and trade.remaining_shares > 0:
            self._sell(trade, max(1.0, round(trade.shares/3, 0)), price)
            trade.partial1_done = True

        # Partial ⅓ at 3R
        if not trade.partial2_done and r >= 3.0 and trade.remaining_shares > 0:
            self._sell(trade, max(1.0, round(trade.shares/3, 0)), price)
            trade.partial2_done = True

        if trade.remaining_shares <= 0:
            trade.exit_date=date_str; trade.exit_price=price; trade.exit_reason="FULL_PARTIAL"
            trade.status="CLOSED"; self.closed_trades.append(trade)
            if trade in self.open_trades: self.open_trades.remove(trade)
            return

        eff_stop = max(trade.trail_stop or 0, trade.stop_price)
        if low <= eff_stop:
            self._close(trade, date_str, eff_stop*(1-self.slippage), "STOP"); return

        # Time stop: 15 days at <0.3R
        if trade.days_in_trade >= 15 and r < 0.3:
            self._close(trade, date_str, price*(1-self.slippage), "TIME_STOP"); return

        # Climax top
        if r >= 4.0 and op > 0 and (price-op)/op > 0.08:
            self._close(trade, date_str, price*(1-self.slippage), "CLIMAX")

    def _check_orders(self, date_str, day_num):
        to_remove = []
        for ticker, order in self.pending_orders.items():
            if date_str > order["expires"]: to_remove.append(ticker); continue
            df = self.stocks.get(ticker)
            if df is None or day_num >= len(df): to_remove.append(ticker); continue
            row = df.iloc[day_num]
            high=float(row["High"]); vol_td=float(row["Volume"])
            vol50=float(df["Volume"].iloc[max(0,day_num-50):day_num].mean()) if day_num>50 else vol_td
            if high < order["buy_stop"]: continue
            if vol50>0 and vol_td/vol50 < self.vol_mult: continue
            entry = order["buy_stop"]*(1+self.slippage)
            if entry > order["pivot"]*(1+self.max_chase): to_remove.append(ticker); continue
            stop = order["stop"]; r_ps = entry-stop
            if r_ps <= 0: to_remove.append(ticker); continue
            total_eq = self._total_eq()
            dollar_risk = total_eq*self.risk_pct
            if self._heat() >= self.max_heat: continue
            if len(self.open_trades) >= self.max_pos: continue
            shares = dollar_risk/r_ps
            if shares*entry > total_eq*0.20: shares=(total_eq*0.20)/entry
            cost = shares*entry+self.commission
            if cost > self.cash*0.95: to_remove.append(ticker); continue
            self.cash -= cost
            self.open_trades.append(Trade(ticker=ticker,entry_date=date_str,entry_price=entry,
                stop_price=stop,pivot=order["pivot"],shares=shares,initial_risk=dollar_risk,
                quality=order["quality"],rs_rating=order["rs"]))
            to_remove.append(ticker)
        for t in to_remove: self.pending_orders.pop(t, None)

    def run(self):
        all_dates = sorted(set(d for df in self.stocks.values() for d in df.index))
        all_dates = [str(d.date()) for d in all_dates]
        for day_num, date_str in enumerate(all_dates):
            for trade in list(self.open_trades):
                df = self.stocks.get(trade.ticker)
                if df is None: continue
                dt = pd.Timestamp(date_str)
                if dt not in df.index: continue
                self._manage(trade, df.loc[dt], date_str)
            self._check_orders(date_str, day_num)
            if day_num >= self.warmup:
                passing = sum(1 for t,df in self.stocks.items()
                              if trend_template_score(df["Close"].loc[:date_str])==9)
                regime_ok = passing >= 3
            else:
                passing=0; regime_ok=False
            self.regime_history.append((date_str, passing))
            if day_num >= self.warmup and day_num%5==0 and regime_ok:
                closes_today={t:df["Close"].loc[:date_str] for t,df in self.stocks.items()}
                rs_ratings=compute_rs(closes_today)
                for ticker,df in self.stocks.items():
                    if ticker in self.pending_orders: continue
                    if any(t.ticker==ticker for t in self.open_trades): continue
                    rs=rs_ratings.get(ticker,0)
                    if rs<self.rs_min: continue
                    close_slice=df["Close"].loc[:date_str]
                    if trend_template_score(close_slice)<9: continue
                    if pct_above_sma(close_slice,50)>25: continue
                    sig=detect_vcp_signal(df.loc[:date_str])
                    if not sig["passes"] or sig["quality"]<self.quality_min: continue
                    buy_stop=sig["pivot"]*(1+self.entry_offset)
                    if float(close_slice.iloc[-1])>buy_stop*1.05: continue
                    expires=all_dates[min(day_num+5, len(all_dates)-1)]
                    self.pending_orders[ticker]={"pivot":sig["pivot"],"stop":sig["stop"],
                        "buy_stop":buy_stop,"quality":sig["quality"],"rs":rs,"expires":expires}
                    self.scan_log.append({"date":date_str,"ticker":ticker,"pivot":sig["pivot"],
                                          "quality":sig["quality"],"rs":rs})
            te=self._total_eq(date_str)
            self.equity_history.append((date_str,te))
            self.heat_history.append((date_str,self._heat()))
        last_date=all_dates[-1]
        for trade in list(self.open_trades):
            df=self.stocks.get(trade.ticker)
            if df is not None: self._close(trade, last_date, float(df["Close"].iloc[-1]), "END_OF_BACKTEST")
        all_trades=self.closed_trades+self.open_trades
        eq=pd.Series([v for _,v in self.equity_history],index=pd.to_datetime([d for d,_ in self.equity_history]))
        hs=pd.Series([v for _,v in self.heat_history],index=pd.to_datetime([d for d,_ in self.heat_history]))
        rs=pd.Series([v for _,v in self.regime_history],index=pd.to_datetime([d for d,_ in self.regime_history]))
        return BacktestResult(trades=all_trades,equity_curve=eq,daily_heat=hs,regime_series=rs,scan_log=self.scan_log)
