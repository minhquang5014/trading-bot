"""Backtest RIÊNG từng năm: mỗi năm bắt đầu lại từ vốn ban đầu, không mang lệnh hay lời/lỗ từ năm trước.

Khác với bảng "ROI theo năm" trong run.py (chỉ là cắt một lần chạy liên tục thành từng năm).
Chỉ báo vẫn tính trên toàn bộ lịch sử nên đầu năm EMA200, RSI... đã có giá trị (không nhìn trước tương lai).

    .venv/bin/python clone_repo/yearly.py --market crypto
    .venv/bin/python clone_repo/yearly.py --market us_stocks --max-hold 15

--max-hold: chỉ hiện các chiến lược có thời gian giữ lệnh trung bình ≤ N ngày (đánh ngắn).
Kết quả: clone_repo/results/<market>/by_year.md, by_year.csv
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

import strategies as S  # noqa: E402
from sim import simulate  # noqa: E402
from run import to_md  # noqa: E402
from data.storage import load_all  # noqa: E402
from strategy import load_config  # noqa: E402
from strategy.scanner import build_panels  # noqa: E402
from backtest.report import summarize  # noqa: E402


def cut(spec, P_full, cal):
    """Bản sao spec + bảng giá chỉ trong khoảng ngày `cal`."""
    import copy
    s = copy.copy(spec)
    for attr in ("entry", "exit", "atr", "entry_trigger", "stop_level"):
        if getattr(s, attr) is not None:
            setattr(s, attr, getattr(s, attr).loc[cal])
    return s, {k: v.loc[cal] for k, v in P_full.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", default="crypto")
    ap.add_argument("--capital", type=float, default=1000)
    ap.add_argument("--max-open", type=int, default=10)
    ap.add_argument("--max-hold", type=float, default=15, help="Ngưỡng 'đánh ngắn': giữ TB ≤ N ngày")
    args = ap.parse_args()

    cfg = load_config(market=args.market, initial_capital=args.capital, min_order_usd=0)
    frames = load_all(cfg["data_dir"])
    bench = frames[cfg["benchmark"]]
    P_full = build_panels(frames, index=bench.index)
    years = sorted({d.year for d in bench.index if d >= bench.index[0] + pd.Timedelta(days=200)})
    print(f"[{cfg['market']}] chạy riêng từng năm {years[0]}–{years[-1]}, mỗi năm bắt đầu ${args.capital:,.0f}", flush=True)

    rows = []
    specs = [fn(P_full, bench["close"]) for fn in S.ALL]
    from backtest.engine import run_backtest
    bcfg = {**cfg, "position_pct": 1 / args.max_open, "max_positions": args.max_open}

    for y in years:
        cal = bench.index[(bench.index >= f"{y}-01-01") & (bench.index <= f"{y}-12-31")]
        if len(cal) < 20:
            continue
        for spec in specs:
            sp, P = cut(spec, P_full, cal)
            eq, tr = simulate(sp, P, cfg, args.capital, args.max_open)
            s = summarize(eq, tr, bench["close"], cfg)
            rows.append({"key": spec.key, "name": spec.name, "year": y, "roi": s["total_return"],
                         "mdd": s["max_drawdown"], "trades": s["n_trades"], "pf": s["profit_factor"],
                         "win": s["win_rate"], "hold": s["avg_bars"], "fees": s.get("total_costs", 0)})
        eng, eq, tr = run_backtest(bcfg, frames, cal[0], cal[-1])
        s = summarize(eq, tr, bench["close"], bcfg)
        rows.append({"key": "shotgun_bot", "name": "Bot hiện tại (Shotgun)", "year": y, "roi": s["total_return"],
                     "mdd": s["max_drawdown"], "trades": s["n_trades"], "pf": s["profit_factor"],
                     "win": s["win_rate"], "hold": s["avg_bars"], "fees": s.get("total_costs", 0)})
        b = bench["close"].loc[cal]
        rows.append({"key": "buy_hold", "name": f"Giữ {cfg['benchmark'].split('/')[0]}", "year": y,
                     "roi": b.iloc[-1] / b.iloc[0] - 1, "mdd": float((b / b.cummax() - 1).min()),
                     "trades": 0, "pf": np.nan, "win": np.nan, "hold": np.nan, "fees": 0})
        print(f"  {y} xong", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(HERE / "results" / cfg["market"] / "by_year.csv", index=False)

    # Thời gian giữ TB tính trên toàn bộ lệnh của mọi năm
    hold = df.groupby("key").apply(lambda g: np.average(g["hold"].fillna(0), weights=g["trades"].clip(lower=1)))
    roi = df.pivot(index="key", columns="year", values="roi")
    mdd = df.pivot(index="key", columns="year", values="mdd")
    names = df.drop_duplicates("key").set_index("key")["name"]
    summ = pd.DataFrame({
        "Chiến lược": names,
        "Giữ TB": hold.round(1),
        "Năm lời": (roi > 0).sum(axis=1).astype(str) + "/" + roi.notna().sum(axis=1).astype(str),
        "ROI trung vị": roi.median(axis=1),
        "Năm tệ nhất": roi.min(axis=1),
        "MDD tệ nhất": mdd.min(axis=1),
        "Lệnh/năm": df.groupby("key")["trades"].mean().round(0),
    }).sort_values("ROI trung vị", ascending=False)

    def pct(v):
        return "-" if pd.isna(v) else f"{v:+.0%}"

    short = summ[(summ["Giữ TB"] <= args.max_hold) | summ.index.isin(["shotgun_bot", "buy_hold"])]
    long_ = summ[~summ.index.isin(short.index)]

    def show(t):
        t = t.copy()
        for c in ("ROI trung vị", "Năm tệ nhất", "MDD tệ nhất"):
            t[c] = t[c].map(pct)
        t["Lệnh/năm"] = t["Lệnh/năm"].astype(int)
        return t

    roi_s = roi.reindex(summ.index).apply(lambda c: c.map(pct))
    roi_s.insert(0, "Chiến lược", names.reindex(roi_s.index))
    print(f"\n=== Đánh ngắn (giữ TB ≤ {args.max_hold:g} ngày) — mỗi năm chạy riêng từ ${args.capital:,.0f} ===")
    print(show(short).to_string())
    print(f"\n=== Giữ dài (> {args.max_hold:g} ngày) ===")
    print(show(long_).to_string())
    print("\n=== ROI từng năm (chạy riêng) ===")
    print(roi_s.to_string())

    out = HERE / "results" / cfg["market"] / "by_year.md"
    with open(out, "w") as f:
        f.write(f"# Backtest riêng từng năm · {cfg['market']}\n\nMỗi năm bắt đầu lại từ ${args.capital:,.0f}, "
                f"tối đa {args.max_open} lệnh, mỗi lệnh 1/{args.max_open} vốn, không mang lệnh từ năm trước.\n\n")
        f.write(f"## Đánh ngắn (giữ TB ≤ {args.max_hold:g} ngày)\n\n{to_md(show(short))}\n\n")
        f.write(f"## Giữ dài\n\n{to_md(show(long_))}\n\n## ROI từng năm\n\n{to_md(roi_s)}\n")
    print(f"\nĐã lưu {out}")


if __name__ == "__main__":
    main()
