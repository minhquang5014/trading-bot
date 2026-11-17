"""Chỉ báo kỹ thuật viết bằng pandas, chạy trên bảng rộng (hàng = ngày, cột = mã).

Cách tính bám theo TA-Lib (freqtrade dùng TA-Lib) và MetaTrader:
EMA dùng ewm(adjust=False); RSI, ATR, ADX dùng làm mượt Wilder (alpha = 1/n).
"""
import pandas as pd


def ema(df, n):
    return df.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(df, n):
    return df.rolling(n).mean()


def wilder(df, n):
    return df.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close, n=14):
    d = close.diff()
    up, down = wilder(d.clip(lower=0), n), wilder((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / down)


def _tr_panel(high, low, pc):
    """True range = max(high - low, |high - close trước|, |low - close trước|)."""
    a, b, c = high - low, (high - pc).abs(), (low - pc).abs()
    return a.where(a >= b, b).pipe(lambda m: m.where(m >= c, c))


def atr(high, low, close, n=14):
    return wilder(_tr_panel(high, low, close.shift(1)), n)


def dmi(high, low, close, n=14):
    """Trả về (+DI, -DI, ADX) theo Wilder."""
    up, down = high.diff(), -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = wilder(_tr_panel(high, low, close.shift(1)), n)
    pdi = 100 * wilder(plus_dm, n) / tr
    mdi = 100 * wilder(minus_dm, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return pdi, mdi, wilder(dx, n)


def macd(close, fast=12, slow=26, signal=9):
    m = ema(close, fast) - ema(close, slow)
    s = m.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return m, s, m - s


def obv(close, volume):
    sign = close.diff().gt(0).astype(float) - close.diff().lt(0).astype(float)
    return (sign * volume).fillna(0).cumsum().where(close.notna())


def crossed_above(a, b):
    """Giống qtpylib.crossed_above: phiên trước a <= b, phiên này a > b. b có thể là số."""
    a_prev = a.shift(1)
    b_prev = b.shift(1) if hasattr(b, "shift") else b
    return (a > b) & (a_prev <= b_prev)


def crossed_below(a, b):
    a_prev = a.shift(1)
    b_prev = b.shift(1) if hasattr(b, "shift") else b
    return (a < b) & (a_prev >= b_prev)
