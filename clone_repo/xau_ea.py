"""Mô phỏng EA srpo93/XAUUSD-bot (XauusdTrendPullback.mq5) trên dữ liệu đã tải.

    .venv/bin/python clone_repo/xau_ea.py                 # vàng M15 (PAXGUSDT 2021→nay), 5 preset, chạy riêng từng năm
    .venv/bin/python clone_repo/xau_ea.py --spread 0.5    # thử spread rộng hơn
    .venv/bin/python clone_repo/xau_ea.py --fx            # thêm: cùng logic trên forex + vàng GC=F, khung H1/H4

Bám theo code MQL5:
- Xét tín hiệu khi có nến mới ở khung tín hiệu (M15), dùng nến vừa đóng [1]. Lệnh khớp ngay ở giá mở nến mới.
- Xu hướng: nến H1 đã đóng gần nhất: close > EMA200 và EMA200 cao hơn 5 nến trước (mua); ngược lại (bán).
- EMA21 > EMA50 (mua) / < (bán). Trong 5 nến gần nhất có nến low ≤ EMA21 với RSI ≤ 45 (mua) / high ≥ EMA21
  với RSI ≥ 55 (bán). Nến [1] đóng trên EMA21, là nến xanh, RSI ≥ 50 (mua); ngược lại cho bán.
  Giá đóng cửa cách EMA21 không quá 1,5 ATR.
- Chỉ mở lệnh trong phiên 07–16h và 13–20h giờ server, tối đa 1 lệnh.
- SL = 1,5 ATR(14), TP = 2R, khối lượng để mất đúng 1% vốn khi dính SL (làm tròn xuống 0,01 lot = 1 oz).
- Quản lý lệnh (theo preset): hoà vốn +0,1R khi lời 1R; trailing 2 ATR khi lời ≥ 1R; chốt 50% ở 1R.

Những chỗ phải giả định:
- Giờ server = giờ New York + 7 (GMT+2/+3, quy ước phổ biến của sàn MT5).
- PAXG giao dịch cả cuối tuần; các nến thứ Bảy / Chủ nhật (giờ server) bị bỏ để giống thị trường vàng thật.
- Dữ liệu là giá giao dịch (coi như giá giữa): mua ở giá + spread/2, bán ở giá − spread/2.
- Nến M15 không cho biết trong nến giá đi lên trước hay xuống trước: nếu cùng nến chạm cả SL và TP thì coi
  như chạm SL trước (bảo thủ). Hoà vốn / trailing tính theo đỉnh của nến và có hiệu lực từ nến sau.
- MT5 tính ATR bằng trung bình cộng true range (không làm mượt Wilder); RSI dùng Wilder như MT5.
"""
import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]
from run import to_md  # noqa: E402

FX = ROOT / "data" / "csv" / "fx"


@dataclass
class Params:
    trend_ma: int = 200
    trend_slope: int = 5
    fast: int = 21
    slow: int = 50
    rsi_n: int = 14
    rsi_pb_long: float = 45
    rsi_tr_long: float = 50
    rsi_pb_short: float = 55
    rsi_tr_short: float = 50
    lookback: int = 5
    max_dist_atr: float = 1.5
    risk_pct: float = 1.0
    atr_n: int = 14
    sl_atr: float = 1.5
    rr: float = 2.0
    sessions: tuple = ((7, 16), (13, 20))
    direction: int = 0                 # 0 cả hai, 1 chỉ mua, -1 chỉ bán
    breakeven: bool = False
    be_trigger: float = 1.0
    be_offset: float = 0.1
    trailing: bool = False
    trail_start: float = 1.0
    trail_atr: float = 2.0
    partial: bool = False
    partial_r: float = 1.0
    partial_pct: float = 50.0


PRESETS = {
    "01-baseline": Params(),
    "02-breakeven": Params(breakeven=True),
    "03-trailing": Params(trailing=True),
    "04-partial": Params(partial=True),
    "05-all": Params(breakeven=True, trailing=True, partial=True),
    "06-24h, không lọc đuổi giá": Params(sessions=((0, 24),), max_dist_atr=0),
    "chỉ mua": Params(direction=1),
    "chỉ bán": Params(direction=-1),
}


def load(path, start=None):
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df = df[~df.index.duplicated()].sort_index()
    return df


def server_time(idx_utc):
    """Giờ server MT5 phổ biến: New York + 7h (GMT+2 mùa đông, GMT+3 mùa hè)."""
    return idx_utc.tz_localize("UTC").tz_convert("America/New_York").tz_localize(None) + pd.Timedelta(hours=7)


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def atr_mt5(df, n):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def prepare(df, p: Params, signal_tf: str, trend_tf: str, drop_weekend: bool):
    """Tính chỉ báo + xu hướng khung lớn, trả về DataFrame theo nến tín hiệu (index = giờ server, giờ mở nến)."""
    df = df.copy()
    df.index = server_time(df.index)
    if drop_weekend:
        df = df[np.asarray(df.index.dayofweek) < 5]
    tf = pd.Timedelta(signal_tf)
    # Khung xu hướng dựng lại từ chính dữ liệu khung tín hiệu
    h = df.resample(trend_tf, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min",
                                                                "close": "last"}).dropna()
    h["ma"] = ema(h["close"], p.trend_ma)
    h["up"] = (h["close"] > h["ma"]) & (h["ma"] > h["ma"].shift(p.trend_slope))
    h["dn"] = (h["close"] < h["ma"]) & (h["ma"] < h["ma"].shift(p.trend_slope))
    h.loc[h.index[: p.trend_ma + p.trend_slope], ["up", "dn"]] = False      # chưa đủ dữ liệu cho EMA200
    # Tại thời điểm xét (mở nến tín hiệu kế tiếp = open + tf), nến H1 đã đóng gần nhất là nến mở lúc
    # floor(t, trend_tf) - trend_tf. Tra theo nến H1 có giờ đóng ≤ t.
    t_eval = df.index + tf
    h_close_time = h.index + pd.Timedelta(trend_tf)
    pos = np.searchsorted(h_close_time.values, t_eval.values, side="right") - 1
    ok = pos >= 0
    up = np.zeros(len(df), bool)
    dn = np.zeros(len(df), bool)
    up[ok] = h["up"].to_numpy()[pos[ok]]
    dn[ok] = h["dn"].to_numpy()[pos[ok]]

    c, o = df["close"], df["open"]
    f, s = ema(c, p.fast), ema(c, p.slow)
    r, a = rsi(c, p.rsi_n), atr_mt5(df, p.atr_n)
    L = p.lookback
    pb_long = ((df["low"] <= f) & (r <= p.rsi_pb_long)).astype(float).rolling(L, min_periods=1).max().astype(bool)
    pb_short = ((df["high"] >= f) & (r >= p.rsi_pb_short)).astype(float).rolling(L, min_periods=1).max().astype(bool)
    near = (c - f).abs() / a <= p.max_dist_atr if p.max_dist_atr > 0 else pd.Series(True, index=df.index)
    long_sig = up & (f > s) & near & pb_long & (c > f) & (c > o) & (r >= p.rsi_tr_long)
    short_sig = dn & (f < s) & near & pb_short & (c < f) & (c < o) & (r <= p.rsi_tr_short)
    # Phiên giao dịch xét theo giờ server lúc mở nến kế tiếp
    mins = (t_eval.hour * 60 + t_eval.minute).to_numpy()
    in_sess = np.zeros(len(df), bool)
    for a_, b_ in p.sessions:
        in_sess |= (mins >= a_ * 60) & (mins < b_ * 60)
    sig = np.where(long_sig & in_sess, 1, np.where(short_sig & in_sess, -1, 0))
    if p.direction:
        sig = np.where(sig == p.direction, sig, 0)
    df["sig"], df["atr"] = sig, a
    return df


def simulate(df, p: Params, spread, capital=1000.0, unit_step=1.0, usd_per_quote=None, leverage=100,
             start=None, end=None):
    """Chạy EA trên df đã prepare. usd_per_quote: Series quy đổi 1 đơn vị tiền định giá ra USD (None = đã là USD)."""
    if start or end:
        df = df.loc[start:end]
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    SIG, ATR = df["sig"].to_numpy(), df["atr"].to_numpy(float)
    conv = np.ones(len(df)) if usd_per_quote is None else \
        usd_per_quote.reindex(df.index, method="ffill").bfill().to_numpy(float)
    hs = spread / 2
    eq = capital
    pos = None
    trades, curve = [], np.empty(len(df))

    def close(i, px, units, why):
        nonlocal eq
        pnl = (px - pos["entry"]) * units * pos["dir"] * conv[i]
        eq += pnl
        pos["pnl"] += pnl
        pos["units"] -= units
        if pos["units"] <= 1e-9:
            trades.append({"open": df.index[pos["i"]], "close": df.index[i], "dir": pos["dir"],
                           "entry": pos["entry"], "exit": px, "pnl": pos["pnl"],
                           "r": pos["pnl"] / pos["risk_usd"], "bars": i - pos["i"], "why": why})
            return True
        return False

    for i in range(len(df)):
        # ---- mở nến i: vào lệnh theo tín hiệu của nến i-1 ----
        if pos is None and i > 0 and SIG[i - 1] != 0 and ATR[i - 1] > 0:
            d = int(SIG[i - 1])
            entry = O[i] + hs if d > 0 else O[i] - hs
            dist = max(p.sl_atr * ATR[i - 1], spread)
            risk_usd = eq * p.risk_pct / 100
            units = np.floor(risk_usd / (dist * conv[i]) / unit_step + 1e-9) * unit_step
            # ký quỹ (đòn bẩy) không vượt 90% vốn
            while units >= unit_step and units * entry * conv[i] / leverage > 0.9 * eq:
                units -= unit_step
            if units >= unit_step:
                pos = {"i": i, "dir": d, "entry": entry, "units": units, "units0": units, "risk": dist,
                       "sl": entry - d * dist, "tp": entry + d * dist * p.rr, "best": entry,
                       "pnl": 0.0, "risk_usd": units * dist * conv[i], "partial_done": False}
        # ---- trong nến i ----
        if pos is not None:
            d = pos["dir"]
            # giá thoát lệnh: mua thoát ở bid, bán thoát ở ask
            o_, h_, l_ = (O[i] - hs, H[i] - hs, L[i] - hs) if d > 0 else (O[i] + hs, H[i] + hs, L[i] + hs)
            adverse, favor = (l_, h_) if d > 0 else (h_, l_)
            hit_sl = (adverse - pos["sl"]) * d <= 0
            done = False
            if i > pos["i"] and (o_ - pos["sl"]) * d <= 0:              # mở cửa nhảy qua SL
                done = close(i, o_, pos["units"], "sl_gap")
            elif hit_sl:
                done = close(i, pos["sl"], pos["units"], "be" if (pos["sl"] - pos["entry"]) * d > 0 else "sl")
            else:
                if p.partial and not pos["partial_done"] and (favor - (pos["entry"] + d * p.partial_r * pos["risk"])) * d >= 0:
                    cu = np.floor(pos["units0"] * p.partial_pct / 100 / unit_step + 1e-9) * unit_step
                    pos["partial_done"] = True
                    if cu >= unit_step and pos["units"] - cu >= unit_step:
                        close(i, pos["entry"] + d * p.partial_r * pos["risk"], cu, "partial")
                if (favor - pos["tp"]) * d >= 0:
                    done = close(i, pos["tp"], pos["units"], "tp")
            if done:
                pos = None
            else:
                # quản lý lệnh theo giá tốt nhất trong nến, có hiệu lực từ nến sau
                pos["best"] = max(pos["best"] * d, favor * d) * d
                prof_r = (pos["best"] - pos["entry"]) * d / pos["risk"]
                if p.breakeven and prof_r >= p.be_trigger:
                    be = pos["entry"] + d * p.be_offset * pos["risk"]
                    if (be - pos["sl"]) * d > 0:
                        pos["sl"] = be
                if p.trailing and prof_r >= p.trail_start and ATR[i] > 0:
                    tr = pos["best"] - d * p.trail_atr * ATR[i]
                    if (tr - pos["sl"]) * d > 0:
                        pos["sl"] = tr
        mtm = 0.0
        if pos is not None:
            mtm = (C[i] - d * hs - pos["entry"]) * pos["units"] * d * conv[i]
        curve[i] = eq + mtm
    if pos is not None:
        close(len(df) - 1, C[-1] - pos["dir"] * hs, pos["units"], "end")
        curve[-1] = eq
    return pd.Series(curve, index=df.index), pd.DataFrame(trades)


def stats(curve, tr, capital):
    if len(curve) == 0:
        return {}
    mdd = float((curve / curve.cummax() - 1).min())
    n = len(tr)
    wins = tr[tr["pnl"] > 0] if n else tr
    gl = -tr.loc[tr["pnl"] <= 0, "pnl"].sum() if n else 0
    return {"ROI": curve.iloc[-1] / capital - 1, "MDD": mdd, "Lệnh": n,
            "Thắng": len(wins) / n if n else np.nan,
            "R TB/lệnh": tr["r"].mean() if n else np.nan, "Tổng R": tr["r"].sum() if n else 0.0,
            "PF": wins["pnl"].sum() / gl if n and gl > 0 else np.nan,
            "Giữ TB (nến)": tr["bars"].mean() if n else np.nan}


def by_year(df, p, spread, years, **kw):
    rows = []
    for y in years:
        cur, tr = simulate(df, p, spread, start=f"{y}-01-01", end=f"{y}-12-31 23:59", **kw)
        rows.append({"year": y, **stats(cur, tr, kw.get("capital", 1000.0))})
    return pd.DataFrame(rows).set_index("year")


def pct(v):
    return "-" if pd.isna(v) else f"{v:+.1%}"


def gold_m15(spread, out, capital=1000.0):
    raw = load(FX / "15m" / "PAXGUSDT.csv")
    raw = raw[raw.index >= "2020-10-01"]                   # để EMA200 H1 có đủ dữ liệu trước 2021
    years = list(range(2021, raw.index[-1].year + 1))
    summ, yearly = [], {}
    for name, p in PRESETS.items():
        df = prepare(raw, p, "15min", "1h", drop_weekend=True)
        cur, tr = simulate(df, p, spread, capital=capital, start="2021-01-01")
        s = stats(cur, tr, capital)
        yb = by_year(df, p, spread, years, capital=capital)
        yearly[name] = yb["ROI"]
        summ.append({"Preset": name, "Vốn cuối (chạy liền) $": round(capital * (1 + s["ROI"])),
                     "ROI liền": pct(s["ROI"]), "MDD liền": pct(s["MDD"]), "Lệnh": s["Lệnh"],
                     "Thắng": f"{s['Thắng']:.0%}", "R TB/lệnh": f"{s['R TB/lệnh']:+.3f}", "PF": f"{s['PF']:.2f}",
                     "Giữ TB": f"{s['Giữ TB (nến)'] * 15 / 60:.1f}h",
                     "Năm lời (chạy riêng)": f"{(yb['ROI'] > 0).sum()}/{len(yb)}",
                     "Năm tệ nhất": pct(yb["ROI"].min()), "MDD tệ nhất/năm": pct(yb["MDD"].min())})
        if name == "01-baseline":
            tr.to_csv(out / "xau_m15_trades_baseline.csv", index=False)
            base_df, base_tr = df, tr
        print(f"  {name:<28} {s['Lệnh']:>5} lệnh  ROI {s['ROI']:+8.1%}  MDD {s['MDD']:7.1%}  "
              f"R TB {s['R TB/lệnh']:+.3f}", flush=True)
    gold = load(FX / "1d" / "XAUUSD_GCF.csv")["close"]
    hold = {y: gold.loc[str(y)].iloc[-1] / gold.loc[str(y)].iloc[0] - 1 for y in years}
    yt = pd.DataFrame(yearly).T
    yt.loc["Giữ vàng (GC=F)"] = pd.Series(hold)
    return pd.DataFrame(summ).set_index("Preset"), yt.map(pct), (base_df, base_tr)


FX_PAIRS = {  # tên: (spread theo giá, đơn vị lot nhỏ nhất, cách quy đổi tiền định giá ra USD)
    "XAUUSD_GCF": (0.30, 1, None), "EURUSD": (0.00010, 1000, None), "GBPUSD": (0.00015, 1000, None),
    "AUDUSD": (0.00012, 1000, None), "NZDUSD": (0.00018, 1000, None),
    "USDJPY": (0.012, 1000, ("inv", "USDJPY")), "USDCAD": (0.00015, 1000, ("inv", "USDCAD")),
    "USDCHF": (0.00015, 1000, ("inv", "USDCHF")), "EURJPY": (0.018, 1000, ("inv", "USDJPY")),
    "GBPJPY": (0.025, 1000, ("inv", "USDJPY")), "EURGBP": (0.00015, 1000, ("mul", "GBPUSD")),
}


def forex_h1(out, capital=1000.0):
    """Cùng logic EA nhưng khung tín hiệu H1, xu hướng H4 (Yahoo chỉ có ~2 năm nến 1h, 60 ngày nến 15m)."""
    rows = []
    p = Params()
    for name, (spread, step, cv) in FX_PAIRS.items():
        raw = load(FX / "1h" / f"{name}.csv")
        conv = None
        if cv:
            ref = load(FX / "1h" / f"{cv[1]}.csv")["close"]
            ref.index = server_time(ref.index)
            conv = 1 / ref if cv[0] == "inv" else ref
        df = prepare(raw, p, "1h", "4h", drop_weekend=True)
        start = df.index[0] + pd.Timedelta(days=150)        # chờ EMA200 H4 đủ dữ liệu
        cur, tr = simulate(df, p, spread, capital=capital, unit_step=step, usd_per_quote=conv, start=start)
        s = stats(cur, tr, capital)
        rows.append({"Cặp": name, "Từ": str(start.date()), "Lệnh": s["Lệnh"], "ROI": pct(s["ROI"]),
                     "MDD": pct(s["MDD"]), "Thắng": f"{s['Thắng']:.0%}" if s["Lệnh"] else "-",
                     "R TB/lệnh": f"{s['R TB/lệnh']:+.3f}" if s["Lệnh"] else "-", "Tổng R": f"{s['Tổng R']:+.1f}",
                     "PF": f"{s['PF']:.2f}" if pd.notna(s["PF"]) else "-"})
        print(f"  {name:<11} {s['Lệnh']:>4} lệnh  ROI {s['ROI']:+7.1%}  tổng {s['Tổng R']:+6.1f}R", flush=True)
    return pd.DataFrame(rows).set_index("Cặp")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spread", type=float, default=0.30, help="Spread vàng, USD/oz (mặc định 0.30)")
    ap.add_argument("--capital", type=float, default=10000, help="Vốn (mặc định 10000; $1000 thì lot tối thiểu 0,01 chặn nhiều lệnh vàng)")
    ap.add_argument("--fx", action="store_true", help="Chạy thêm trên forex + GC=F (H1/H4, ~2 năm)")
    args = ap.parse_args()
    out = HERE / "results" / "xau"
    out.mkdir(parents=True, exist_ok=True)

    print(f"Vàng M15 (PAXGUSDT), xu hướng H1, spread ${args.spread}/oz, vốn ${args.capital:,.0f}, rủi ro 1%/lệnh:", flush=True)
    summ, yt, _ = gold_m15(args.spread, out, args.capital)
    print("\n=== Tổng hợp (chạy liền 2021→nay; cột 'năm' là chạy riêng từng năm từ cùng số vốn) ===")
    print(summ.to_string())
    print(f"\n=== ROI từng năm, mỗi năm chạy riêng từ ${args.capital:,.0f} ===")
    print(yt.to_string())
    md = [f"# XAUUSD-bot trên vàng M15 (PAXGUSDT), spread ${args.spread}/oz\n", to_md(summ), "",
          "## ROI từng năm (chạy riêng từ $1000)\n", to_md(yt), ""]
    if args.fx:
        print("\nForex + vàng GC=F, khung H1 / xu hướng H4 (~2 năm):", flush=True)
        fx = forex_h1(out, args.capital)
        print(fx.to_string())
        md += ["## Forex + GC=F, khung H1 / xu hướng H4\n", to_md(fx), ""]
    tag = f"_spread{args.spread:g}_cap{args.capital:g}"
    (out / f"xau_ea{tag}.md").write_text("\n".join(md))
    print(f"\nĐã lưu {out}/xau_ea{tag}.md")


if __name__ == "__main__":
    main()
