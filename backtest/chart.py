"""Biểu đồ tương tác kiểu TradingView cho kết quả backtest, vẽ bằng HTML + canvas thuần (không thư viện).

Xuất một file HTML tự chứa: nến + khối lượng + MA5/25/200 + điểm mua/bán của từng lệnh,
đường vốn so với giữ chỉ số chuẩn, bảng lệnh (bấm để nhảy tới lệnh đó).

    python backtest/chart.py --market crypto --open
    python backtest/chart.py --market us_stocks --top 50 --open
    python backtest/chart.py --market crypto --symbols SOL/USDT,DOGE/USDT --open
"""
import argparse
import json
import sys
import webbrowser
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.engine import run_backtest  # noqa: E402
from backtest.report import summarize  # noqa: E402
from data.storage import load_all  # noqa: E402
from strategy import load_config  # noqa: E402

TEMPLATE = Path(__file__).with_name("chart_template.html")


def _r(values):
    """Làm tròn 6 chữ số có nghĩa để file nhẹ."""
    return [None if pd.isna(v) else float(f"{v:.6g}") for v in values]


def candles_payload(df: pd.DataFrame) -> dict:
    df = df.dropna(subset=["open", "high", "low", "close"])
    return {
        "t": [d.strftime("%Y-%m-%d") for d in df.index],
        "o": _r(df["open"]), "h": _r(df["high"]), "l": _r(df["low"]), "c": _r(df["close"]),
        "v": _r(df["volume"] * df["close"]),  # khối lượng theo tiền (USD/USDT)
    }


def build(cfg: dict, symbols=None, top: int = 30, start=None, end=None) -> dict:
    frames = load_all(cfg["data_dir"])
    eng, eq, trades = run_backtest(cfg, frames, start, end)
    s = summarize(eq, trades, eng.regime["close"], cfg)
    bench = cfg["benchmark"]

    if symbols is None:
        counts = trades["symbol"].value_counts() if not trades.empty else pd.Series(dtype=int)
        symbols = list(counts.index[:top])
    symbols = [bench] + [x for x in symbols if x != bench and x in frames]

    lo = eq.index[0] - pd.Timedelta(days=300) if not eq.empty else None
    data = {}
    for sym in symbols:
        df = frames[sym]
        if lo is not None:
            df = df[df.index >= lo]
        data[sym] = candles_payload(df)

    tr = trades[trades["symbol"].isin(symbols)] if not trades.empty else trades
    trade_rows = [] if tr.empty else [
        {"s": r.symbol, "ed": r.entry_date, "xd": r.exit_date, "ep": float(f"{r.entry_price:.6g}"),
         "xp": float(f"{r.exit_price:.6g}"), "ret": round(float(r.ret), 4), "pnl": round(float(r.pnl), 2),
         "bars": int(r.bars_held), "why": r.reason}
        for r in tr.itertuples()
    ]

    bench_eq = eng.regime["close"].reindex(eq.index).ffill()
    bench_eq = bench_eq / bench_eq.iloc[0] * cfg["initial_capital"]
    dd = eq["equity"] / eq["equity"].cummax() - 1

    def pct(v):
        return None if v is None or pd.isna(v) else round(float(v), 4)

    return {
        "market": cfg["market"], "benchmark": bench, "ccy": cfg.get("quote_ccy", cfg.get("quote", "")),
        "params": {k: cfg.get(k) for k in ("max_positions", "position_pct", "stop_loss_pct", "time_stop_days",
                                           "time_stop_min_gain", "trail_trigger_pct", "trail_pct",
                                           "regime_fast_ma", "regime_slow_ma", "regime_long_ma",
                                           "fee_pct", "slippage_pct")},
        "summary": {
            "start": str(s["start"]), "end": str(s["end"]), "capital": cfg["initial_capital"],
            "final": round(s["final_equity"], 2), "ret": pct(s["total_return"]), "cagr": pct(s["cagr"]),
            "mdd": pct(s["max_drawdown"]), "sharpe": pct(s["sharpe"]), "trades": int(s["n_trades"]),
            "win": pct(s["win_rate"]), "payoff": pct(s["payoff"]), "bench_ret": pct(s["bench_return"]),
            "bench_mdd": pct(s["bench_max_drawdown"]), "costs": round(s.get("total_costs", 0), 2),
        },
        "symbols": symbols,
        "candles": data,
        "trades": trade_rows,
        "equity": {"t": [d.strftime("%Y-%m-%d") for d in eq.index], "eq": _r(eq["equity"]),
                   "bench": _r(bench_eq), "dd": _r(dd), "n": eq["n_positions"].astype(int).tolist()},
        # Ngày được phép mở lệnh mới theo bộ lọc thị trường (để tô dải màu trên biểu đồ)
        "regime": {"t": [d.strftime("%Y-%m-%d") for d in eng.regime.index],
                   "ok": eng.regime["ok"].astype(int).tolist()},
    }


def render(payload: dict, out: Path) -> Path:
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("__DATA__", blob).replace("__TITLE__", f"Shotgun Chart – {payload['market']}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--market")
    ap.add_argument("--symbols", help="Danh sách mã, mặc định lấy các mã có nhiều lệnh nhất")
    ap.add_argument("--top", type=int, default=30, help="Số mã đưa vào biểu đồ (mặc định 30)")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--out")
    ap.add_argument("--open", action="store_true", help="Mở trong trình duyệt")
    args = ap.parse_args()

    cfg = load_config(args.config, market=args.market)
    symbols = [x.strip() for x in args.symbols.split(",")] if args.symbols else None
    payload = build(cfg, symbols, args.top, args.start, args.end)
    out = Path(args.out) if args.out else ROOT / "backtest" / "results" / cfg["market"] / "chart.html"
    render(payload, out)
    size = out.stat().st_size / 1e6
    print(f"Đã tạo {out} ({size:.1f} MB, {len(payload['symbols'])} mã, {len(payload['trades'])} lệnh)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
