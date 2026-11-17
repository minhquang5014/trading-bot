"""Backtest phương pháp của từng repo tham khảo trên dữ liệu đã tải (data/csv/...), so với bot hiện tại và giữ BTC/SPY.

    .venv/bin/python clone_repo/run.py --market crypto
    .venv/bin/python clone_repo/run.py --market us_stocks --capital 1000 --max-open 10
    .venv/bin/python clone_repo/run.py --market crypto --only xau_pullback,mt5_pullback --chart

Kết quả: clone_repo/results/<market>/summary.md, summary.csv, yearly.csv, trades_<chiến lược>.csv,
và chart_<chiến lược>.html (với --chart) mở được bằng trình duyệt.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT)]

import strategies as S  # noqa: E402
from sim import simulate  # noqa: E402
from data.storage import load_all  # noqa: E402
from strategy import load_config  # noqa: E402
from strategy.scanner import build_panels  # noqa: E402
from backtest.report import summarize  # noqa: E402


def stats_row(name, repo, eq, trades, bench_close, cfg):
    s = summarize(eq, trades, bench_close, cfg)
    fin = s["final_equity"]
    return {
        "Chiến lược": name, "Repo": repo,
        "Vốn cuối $": round(fin), "P&L $": round(fin - cfg["initial_capital"]),
        "ROI": s["total_return"], "CAGR": s["cagr"], "MDD": s["max_drawdown"], "Sharpe": s["sharpe"],
        "Số lệnh": s["n_trades"], "Thắng": s["win_rate"], "Lời/Lỗ": s["payoff"], "PF": s["profit_factor"],
        "TB/lệnh": float(trades["ret"].mean()) if len(trades) else np.nan,
        "Giữ TB (ngày)": s["avg_bars"], "Phí $": round(s.get("total_costs", 0)),
        "Có lệnh": s["exposure"],
    }, s["yearly"].iloc[:, 0]


def fmt_table(df):
    pct = ["ROI", "CAGR", "MDD", "Thắng", "TB/lệnh", "Có lệnh"]
    out = df.copy()
    for c in pct:
        out[c] = out[c].map(lambda v: "-" if pd.isna(v) else f"{v:+.1%}" if c in ("ROI", "CAGR", "TB/lệnh") else f"{v:.1%}")
    for c in ["Sharpe", "Lời/Lỗ", "PF", "Giữ TB (ngày)"]:
        out[c] = out[c].map(lambda v: "-" if pd.isna(v) else f"{v:.2f}")
    return out


def to_md(df):
    """Bảng markdown đơn giản (không cần thư viện tabulate)."""
    cols = [df.index.name or ""] + [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join([str(i)] + [str(v) for v in r]) + " |" for i, r in zip(df.index, df.to_numpy())]
    return "\n".join(lines)


def make_chart(spec, cfg, frames, eq, trades, out, top=30):
    from backtest.chart import candles_payload, render
    counts = trades["symbol"].value_counts() if len(trades) else pd.Series(dtype=int)
    symbols = [cfg["benchmark"]] + [s for s in counts.index[:top] if s != cfg["benchmark"]]
    lo = eq.index[0] - pd.Timedelta(days=300)
    candles = {s: candles_payload(frames[s][frames[s].index >= lo]) for s in symbols if s in frames}
    tr = trades[trades["symbol"].isin(symbols)]
    bench = frames[cfg["benchmark"]]["close"].reindex(eq.index).ffill()
    bench = bench / bench.iloc[0] * cfg["initial_capital"]
    stop_pct = spec.stop_pct or (float(trades["init_stop_pct"].median()) if len(trades) and trades["init_stop_pct"].notna().any() else 0)
    s = summarize(eq, trades, frames[cfg["benchmark"]]["close"], cfg)
    r4 = lambda v: None if v is None or pd.isna(v) else round(float(v), 4)  # noqa: E731
    payload = {
        "market": f"{cfg['market']} · {spec.key}", "benchmark": cfg["benchmark"],
        "ccy": cfg.get("quote_ccy", ""),
        "params": {"regime_fast_ma": 5, "regime_slow_ma": 25, "regime_long_ma": 200, "stop_loss_pct": stop_pct,
                   "max_positions": 10},
        "summary": {"start": str(s["start"]), "end": str(s["end"]), "capital": cfg["initial_capital"],
                    "final": round(s["final_equity"], 2), "ret": r4(s["total_return"]), "cagr": r4(s["cagr"]),
                    "mdd": r4(s["max_drawdown"]), "sharpe": r4(s["sharpe"]), "trades": int(s["n_trades"]),
                    "win": r4(s["win_rate"]), "payoff": r4(s["payoff"]), "bench_ret": r4(s["bench_return"]),
                    "bench_mdd": r4(s["bench_max_drawdown"]), "costs": round(s.get("total_costs", 0), 2)},
        "symbols": list(candles),
        "candles": candles,
        "trades": [{"s": r.symbol, "ed": r.entry_date, "xd": r.exit_date, "ep": float(f"{r.entry_price:.6g}"),
                    "xp": float(f"{r.exit_price:.6g}"), "ret": round(float(r.ret), 4), "pnl": round(float(r.pnl), 2),
                    "bars": int(r.bars_held), "why": r.reason} for r in tr.itertuples()],
        "equity": {"t": [d.strftime("%Y-%m-%d") for d in eq.index],
                   "eq": [round(float(x), 2) for x in eq["equity"]],
                   "bench": [round(float(x), 2) for x in bench],
                   "dd": [round(float(x), 4) for x in eq["equity"] / eq["equity"].cummax() - 1],
                   "n": eq["n_positions"].astype(int).tolist()},
        "regime": {"t": [d.strftime("%Y-%m-%d") for d in eq.index], "ok": [1] * len(eq)},
    }
    render(payload, out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", default="crypto")
    ap.add_argument("--capital", type=float, default=1000)
    ap.add_argument("--max-open", type=int, default=10)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--only", help="Danh sách key chiến lược, cách nhau bằng dấu phẩy")
    ap.add_argument("--chart", action="store_true", help="Xuất biểu đồ HTML cho từng chiến lược")
    args = ap.parse_args()

    cfg = load_config(market=args.market, initial_capital=args.capital, min_order_usd=0)
    frames = load_all(cfg["data_dir"])
    bench = frames[cfg["benchmark"]]
    cal = bench.index
    if args.start:
        cal = cal[cal >= pd.Timestamp(args.start)]
    if args.end:
        cal = cal[cal <= pd.Timestamp(args.end)]
    # Chỉ báo cần lịch sử trước ngày bắt đầu, nên tính trên toàn bộ dữ liệu rồi mới cắt.
    P_full = build_panels(frames, index=bench.index)
    print(f"[{cfg['market']}] {len(frames)} mã, {cal[0].date()} → {cal[-1].date()}, vốn ${args.capital:,.0f}, "
          f"tối đa {args.max_open} lệnh, phí {cfg['fee_pct']:.2%} + trượt giá {cfg['slippage_pct']:.2%} mỗi chiều", flush=True)

    out_dir = HERE / "results" / cfg["market"]
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, yearly, notes = [], {}, []
    funcs = S.ALL if not args.only else [f for f in S.ALL if f.__name__ in args.only.split(",")]
    for fn in funcs:
        t = time.time()
        spec = fn(P_full, bench["close"])
        P = {k: v.loc[cal] for k, v in P_full.items()}
        spec.entry = spec.entry.loc[cal]
        for attr in ("exit", "atr", "entry_trigger", "stop_level"):
            if getattr(spec, attr) is not None:
                setattr(spec, attr, getattr(spec, attr).loc[cal])
        eq, tr = simulate(spec, P, cfg, args.capital, args.max_open)
        row, yr = stats_row(spec.name, spec.repo, eq, tr, bench["close"], cfg)
        rows.append({"key": spec.key, **row})
        yearly[spec.key] = yr
        notes.append((spec, row))
        tr.to_csv(out_dir / f"trades_{spec.key}.csv", index=False)
        if args.chart and len(tr):
            make_chart(spec, cfg, frames, eq, tr, out_dir / f"chart_{spec.key}.html")
        print(f"  {spec.key:<18} {row['Số lệnh']:>5} lệnh  ROI {row['ROI']:+8.1%}  MDD {row['MDD']:7.1%}  "
              f"({time.time() - t:.0f}s)", flush=True)

    # Bot hiện tại (không sửa gì, chỉ gọi engine với cùng vốn và cùng 1/10 vốn mỗi lệnh)
    from backtest.engine import run_backtest
    bcfg = {**cfg, "position_pct": 1 / args.max_open, "max_positions": args.max_open}
    eng, eq, tr = run_backtest(bcfg, frames, cal[0], cal[-1])
    row, yr = stats_row("Bot hiện tại (Shotgun)", "repo này", eq, tr, bench["close"], bcfg)
    rows.append({"key": "shotgun_bot", **row})
    yearly["shotgun_bot"] = yr

    # Giữ chỉ số chuẩn
    b = bench["close"].loc[cal]
    beq = pd.DataFrame({"equity": b / b.iloc[0] * args.capital, "n_positions": 1})
    row, yr = stats_row(f"Giữ {cfg['benchmark'].split('/')[0]}", "-", beq, pd.DataFrame(), bench["close"], cfg)
    rows.append({"key": "buy_hold", **row})
    yearly["buy_hold"] = yr

    df = pd.DataFrame(rows).set_index("key").sort_values("ROI", ascending=False)
    df.to_csv(out_dir / "summary.csv")
    yd = pd.DataFrame(yearly).T.reindex(df.index)
    yd.to_csv(out_dir / "yearly.csv")

    show = fmt_table(df).drop(columns=["Repo"])
    print("\n=== Xếp theo ROI ===")
    print(show.to_string())
    print("\n=== ROI theo năm ===")
    print(yd.apply(lambda c: c.map(lambda v: "-" if pd.isna(v) else f"{v:+.0%}")).to_string())

    with open(out_dir / "summary.md", "w") as f:
        f.write(f"# Backtest các repo tham khảo · {cfg['market']}\n\n")
        f.write(f"{cal[0].date()} → {cal[-1].date()} · vốn ${args.capital:,.0f} · tối đa {args.max_open} lệnh, "
                f"mỗi lệnh 1/{args.max_open} vốn · phí {cfg['fee_pct']:.2%} + trượt giá {cfg['slippage_pct']:.2%} mỗi chiều\n\n")
        f.write(to_md(fmt_table(df)) + "\n\n## ROI theo năm\n\n")
        f.write(to_md(yd.apply(lambda c: c.map(lambda v: "-" if pd.isna(v) else f"{v:+.0%}"))) + "\n\n")
        f.write("## Cách chuyển từng repo sang nến ngày\n\n")
        for spec, _ in notes:
            f.write(f"- **{spec.name}** ({spec.repo}): " + " ".join(spec.notes) + "\n")
    print(f"\nĐã lưu {out_dir}/summary.md, summary.csv, yearly.csv, trades_*.csv" + (", chart_*.html" if args.chart else ""))


if __name__ == "__main__":
    main()
