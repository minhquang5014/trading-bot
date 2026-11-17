"""Thử nghiệm biến thể của các phương pháp đánh ngắn, mỗi năm backtest riêng (bắt đầu lại từ vốn ban đầu).

    .venv/bin/python clone_repo/experiments.py a    # cổ phiếu Mỹ: mua khi giảm (RSI4, EMASkipPump) + bộ lọc
    .venv/bin/python clone_repo/experiments.py b    # crypto: chi phí thấp hơn + chỉ top 30/50 coin thanh khoản

Kết quả: clone_repo/results/<market>/experiment_<a|b>.md và .csv
"""
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

import indicators as ta  # noqa: E402
import strategies as S  # noqa: E402
from sim import simulate  # noqa: E402
from run import to_md  # noqa: E402
from yearly import cut  # noqa: E402
from data.storage import load_all  # noqa: E402
from strategy import load_config  # noqa: E402
from strategy.regime import compute_regime  # noqa: E402
from strategy.scanner import build_panels  # noqa: E402
from backtest.report import summarize  # noqa: E402


def with_entry(spec, mask, key, name):
    """Bản sao spec, chỉ cho vào lệnh khi `mask` đúng (mask: Series theo ngày hoặc DataFrame ngày × mã)."""
    s = copy.copy(spec)
    s.entry = spec.entry.mul(mask, axis=0).astype(bool) if isinstance(mask, pd.Series) else (spec.entry & mask)
    s.key, s.name = key, name
    return s


def top_n_mask(P, n):
    dv = (P["close"] * P["volume"]).rolling(20).mean()
    return dv.rank(axis=1, ascending=False) <= n


def by_year(variants, cfg, P_full, bench, capital=1000.0, max_open=10):
    years = sorted({d.year for d in bench.index if d >= bench.index[0] + pd.Timedelta(days=200)})
    rows = []
    for y in years:
        cal = bench.index[(bench.index >= f"{y}-01-01") & (bench.index <= f"{y}-12-31")]
        if len(cal) < 20:
            continue
        for spec, vcfg in variants:
            sp, P = cut(spec, P_full, cal)
            c = {**cfg, **vcfg}
            eq, tr = simulate(sp, P, c, capital, max_open)
            s = summarize(eq, tr, bench["close"], c)
            rows.append({"key": spec.key, "name": spec.name, "year": y, "roi": s["total_return"],
                         "mdd": s["max_drawdown"], "trades": s["n_trades"], "hold": s["avg_bars"],
                         "pf": s["profit_factor"], "exposure": s["exposure"]})
        b = bench["close"].loc[cal]
        rows.append({"key": "buy_hold", "name": f"Giữ {cfg['benchmark'].split('/')[0]}", "year": y,
                     "roi": b.iloc[-1] / b.iloc[0] - 1, "mdd": float((b / b.cummax() - 1).min()),
                     "trades": 0, "hold": np.nan, "pf": np.nan, "exposure": 1.0})
        print(f"  {y} xong", flush=True)
    return pd.DataFrame(rows)


def report(df, title, out_base, order):
    def pct(v):
        return "-" if pd.isna(v) else f"{v:+.0%}"

    roi = df.pivot(index="key", columns="year", values="roi").reindex(order)
    mdd = df.pivot(index="key", columns="year", values="mdd").reindex(order)
    g = df.groupby("key")
    hold = g.apply(lambda x: np.average(x["hold"].fillna(0), weights=x["trades"].clip(lower=1)), include_groups=False)
    names = df.drop_duplicates("key").set_index("key")["name"]
    summ = pd.DataFrame({
        "Biến thể": names.reindex(order),
        "Năm lời": ((roi > 0).sum(axis=1).astype(str) + "/" + roi.notna().sum(axis=1).astype(str)),
        "ROI trung vị": roi.median(axis=1).map(pct),
        "ROI TB": roi.mean(axis=1).map(pct),
        "Năm tệ nhất": roi.min(axis=1).map(pct),
        "MDD tệ nhất": mdd.min(axis=1).map(pct),
        "Lệnh/năm": g["trades"].mean().reindex(order).round(0).astype(int),
        "Giữ TB": hold.reindex(order).round(1),
        "Có lệnh": g["exposure"].mean().reindex(order).map(lambda v: f"{v:.0%}"),
    })
    yr = roi.apply(lambda c: c.map(pct))
    yr.insert(0, "Biến thể", names.reindex(order))
    print(f"\n=== {title} ===")
    print(summ.to_string())
    print()
    print(yr.to_string())
    df.to_csv(out_base.with_suffix(".csv"), index=False)
    with open(out_base.with_suffix(".md"), "w") as f:
        f.write(f"# {title}\n\nMỗi năm backtest riêng từ $1,000, tối đa 10 lệnh, mỗi lệnh 1/10 vốn.\n\n")
        f.write(to_md(summ) + "\n\n## ROI từng năm\n\n" + to_md(yr) + "\n")
    print(f"\nĐã lưu {out_base.with_suffix('.md')}")


def exp_a():
    """Cổ phiếu Mỹ: phương pháp mua khi giảm + bộ lọc thị trường SPY và xu hướng của mã."""
    cfg = load_config(market="us_stocks", initial_capital=1000, min_order_usd=0)
    frames = load_all(cfg["data_dir"])
    bench = frames[cfg["benchmark"]]
    P = build_panels(frames, index=bench.index)
    spy = bench["close"]
    spy_ok = spy > spy.rolling(200).mean()                    # PTJ: SPY trên MA200
    trend200 = P["close"] > ta.ema(P["close"], 200)           # chính mã trên EMA200
    rsi, skip = S.pc_rsi(P, spy), S.ft_ema_skip_pump(P, spy)
    v = [
        (rsi, {}),
        (with_entry(rsi, spy_ok, "rsi4_spy", "RSI4 + SPY > MA200"), {}),
        (with_entry(rsi, trend200.mul(spy_ok, axis=0).astype(bool), "rsi4_spy_t200", "RSI4 + SPY > MA200 + mã > EMA200"), {}),
        (skip, {}),
        (with_entry(skip, spy_ok, "skip_spy", "EMASkipPump + SPY > MA200"), {}),
        (with_entry(skip, trend200, "skip_t200", "EMASkipPump + mã > EMA200"), {}),
        (with_entry(skip, trend200.mul(spy_ok, axis=0).astype(bool), "skip_spy_t200", "EMASkipPump + SPY > MA200 + mã > EMA200"), {}),
    ]
    df = by_year(v, cfg, P, bench)
    report(df, "(a) Cổ phiếu Mỹ: mua khi giảm + bộ lọc", HERE / "results" / "us_stocks" / "experiment_a",
           [s.key for s, _ in v] + ["buy_hold"])


def exp_b():
    """Crypto: chi phí sát thực tế hơn, chỉ top N coin thanh khoản, có/không bộ lọc BTC của bot."""
    cfg = load_config(market="crypto", initial_capital=1000, min_order_usd=0)
    frames = load_all(cfg["data_dir"])
    bench = frames[cfg["benchmark"]]
    P = build_panels(frames, index=bench.index)
    btc = bench["close"]
    btc_ok = compute_regime(btc, cfg)["ok"].reindex(P["close"].index).fillna(False)   # BTC > MA200 và MA5 > MA25
    low_cost = {"fee_pct": 0.001, "slippage_pct": 0.0005}     # phí taker Binance 0,1% + trượt giá 0,05% (coin lớn)
    top = {n: top_n_mask(P, n) for n in (30, 50)}
    v = []
    for fn in (S.pc_rsi, S.pc_macd, S.xau_pullback, S.mt5_pullback, S.ft_ema_skip_pump, S.ft_trendrider,
               S.ft_adx_momentum):
        sp = fn(P, btc)
        v.append((with_entry(sp, top[50], f"{sp.key}|top50", f"{sp.name} · top50"), low_cost))
        v.append((with_entry(sp, top[30], f"{sp.key}|top30", f"{sp.name} · top30"), low_cost))
        v.append((with_entry(with_entry(sp, top[30], "", ""), btc_ok, f"{sp.key}|top30_btc",
                             f"{sp.name} · top30 + lọc BTC"), low_cost))
    df = by_year(v, cfg, P, bench)
    report(df, "(b) Crypto: phí 0,1% + trượt giá 0,05%, chỉ top 30/50 coin thanh khoản",
           HERE / "results" / "crypto" / "experiment_b", [s.key for s, _ in v] + ["buy_hold"])


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "ab"
    if "a" in which:
        exp_a()
    if "b" in which:
        exp_b()
