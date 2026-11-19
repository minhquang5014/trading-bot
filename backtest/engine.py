"""Engine mô phỏng nhiều mã cùng lúc, chạy theo từng ngày.

Thứ tự trong một ngày t:
  1. Mở cửa: khớp lệnh bán chờ (từ tín hiệu đóng cửa t-1), rồi lệnh mua chờ.
  2. Trong phiên: kiểm tra cắt lỗ cứng / trailing (mở cửa nhảy qua stop thì khớp ở giá mở cửa).
  3. Đóng cửa: cập nhật trailing, xét các luật thoát theo giá đóng cửa, chấm điểm và đặt lệnh mua cho ngày t+1.

Tín hiệu ngày t chỉ dùng dữ liệu tới hết ngày t, lệnh khớp ở giá mở cửa t+1, nên không nhìn trước tương lai.

    python backtest/engine.py                          # thị trường mặc định (crypto)
    python backtest/engine.py --market us_stocks
    python backtest/engine.py --start 2021-01-01 --end 2021-12-31
    python backtest/engine.py --experiments            # chạy các bài kiểm tra trong README mục 6
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.storage import load_all  # noqa: E402
from strategy import load_config  # noqa: E402
from strategy.exits import Position, check_stop, check_take_profit, close_exit_reason, update_trailing  # noqa: E402
from strategy.regime import compute_regime  # noqa: E402
from strategy.scanner import build_panels, compute_signals, rank_candidates  # noqa: E402
from strategy.sizing import position_notional  # noqa: E402


class Engine:
    def __init__(self, cfg: dict, frames: dict):
        self.cfg = cfg
        bench_sym = cfg["benchmark"]
        if bench_sym not in frames:
            raise SystemExit(f"Không có dữ liệu {bench_sym}. Chạy: python data/fetch.py --market {cfg['market']}")
        bench = frames[bench_sym]
        alts = {s: df for s, df in frames.items() if s != bench_sym}
        if not alts:
            raise SystemExit("Không có dữ liệu altcoin nào trong " + cfg["data_dir"])

        self.calendar = bench.index
        self.p = build_panels(alts, index=self.calendar)
        self.sig = compute_signals(self.p, cfg, bench["close"])
        self.regime = compute_regime(bench["close"], cfg)
        self.last_valid = self.p["close"].apply(lambda s: s.last_valid_index()).to_dict()
        self.reset(cfg["initial_capital"])

    # ---------- trạng thái ----------
    def reset(self, capital: float):
        self.cash = float(capital)
        self.positions: dict[str, Position] = {}
        self.pending_entries: list[tuple[str, float, float]] = []   # (mã, tiền, % cắt lỗ)
        self.pending_exits: dict[str, str] = {}
        self.trades: list[dict] = []
        self.equity_curve: list[dict] = []
        self.last_loss: dict[str, str] = {}   # mã -> ngày thoát lỗ gần nhất (cho cooldown)
        self.stop_dates: list[str] = []       # ngày các lệnh dính cắt lỗ (cho stoploss_guard)
        self.paused_until: str | None = None  # ngừng mở lệnh mới tới hết ngày này

    def to_state(self) -> dict:
        return {
            "cash": self.cash,
            "positions": {s: p.to_dict() for s, p in self.positions.items()},
            "pending_entries": self.pending_entries,
            "pending_exits": self.pending_exits,
            "last_loss": self.last_loss,
            "stop_dates": self.stop_dates,
            "paused_until": self.paused_until,
        }

    def load_state(self, state: dict):
        self.cash = state["cash"]
        self.positions = {s: Position(**p) for s, p in state["positions"].items()}
        sl = self.cfg["stop_loss_pct"]
        self.pending_entries = [tuple(x) if len(x) == 3 else (x[0], x[1], sl) for x in state["pending_entries"]]
        self.pending_exits = dict(state["pending_exits"])
        self.last_loss = dict(state.get("last_loss", {}))
        self.stop_dates = list(state.get("stop_dates", []))
        self.paused_until = state.get("paused_until")

    # ---------- khớp lệnh ----------
    def _buy(self, sym: str, raw_price: float, notional: float, date, stop_pct: float = None):
        cfg = self.cfg
        fill = raw_price * (1 + cfg["slippage_pct"])
        qty = notional / (fill * (1 + cfg["fee_pct"]))
        self.cash -= notional
        self.positions[sym] = Position(
            symbol=sym, entry_date=str(pd.Timestamp(date).date()), entry_price=fill, qty=qty,
            cost=notional, stop=fill * (1 - stop_pct), highest=fill, last_price=raw_price, risk_pct=stop_pct,
        )

    def _sell(self, sym: str, raw_price: float, date, reason: str):
        cfg = self.cfg
        pos = self.positions.pop(sym)
        self.pending_exits.pop(sym, None)
        fill = raw_price * (1 - cfg["slippage_pct"])
        proceeds = pos.qty * fill * (1 - cfg["fee_pct"])
        self.cash += proceeds
        pnl = proceeds - pos.cost
        self.trades.append({
            "symbol": sym, "entry_date": pos.entry_date, "exit_date": str(pd.Timestamp(date).date()),
            "entry_price": pos.entry_price, "exit_price": fill, "cost": pos.cost, "pnl": pnl,
            "ret": pnl / pos.cost, "bars_held": pos.bars_held, "reason": reason, "risk_pct": pos.r_pct(cfg),
        })
        if pnl <= 0:
            self.last_loss[sym] = str(pd.Timestamp(date).date())
        if reason == "stop_loss":
            self.stop_dates.append(str(pd.Timestamp(date).date()))
            self.stop_dates = self.stop_dates[-50:]

    # ---------- một ngày ----------
    def step(self, date):
        self.on_open(date)
        self.on_close(date)

    def on_open(self, date):
        P, cfg = self.p, self.cfg

        # Mã đã hết dữ liệu (bị gỡ khỏi sàn): thoát ở giá cuối cùng biết được.
        for sym, pos in list(self.positions.items()):
            lv = self.last_valid.get(sym)
            if lv is None or pd.isna(lv) or date > lv:
                self._sell(sym, pos.last_price, date, "delisted")

        for sym, reason in list(self.pending_exits.items()):
            if sym not in self.positions:
                self.pending_exits.pop(sym)
                continue
            o = P["open"].at[date, sym]
            if not pd.isna(o):
                self._sell(sym, o, date, reason)

        entries, self.pending_entries = self.pending_entries, []
        for sym, notional, stop_pct in entries:
            if sym in self.positions or len(self.positions) >= cfg["max_positions"]:
                continue
            o = P["open"].at[date, sym] if sym in P["open"].columns else float("nan")
            notional = min(notional, self.cash)
            if pd.isna(o) or notional < cfg.get("min_order_usd", 10):
                continue
            self._buy(sym, o, notional, date, stop_pct)

        for sym, pos in list(self.positions.items()):
            o, low, high = P["open"].at[date, sym], P["low"].at[date, sym], P["high"].at[date, sym]
            if pd.isna(o) or pd.isna(low):
                continue
            # Cùng phiên chạm cả cắt lỗ lẫn chốt lời: giả định chạm cắt lỗ trước (bảo thủ).
            px = check_stop(pos, o, low)
            if px is not None:
                reason = "trailing_stop" if pos.trailing else "breakeven" if pos.breakeven else "stop_loss"
                self._sell(sym, px, date, reason)
                continue
            px = check_take_profit(pos, o, high, cfg)
            if px is not None:
                self._sell(sym, px, date, "take_profit")

    def on_close(self, date):
        P, cfg = self.p, self.cfg
        reg = self.regime.loc[date]
        market_ok = bool(reg["above_long"]) or not cfg.get("use_regime_long", True)

        for sym, pos in self.positions.items():
            c, h = P["close"].at[date, sym], P["high"].at[date, sym]
            if pd.isna(c):
                continue
            pos.last_price = float(c)
            pos.bars_held += 1
            update_trailing(pos, float(h), cfg)
            reason = close_exit_reason(pos, c, self.sig["sma_exit"].at[date, sym], market_ok, cfg)
            if reason and sym not in self.pending_exits:
                self.pending_exits[sym] = reason

        equity = self.cash + sum(p.qty * p.last_price for p in self.positions.values())
        self.equity_curve.append({"date": date, "equity": equity, "cash": self.cash,
                                  "n_positions": len(self.positions)})

        self.pending_entries = []
        if bool(reg["ok"]) and not self._paused(date):
            slots = cfg["max_positions"] - len(self.positions) + len(self.pending_exits)
            if slots > 0:
                cands = rank_candidates(self.sig, date, exclude=set(self.positions) | self._cooling(date))[:slots]
                for s in cands:
                    sp = self._stop_pct(date, s)
                    self.pending_entries.append((s, position_notional(equity, sp, cfg), sp))

    def _stop_pct(self, date, sym) -> float:
        """Cắt lỗ cố định stop_loss_pct, hoặc stop_atr × ATR / giá (giới hạn trong stop_atr_range)."""
        cfg = self.cfg
        k = cfg.get("stop_atr")
        if not k:
            return cfg["stop_loss_pct"]
        a, c = self.sig["atr"].at[date, sym], self.p["close"].at[date, sym]
        if pd.isna(a) or pd.isna(c) or c <= 0:
            return cfg["stop_loss_pct"]
        lo, hi = cfg.get("stop_atr_range") or (0.02, 0.25)
        return float(min(max(float(k) * a / c, lo), hi))

    def _paused(self, date) -> bool:
        """StoplossGuard và MaxDrawdown (freqtrade): tạm ngừng mở lệnh mới sau chuỗi thua."""
        cfg, d = self.cfg, pd.Timestamp(date)
        if self.paused_until and d <= pd.Timestamp(self.paused_until):
            return True
        pause = None
        g = cfg.get("stoploss_guard")      # {count, days, pause_days}
        if g:
            lim = d - pd.Timedelta(days=int(g["days"]))
            if sum(pd.Timestamp(x) > lim for x in self.stop_dates) >= int(g["count"]):
                pause = int(g["pause_days"])
                self.stop_dates = []       # đếm lại từ đầu sau khi nghỉ
        g = cfg.get("drawdown_guard")      # {pct, days, pause_days}
        if g and self.equity_curve:
            n = int(g["days"])
            recent = [r["equity"] for r in self.equity_curve[-n:]]
            if recent[-1] / max(recent) - 1 <= -float(g["pct"]):
                pause = max(pause or 0, int(g["pause_days"]))
        if pause:
            self.paused_until = str((d + pd.Timedelta(days=pause)).date())
            return True
        return False

    def _cooling(self, date) -> set:
        """Mã vừa thoát lỗ trong `cooldown_days` ngày gần đây: chưa được mua lại."""
        n = (self.cfg.get("filters") or {}).get("cooldown_days") or 0
        if not n:
            return set()
        lim = pd.Timestamp(date) - pd.Timedelta(days=int(n))
        return {s for s, d in self.last_loss.items() if pd.Timestamp(d) > lim}

    # ---------- chạy cả giai đoạn ----------
    def run(self, start=None, end=None):
        dates = self.calendar
        if start:
            dates = dates[dates >= pd.Timestamp(start)]
        if end:
            dates = dates[dates <= pd.Timestamp(end)]
        for d in dates:
            self.step(d)
        # Đóng các lệnh còn mở ở giá đóng cửa cuối để thống kê đủ.
        if len(dates):
            for sym, pos in list(self.positions.items()):
                self._sell(sym, pos.last_price, dates[-1], "end_of_test")
            self.equity_curve[-1]["equity"] = self.cash
        return self.results()

    def results(self):
        eq = pd.DataFrame(self.equity_curve).set_index("date") if self.equity_curve else pd.DataFrame()
        trades = pd.DataFrame(self.trades)
        return eq, trades


def run_backtest(cfg, frames, start=None, end=None):
    eng = Engine(cfg, frames)
    eq, trades = eng.run(start, end)
    return eng, eq, trades


def run_experiments(base_cfg, frames, start, end):
    from backtest.report import bench_label, summarize

    variants = [
        ("Gốc", {}),
        ("Bỏ lọc 200 ngày", {"use_regime_long": False}),
        ("5 mã", {"max_positions": 5}),
        ("15 mã", {"max_positions": 15}),
    ]
    rows = []
    for name, ov in variants:
        cfg = {**base_cfg, **ov}
        eng, eq, trades = run_backtest(cfg, frames, start, end)
        s = summarize(eq, trades, eng.regime["close"], cfg)
        rows.append({"Biến thể": name, "Lợi nhuận": s["total_return"], "CAGR": s["cagr"],
                     "Sụt giảm max": s["max_drawdown"], "Sharpe": s["sharpe"], "Số lệnh": s["n_trades"],
                     "Tỷ lệ thắng": s["win_rate"], "Lời TB/Lỗ TB": s["payoff"]})
    df = pd.DataFrame(rows).set_index("Biến thể")
    fmt = {c: "{:.1%}" for c in ["Lợi nhuận", "CAGR", "Sụt giảm max", "Tỷ lệ thắng"]}
    fmt.update({"Sharpe": "{:.2f}", "Lời TB/Lỗ TB": "{:.2f}", "Số lệnh": "{:.0f}"})
    print("\n=== So sánh biến thể ===")
    print(df.apply(lambda col: col.map(lambda v: fmt[col.name].format(v) if pd.notna(v) else "-")).to_string())

    print("\n=== Theo giai đoạn (cấu hình gốc) ===")
    periods = [("Lên 2021", "2021-01-01", "2021-12-31"), ("Xuống 2022", "2022-01-01", "2022-12-31"),
               ("Ngang 2023", "2023-01-01", "2023-12-31")]
    prow = []
    for name, a, b in periods:
        eng, eq, trades = run_backtest(base_cfg, frames, a, b)
        if eq.empty:
            continue
        s = summarize(eq, trades, eng.regime["close"], base_cfg)
        prow.append({"Giai đoạn": name, "Chiến lược": f"{s['total_return']:.1%}",
                     bench_label(base_cfg): f"{s['bench_return']:.1%}", "Sụt giảm max": f"{s['max_drawdown']:.1%}",
                     "Số lệnh": s["n_trades"]})
    if prow:
        print(pd.DataFrame(prow).set_index("Giai đoạn").to_string())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--market", help="Tên thị trường trong config.yaml (crypto, us_stocks, ...)")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--max-positions", type=int)
    ap.add_argument("--no-regime-long", action="store_true", help="Tắt bộ lọc 200 ngày")
    ap.add_argument("--experiments", action="store_true", help="Chạy các bài kiểm tra ở README mục 6")
    ap.add_argument("--html", help="Xuất báo cáo quantstats ra file HTML (cần cài quantstats)")
    args = ap.parse_args()

    cfg = load_config(args.config, market=args.market, max_positions=args.max_positions,
                      use_regime_long=False if args.no_regime_long else None)
    frames = load_all(cfg["data_dir"])
    print(f"[{cfg['market']}] Đã nạp {len(frames)} mã từ {cfg['data_dir']}")

    if args.experiments:
        run_experiments(cfg, frames, args.start, args.end)
        return

    from backtest.report import print_report, save_quantstats

    eng, eq, trades = run_backtest(cfg, frames, args.start, args.end)
    out = ROOT / "backtest" / "results" / cfg["market"]
    out.mkdir(parents=True, exist_ok=True)
    eq.to_csv(out / "equity.csv")
    trades.to_csv(out / "trades.csv", index=False)
    print_report(eq, trades, eng.regime["close"], cfg)
    print(f"\nĐã lưu {out / 'equity.csv'} và {out / 'trades.csv'}")
    if args.html:
        save_quantstats(eq, eng.regime["close"], args.html, cfg)


if __name__ == "__main__":
    main()
