"""Chạy giấy: dùng đúng engine của backtest, lưu trạng thái giữa các lần chạy, không đặt lệnh thật.

Chạy mỗi ngày một lần sau 00:00 UTC (nến ngày của Binance đóng lúc đó):

    python live/paper.py                      # crypto: tải nến mới, xử lý các ngày chưa chạy, in lệnh phiên tới
    python live/paper.py --market us_stocks   # cổ phiếu Mỹ: chạy sau khi sàn Mỹ đóng cửa
    python live/paper.py --no-fetch   # dùng dữ liệu đã có
    python live/paper.py --reset      # xoá trạng thái, bắt đầu lại với vốn trong config

Lệnh chờ được tính ở giá đóng cửa ngày gần nhất, và sẽ "khớp" ở giá mở cửa của nến kế tiếp
trong lần chạy sau (giống backtest).
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.engine import Engine  # noqa: E402
from data.fetch import update_all  # noqa: E402
from data.storage import load_all  # noqa: E402
from strategy import load_config  # noqa: E402

LIVE_DIR = ROOT / "live"


def append_csv(path: Path, rows: list):
    if not rows:
        return
    df = pd.DataFrame(rows)
    df.to_csv(path, mode="a", header=not path.exists(), index=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--market", help="Tên thị trường trong config.yaml")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config, market=args.market)
    m = cfg["market"]
    STATE_FILE = LIVE_DIR / f"state_{m}.json"
    TRADES_LOG = LIVE_DIR / f"paper_trades_{m}.csv"
    EQUITY_LOG = LIVE_DIR / f"paper_equity_{m}.csv"
    ccy = cfg.get("quote_ccy", cfg.get("quote", ""))
    if args.reset:
        for p in (STATE_FILE, TRADES_LOG, EQUITY_LOG):
            p.unlink(missing_ok=True)
        print(f"Đã xoá trạng thái chạy giấy [{m}].")

    if not args.no_fetch:
        print(f"[{m}] Đang cập nhật dữ liệu...")
        update_all(cfg, include_inactive=False, verbose=False)

    eng = Engine(cfg, load_all(cfg["data_dir"]))
    last_bar = eng.calendar[-1]

    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        eng.load_state(state)
        last_done = pd.Timestamp(state["last_date"])
        new_dates = eng.calendar[eng.calendar > last_done]
        for d in new_dates:
            eng.step(d)
        print(f"Đã xử lý {len(new_dates)} ngày mới ({last_done.date()} → {last_bar.date()}).")
    else:
        # Lần đầu: không giao dịch quá khứ, chỉ chấm điểm ngày gần nhất để có lệnh cho phiên tới.
        eng.reset(cfg["initial_capital"])
        eng.on_close(last_bar)
        print(f"Khởi tạo chạy giấy với vốn {cfg['initial_capital']:,.0f} {ccy} tại {last_bar.date()}.")

    append_csv(TRADES_LOG, eng.trades)
    append_csv(EQUITY_LOG, [{**r, "date": pd.Timestamp(r["date"]).date()} for r in eng.equity_curve])
    STATE_FILE.write_text(json.dumps({**eng.to_state(), "last_date": str(last_bar.date())}, indent=2))

    reg = eng.regime.loc[last_bar]
    equity = eng.equity_curve[-1]["equity"] if eng.equity_curve else eng.cash
    print(f"\n[{m}] Thị trường {last_bar.date()}: {cfg['benchmark']} trên MA{cfg['regime_long_ma']}={bool(reg['above_long'])}, "
          f"MA{cfg['regime_fast_ma']}>MA{cfg['regime_slow_ma']}={bool(reg['fast_above_slow'])} "
          f"→ {'ĐƯỢC mở lệnh' if reg['ok'] else 'KHÔNG mở lệnh mới'}")
    print(f"Vốn: {equity:,.2f} | Tiền mặt: {eng.cash:,.2f} | Đang giữ: {len(eng.positions)} mã")

    for t in eng.trades:
        print(f"  ĐÃ BÁN {t['symbol']:<14} {t['ret']:+.1%}  ({t['reason']})")
    for sym, pos in eng.positions.items():
        gain = pos.last_price / pos.entry_price - 1
        print(f"  GIỮ    {sym:<14} vào {pos.entry_date} @ {pos.entry_price:.6g}, hiện {gain:+.1%}, "
              f"stop {pos.stop:.6g}{' (trailing)' if pos.trailing else ''}")

    print("\nLệnh cho phiên mở cửa tới:")
    if not eng.pending_exits and not eng.pending_entries:
        print("  (không có)")
    for sym, reason in eng.pending_exits.items():
        print(f"  BÁN {sym:<14} lý do: {reason}")
    for sym, notional in eng.pending_entries:
        print(f"  MUA {sym:<14} ~{notional:,.2f} {ccy}, cắt lỗ -{cfg['stop_loss_pct']:.0%}")


if __name__ == "__main__":
    main()
