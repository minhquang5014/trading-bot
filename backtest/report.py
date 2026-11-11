"""Thống kê kết quả: lợi nhuận, sụt giảm lớn nhất, tỷ lệ thắng, lời/lỗ trung bình, so với giữ BTC."""
import numpy as np
import pandas as pd



def _max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min()) if len(equity) else float("nan")


def _cagr(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return float("nan")
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else float("nan")


def summarize(eq: pd.DataFrame, trades: pd.DataFrame, bench_close: pd.Series, cfg: dict) -> dict:
    nan = float("nan")
    if eq.empty:
        return {}
    equity = eq["equity"]
    start_cap = cfg["initial_capital"]
    rets = equity.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(cfg.get("periods_per_year", 365))) if rets.std() > 0 else nan

    bench = bench_close.reindex(equity.index).ffill()
    bench_eq = bench / bench.iloc[0] * start_cap

    s = {
        "start": equity.index[0].date(), "end": equity.index[-1].date(),
        "final_equity": float(equity.iloc[-1]),
        "total_return": float(equity.iloc[-1] / start_cap - 1),
        "cagr": _cagr(pd.concat([pd.Series([start_cap], index=[equity.index[0]]), equity.iloc[1:]])),
        "max_drawdown": _max_drawdown(equity),
        "sharpe": sharpe,
        "exposure": float((eq["n_positions"] > 0).mean()),
        "avg_positions": float(eq["n_positions"].mean()),
        "bench_return": float(bench_eq.iloc[-1] / start_cap - 1),
        "bench_cagr": _cagr(bench_eq),
        "bench_max_drawdown": _max_drawdown(bench_eq),
        "n_trades": 0, "win_rate": nan, "avg_win": nan, "avg_loss": nan, "payoff": nan,
        "profit_factor": nan, "avg_bars": nan, "reasons": {},
    }
    s["calmar"] = s["cagr"] / abs(s["max_drawdown"]) if s["max_drawdown"] < 0 else nan

    if not trades.empty:
        wins, losses = trades[trades["pnl"] > 0], trades[trades["pnl"] <= 0]
        s["n_trades"] = len(trades)
        s["win_rate"] = len(wins) / len(trades)
        s["avg_win"] = float(wins["ret"].mean()) if len(wins) else nan
        s["avg_loss"] = float(losses["ret"].mean()) if len(losses) else nan
        if len(wins) and len(losses) and s["avg_loss"] != 0:
            s["payoff"] = s["avg_win"] / abs(s["avg_loss"])
        gross_loss = -losses["pnl"].sum()
        s["profit_factor"] = float(wins["pnl"].sum() / gross_loss) if gross_loss > 0 else nan
        s["avg_bars"] = float(trades["bars_held"].mean())
        # Ước lượng phí + trượt giá: mỗi chiều (fee + slippage) trên giá trị lệnh.
        s["total_costs"] = float((trades["cost"] * 2 * (cfg["fee_pct"] + cfg["slippage_pct"])).sum())
        s["net_pnl"] = float(trades["pnl"].sum())
        s["reasons"] = trades["reason"].value_counts().to_dict()
        s["best"] = trades.nlargest(5, "ret")[["symbol", "entry_date", "ret"]]

    bl = bench_label(cfg)
    yearly = pd.DataFrame({
        "Chiến lược": equity.resample("YE").last().pct_change(),
        bl: bench_eq.resample("YE").last().pct_change(),
    })
    first_year = yearly.index[0]
    yearly.loc[first_year, "Chiến lược"] = equity.resample("YE").last().iloc[0] / start_cap - 1
    yearly.loc[first_year, bl] = bench_eq.resample("YE").last().iloc[0] / start_cap - 1
    yearly.index = yearly.index.year
    s["yearly"] = yearly
    return s


def bench_label(cfg):
    return "Giữ " + cfg["benchmark"].split("/")[0]


def _pct(v):
    return "-" if pd.isna(v) else f"{v:.1%}"


def _num(v):
    return "-" if pd.isna(v) else f"{v:.2f}"


def print_report(eq, trades, bench_close, cfg):
    s = summarize(eq, trades, bench_close, cfg)
    if not s:
        print("Không có dữ liệu trong giai đoạn đã chọn.")
        return s
    print(f"\n=== [{cfg['market']}] Kết quả {s['start']} → {s['end']} ===")
    print(f"Vốn cuối            {s['final_equity']:,.0f} (bắt đầu {cfg['initial_capital']:,.0f})")
    print(f"Lợi nhuận           {_pct(s['total_return'])}   | {bench_label(cfg)}: {_pct(s['bench_return'])}")
    print(f"CAGR                {_pct(s['cagr'])}   | {bench_label(cfg)}: {_pct(s['bench_cagr'])}")
    print(f"Sụt giảm lớn nhất   {_pct(s['max_drawdown'])}   | {bench_label(cfg)}: {_pct(s['bench_max_drawdown'])}")
    print(f"Sharpe / Calmar     {_num(s['sharpe'])} / {_num(s['calmar'])}")
    print(f"Thời gian có lệnh   {_pct(s['exposure'])}, trung bình {s['avg_positions']:.1f} mã")
    print(f"\nSố lệnh             {s['n_trades']}")
    print(f"Tỷ lệ thắng         {_pct(s['win_rate'])}")
    print(f"Lời TB / Lỗ TB      {_pct(s['avg_win'])} / {_pct(s['avg_loss'])}  → tỷ lệ {_num(s['payoff'])}")
    print(f"Profit factor       {_num(s['profit_factor'])}")
    print(f"Giữ lệnh TB         {_num(s['avg_bars'])} ngày")
    if "total_costs" in s:
        print(f"Lời/lỗ ròng         {s['net_pnl']:,.0f} | phí + trượt giá đã trả ~{s['total_costs']:,.0f}"
              f" → trước chi phí ~{s['net_pnl'] + s['total_costs']:,.0f}")
    if s["reasons"]:
        print("Lý do thoát         " + ", ".join(f"{k}: {v}" for k, v in s["reasons"].items()))

    print("\nTheo năm:")
    print(s["yearly"].apply(lambda c: c.map(_pct)).to_string())

    print("\nKiểm tra (README mục 6):")
    beat = s["total_return"] > s["bench_return"]
    print(f"  [{'x' if beat else ' '}] Thắng {bench_label(cfg)} sau phí")
    print(f"  [{'x' if s['max_drawdown'] > -0.25 else ' '}] Sụt giảm lớn nhất dưới 25%")
    print(f"  [{'x' if (s['payoff'] or 0) >= 3 else ' '}] Lời TB / Lỗ TB ≥ 3")
    return s


def save_quantstats(eq, bench_close, path, cfg):
    try:
        import quantstats as qs
    except ImportError:
        print("Chưa cài quantstats: pip install quantstats")
        return
    rets = eq["equity"].pct_change().dropna()
    bench = bench_close.reindex(eq.index).pct_change().dropna()
    qs.reports.html(rets, benchmark=bench, output=path, periods_per_year=cfg.get("periods_per_year", 365),
                    title=f"Shotgun Momentum - {cfg['market']}")
    print(f"Đã lưu báo cáo quantstats: {path}")
