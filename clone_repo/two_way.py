"""Bot 2 chiều cho vàng + forex: bản ghép đang lời với cổ phiếu Mỹ (README mục 9), thêm chiều bán,
chỉ vào lệnh khi xu hướng rõ. So các khung thời gian ngắn với nhau (M15, H1, H4, D1).

    .venv/bin/python clone_repo/two_way.py

Luật (chiều bán là gương của chiều mua):
  Xu hướng rõ (bộ lọc "không đánh khi đi ngang"): giá > EMA200, EMA200 cao hơn 5 nến trước, EMA50 > EMA200,
      ADX(14) ≥ 25 và +DI > −DI. Không có bộ lọc: chỉ cần EMA50 > EMA200 và giá > EMA200.
  Vào lệnh: trong 5 nến gần nhất có nến chạm EMA21 (low ≤ EMA21) mà vẫn đóng trên EMA50; nến hiện tại xanh,
      đóng trên EMA21, EMA21 > EMA50. Khớp ở giá mở cửa nến sau.
  Thoát: cắt lỗ 2 ATR, dời về hoà vốn khi lời 1R, chốt lời 2,5R, hoặc đóng khi giá đóng cửa qua EMA200 ngược chiều.
  Rủi ro 1% vốn mỗi lệnh, mỗi mã 1 lệnh, tính riêng từng mã. Kết quả tính bằng R (1R = 1% vốn).

Chưa tính phí qua đêm (swap) của forex/vàng: với lệnh giữ nhiều ngày, swap có thể ăn 0,01–0,05R mỗi đêm.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

from gold_engine import Engine, Order, Strategy, atr_mt5, ema, resample  # noqa: E402
from strategy.indicators import dmi  # noqa: E402
from run import to_md  # noqa: E402

FX = ROOT / "data" / "csv" / "fx"
SPREAD = {"XAUUSD": 0.30, "EURUSD": 0.00010, "GBPUSD": 0.00015, "AUDUSD": 0.00012, "NZDUSD": 0.00018,
          "USDJPY": 0.012, "USDCAD": 0.00015, "USDCHF": 0.00015, "EURJPY": 0.018, "GBPJPY": 0.025, "EURGBP": 0.00015}
PAIRS = [k for k in SPREAD if k != "XAUUSD"]
CAPITAL = 1_000_000.0      # đơn vị tiền định giá; lớn để làm tròn khối lượng không ảnh hưởng. ROI không phụ thuộc.


class TwoWay(Strategy):
    def __init__(self, df, sides="both", trend_filter=True, adx_min=25, sl_atr=2.0, be_r=1.0, tp_r=2.5, name=""):
        self.name, self.sides, self.filt = name, sides, trend_filter
        self.sl_atr, self.be_r, self.tp_r = sl_atr, be_r, tp_r
        o, h, l, c = (df[k] for k in ("open", "high", "low", "close"))
        e21, e50, e200 = ema(c, 21), ema(c, 50), ema(c, 200)
        pdi, mdi, adx = dmi(h, l, c, 14)
        if trend_filter:
            up = (c > e200) & (e200 > e200.shift(5)) & (e50 > e200) & (adx >= adx_min) & (pdi > mdi)
            dn = (c < e200) & (e200 < e200.shift(5)) & (e50 < e200) & (adx >= adx_min) & (mdi > pdi)
        else:
            up, dn = (e50 > e200) & (c > e200), (e50 < e200) & (c < e200)
        touch_l = ((l <= e21) & (c > e50)).astype(int).rolling(5, min_periods=1).max().astype(bool)
        touch_s = ((h >= e21) & (c < e50)).astype(int).rolling(5, min_periods=1).max().astype(bool)
        long_ = up & touch_l & (c > o) & (c > e21) & (e21 > e50)
        short = dn & touch_s & (c < o) & (c < e21) & (e21 < e50)
        if sides == "long":
            short = short & False
        self.sig = np.where(long_, 1, np.where(short, -1, 0))
        self.sig[:200] = 0                                    # chờ EMA200 có nghĩa
        self.atr = atr_mt5(df, 14).to_numpy()
        self.c, self.h, self.l = c.to_numpy(), h.to_numpy(), l.to_numpy()
        self.e200 = e200.to_numpy()

    def manage(self, eng, p, i):
        if not p.extra.get("be") and self.be_r:
            fav = (self.h[i] - p.entry) if p.side > 0 else (p.entry - self.l[i] - eng.spread)
            if fav >= self.be_r * p.risk:
                p.extra["be"] = True
                p.sl = max(p.sl, p.entry) if p.side > 0 else min(p.sl, p.entry)
        if (p.side > 0 and self.c[i] < self.e200[i]) or (p.side < 0 and self.c[i] > self.e200[i]):
            p.extra["exit"] = "ema200"

    def on_bar(self, eng, i):
        s = self.sig[i]
        if not s or eng.pos or eng.market or np.isnan(self.atr[i]):
            return []
        d = self.sl_atr * self.atr[i]
        return [Order(side=int(s), sl_dist=d, tp_dist=self.tp_r * d, risk_pct=1.0)]


def repair_fx_daily(df):
    """Nến ngày forex của Yahoo từ ~2010: 'close' là giá chụp lúc ĐẦU ngày (≈ open), high/low vẫn đúng.
    Sửa: open(D) = close_yahoo(D), close(D) = close_yahoo(D+1). Chỉ áp dụng ở đoạn bị lỗi (nến 'phẳng' > 50%)."""
    flat = ((df["close"] - df["open"]).abs() / (df["high"] - df["low"]) < 0.1).astype(float)
    broken = flat.rolling(60, min_periods=20).mean() > 0.5
    out = df.copy()
    nxt = df["close"].shift(-1)
    out.loc[broken, "open"] = df.loc[broken, "close"]
    out.loc[broken, "close"] = nxt[broken]
    out["high"] = out[["high", "open", "close"]].max(axis=1)
    out["low"] = out[["low", "open", "close"]].min(axis=1)
    return out.dropna(subset=["close"])


def load(tf):
    """{mã: DataFrame} cho một khung thời gian."""
    out = {}
    if tf == "D1":
        for name in ["XAUUSD"] + PAIRS:
            f = FX / "1d" / f"{'XAUUSD_GCF' if name == 'XAUUSD' else name}.csv"
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            if name != "XAUUSD":
                df = repair_fx_daily(df)
            out[name] = df[df.index >= "2003-01-01"]
        return out
    from gold_repos import load_gold
    rule = {"M15": "15min", "H1": "1h", "H4": "4h"}[tf]
    g = load_gold("15m")
    out["XAUUSD"] = g if tf == "M15" else resample(g, rule)
    if tf in ("H1", "H4"):
        for name in PAIRS:
            df = pd.read_csv(FX / "1h" / f"{name}.csv", index_col=0, parse_dates=True)
            out[name] = df if tf == "H1" else resample(df, rule)
    return out


def run_one(df, strat, spread, start):
    eng = Engine(df, spread=spread, capital=CAPITAL, max_positions=1)
    cur, tr = eng.run(strat, start=start)
    return cur, tr


VARIANTS = [("2 chiều + lọc xu hướng rõ", dict(sides="both", trend_filter=True)),
            ("2 chiều, không lọc", dict(sides="both", trend_filter=False)),
            ("chỉ mua + lọc xu hướng rõ", dict(sides="long", trend_filter=True))]


def main():
    out_dir = HERE / "results" / "two_way"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary, all_tr = [], []
    for tf in ("D1", "H4", "H1", "M15"):
        data = load(tf)
        for vname, kw in VARIANTS:
            for sym, df in data.items():
                if len(df) < 400:
                    continue
                start = df.index[min(250, len(df) - 1)]
                if tf != "D1" and sym == "XAUUSD":
                    start = max(start, pd.Timestamp("2021-01-01"))
                cur, tr = run_one(df, TwoWay(df, **kw), SPREAD[sym], start)
                if len(tr):
                    tr = tr.assign(tf=tf, variant=vname, sym=sym)
                    all_tr.append(tr)
                summary.append({"tf": tf, "variant": vname, "sym": sym, "start": start, "end": df.index[-1],
                                "bars": len(cur)})
            print(f"  {tf} · {vname} xong", flush=True)
    T = pd.concat(all_tr, ignore_index=True)
    T["close"] = pd.to_datetime(T["close"])
    T["year"], T["q"] = T["close"].dt.year, T["close"].dt.to_period("Q").astype(str)
    T.to_csv(out_dir / "trades.csv", index=False)
    report(T, out_dir)


def pf(r):
    g, l = r[r > 0].sum(), -r[r <= 0].sum()
    return g / l if l > 0 else np.inf


def report(T, out_dir):
    rows = []
    for (tf, v), g in T.groupby(["tf", "variant"], sort=False):
        gold = g[g.sym == "XAUUSD"]
        fx = g[g.sym != "XAUUSD"]
        yr = g.groupby("year")["r"].sum()
        q = g.groupby("q")["r"].sum()
        rows.append({"Khung": tf, "Biến thể": v, "Lệnh": len(g), "Thắng": f"{(g.r > 0).mean():.0%}",
                     "R TB/lệnh": f"{g.r.mean():+.3f}", "PF": f"{pf(g.r):.2f}", "Tổng R": f"{g.r.sum():+.0f}",
                     "Vàng R": f"{gold.r.sum():+.0f} ({len(gold)})", "Forex R": f"{fx.r.sum():+.0f} ({len(fx)})",
                     "Năm lời": f"{(yr > 0).sum()}/{len(yr)}", "Quý lời": f"{(q > 0).mean():.0%} ({len(q)})",
                     "Năm tệ nhất R": f"{yr.min():+.0f}", "Mua R": f"{g[g.side > 0].r.sum():+.0f}",
                     "Bán R": f"{g[g.side < 0].r.sum():+.0f}"})
    S = pd.DataFrame(rows)
    print("\n" + S.to_string(index=False))

    # Theo năm, D1: tổng R mỗi năm (mọi mã) và vàng riêng, có / không bộ lọc
    d1 = T[T.tf == "D1"]
    Y = d1.pivot_table(index="year", columns="variant", values="r", aggfunc="sum").round(1)
    N = d1.pivot_table(index="year", columns="variant", values="r", aggfunc="count")
    Y.columns = [f"{c} · R" for c in Y.columns]
    N.columns = [f"{c} · lệnh" for c in N.columns]
    YN = Y.join(N).fillna(0)
    print("\nD1 theo năm (tổng R của vàng + 10 cặp forex):\n" + YN.to_string())
    per_sym = d1[d1.variant == VARIANTS[0][0]].pivot_table(index="sym", columns="year", values="r",
                                                             aggfunc="sum").round(1).fillna(0)
    print("\nD1 · 2 chiều + lọc · R theo mã và năm:\n" + per_sym.to_string())
    with open(out_dir / "two_way.md", "w") as f:
        f.write("# Bot 2 chiều vàng + forex\n\nR = lời/lỗ theo đơn vị rủi ro (1R = 1% vốn). Mỗi mã chạy riêng, "
                "1 lệnh/mã. Spread theo SPREAD trong two_way.py, chưa tính swap.\n\n")
        f.write(to_md(S.set_index("Khung")) + "\n\n## D1 theo năm\n\n" + to_md(YN) +
                "\n\n## D1 · 2 chiều + lọc · R theo mã\n\n" + to_md(per_sym) + "\n")
    print(f"\nĐã lưu {out_dir / 'two_way.md'}")


if __name__ == "__main__":
    main()
