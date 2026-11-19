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
Swap qua đêm: ước lượng từ lãi suất điều hành trung bình từng năm (bảng RATES, số làm tròn, có thể lệch
0,25–0,5 điểm %) cộng phí broker `markup` %/năm cho mỗi chiều. Lệnh mua cặp BASE/QUOTE nhận
(lãi BASE − lãi QUOTE − markup)/365 × giá trị lệnh mỗi đêm; lệnh bán thì ngược dấu. Vàng: coi như vay USD để mua
(mua trả lãi USD + markup, bán nhận lãi USD − markup). Số đêm = số ngày lịch giữa vào và ra (gồm phí cuối tuần).
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
        d = self.sl_atr * self.atr[i]
        return [Order(side=int(s), sl_dist=d, risk_pct=1.0, tag=d)]     # tag = khoảng cắt lỗ, để tính swap theo R


# Lãi suất điều hành trung bình năm (%/năm), ước lượng. EUR = lãi suất tiền gửi ECB.
CCY = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"]
RATES = pd.DataFrame([
    (2003, 1.1, 2.3, 3.7, 0.0, 0.3, 4.8, 5.3, 3.0), (2004, 1.4, 2.0, 4.4, 0.0, 0.5, 5.3, 5.8, 2.3),
    (2005, 3.2, 2.1, 4.7, 0.0, 0.8, 5.5, 6.8, 2.7), (2006, 5.0, 2.8, 4.6, 0.2, 1.4, 5.8, 7.3, 4.0),
    (2007, 5.0, 3.8, 5.5, 0.5, 2.4, 6.4, 8.0, 4.3), (2008, 2.0, 3.9, 4.7, 0.5, 2.2, 6.7, 7.5, 3.0),
    (2009, 0.2, 1.3, 0.6, 0.1, 0.4, 3.3, 3.0, 0.4), (2010, 0.2, 1.0, 0.5, 0.1, 0.3, 4.4, 2.8, 0.6),
    (2011, 0.1, 1.3, 0.5, 0.1, 0.1, 4.7, 2.5, 1.0), (2012, 0.2, 0.9, 0.5, 0.1, 0.0, 3.7, 2.5, 1.0),
    (2013, 0.1, 0.6, 0.5, 0.1, 0.0, 2.8, 2.5, 1.0), (2014, 0.1, 0.2, 0.5, 0.1, 0.0, 2.5, 3.3, 1.0),
    (2015, 0.2, 0.0, 0.5, 0.1, -0.75, 2.1, 3.0, 0.6), (2016, 0.4, 0.0, 0.4, -0.1, -0.75, 1.6, 2.2, 0.5),
    (2017, 1.0, 0.0, 0.3, -0.1, -0.75, 1.5, 1.8, 0.7), (2018, 1.8, 0.0, 0.6, -0.1, -0.75, 1.5, 1.8, 1.4),
    (2019, 2.2, -0.45, 0.75, -0.1, -0.75, 1.0, 1.4, 1.75), (2020, 0.4, -0.5, 0.2, -0.1, -0.75, 0.3, 0.4, 0.5),
    (2021, 0.1, -0.5, 0.1, -0.1, -0.75, 0.1, 0.4, 0.25), (2022, 1.7, 0.6, 1.5, -0.1, -0.1, 1.4, 2.5, 2.0),
    (2023, 5.0, 3.6, 4.7, -0.1, 1.5, 3.9, 5.3, 4.7), (2024, 5.1, 3.7, 5.1, 0.1, 1.3, 4.35, 5.4, 4.6),
    (2025, 4.2, 2.2, 4.3, 0.45, 0.3, 3.9, 3.6, 2.8), (2026, 3.7, 2.0, 3.8, 0.75, 0.0, 3.6, 2.5, 2.25),
], columns=["year"] + CCY).set_index("year") / 100


def add_swap(T, markup=1.0):
    """Thêm cột swap_r (R, âm = phải trả) và r_net = r + swap_r."""
    T = T.copy()
    o, c = pd.to_datetime(T["open"]), pd.to_datetime(T["close"])
    nights = (c.dt.normalize() - o.dt.normalize()).dt.days.clip(lower=0)
    yr = o.dt.year.clip(RATES.index.min(), RATES.index.max())
    sym = T["sym"].where(T["sym"] != "XAUUSD", "XAUUSD")
    base = np.where(sym == "XAUUSD", None, sym.str[:3])
    quote = np.where(sym == "XAUUSD", "USD", sym.str[3:])
    rb = np.array([0.0 if b is None else RATES.at[y, b] for b, y in zip(base, yr)])
    rq = np.array([RATES.at[y, q] for q, y in zip(quote, yr)])
    rate = T["side"].to_numpy() * (rb - rq) - markup / 100          # %/năm theo chiều của lệnh
    T["swap_r"] = nights.to_numpy() * rate / 365 * T["entry"].to_numpy() / T["tag"].astype(float).to_numpy()
    T["r_net"] = T["r"] + T["swap_r"]
    return T


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

    # Sau swap: các bộ "thuận xu hướng dài" trên D1, phí broker 0,5 / 1 / 2 %/năm
    sw = []
    for rsi_n, (lo, hi), ex in product([2, 3, 4], [(5, 95), (10, 90), (20, 80)], [5, 10]):
        T = run_tf("D1", d1, dict(regime="trend", rsi_n=rsi_n, lo=lo, hi=hi, exit_ma=ex))
        fx = T[T.sym != "XAUUSD"]
        row = {"RSI": rsi_n, "Ngưỡng": f"{lo}/{hi}", "Thoát SMA": ex, "Lệnh FX": len(fx),
               "Forex R (chưa swap)": f"{fx.r.sum():+.0f}", "PF": f"{pf(fx.r):.2f}"}
        for mk in (0.5, 1.0, 2.0):
            n = add_swap(fx, mk)
            yr = n.groupby("year")["r_net"].sum()
            row[f"Sau swap {mk}%"] = f"{n.r_net.sum():+.0f} (PF {pf(n.r_net):.2f}, {(yr > 0).sum()}/{len(yr)} năm)"
        sw.append(row)
    W = pd.DataFrame(sw)
    best = add_swap(run_tf("D1", d1, dict(regime="trend", rsi_n=3, lo=10, hi=90, exit_ma=10)), 1.0)
    best = best[best.sym != "XAUUSD"]
    BY = best.groupby("year").agg(lenh=("r", "size"), R=("r", "sum"), swap=("swap_r", "sum"), R_net=("r_net", "sum")).round(1)
    BY.columns = ["Lệnh", "R chưa swap", "Swap R", "R sau swap"]
    BS = best.groupby("sym").agg(lenh=("r", "size"), R=("r", "sum"), swap=("swap_r", "sum"), R_net=("r_net", "sum")).round(1)
    BS.columns = ["Lệnh", "R chưa swap", "Swap R", "R sau swap"]

    d1t = A[(A.tf == "D1") & (A.variant == VARIANTS[2][0])]
    per_sym = pd.DataFrame({sym: summarize(g) for sym, g in d1t.groupby("sym")}).T.drop(columns=["Vàng R", "Forex R"])
    print("\n" + S.to_string(index=False) + "\n\nLưới D1:\n" + G.to_string(index=False) +
          "\n\nD1 · thuận xu hướng dài · theo mã:\n" + per_sym.to_string() +
          "\n\nSau swap (chỉ forex, D1 thuận xu hướng dài):\n" + W.to_string(index=False) +
          "\n\nRSI3 10/90 SMA10, swap 1%: theo năm\n" + BY.to_string() + "\n\ntheo mã\n" + BS.to_string())
    with open(out_dir / "mean_reversion.md", "w") as f:
        f.write("# Bot hồi về trung bình 2 chiều (vàng + forex)\n\nR = đơn vị rủi ro (1R = 1% vốn). Mỗi mã chạy riêng, "
                "1 lệnh/mã. Spread theo SPREAD trong two_way.py, chưa tính swap.\n\n")
        f.write(to_md(S.set_index("Khung")) + "\n\n## Lưới tham số D1\n\n" + to_md(G.set_index("Lọc")) +
                "\n\n## D1 · thuận xu hướng dài · theo mã\n\n" + to_md(per_sym) +
                "\n\n## Sau swap (forex, D1 thuận xu hướng dài)\n\nSwap ước lượng từ lãi suất điều hành từng năm + phí "
                "broker 0,5 / 1 / 2 %/năm.\n\n" + to_md(W.set_index("RSI")) +
                "\n\n### RSI(3) 10/90, thoát SMA10, phí broker 1%: theo năm\n\n" + to_md(BY) +
                "\n\n### Theo mã\n\n" + to_md(BS) + "\n")
    print(f"\nĐã lưu {out_dir / 'mean_reversion.md'}")


if __name__ == "__main__":
    main()
