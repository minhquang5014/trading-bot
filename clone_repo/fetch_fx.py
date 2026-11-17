"""Tải dữ liệu vàng + forex vào data/csv/fx/<khung>/<tên>.csv (giờ UTC).

    .venv/bin/python clone_repo/fetch_fx.py

Nguồn:
- Yahoo Finance: vàng GC=F (hợp đồng tương lai, Yahoo không có XAUUSD giao ngay), bạc SI=F, chỉ số USD
  và 10 cặp forex chính. Nến ngày: toàn bộ lịch sử; nến 1h: ~730 ngày; nến 15m: 60 ngày (giới hạn của Yahoo).
- Binance PAXGUSDT (token bảo chứng 1 ounce vàng thật): nến 15m liên tục từ 08/2020, dùng thay XAUUSD M15
  vì Yahoo chỉ có 60 ngày. Giá bám sát vàng giao ngay nhưng giao dịch cả cuối tuần và thanh khoản thấp hơn.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "csv" / "fx"

YAHOO = {
    "XAUUSD_GCF": "GC=F", "XAGUSD_SIF": "SI=F", "DXY": "DX-Y.NYB",
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X", "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X", "USDCHF": "USDCHF=X", "NZDUSD": "NZDUSD=X", "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X", "EURGBP": "EURGBP=X",
}
BINANCE = "https://data-api.binance.vision/api/v3/klines"


def save(df: pd.DataFrame, interval: str, name: str):
    d = OUT / interval
    d.mkdir(parents=True, exist_ok=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_csv(d / f"{name}.csv", index_label="time")
    return len(df)


def yahoo(interval: str, **kw):
    import yfinance as yf
    for name, tk in YAHOO.items():
        for attempt in range(3):
            try:
                df = yf.download(tk, interval=interval, progress=False, auto_adjust=False, **kw)
                break
            except Exception as e:  # mạng / giới hạn tốc độ
                print(f"  {tk} {interval}: lỗi {e}, thử lại", flush=True)
                time.sleep(5)
        else:
            continue
        if df.empty:
            print(f"  {tk} {interval}: không có dữ liệu", flush=True)
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        idx = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
        df.index = idx.tz_convert("UTC").tz_localize(None)
        n = save(df, interval, name)
        print(f"  {name:<11} {interval:>3}: {n:>6} nến  {df.index[0]} → {df.index[-1]}", flush=True)
        time.sleep(1)


def binance(symbol="PAXGUSDT", interval="15m", start="2020-08-01"):
    path = OUT / interval / f"{symbol}.csv"
    old = pd.read_csv(path, index_col=0, parse_dates=True) if path.exists() else None
    since = int((old.index[-1] if old is not None else pd.Timestamp(start)).timestamp() * 1000) + 1
    step = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}[interval]
    rows, now = [], int(time.time() * 1000)
    while since < now:
        url = f"{BINANCE}?symbol={symbol}&interval={interval}&startTime={since}&limit=1000"
        for attempt in range(3):
            try:
                batch = json.load(urllib.request.urlopen(url, timeout=60))
                break
            except Exception as e:
                print(f"  lỗi {e}, thử lại", flush=True)
                time.sleep(5)
        else:
            raise SystemExit("Không tải được dữ liệu Binance")
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + step
        if len(rows) >= 100_000:                     # lưu dần để bị ngắt giữa chừng vẫn giữ được phần đã tải
            old = _merge_save(old, rows, now, interval, symbol)
            rows = []
        if len(rows) % 20000 < 1000:
            print(f"  {symbol} {interval}: {len(rows)} nến, tới {pd.to_datetime(batch[-1][0], unit='ms')}", flush=True)
    df = _merge_save(old, rows, now, interval, symbol)
    print(f"  {symbol} {interval}: {len(df)} nến  {df.index[0]} → {df.index[-1]}", flush=True)


def _merge_save(old, rows, now, interval, symbol):
    rows = [r for r in rows if r[6] < now]          # bỏ nến chưa đóng
    df = pd.DataFrame([r[:6] for r in rows], columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    df = df.set_index("time").astype(float)
    if old is not None:
        df = pd.concat([old, df])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    save(df, interval, symbol)
    return df


if __name__ == "__main__":
    which = sys.argv[1:] or ["yahoo", "binance"]
    if "yahoo" in which:
        print("Yahoo nến ngày:")
        yahoo("1d", period="max")
        print("Yahoo nến 1h (~730 ngày):")
        yahoo("1h", start=(pd.Timestamp.utcnow() - pd.Timedelta(days=729)).strftime("%Y-%m-%d"))
        print("Yahoo nến 15m (60 ngày):")
        yahoo("15m", period="60d")
    if "binance" in which:
        print("Binance PAXGUSDT:")
        binance("PAXGUSDT", "15m")
    if "binance5m" in which:            # cho các EA khung M5 (BAKOME, PDH/PDL, mt5-gold-ai-trader)
        binance("PAXGUSDT", "5m")
    if "yahoo1m" in which:              # alpha_v3 (XAUUSD-Trading-Bots) cần vàng + DXY nến 1 phút, Yahoo chỉ có ~7 ngày
        for name, tk in (("XAUUSD_GCF", "GC=F"), ("DXY", "DX-Y.NYB")):
            import yfinance as yf
            df = yf.download(tk, interval="1m", period="7d", progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
            df.index = df.index.tz_convert("UTC").tz_localize(None)
            print(f"  {name} 1m: {save(df, '1m', name)} nến")
