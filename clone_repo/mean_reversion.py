"""Bot hồi về trung bình (mean-reversion) 2 chiều cho vàng + forex. So khung D1, H4, H1.

    .venv/bin/python clone_repo/mean_reversion.py

Ý tưởng: forex trong ngắn hạn hay quay về trung bình. Sau vài nến giảm mạnh thì mua, tăng mạnh thì bán.
  Vào lệnh: RSI(2) < 10 → mua; RSI(2) > 90 → bán. Khớp ở giá mở cửa nến sau.
  Thoát: giá đóng cửa vượt lại SMA5 (mua: close > SMA5, bán: close < SMA5) → đóng ở giá mở nến sau;
         hoặc giữ quá 10 nến; cắt lỗ thảm hoạ 2,5 ATR (cũng dùng để tính khối lượng: 1R = 1% vốn).
  Bộ lọc thị trường (biến thể):
    - "chỉ khi đi ngang": ADX(14) < 20 (ngược lại với bot xu hướng: đánh đúng lúc không có xu hướng)
    - "không lọc"
    - "thuận xu hướng dài": chỉ mua khi giá > SMA200, chỉ bán khi giá < SMA200 (kiểu Connors)
Chưa tính swap. Lệnh giữ trung bình 3–5 nến nên swap nhỏ hơn bot xu hướng.
"""
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from gold_engine import Order, Strategy, atr_mt5, rsi_wilder, sma  # noqa: E402
from strategy.indicators import dmi  # noqa: E402
from two_way import SPREAD, load, pf, run_one  # noqa: E402
from run import to_md  # noqa: E402


class MeanRev(Strategy):
    def __init__(self, df, regime="range", rsi_n=2, lo=10, hi=90, exit_ma=5, max_bars=10, sl_atr=2.5,
                 adx_max=20, sides="both"):
        c, h, l = df["close"], df["high"], df["low"]
        r = rsi_wilder(c, rsi_n)
        _, _, adx = dmi(h, l, c, 14)
        s200 = sma(c, 200)
        long_, short = r < lo, r > hi
        if regime == "range":
            long_, short = long_ & (adx < adx_max), short & (adx < adx_max)
        elif regime == "trend":
            long_, short = long_ & (c > s200), short & (c < s200)
        if sides == "long":
            short = short & False
        self.sig = np.where(long_, 1, np.where(short, -1, 0))
        self.sig[:200] = 0
        self.c, self.ma = c.to_numpy(), sma(c, exit_ma).to_numpy()
        self.atr = atr_mt5(df, 14).to_numpy()
        self.max_bars, self.sl_atr = max_bars, sl_atr

    def manage(self, eng, p, i):
        if (p.side > 0 and self.c[i] > self.ma[i]) or (p.side < 0 and self.c[i] < self.ma[i]):
            p.extra["exit"] = "sma"
        elif i - p.open_i >= self.max_bars:
            p.extra["exit"] = "time"

    def on_bar(self, eng, i):
        s = self.sig[i]
        if not s or eng.pos or eng.market or np.isnan(self.atr[i]):
            return []
        return [Order(side=int(s), sl_dist=self.sl_atr * self.atr[i], risk_pct=1.0)]


VARIANTS = [("chỉ khi đi ngang (ADX<20)", dict(regime="range")),
            ("không lọc", dict(regime="none")),
            ("thuận xu hướng dài (SMA200)", dict(regime="trend"))]


def start_of(tf, sym, df):
    start = df.index[min(250, len(df) - 1)]
    if tf != "D1" and sym == "XAUUSD":
        start = max(start, pd.Timestamp("2021-01-01"))
    return start


def run_tf(tf, data, kw):
    out = []
    for sym, df in data.items():
        if len(df) < 400:
            continue
        _, tr = run_one(df, MeanRev(df, **kw), SPREAD[sym], start_of(tf, sym, df))
        if len(tr):
            out.append(tr.assign(sym=sym))
    T = pd.concat(out, ignore_index=True)
    T["close"] = pd.to_datetime(T["close"])
    T["year"], T["q"] = T["close"].dt.year, T["close"].dt.to_period("Q").astype(str)
    return T


def summarize(g):
    yr, q = g.groupby("year")["r"].sum(), g.groupby("q")["r"].sum()
    gold, fx = g[g.sym == "XAUUSD"], g[g.sym != "XAUUSD"]
    return {"Lệnh": len(g), "Thắng": f"{(g.r > 0).mean():.0%}", "R TB/lệnh": f"{g.r.mean():+.3f}",
            "PF": f"{pf(g.r):.2f}", "Tổng R": f"{g.r.sum():+.0f}",
            "Vàng R": f"{gold.r.sum():+.0f} ({len(gold)})", "Forex R": f"{fx.r.sum():+.0f} ({len(fx)})",
            "Năm lời": f"{(yr > 0).sum()}/{len(yr)}", "Quý lời": f"{(q > 0).mean():.0%} ({len(q)})",
            "Năm tệ nhất R": f"{yr.min():+.0f}", "Mua R": f"{g[g.side > 0].r.sum():+.0f}",
            "Bán R": f"{g[g.side < 0].r.sum():+.0f}"}


def main():
    out_dir = HERE / "results" / "mean_reversion"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, trades = [], []
    for tf in ("D1", "H4", "H1"):
        data = load(tf)
        for vname, kw in VARIANTS:
            T = run_tf(tf, data, kw)
            trades.append(T.assign(tf=tf, variant=vname))
            rows.append({"Khung": tf, "Biến thể": vname, **summarize(T)})
            print(f"  {tf} · {vname}: {rows[-1]['Tổng R']}R PF {rows[-1]['PF']}", flush=True)
        if tf == "D1":
            d1 = data
    S = pd.DataFrame(rows)
    A = pd.concat(trades, ignore_index=True)
    A.to_csv(out_dir / "trades.csv", index=False)

    # Lưới tham số D1 để xem kết quả có phụ thuộc một bộ số may mắn không
    grid = []
    for regime, rsi_n, (lo, hi), ex in product(["range", "none", "trend"], [2, 3, 4], [(5, 95), (10, 90), (20, 80)],
                                               [5, 10]):
        T = run_tf("D1", d1, dict(regime=regime, rsi_n=rsi_n, lo=lo, hi=hi, exit_ma=ex))
        s = summarize(T)
        grid.append({"Lọc": regime, "RSI": rsi_n, "Ngưỡng": f"{lo}/{hi}", "Thoát SMA": ex, "Lệnh": s["Lệnh"],
                     "PF": s["PF"], "Tổng R": s["Tổng R"], "Vàng R": s["Vàng R"], "Forex R": s["Forex R"],
                     "Năm lời": s["Năm lời"]})
    G = pd.DataFrame(grid)

    d1t = A[(A.tf == "D1") & (A.variant == VARIANTS[2][0])]
    per_sym = pd.DataFrame({sym: summarize(g) for sym, g in d1t.groupby("sym")}).T.drop(columns=["Vàng R", "Forex R"])
    print("\n" + S.to_string(index=False) + "\n\nLưới D1:\n" + G.to_string(index=False) +
          "\n\nD1 · thuận xu hướng dài · theo mã:\n" + per_sym.to_string())
    with open(out_dir / "mean_reversion.md", "w") as f:
        f.write("# Bot hồi về trung bình 2 chiều (vàng + forex)\n\nR = đơn vị rủi ro (1R = 1% vốn). Mỗi mã chạy riêng, "
                "1 lệnh/mã. Spread theo SPREAD trong two_way.py, chưa tính swap.\n\n")
        f.write(to_md(S.set_index("Khung")) + "\n\n## Lưới tham số D1\n\n" + to_md(G.set_index("Lọc")) +
                "\n\n## D1 · thuận xu hướng dài · theo mã\n\n" + to_md(per_sym) + "\n")
    print(f"\nĐã lưu {out_dir / 'mean_reversion.md'}")


if __name__ == "__main__":
    main()
