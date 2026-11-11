"""Bước 4: các luật thoát lệnh (cắt nhanh, để lời chạy)."""
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class Position:
    symbol: str
    entry_date: str
    entry_price: float   # giá khớp, đã tính trượt giá
    qty: float
    cost: float          # tổng tiền bỏ ra, đã gồm phí
    stop: float
    highest: float
    last_price: float
    bars_held: int = 0
    trailing: bool = False

    def to_dict(self):
        return asdict(self)


def check_stop(pos: Position, open_: float, low: float):
    """Giá khớp nếu chạm cắt lỗ trong phiên. Mở cửa đã nhảy qua điểm cắt lỗ thì khớp ở giá mở cửa."""
    if open_ <= pos.stop:
        return open_
    if low <= pos.stop:
        return pos.stop
    return None


def update_trailing(pos: Position, high: float, cfg: dict) -> None:
    """Gọi lúc đóng cửa. Điểm cắt lỗ mới chỉ có hiệu lực từ phiên sau."""
    pos.highest = max(pos.highest, high)
    if not pos.trailing and pos.highest >= pos.entry_price * (1 + cfg["trail_trigger_pct"]):
        pos.trailing = True
    if pos.trailing:
        pos.stop = max(pos.stop, pos.entry_price, pos.highest * (1 - cfg["trail_pct"]))


def close_exit_reason(pos: Position, close: float, sma_fast: float, market_ok: bool, cfg: dict):
    """Các luật thoát xét ở giá đóng cửa; lệnh bán khớp ở giá mở cửa phiên sau."""
    if not market_ok:
        return "regime"
    if pos.bars_held >= cfg["time_stop_days"] and close / pos.entry_price - 1 < cfg["time_stop_min_gain"]:
        return "time_stop"
    if not pd.isna(sma_fast) and close < sma_fast:
        return "below_fast_ma"
    return None
