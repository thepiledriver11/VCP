import numpy as np
import pandas as pd
from scipy.signal import argrelextrema


def trend_template_score(close: pd.Series) -> int:
    if len(close) < 260:
        return 0
    c = close.iloc[-1]
    sma50  = close.iloc[-50:].mean()
    sma150 = close.iloc[-150:].mean()
    sma200 = close.iloc[-200:].mean()
    sma200_22d = close.iloc[-222:-22].mean() if len(close) >= 222 else sma200
    w52 = close.iloc[-252:]
    w52_low  = w52.min()
    w52_high = w52.max()
    score = sum([
        bool(c > sma50),
        bool(c > sma150),
        bool(c > sma200),
        bool(sma50 > sma150),
        bool(sma50 > sma200),
        bool(sma150 > sma200),
        bool(sma200 > sma200_22d),
        bool(c >= w52_low * 1.25),
        bool(c >= w52_high * 0.75),
    ])
    return score


def compute_rs(closes_dict: dict, lookback: int = 252) -> dict:
    scores = {}
    for ticker, c in closes_dict.items():
        if len(c) < lookback:
            scores[ticker] = 0
            continue
        scores[ticker] = (c.iloc[-1] / c.iloc[-lookback]) - 1
    if not scores:
        return {}
    s = pd.Series(scores)
    ranked = (s.rank(pct=True) * 99).clip(1, 99).round().astype(int)
    return ranked.to_dict()


def detect_vcp_signal(
    df: pd.DataFrame,
    min_contractions: int = 2,
    max_contractions: int = 5,
    max_depth: float = 35.0,
    min_weeks: int = 3,
    max_weeks: int = 65,
    vol_dry_threshold: float = 0.65,
) -> dict:
    null = {"passes": False, "pivot": None, "stop": None, "quality": 0.0, "depth": 0.0, "n_contractions": 0}

    close  = df["Close"]
    volume = df["Volume"]

    if len(close) < 60:
        return null

    lookback   = min(max_weeks * 5, len(close))
    base_close = close.iloc[-lookback:]
    base_vol   = volume.iloc[-lookback:]
    arr        = base_close.values

    order  = max(3, len(arr) // 20)
    peaks   = sorted(argrelextrema(arr, np.greater_equal, order=order)[0])
    troughs = sorted(argrelextrema(arr, np.less_equal,   order=order)[0])

    if len(peaks) < 1 or len(troughs) < 1:
        return null

    pivots = sorted([(i, "P") for i in peaks] + [(i, "T") for i in troughs], key=lambda x: x[0])

    all_contractions = []
    i = 0
    while i < len(pivots) - 1:
        idx, ptype = pivots[i]
        if ptype == "P":
            j = i + 1
            while j < len(pivots) and pivots[j][1] != "T":
                j += 1
            if j < len(pivots):
                tidx = pivots[j][0]
                h = float(arr[idx])
                l = float(arr[tidx])
                if h > l:
                    depth   = (h - l) / h * 100
                    avg_vol = float(base_vol.iloc[idx:tidx+1].mean())
                    all_contractions.append({"high": h, "low": l, "depth": depth,
                                             "avg_vol": avg_vol, "hi": idx, "lo": tidx})
            i = j
        else:
            i += 1

    # Find the best (longest recent) consecutive run of decreasing depth + higher lows
    best_seq = []
    for start in range(len(all_contractions)):
        seq = [all_contractions[start]]
        for k in range(start + 1, len(all_contractions)):
            prev = seq[-1]
            curr = all_contractions[k]
            if curr["depth"] < prev["depth"] and curr["low"] > prev["low"]:
                seq.append(curr)
            else:
                break
        if len(seq) >= min_contractions and len(seq) > len(best_seq):
            best_seq = seq

    if len(best_seq) < min_contractions:
        return null

    contractions = best_seq[-max_contractions:]
    n = len(contractions)

    base_peak   = max(c["high"] for c in contractions)
    base_trough = min(c["low"]  for c in contractions)
    base_depth  = (base_peak - base_trough) / base_peak * 100
    if base_depth > max_depth:
        return null

    base_days = contractions[-1]["lo"] - contractions[0]["hi"]
    if base_days < min_weeks * 5 or base_days > max_weeks * 5:
        return null

    # Volume checks
    vols = [c["avg_vol"] for c in contractions]
    vol_declining = all(vols[i] < vols[i-1] for i in range(1, len(vols)))

    vol_50d_avg = float(volume.iloc[-50:].mean())
    vol_dry = contractions[-1]["avg_vol"] < vol_50d_avg * vol_dry_threshold

    # Quality
    q = 25.0 + 20.0  # half_rule + higher_lows always true here
    if vol_declining: q += 20
    if vol_dry:       q += 15
    if 3 <= n <= 4:   q += 10
    elif n == 2:      q += 5
    final_depth = contractions[-1]["depth"]
    if final_depth <= 6:   q += 10
    elif final_depth <= 10: q += 5

    return {
        "passes":         True,
        "pivot":          contractions[-1]["high"],
        "stop":           contractions[-1]["low"],
        "quality":        q,
        "depth":          base_depth,
        "n_contractions": n,
        "vol_dry":        vol_dry,
    }


def pct_above_sma(close: pd.Series, window: int = 50) -> float:
    if len(close) < window:
        return 0.0
    sma = close.iloc[-window:].mean()
    return (close.iloc[-1] - sma) / sma * 100
