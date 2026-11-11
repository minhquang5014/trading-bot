"""Bước 2: lọc và xếp hạng altcoin ("bắn shotgun")."""
import pandas as pd

FIELDS = ("open", "high", "low", "close", "volume")


def build_panels(frames: dict, index=None) -> dict:
    """Ghép {symbol: DataFrame OHLCV} thành các bảng rộng: hàng = ngày, cột = mã."""
    panels = {f: pd.DataFrame({s: df[f] for s, df in frames.items()}) for f in FIELDS}
    if index is not None:
        panels = {f: p.reindex(index) for f, p in panels.items()}
    return panels


def compute_signals(panels: dict, cfg: dict) -> dict:
    close = panels["close"]
    vol_usd = close * panels["volume"]
    vlb = cfg["volume_lookback"]

    sma_fast = close.rolling(cfg["regime_fast_ma"]).mean()
    sma_slow = close.rolling(cfg["regime_slow_ma"]).mean()
    momentum = close / close.shift(cfg["momentum_lookback"]) - 1
    avg_vol = vol_usd.rolling(vlb).mean()
    # Khối lượng hôm nay so với trung bình 20 ngày trước đó (không tính hôm nay).
    vol_ratio = vol_usd / vol_usd.shift(1).rolling(vlb).mean()

    eligible = (avg_vol >= cfg["min_volume_usd"]) & (close > sma_slow) & (sma_fast > sma_slow)

    # Điểm = trung bình thứ hạng phần trăm của đà tăng và đột biến khối lượng, chỉ trong nhóm đủ điều kiện.
    mom_rank = momentum.where(eligible).rank(axis=1, pct=True)
    vol_rank = vol_ratio.where(eligible).rank(axis=1, pct=True)
    score = (mom_rank + vol_rank) / 2

    return {
        "sma_fast": sma_fast,
        "sma_slow": sma_slow,
        "momentum": momentum,
        "vol_ratio": vol_ratio,
        "eligible": eligible,
        "score": score,
    }


def rank_candidates(signals: dict, date, exclude=()) -> list:
    row = signals["score"].loc[date].dropna()
    row = row[~row.index.isin(list(exclude))]
    return list(row.sort_values(ascending=False).index)
