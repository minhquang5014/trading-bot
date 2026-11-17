"""Thử từng đề xuất ở README mục 9, mỗi ý bật riêng trên nền cấu hình trong config.yaml, rồi ghép các ý có ích.
Lưu ý: us_stocks đã bật bản ghép, nên "Bot hiện tại" của us_stocks chính là bản ghép.

Mỗi năm backtest riêng (bắt đầu lại từ vốn ban đầu, không mang lệnh từ năm trước) để tránh kết quả
chỉ đẹp nhờ một giai đoạn. Chỉ báo vẫn tính trên toàn bộ lịch sử nên không nhìn trước tương lai.

    .venv/bin/python backtest/ablation.py --market crypto
    .venv/bin/python backtest/ablation.py --market us_stocks
    .venv/bin/python backtest/ablation.py --market crypto --only base,pullback,atr15

Kết quả: backtest/results/<market>/ablation.md và ablation.csv
"""
import argparse
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.engine import Engine  # noqa: E402
from backtest.report import summarize  # noqa: E402
from data.storage import load_all  # noqa: E402
from strategy import load_config  # noqa: E402

PULLBACK = {"fast": 21, "slow": 50, "lookback": 5, "rsi_max": None, "rsi_min": None}
# Vào kiểu hồi về EMA mâu thuẫn với "đóng cửa cao nhất 20 phiên" và "khối lượng ≥ 1,5 lần" của crypto.
NO_BREAKOUT = {"breakout_days": None, "min_vol_ratio": None}
# Thoát theo R như XAUUSD-bot: bỏ time stop 2 ngày và thoát dưới MA5, chỉ còn cắt lỗ / hoà vốn / chốt lời / trailing.
R_EXITS = {"time_stop_days": 10_000, "exit_ma": 0, "breakeven_r": 1.0, "take_profit_r": 2.5}
GUARDS = {"stoploss_guard": {"count": 3, "days": 10, "pause_days": 5},
          "drawdown_guard": {"pct": 0.10, "days": 30, "pause_days": 10}}


def variants():
    """(key, tên, ghi đè config, ghi đè filters)."""
    return [
        ("base", "Bot hiện tại", {}, {}),
        # 1. Vào lệnh kiểu hồi về EMA
        ("pullback", "Hồi về EMA21", {}, {"pullback": PULLBACK, **NO_BREAKOUT}),
        ("pullback_rsi", "Hồi về EMA21 + RSI ≤45 lúc hồi, ≥50 lúc vào", {},
         {"pullback": {**PULLBACK, "rsi_max": 45, "rsi_min": 50}, **NO_BREAKOUT}),
        ("pullback_dist", "Hồi về EMA21 + không đuổi giá 1,5 ATR", {},
         {"pullback": PULLBACK, "max_dist_atr": 1.5, **NO_BREAKOUT}),
        # 2. Cắt lỗ theo ATR
        ("atr15", "Cắt lỗ 1,5 ATR", {"stop_atr": 1.5}, {}),
        ("atr2", "Cắt lỗ 2 ATR", {"stop_atr": 2.0}, {}),
        # 3. Lọc xu hướng
        ("slope", "EMA200 dốc lên (5 phiên)", {}, {"ema_slope": [200, 5]}),
        ("adx", "ADX ≥ 25 và +DI > −DI", {}, {"adx_min": 25}),
        # 4. Không mua lúc quá nóng
        ("no_pump", "RSI < 70, bỏ khối lượng > 5 lần, xếp hạng chỉ theo đà", {"rank_by": "momentum"},
         {"max_rsi": 70, "max_vol_ratio": 5}),
        # 5. Nghỉ sau chuỗi thua
        ("guards", "StoplossGuard 3/10 ngày + MaxDrawdown 10%", GUARDS, {}),
        # 6. Hoà vốn ở 1R, chốt lời theo R
        ("be_tp", "Hoà vốn 1R + chốt 2,5R (giữ time stop, MA5)", {"breakeven_r": 1.0, "take_profit_r": 2.5}, {}),
        ("r_exits", "Chỉ thoát theo R: hoà vốn 1R, chốt 2,5R, bỏ time stop + MA5", R_EXITS, {}),
        # Ghép
        ("combo_pb_atr", "Hồi EMA21 + cắt lỗ 2 ATR + thoát theo R", {"stop_atr": 2.0, **R_EXITS},
         {"pullback": PULLBACK, **NO_BREAKOUT}),
        ("combo_all", "Hồi EMA21 + 2 ATR + thoát R + EMA dốc + ADX + RSI<70 + guards",
         {"stop_atr": 2.0, **R_EXITS, **GUARDS, "rank_by": "momentum"},
         {"pullback": PULLBACK, **NO_BREAKOUT, "ema_slope": [200, 5], "adx_min": 25, "max_rsi": 70}),
    ]


def to_md(t: pd.DataFrame) -> str:
    cols = [t.index.name or ""] + [str(c) for c in t.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join([str(i)] + [str(v) for v in r]) + " |" for i, r in zip(t.index, t.values)]
    return "\n".join(lines)


def make_cfg(base, ov, fov):
    c = copy.deepcopy(base)
    c.update(ov)
    c["filters"] = {**(c.get("filters") or {}), **fov}
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", default="crypto")
    ap.add_argument("--capital", type=float, default=1000)
    ap.add_argument("--position-pct", type=float, default=0.10, help="Trần mỗi lệnh (mặc định 1/10 vốn)")
    ap.add_argument("--only", help="Danh sách key, cách nhau bằng dấu phẩy")
    args = ap.parse_args()

    base = load_config(market=args.market, initial_capital=args.capital, position_pct=args.position_pct)
    frames = load_all(base["data_dir"])
    bench = frames[base["benchmark"]]["close"]
    years = sorted({d.year for d in bench.index if d >= bench.index[0] + pd.Timedelta(days=200)})
    vs = [v for v in variants() if not args.only or v[0] in args.only.split(",")]
    print(f"[{base['market']}] {len(vs)} biến thể × {len(years)} năm, mỗi năm từ ${args.capital:,.0f}, "
          f"trần {args.position_pct:.0%}/lệnh", flush=True)

    rows, all_trades = [], {}
    for key, name, ov, fov in vs:
        cfg = make_cfg(base, ov, fov)
        eng = Engine(cfg, frames)
        trades = []
        for y in years:
            cal = eng.calendar[(eng.calendar >= f"{y}-01-01") & (eng.calendar <= f"{y}-12-31")]
            if len(cal) < 20:
                continue
            eng.reset(args.capital)
            eq, tr = eng.run(cal[0], cal[-1])
            s = summarize(eq, tr, bench, cfg)
            rows.append({"key": key, "name": name, "year": y, "roi": s["total_return"], "mdd": s["max_drawdown"],
                         "trades": s["n_trades"], "pf": s["profit_factor"], "win": s["win_rate"]})
            if len(tr):
                trades.append(tr.assign(year=y))
        all_trades[key] = pd.concat(trades) if trades else pd.DataFrame()
        print(f"  {key:<14} xong", flush=True)
    for y in years:
        b = bench[(bench.index >= f"{y}-01-01") & (bench.index <= f"{y}-12-31")]
        if len(b) >= 20:
            rows.append({"key": "buy_hold", "name": f"Giữ {base['benchmark'].split('/')[0]}", "year": y,
                         "roi": b.iloc[-1] / b.iloc[0] - 1, "mdd": float((b / b.cummax() - 1).min()),
                         "trades": 0, "pf": np.nan, "win": np.nan})

    df = pd.DataFrame(rows)
    order = [v[0] for v in vs] + ["buy_hold"]
    roi = df.pivot(index="key", columns="year", values="roi").reindex(order)
    mdd = df.pivot(index="key", columns="year", values="mdd").reindex(order)
    names = df.drop_duplicates("key").set_index("key")["name"].reindex(order)

    def pct(v):
        return "-" if pd.isna(v) else f"{v:+.0%}"

    def agg(k):
        t = all_trades.get(k)
        if t is None or t.empty:
            return pd.Series({"Lệnh": 0, "Thắng": "-", "PF": "-", "TB/lệnh": "-", "Cắt lỗ ngày 1": "-"})
        g, l = t.loc[t.pnl > 0, "pnl"].sum(), -t.loc[t.pnl <= 0, "pnl"].sum()
        d1 = ((t.reason == "stop_loss") & (t.bars_held <= 1)).mean()
        return pd.Series({"Lệnh": len(t), "Thắng": f"{(t.pnl > 0).mean():.0%}", "PF": f"{g / l:.2f}" if l else "-",
                          "TB/lệnh": f"{t.ret.mean():+.2%}", "Cắt lỗ ngày 1": f"{d1:.0%}"})

    summ = pd.DataFrame({
        "Biến thể": names,
        "Năm lời": (roi > 0).sum(axis=1).astype(str) + "/" + roi.notna().sum(axis=1).astype(str),
        "ROI trung vị": roi.median(axis=1).map(pct),
        "Năm tệ nhất": roi.min(axis=1).map(pct),
        "MDD tệ nhất": mdd.min(axis=1).map(pct),
    })
    summ = summ.join(pd.DataFrame({k: agg(k) for k in order}).T)
    yr = roi.apply(lambda c: c.map(pct))
    yr.insert(0, "Biến thể", names)

    print("\n=== Tổng hợp (mỗi năm chạy riêng) ===")
    print(summ.to_string())
    print("\n=== ROI từng năm ===")
    print(yr.to_string())

    out = ROOT / "backtest" / "results" / base["market"]
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "ablation.csv", index=False)
    with open(out / "ablation.md", "w") as f:
        f.write(f"# Thử từng đề xuất README mục 9 · {base['market']}\n\nMỗi năm backtest riêng từ "
                f"${args.capital:,.0f}, tối đa {base['max_positions']} lệnh, trần {args.position_pct:.0%} vốn/lệnh, "
                f"rủi ro {base['risk_per_trade']:.0%}/lệnh, phí {base['fee_pct']:.2%} + trượt giá "
                f"{base['slippage_pct']:.2%} mỗi chiều. PF, thắng, TB/lệnh tính trên toàn bộ lệnh của mọi năm.\n\n")
        f.write(to_md(summ) + "\n\n## ROI từng năm\n\n" + to_md(yr) + "\n")
    print(f"\nĐã lưu {out / 'ablation.md'}")


if __name__ == "__main__":
    main()
