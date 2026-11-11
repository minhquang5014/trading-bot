"""Bước 1: bộ lọc "thời tiết" thị trường dựa trên chỉ số chung (mặc định BTC)."""
import pandas as pd


def compute_regime(bench_close: pd.Series, cfg: dict) -> pd.DataFrame:
    long_ma = bench_close.rolling(cfg["regime_long_ma"]).mean()
    fast_ma = bench_close.rolling(cfg["regime_fast_ma"]).mean()
    slow_ma = bench_close.rolling(cfg["regime_slow_ma"]).mean()

    df = pd.DataFrame(index=bench_close.index)
    df["close"] = bench_close
    # So sánh với NaN (giai đoạn khởi động) cho ra False, nên chưa đủ dữ liệu thì chưa giao dịch.
    df["above_long"] = bench_close > long_ma
    df["fast_above_slow"] = fast_ma > slow_ma
    if cfg.get("use_regime_long", True):
        df["ok"] = df["above_long"] & df["fast_above_slow"]
    else:
        df["ok"] = df["fast_above_slow"]
    return df
