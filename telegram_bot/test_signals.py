"""Kiểm tra offline: tín hiệu live (signals.pdh_pdl_signal, chỉ thấy 1000 nến M5 gần nhất) có báo đúng
những nến mà logic backtest PdhPdl (chạy trên toàn bộ lịch sử) cho tín hiệu không. Không cần MT5.

    .venv\\Scripts\\python telegram_bot\\test_signals.py              # năm 2025
    .venv\\Scripts\\python telegram_bot\\test_signals.py --start 2024-01-01 --end 2026-12-31

Cần dữ liệu PAXG M5: python clone_repo/fetch_fx.py binance5m
"""
import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "clone_repo"))

from gold_repos import load_gold  # noqa: E402
from gold_repos_m5 import PdhPdl  # noqa: E402
from telegram_bot.signals import M5_BARS, _StubEngine, pdh_pdl_signal  # noqa: E402

WINDOW = M5_BARS   # bot lấy chừng này nến M5 mỗi lần kiểm tra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    m5 = load_gold("5m")
    m5 = m5[(m5.index >= np.datetime64(args.start) - np.timedelta64(60, "D")) & (m5.index <= args.end)]
    first = int(np.searchsorted(m5.index.values, np.datetime64(args.start)))

    # chuẩn: backtest trên cả chuỗi, không có lệnh mở chặn (bot live cũng báo cả khi đang có lệnh)
    s, eng = PdhPdl(m5), _StubEngine()
    s.reset(eng, 0)
    expected = []
    for i in range(4, len(m5)):
        if s.on_bar(eng, i) and i >= first:
            expected.append(i)
    exp_set = set(expected)

    # live: tại mỗi nến chỉ nhìn WINDOW nến gần nhất
    missed = [i for i in expected if pdh_pdl_signal(m5.iloc[i - WINDOW + 1:i + 1]) is None]
    # báo nhầm: nến ngay sau mỗi tín hiệu và một mẫu nến ngẫu nhiên không có tín hiệu
    rng = np.random.default_rng(0)
    probe = {i + 1 for i in expected if i + 1 < len(m5)} | set(rng.integers(max(first, WINDOW), len(m5), 3000).tolist())
    probe -= exp_set
    false = [i for i in sorted(probe) if pdh_pdl_signal(m5.iloc[i - WINDOW + 1:i + 1]) is not None]

    print(f"{args.start} → {args.end}: backtest có {len(expected)} nến tín hiệu")
    print(f"  live bỏ sót: {len(missed)}  {[str(m5.index[i]) for i in missed[:5]]}")
    print(f"  live báo nhầm: {len(false)}/{len(probe)} nến thử  {[str(m5.index[i]) for i in false[:5]]}")
    if expected:
        sig = pdh_pdl_signal(m5.iloc[expected[-1] - WINDOW + 1:expected[-1] + 1])
        sl, tp1, tp2 = sig.levels(sig.ref_price)
        print(f"  ví dụ {sig.bar_time}: {'MUA' if sig.side > 0 else 'BÁN'} @ {sig.ref_price:.2f} · SL {sl:.2f} · "
              f"TP1 {tp1:.2f} · TP2 {tp2:.2f} · {sig.note}")
    sys.exit(1 if missed or false else 0)


if __name__ == "__main__":
    main()
