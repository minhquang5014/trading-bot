"""Bước 3: chia vốn. Không đòn bẩy, mỗi lệnh lỗ tối đa RISK_PER_TRADE tổng vốn."""


def position_notional(equity: float, stop_distance_pct: float, cfg: dict) -> float:
    by_risk = equity * cfg["risk_per_trade"] / stop_distance_pct
    # Trần mỗi lệnh: position_pct nếu có, mặc định 1 / max_positions.
    cap = equity * (cfg.get("position_pct") or 1 / cfg["max_positions"])
    return min(by_risk, cap)
