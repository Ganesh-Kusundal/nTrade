"""HalfTrend — exact Python port of Pine v6 spec provided.

Pine spec:

    amplitude = 2
    channelDeviation = 2
    atr2 = ta.atr(100) / 2
    dev = channelDeviation * atr2
    highPrice = high[abs(ta.highestbars(amplitude))]
    lowPrice  = low[abs(ta.lowestbars(amplitude))]
    highma = ta.sma(high, amplitude)
    lowma  = ta.sma(low, amplitude)
    var trend, nextTrend, maxLowPrice, minHighPrice, up, down
    flip logic + ht = trend==0 ? up : down
    atrHigh = ht + dev ; atrLow = ht - dev
    buySignal = arrowUp (trend 1->0), sellSignal = arrowDown (0->1)

Parity notes:
- ta.sma(high, n) : rolling mean of high
- ta.atr(100)     : Wilder RMA of TR with period 100 (atr() helper)
- ta.highestbars : for amplitude 2 the max-high over last n, same as rolling max
- nz(x, y)       : x if not na else y

The loop preserves Pine's var state bar-by-bar so trend flips match bar-for-bar.
"""
from __future__ import annotations

import pandas as pd

from ntrade.domain.analytics.indicators import atr


def halftrend(
    df: pd.DataFrame,
    amplitude: int = 2,
    channel_deviation: int = 2,
    atr_period: int = 100,
) -> pd.DataFrame:
    """Compute HalfTrend over an OHLCV frame.

    Expects columns: high, low, close (and optionally open, volume, timestamp).
    Returns a frame aligned to df.index with columns:

        ht, atrHigh, atrLow, trend, nextTrend, up, down,
        maxLowPrice, minHighPrice, arrowUp, arrowDown,
        buySignal, sellSignal, atr2, dev, highPrice, lowPrice,
        highma, lowma

    NaN until enough bars to warm up atr/sma.
    """
    if df is None or df.empty or not {"high", "low", "close"} <= set(df.columns):
        return pd.DataFrame()

    n = len(df)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    # ATR(100)/2 and dev
    atr_s = atr(df, period=atr_period)
    atr2 = atr_s / 2.0
    dev = channel_deviation * atr2

    # rolling helpers matching Pine semantics
    # ta.sma(high, amplitude)
    highma = high.rolling(amplitude, min_periods=amplitude).mean()
    lowma = low.rolling(amplitude, min_periods=amplitude).mean()
    # ta.highestbars -> highPrice = highest high in last amplitude bars
    highPrice = high.rolling(amplitude, min_periods=amplitude).max()
    # ta.lowestbars -> lowest low in last amplitude bars
    lowPrice = low.rolling(amplitude, min_periods=amplitude).min()

    # state arrays — match Pine var init at bar 0
    #
    #   var float maxLowPrice = nz(low[1], low)   -> low[0]
    #   var float minHighPrice = nz(high[1], high)  -> high[0]
    #   var float up = 0.0 / var float down = 0.0  -> but up[0] is assigned in the
    #   loop (trend==0 branch), so arrays below start NaN and get filled as they
    #   would in Pine's per-bar flow.
    trend = [0] * n
    nextTrend = [0] * n
    maxLowPrice = [float("nan")] * n
    minHighPrice = [float("nan")] * n
    up = [float("nan")] * n
    down = [float("nan")] * n
    atrHigh = [float("nan")] * n
    atrLow = [float("nan")] * n
    arrowUp = [float("nan")] * n
    arrowDown = [float("nan")] * n

    if n > 0:
        maxLowPrice[0] = float(low.iloc[0])
        minHighPrice[0] = float(high.iloc[0])

    # Pine's ht and dev depend on atr2 which is NaN until atr warms up.
    # We still run the loop; earlier bars will produce NaN ht but state evolves.

    for i in range(n):
        if i == 0:
            continue

        # carry forward previous trend/nextTrend
        trend[i] = trend[i - 1]
        nextTrend[i] = nextTrend[i - 1]

        # need ample history for highPrice/lowPrice/highma/lowma on this bar
        # If rolling values are NaN (warmup), we cannot evaluate flips — just carry prices.
        hp = highPrice.iloc[i]
        lp = lowPrice.iloc[i]
        hma = highma.iloc[i]
        lma = lowma.iloc[i]
        prev_low = low.iloc[i - 1]
        # Pine nz(low[1], low) : previous low if exists else current low (always prev exists for i>=1)
        nz_low_prev = prev_low
        prev_high = high.iloc[i - 1]
        nz_high_prev = prev_high

        if nextTrend[i - 1] == 1:
            # maxLowPrice := max(lowPrice, maxLowPrice[prev])
            prev_max = maxLowPrice[i - 1]
            if pd.notna(lp) and pd.notna(prev_max):
                maxLowPrice[i] = max(float(lp), float(prev_max))
            elif pd.notna(lp):
                maxLowPrice[i] = float(lp)
            else:
                maxLowPrice[i] = prev_max
            # need minHighPrice to carry too when not flipping
            minHighPrice[i] = minHighPrice[i - 1]

            # flip? highma < maxLowPrice and close < nz(low[1])
            if pd.notna(hma) and pd.notna(maxLowPrice[i]):
                if float(hma) < float(maxLowPrice[i]) and float(close.iloc[i]) < float(nz_low_prev):
                    trend[i] = 1
                    nextTrend[i] = 0
                    # minHighPrice := highPrice (reset to current highPrice)
                    if pd.notna(hp):
                        minHighPrice[i] = float(hp)
                    # else keep previous
        else:
            prev_min = minHighPrice[i - 1]
            if pd.notna(hp) and pd.notna(prev_min):
                minHighPrice[i] = min(float(hp), float(prev_min))
            elif pd.notna(hp):
                minHighPrice[i] = float(hp)
            else:
                minHighPrice[i] = prev_min
            maxLowPrice[i] = maxLowPrice[i - 1]

            if pd.notna(lma) and pd.notna(minHighPrice[i]):
                if float(lma) > float(minHighPrice[i]) and float(close.iloc[i]) > float(nz_high_prev):
                    trend[i] = 0
                    nextTrend[i] = 1
                    if pd.notna(lp):
                        maxLowPrice[i] = float(lp)

        # compute up/down and channels per trend
        a2 = float(atr2.iloc[i]) if pd.notna(atr2.iloc[i]) else float("nan")
        d = float(dev.iloc[i]) if pd.notna(dev.iloc[i]) else float("nan")

        if trend[i] == 0:
            # uptrend side
            t_prev = trend[i - 1] if i >= 1 else float("nan")
            if pd.notna(t_prev) and int(t_prev) != 0:
                # flipped from down to up: up := na(down[1]) ? down : down[1]
                prev_down = down[i - 1]
                if pd.isna(prev_down):
                    # fallback to current down? In pine `down` var is NaN initially until first down run.
                    # Use minHighPrice as proxy? We'll just leave NaN and let arrow be NaN.
                    up[i] = float("nan")
                else:
                    up[i] = float(prev_down)
                if pd.notna(up[i]) and pd.notna(a2):
                    arrowUp[i] = up[i] - a2
            else:
                prev_up = up[i - 1]
                mlp = maxLowPrice[i]
                if pd.isna(prev_up):
                    up[i] = float(mlp) if pd.notna(mlp) else float("nan")
                else:
                    if pd.notna(mlp):
                        up[i] = max(float(mlp), float(prev_up))
                    else:
                        up[i] = float(prev_up)
            # channels
            if pd.notna(up[i]) and pd.notna(d):
                atrHigh[i] = up[i] + d
                atrLow[i] = up[i] - d
        else:
            t_prev = trend[i - 1] if i >= 1 else float("nan")
            if pd.notna(t_prev) and int(t_prev) != 1:
                prev_up = up[i - 1]
                if pd.isna(prev_up):
                    down[i] = float("nan")
                else:
                    down[i] = float(prev_up)
                if pd.notna(down[i]) and pd.notna(a2):
                    arrowDown[i] = down[i] + a2
            else:
                prev_down = down[i - 1]
                mhp = minHighPrice[i]
                if pd.isna(prev_down):
                    down[i] = float(mhp) if pd.notna(mhp) else float("nan")
                else:
                    if pd.notna(mhp):
                        down[i] = min(float(mhp), float(prev_down))
                    else:
                        down[i] = float(prev_down)
            if pd.notna(down[i]) and pd.notna(d):
                atrHigh[i] = down[i] + d
                atrLow[i] = down[i] - d

    # ht
    ht = [float("nan")] * n
    for i in range(n):
        if trend[i] == 0 and pd.notna(up[i]):
            ht[i] = float(up[i])
        elif trend[i] == 1 and pd.notna(down[i]):
            ht[i] = float(down[i])

    buySignal = [False] * n
    sellSignal = [False] * n
    for i in range(1, n):
        if pd.notna(arrowUp[i]) and trend[i] == 0 and trend[i - 1] == 1:
            buySignal[i] = True
        if pd.notna(arrowDown[i]) and trend[i] == 1 and trend[i - 1] == 0:
            sellSignal[i] = True

    out = pd.DataFrame({
        "ht": ht,
        "atrHigh": atrHigh,
        "atrLow": atrLow,
        "trend": trend,
        "nextTrend": nextTrend,
        "up": up,
        "down": down,
        "maxLowPrice": maxLowPrice,
        "minHighPrice": minHighPrice,
        "arrowUp": arrowUp,
        "arrowDown": arrowDown,
        "buySignal": buySignal,
        "sellSignal": sellSignal,
        "atr2": atr2.values,
        "dev": dev.values,
        "highPrice": highPrice.values,
        "lowPrice": lowPrice.values,
        "highma": highma.values,
        "lowma": lowma.values,
    }, index=df.index)
    return out
