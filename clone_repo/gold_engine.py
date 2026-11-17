"""Engine mô phỏng EA vàng kiểu MetaTrader trên nến (dùng chung cho gold_repos.py).

Giả định (giống xau_ea.py):
- Giá nến là giá bid. Mua khớp ở ask = bid + spread, bán khớp ở bid. Lệnh mua cắt lỗ / chốt lời khi bid chạm,
  lệnh bán khi ask chạm (high + spread ≥ SL).
- 1 lot = 100 oz, lot nhỏ nhất 0,01 (1 oz), bước 0,01. Đòn bẩy 1:100: không đủ ký quỹ thì lệnh bị từ chối.
- Trong cùng một nến: cắt lỗ được xét trước chốt lời (bảo thủ). Mở cửa nhảy qua SL/TP thì khớp ở giá mở cửa.
- Lệnh thị trường đặt lúc đóng cửa nến i khớp ở giá mở cửa nến i+1. Lệnh limit khớp khi giá chạm.
- Dời SL (hoà vốn, trailing) quyết định ở giá đóng cửa, có hiệu lực từ nến sau.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

OZ_PER_LOT = 100.0


@dataclass
class Pos:
    side: int            # 1 mua, -1 bán
    qty: float           # oz
    entry: float
    sl: float
    tp: float
    open_i: int
    tag: object = None
    risk: float = 0.0    # khoảng cách SL ban đầu (giá)
    tp1: float = None    # chốt một phần
    tp1_frac: float = 0.0
    be_after_tp1: bool = False
    pnl: float = 0.0     # lời/lỗ đã chốt (phần đã đóng)
    extra: dict = field(default_factory=dict)


@dataclass
class Order:
    side: int
    sl: float = None     # giá SL tuyệt đối (lệnh limit) ...
    tp: float = None
    sl_dist: float = None   # ... hoặc khoảng cách tính từ giá khớp (lệnh thị trường)
    tp_dist: float = None
    kind: str = "market"    # market | limit
    price: float = None     # giá limit
    qty: float = None       # oz; None = tính theo risk_pct
    risk_pct: float = None
    tag: object = None
    tp1_r: float = None
    tp1_frac: float = 0.0
    be_after_tp1: bool = False


def lot_round(oz):
    return np.floor(oz + 1e-9)          # bước 0,01 lot = 1 oz


class Engine:
    def __init__(self, df, spread=0.30, capital=10_000.0, leverage=100, max_positions=1, sub=None):
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.t = df.index
        # sub: nến nhỏ hơn (vd M5) để biết thứ tự giá trong nến khớp lệnh limit
        self.sub = None
        if sub is not None:
            self.sub = tuple(sub[k].to_numpy(float) for k in ("open", "high", "low", "close"))
            edges = np.append(self.t.values, self.t[-1] + (self.t[-1] - self.t[-2]))
            self.sub_lo = np.searchsorted(sub.index.values, edges[:-1])
            self.sub_hi = np.searchsorted(sub.index.values, edges[1:])
        self.spread, self.capital, self.lev, self.maxpos = spread, capital, leverage, max_positions

    # ----- tiện ích cho chiến lược -----
    def equity(self, i):
        c = self.c[i]
        return self.balance + sum(p.qty * ((c - p.entry) if p.side > 0 else (p.entry - c - self.spread))
                                  for p in self.pos)

    def risk_qty(self, risk_pct, dist, balance=None):
        bal = self.balance if balance is None else balance
        return lot_round(bal * risk_pct / 100 / dist) if dist > 0 else 0.0

    def _close(self, p, i, px, qty, reason):
        pnl = qty * ((px - p.entry) if p.side > 0 else (p.entry - px))
        self.balance += pnl
        p.pnl += pnl
        p.qty -= qty
        if p.qty <= 1e-9:
            self.pos.remove(p)
            self.trades.append({"open": self.t[p.open_i], "close": self.t[i], "side": p.side, "entry": p.entry,
                                "exit": px, "pnl": p.pnl, "r": p.pnl / p.extra["risk_usd"] if p.extra.get("risk_usd") else np.nan,
                                "bars": i - p.open_i, "reason": reason, "tag": p.tag})
            self.strategy.on_trade_closed(self, p, i)

    def _open(self, od: Order, i, px):
        sl = od.sl if od.sl is not None else px - od.side * od.sl_dist
        tp = od.tp if od.tp is not None else (px + od.side * od.tp_dist if od.tp_dist else None)
        dist = abs(px - sl)
        qty = od.qty if od.qty is not None else self.risk_qty(od.risk_pct, dist)
        if qty < 1 or dist <= 0:
            self.rejected += 1
            return None
        used = sum(p.qty * p.entry for p in self.pos) / self.lev
        if used + qty * px / self.lev > self.equity(max(i - 1, 0)):
            self.rejected += 1                       # không đủ ký quỹ
            return None
        p = Pos(od.side, qty, px, sl, tp, i, od.tag, dist,
                tp1=px + od.side * od.tp1_r * dist if od.tp1_r else None, tp1_frac=od.tp1_frac,
                be_after_tp1=od.be_after_tp1)
        p.extra["risk_usd"] = qty * dist
        self.pos.append(p)
        self.strategy.on_trade_opened(self, p, i)
        return p

    def _resolve_sub(self, p, od, i):
        """Đi qua từng nến nhỏ của nến i: tìm lúc khớp, rồi xét SL/TP ở các nến nhỏ SAU đó (nến khớp: chỉ xét SL)."""
        so, sh, sl_, sc = self.sub
        sp = self.spread
        js = range(self.sub_lo[i], self.sub_hi[i])
        filled = None
        for j in js:
            if filled is None:
                if (p.side > 0 and sl_[j] + sp <= od.price) or (p.side < 0 and sh[j] >= od.price):
                    filled = j
                    if (p.side > 0 and sl_[j] <= p.sl) or (p.side < 0 and sh[j] + sp >= p.sl):
                        self._close(p, i, p.sl, p.qty, "sl")
                        return
                continue
            if p.side > 0:
                if sl_[j] <= p.sl:
                    self._close(p, i, min(p.sl, so[j]), p.qty, "sl"); return
                if p.tp is not None and sh[j] >= p.tp:
                    self._close(p, i, max(p.tp, so[j]), p.qty, "tp"); return
            else:
                if sh[j] + sp >= p.sl:
                    self._close(p, i, max(p.sl, so[j] + sp), p.qty, "sl"); return
                if p.tp is not None and sl_[j] + sp <= p.tp:
                    self._close(p, i, min(p.tp, so[j] + sp), p.qty, "tp"); return
        p.extra["resolved"] = True

    # ----- vòng lặp -----
    def run(self, strategy, start=None, end=None):
        self.strategy = strategy
        idx = np.arange(len(self.t))
        if start is not None:
            idx = idx[self.t >= pd.Timestamp(start)]
        if end is not None:
            idx = idx[self.t[idx] <= pd.Timestamp(end)]
        self.balance, self.pos, self.trades, self.pending, self.market = self.capital, [], [], [], []
        self.rejected, curve = 0, np.empty(len(idx))
        strategy.reset(self, idx[0] if len(idx) else 0)
        sp = self.spread
        for k, i in enumerate(idx):
            o, h, l, c = self.o[i], self.h[i], self.l[i], self.c[i]
            # 0. lệnh đóng chủ động (chiến lược đặt p.extra["exit"] lúc đóng cửa nến trước)
            for p in list(self.pos):
                if p.extra.get("exit"):
                    self._close(p, i, o if p.side > 0 else o + sp, p.qty, p.extra["exit"])
            # 1. lệnh thị trường từ nến trước
            for od in self.market:
                if len(self.pos) < self.maxpos:
                    self._open(od, i, o + sp if od.side > 0 else o)
            self.market = []
            # 2. lệnh limit chờ
            for od in list(self.pending):
                ask_l, bid_h = l + sp, h
                p = None
                if od.side > 0 and ask_l <= od.price:
                    self.pending.remove(od)
                    p = self._open(od, i, min(od.price, o + sp))
                elif od.side < 0 and bid_h >= od.price:
                    self.pending.remove(od)
                    p = self._open(od, i, max(od.price, o))
                if p is not None:
                    p.extra["limit_bar"] = i     # không biết giá chạm TP trước hay sau lúc khớp → không tính TP nến này
                    if self.sub is not None:
                        self._resolve_sub(p, od, i)
            # 3. SL / TP trong phiên
            for p in list(self.pos):
                fresh = p.extra.get("limit_bar") == i
                if fresh and p.extra.get("resolved"):
                    continue                     # đã xét bằng nến nhỏ
                if p.side > 0:
                    if l <= p.sl:
                        self._close(p, i, min(p.sl, o), p.qty, "sl" if p.sl < p.entry else "be/trail")
                        continue
                    if fresh:
                        continue
                    if p.tp1 is not None and h >= p.tp1:
                        q = lot_round(p.qty * p.tp1_frac)
                        px = max(p.tp1, o)
                        p.tp1 = None
                        if q >= 1:
                            self._close(p, i, px, q, "tp1")
                        if p in self.pos and p.be_after_tp1:
                            p.sl = max(p.sl, p.entry)
                        if p not in self.pos:
                            continue
                    if p.tp is not None and h >= p.tp:
                        self._close(p, i, max(p.tp, o), p.qty, "tp")
                else:
                    if h + sp >= p.sl:
                        self._close(p, i, max(p.sl, o + sp), p.qty, "sl" if p.sl > p.entry else "be/trail")
                        continue
                    if fresh:
                        continue
                    if p.tp1 is not None and l + sp <= p.tp1:
                        q = lot_round(p.qty * p.tp1_frac)
                        px = min(p.tp1, o + sp)
                        p.tp1 = None
                        if q >= 1:
                            self._close(p, i, px, q, "tp1")
                        if p in self.pos and p.be_after_tp1:
                            p.sl = min(p.sl, p.entry)
                        if p not in self.pos:
                            continue
                    if p.tp is not None and l + sp <= p.tp:
                        self._close(p, i, min(p.tp, o + sp), p.qty, "tp")
            # 4. đóng cửa: quản lý lệnh, tín hiệu mới
            for p in list(self.pos):
                if p.extra.get("limit_bar") != i:
                    strategy.manage(self, p, i)
            for od in strategy.on_bar(self, i) or []:
                (self.pending if od.kind == "limit" else self.market).append(od)
            curve[k] = self.equity(i)
        if len(idx):
            i = idx[-1]
            for p in list(self.pos):
                self._close(p, i, self.c[i] if p.side > 0 else self.c[i] + sp, p.qty, "end")
            curve[-1] = self.balance
        return pd.Series(curve, index=self.t[idx]), pd.DataFrame(self.trades)


class Strategy:
    """Lớp cơ sở. on_bar trả về danh sách Order đặt ở giá đóng cửa nến i."""
    name = ""

    def reset(self, eng, i0):
        pass

    def manage(self, eng, p, i):
        pass

    def on_bar(self, eng, i):
        return []

    def on_trade_opened(self, eng, p, i):
        pass

    def on_trade_closed(self, eng, p, i):
        pass


# ----- chỉ báo kiểu MetaTrader -----
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def sma(s, n):
    return s.rolling(n).mean()


def atr_mt5(df, n=14):
    """iATR của MT5: trung bình cộng true range."""
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def rsi_wilder(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def server_time(idx_utc):
    """Giờ server MT5 phổ biến: New York + 7h."""
    return idx_utc.tz_localize("UTC").tz_convert("America/New_York").tz_localize(None) + pd.Timedelta(hours=7)


def htf_value(df_ltf, series_htf, rule):
    """Giá trị khung lớn của nến khung lớn ĐÃ ĐÓNG gần nhất, gióng về khung nhỏ."""
    closed_at = series_htf.index + pd.Timedelta(rule)
    pos = np.searchsorted(closed_at.values, (df_ltf.index + (df_ltf.index[1] - df_ltf.index[0])).values,
                          side="right") - 1
    out = np.full(len(df_ltf), np.nan)
    ok = pos >= 0
    out[ok] = series_htf.to_numpy(float)[pos[ok]]
    return out


def resample(df, rule):
    return df.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                  "volume": "sum"}).dropna(subset=["close"])


def stats(curve, tr, capital):
    n = len(tr)
    if n == 0:
        return {"ROI": curve.iloc[-1] / capital - 1 if len(curve) else 0.0,
                "MDD": float((curve / curve.cummax() - 1).min()) if len(curve) else 0.0,
                "Lệnh": 0, "Thắng": np.nan, "R TB": np.nan, "PF": np.nan}
    gw, gl = tr.loc[tr.pnl > 0, "pnl"].sum(), -tr.loc[tr.pnl <= 0, "pnl"].sum()
    return {"ROI": curve.iloc[-1] / capital - 1, "MDD": float((curve / curve.cummax() - 1).min()), "Lệnh": n,
            "Thắng": (tr.pnl > 0).mean(), "R TB": tr["r"].mean(), "PF": gw / gl if gl > 0 else np.inf}
