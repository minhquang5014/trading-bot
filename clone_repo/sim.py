"""Mô phỏng danh mục chung cho mọi chiến lược, để so sánh công bằng.

Luật chung (giống max_open_trades + stake "unlimited" của freqtrade):
- Tối đa `max_open` lệnh cùng lúc, mỗi lệnh = vốn / max_open (không vượt tiền mặt). Spot, không đòn bẩy.
- Chỉ mua mã đủ thanh khoản: khối lượng tiền trung bình 20 phiên ≥ min_volume_usd của thị trường.
  Nhiều tín hiệu hơn số chỗ trống thì ưu tiên mã thanh khoản cao nhất.
- Tín hiệu ở giá đóng cửa ngày t, khớp ở giá mở cửa t+1. Phí + trượt giá lấy từ config.yaml.
- Trong một phiên: mở cửa đã vượt stop / chốt lời thì khớp ở giá mở cửa; nếu cùng phiên chạm cả stop lẫn
  chốt lời thì giả định chạm stop trước (bảo thủ). Trailing chỉ cập nhật ở giá đóng cửa, có hiệu lực phiên sau.
"""
import numpy as np
import pandas as pd


def _roi_threshold(roi: dict, minutes: float):
    keys = sorted(k for k in roi if k <= minutes)
    return roi[keys[-1]] if keys else None


def simulate(spec, P, cfg, capital=1000.0, max_open=10):
    dates = P["close"].index
    syms = list(P["close"].columns)
    O, H, L, C = (P[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    liq = ((P["close"] * P["volume"]).rolling(20).mean() >= cfg["min_volume_usd"]).to_numpy()
    entry = spec.entry.reindex(index=dates, columns=syms).fillna(False).to_numpy(bool) & liq
    exit_ = None if spec.exit is None else spec.exit.reindex(index=dates, columns=syms).fillna(False).to_numpy(bool)
    rank = (P["close"] * P["volume"]).rolling(20).mean().to_numpy()

    def arr(df):
        return None if df is None else df.reindex(index=dates, columns=syms).to_numpy(float)

    ATR, TRIG, LEVEL = arr(spec.atr), arr(spec.entry_trigger), arr(spec.stop_level)
    last_valid = np.array([np.max(np.nonzero(~np.isnan(C[:, j]))[0]) if (~np.isnan(C[:, j])).any() else -1
                           for j in range(len(syms))])
    fee, slip = cfg["fee_pct"], cfg["slippage_pct"]

    cash, equity = float(capital), float(capital)
    pos, pend_exit, pend_entry = {}, {}, []
    trades, curve = [], []

    def sell(j, raw, i, why):
        nonlocal cash
        p = pos.pop(j)
        pend_exit.pop(j, None)
        fill = raw * (1 - slip)
        proceeds = p["qty"] * fill * (1 - fee)
        cash += proceeds
        trades.append({"symbol": syms[j], "entry_date": str(dates[p["i"]].date()), "exit_date": str(dates[i].date()),
                       "entry_price": p["fill"], "exit_price": fill, "cost": p["cost"], "pnl": proceeds - p["cost"],
                       "ret": proceeds / p["cost"] - 1, "bars_held": p["bars"], "reason": why,
                       "init_stop_pct": p["init_stop_pct"]})

    for i in range(len(dates)):
        # ---- mở cửa ----
        for j in list(pos):
            if i > last_valid[j]:
                sell(j, pos[j]["last"], i, "delisted")
        for j in list(pend_exit):
            if j in pos and not np.isnan(O[i, j]):
                sell(j, O[i, j], i, "signal")
        new = set()
        for j, sig_i in pend_entry:
            if j in pos or len(pos) >= max_open or np.isnan(O[i, j]):
                continue
            raw = O[i, j]
            if TRIG is not None:
                trig = TRIG[sig_i, j]
                if np.isnan(trig) or H[i, j] < trig:
                    continue                      # lệnh chờ không khớp, huỷ
                raw = max(raw, trig)
            stake = min(equity / max_open, cash)
            if stake < 1:
                continue
            fill = raw * (1 + slip)
            stops = []
            if spec.stop_pct:
                stops.append(fill * (1 - spec.stop_pct))
            if spec.stop_atr and ATR is not None:
                stops.append(fill - spec.stop_atr * ATR[sig_i, j])
            if LEVEL is not None:
                stops.append(LEVEL[sig_i, j])
            stops = [s for s in stops if not np.isnan(s)]
            stop = max(stops) if stops else -np.inf
            if stop >= fill:
                continue                          # cắt lỗ nằm trên giá vào: bỏ lệnh
            risk = fill - stop if np.isfinite(stop) else None
            tp = np.inf
            if spec.tp_pct:
                tp = min(tp, fill * (1 + spec.tp_pct))
            if spec.tp_atr and ATR is not None:
                tp = min(tp, fill + spec.tp_atr * ATR[sig_i, j])
            if spec.tp_r and risk:
                tp = min(tp, fill + spec.tp_r * risk)
            cash -= stake
            pos[j] = {"i": i, "fill": fill, "qty": stake / (fill * (1 + fee)), "cost": stake, "stop": stop,
                      "tp": tp, "high": fill, "bars": 0, "last": raw,
                      "atr": ATR[sig_i, j] if ATR is not None else np.nan,
                      "init_stop_pct": (1 - stop / fill) if np.isfinite(stop) else np.nan}
            new.add(j)
        pend_entry = []

        # ---- trong phiên: cắt lỗ / chốt lời ----
        for j in list(pos):
            p = pos[j]
            o, h, l = O[i, j], H[i, j], L[i, j]
            if np.isnan(o) or np.isnan(l):
                continue
            target, why_t = p["tp"], "take_profit"
            if spec.roi:
                thr = _roi_threshold(spec.roi, p["bars"] * 1440)
                if thr is not None:
                    px = p["fill"] * (1 + fee) * (1 + thr) / (1 - fee)
                    if px < target:
                        target, why_t = px, "roi"
            why_s = "trailing_stop" if p.get("trailing") else "stop_loss"
            if j not in new and o <= p["stop"]:
                sell(j, o, i, why_s)
            elif j not in new and o >= target:
                sell(j, o, i, why_t)
            elif l <= p["stop"]:
                sell(j, p["stop"], i, why_s)
            elif h >= target:
                sell(j, target, i, why_t)

        # ---- đóng cửa ----
        for j, p in pos.items():
            c, h = C[i, j], H[i, j]
            if np.isnan(c):
                continue
            p["last"] = c
            p["bars"] += 1
            p["high"] = max(p["high"], h)
            if spec.trail_pct and p["high"] / p["fill"] - 1 >= spec.trail_offset:
                ns = p["high"] * (1 - spec.trail_pct)
                if ns > p["stop"]:
                    p["stop"], p["trailing"] = ns, True
            if spec.trail_atr_dist and not np.isnan(p["atr"]) and p["high"] - p["fill"] >= spec.trail_atr_start * p["atr"]:
                ns = p["high"] - spec.trail_atr_dist * p["atr"]
                if ns > p["stop"]:
                    p["stop"], p["trailing"] = ns, True
            if exit_ is not None and exit_[i, j]:
                pend_exit[j] = True

        equity = cash + sum(p["qty"] * p["last"] for p in pos.values())
        curve.append({"date": dates[i], "equity": equity, "cash": cash, "n_positions": len(pos)})

        slots = max_open - len(pos) + len(pend_exit)
        if slots > 0:
            cand = np.nonzero(entry[i])[0]
            cand = [j for j in cand if j not in pos]
            cand.sort(key=lambda j: -(rank[i, j] if not np.isnan(rank[i, j]) else 0))
            pend_entry = [(j, i) for j in cand[:slots]]

    for j in list(pos):
        sell(j, pos[j]["last"], len(dates) - 1, "end_of_test")
    if curve:
        curve[-1]["equity"] = cash
    eq = pd.DataFrame(curve).set_index("date")
    return eq, pd.DataFrame(trades)
