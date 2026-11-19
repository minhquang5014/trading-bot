"""Tín hiệu PDH/PDL (P1Piyush/XAUUSD-Trading-Bots) chạy trên nến M5 thật của broker.

Dùng lại đúng lớp `PdhPdl` đã backtest trong clone_repo/gold_repos_m5.py (spread $0,30: +4,5%, PF 1,21,
37 lệnh / 6 năm — rất ít lệnh, chưa đủ để tin; xem clone_repo/README.md). Ở đây chỉ phát hiện tín hiệu ở
nến M5 vừa đóng và trả về giá vào / SL / TP để bot gửi cảnh báo; đặt lệnh hay không là do người dùng bấm nút.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clone_repo"))

from gold_repos_m5 import PdhPdl  # noqa: E402

# Số nến M5 bot lấy mỗi lần: EMA200 khung H1 cần ~600 nến H1 (= 7200 nến M5) mới hội tụ giống backtest.
# Với 1000 nến, xu hướng H1 có thể sai và bỏ sót tín hiệu (telegram_bot/test_signals.py).
M5_BARS = 10000


class _StubEngine:
    """Đủ thuộc tính cho PdhPdl.on_bar: không có lệnh mở, vốn không đổi (bỏ luật dừng khi lỗ 3%/ngày)."""
    pos, market = [], []
    capital = balance = 1.0

    def equity(self, i):
        return 1.0


@dataclass
class Signal:
    strategy: str
    side: int            # 1 mua, -1 bán
    bar_time: object     # giờ UTC của nến M5 vừa đóng
    ref_price: float     # giá đóng cửa nến tín hiệu
    sl_dist: float
    tp1_r: float
    tp2_dist: float
    note: str

    def levels(self, entry):
        sl = entry - self.side * self.sl_dist
        tp1 = entry + self.side * self.sl_dist * self.tp1_r
        tp2 = entry + self.side * self.tp2_dist
        return sl, tp1, tp2


def pdh_pdl_signal(m5):
    """m5: nến M5 đã đóng (index UTC), nên dài M5_BARS nến. Trả về Signal nếu nến cuối cùng cho tín hiệu."""
    if len(m5) < 600:
        return None
    strat = PdhPdl(m5)
    eng = _StubEngine()
    strat.reset(eng, 0)
    # chạy lại từ đầu ngày UTC hiện tại để cờ "mức đã chạm" (tapped) đúng như backtest
    day = m5.index[-1].normalize()
    i0 = max(4, int(np.searchsorted(m5.index.values, day.to_datetime64())))
    orders = []
    for i in range(i0, len(m5)):
        orders = strat.on_bar(eng, i)
    if not orders:
        return None
    od = orders[0]
    i = len(m5) - 1
    lvl = strat.pdh[i] if od.side > 0 else strat.pdl[i]
    return Signal("PDH/PDL", od.side, m5.index[-1], float(m5["close"].iloc[-1]), float(od.sl_dist),
                  float(od.tp1_r), float(od.tp_dist),
                  f"{'Phá đỉnh' if od.side > 0 else 'Phá đáy'} hôm qua ({lvl:.2f}) rồi retest")


def prev_day_levels(m5):
    """PDH / PDL theo ngày server (New York + 7), giống backtest."""
    strat = PdhPdl(m5)
    return float(strat.pdh[-1]), float(strat.pdl[-1])
