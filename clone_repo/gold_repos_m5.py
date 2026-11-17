"""Các EA khung M5 / M1: BAKOME, PDH/PDL (XAUUSD-Trading-Bots), mt5-gold-ai-trader, alpha_v3. Xem gold_repos.py."""
import numpy as np
import pandas as pd

from gold_engine import Order, Strategy, atr_mt5, ema, htf_value, resample, rsi_wilder, server_time, sma


def _server_htf(df, rule, n):
    """EMA n của khung lớn dựng theo giờ server (nến H4 của MT5 bắt đầu 0h, 4h, 8h... giờ server)."""
    s = df.copy()
    s.index = server_time(df.index)
    big = resample(s, rule)
    v = htf_value(s, ema(big["close"], n), rule)
    return v


# =====================================================================================
# 3. BAKOME Ultimate ICT Gold Scalper — M5
# =====================================================================================
class Bakome(Strategy):
    """Code thật (khác README): FVG / Order Block / liquidity được tính nhưng KHÔNG dùng để vào lệnh.
    Vào lệnh = trong giờ server 8h và 15h (giao của "Silver Bullet" và phiên London/NY), mua nếu giá M5 > EMA200 H4,
    bán nếu dưới; tối đa 2 lệnh, SL 2 ATR(M5), TP 3 ATR; ATR M5 ≥ $1.
    Lỗi trong code gốc: lot = rủi ro / (ATR × 0.01 × 10) → hàng trăm lot (giới hạn 100) nên sàn từ chối vì thiếu ký quỹ;
    bộ đếm lệnh/ngày và số dư đầu ngày không bao giờ reset (ngừng hẳn sau 10 lệnh hoặc khi lời 8% / lỗ 5% tính từ đầu);
    hoà vốn không bao giờ chạy (chỉ khi SL = 0); trailing so lời bằng $ với ATR bằng giá và chỉ dời 1 lần;
    chốt một phần không có trong code.
    fix='lot': chỉ sửa cỡ lệnh (1% rủi ro thật). fix='all': sửa hết, làm đúng như README mô tả."""

    def __init__(self, df, fix=None, name="BAKOME"):
        self.name, self.fix = name, fix
        self.c, self.h, self.l = df["close"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
        self.atr = atr_mt5(df, 14).to_numpy()
        self.ema_h4 = _server_htf(df, "4h", 200)
        nxt = server_time(df.index + pd.Timedelta("5min"))
        self.hour = np.asarray(nxt.hour)
        self.sday = np.asarray(nxt.normalize())

    def reset(self, eng, i0):
        self.count, self.day, self.day_bal = 0, None, eng.capital

    def on_trade_opened(self, eng, p, i):
        self.count += 1
        p.extra["trail_once"] = False

    def manage(self, eng, p, i):
        a, c = self.atr[i], self.c[i]
        move = (c - p.entry) if p.side > 0 else (p.entry - c - eng.spread)
        if self.fix == "all":
            if move >= 1.0 * a and not p.extra.get("be"):
                p.extra["be"] = True
                p.sl = max(p.sl, p.entry) if p.side > 0 else min(p.sl, p.entry)
            if move >= 1.5 * a:
                new = c - 0.5 * a if p.side > 0 else c + eng.spread + 0.5 * a
                p.sl = max(p.sl, new) if p.side > 0 else min(p.sl, new)
            return
        # Bản gốc: Profit() tính bằng $ so với ATR tính bằng giá; dời đúng 1 lần.
        if not p.extra["trail_once"] and p.qty * move >= 1.5 * a:
            new = c - 0.5 * a if p.side > 0 else c + eng.spread + 0.5 * a
            if (p.side > 0 and new > p.sl) or (p.side < 0 and new < p.sl):
                p.sl = new
                p.extra["trail_once"] = True

    def on_bar(self, eng, i):
        if self.fix == "all" and self.sday[i] != self.day:          # bản sửa: reset mỗi ngày
            self.day, self.count, self.day_bal = self.sday[i], 0, eng.balance
        if self.hour[i] not in (8, 15):
            return []
        a, e = self.atr[i], self.ema_h4[i]
        if np.isnan(a) or np.isnan(e) or a < 1.0 or len(eng.pos) >= 2:
            return []
        if self.count >= 10:
            return []
        pl = (eng.equity(i) - self.day_bal) / self.day_bal * 100
        if pl <= -5 or pl >= 8:
            return []
        side = 1 if self.c[i] > e else -1 if self.c[i] < e else 0
        if side == 0:
            return []
        out = []
        for _ in range(2 - len(eng.pos)):
            if self.fix:
                qty = eng.risk_qty(1.0, 2 * a)
            else:
                lots = min(np.round(eng.balance * 0.01 / (a * 0.01 * 10), 2), 100.0)
                qty = lots * 100
            od = Order(side=side, sl_dist=2 * a, tp_dist=3 * a, qty=qty)
            if self.fix == "all":
                od.tp1_r, od.tp1_frac = 0.5, 0.5          # chốt 50% khi lời 1 ATR (= 0,5R với SL 2 ATR)
            out.append(od)
        return out


# =====================================================================================
# 4. PDH/PDL bot (P1Piyush/XAUUSD-Trading-Bots, pdh_bot.py) — M5
# =====================================================================================
class PdhPdl(Strategy):
    """Phá đỉnh ngày hôm trước (PDH) rồi retest: 1 trong 3 nến trước có low nằm trong ±0,15% quanh PDH và đóng cửa
    trên PDH, nến trước nữa đóng dưới PDH, nến hiện tại đóng trên PDH + 0,15%. Cùng hướng EMA50/200 H1, RSI 22–78,
    giờ UTC 6–20 trừ 9h, 10h. Mỗi mức 1 lệnh/ngày. SL 1 ATR(M5), TP1 1,5R (chốt 50%), TP2 3R.
    Lệnh do EA đã biên dịch (XAU_Bot.ex5) thực hiện nên không biết cỡ lệnh; ở đây dùng 1% rủi ro.
    Các setup "sweep đảo chiều" bị chính tác giả tắt (vẫn đánh dấu mức đã chạm trong ngày)."""

    def __init__(self, df, be_after_tp1=False, name="PDH/PDL"):
        self.name, self.be = name, be_after_tp1
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        pc = df["close"].shift(1)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
        self.atr = tr.rolling(14).mean().to_numpy()
        d = df["close"].diff()
        g, lo = d.clip(lower=0).rolling(14).mean(), (-d.clip(upper=0)).rolling(14).mean()
        self.rsi = (100 - 100 / (1 + g / lo.replace(0, np.nan))).to_numpy()
        h1 = resample(df, "1h")
        e50, e200 = htf_value(df, ema(h1["close"], 50), "1h"), htf_value(df, ema(h1["close"], 200), "1h")
        self.bias = np.where(e50 > e200, 1, -1)
        sd = server_time(df.index).normalize()
        daily = df.groupby(sd).agg(high=("high", "max"), low=("low", "min"))
        prev = daily.shift(1)
        self.pdh = prev["high"].reindex(sd).to_numpy()
        self.pdl = prev["low"].reindex(sd).to_numpy()
        self.hour = np.asarray(df.index.hour)
        self.uday = np.asarray(df.index.normalize())

    def reset(self, eng, i0):
        self.day, self.tapped, self.day_bal = None, {"H": False, "L": False}, eng.capital

    def on_bar(self, eng, i):
        if self.uday[i] != self.day:
            self.day, self.tapped, self.day_bal = self.uday[i], {"H": False, "L": False}, eng.balance
        if eng.pos or eng.market:
            return []
        if (1 - eng.equity(i) / self.day_bal) * 100 >= 3.0:
            return []
        h = self.hour[i]
        if h in (1, 9, 10) or not (6 <= h < 20):
            return []
        a, r, cur = self.atr[i], self.rsi[i], self.c[i]
        pdh, pdl = self.pdh[i], self.pdl[i]
        if np.isnan(a) or np.isnan(pdh) or np.isnan(r) or r > 78 or r < 22:
            return []
        prox, b = cur * 0.0015, self.bias[i]
        c, o, hi, lo = self.c, self.o, self.h, self.l
        if not self.tapped["H"] and abs(cur - pdh) < prox * 2:
            if hi[i] > pdh + prox * 0.3 and c[i] < pdh and c[i - 1] > o[i - 1] and c[i] < o[i] and b == -1:
                self.tapped["H"] = True
                return []
            if c[i] > pdh + prox and c[i - 1] < pdh:
                if any(pdh - prox <= lo[i - k] <= pdh + prox and c[i - k] > pdh for k in (1, 2, 3)) and b == 1:
                    self.tapped["H"] = True
                    return [Order(side=1, sl_dist=a, tp_dist=3 * a, risk_pct=1.0, tp1_r=1.5, tp1_frac=0.5,
                                  be_after_tp1=self.be)]
        if not self.tapped["L"] and abs(cur - pdl) < prox * 2:
            if lo[i] < pdl - prox * 0.3 and c[i] > pdl and c[i - 1] < o[i - 1] and c[i] > o[i] and b == 1:
                self.tapped["L"] = True
                return []
            if c[i] < pdl - prox and c[i - 1] > pdl:
                if any(pdl - prox <= hi[i - k] <= pdl + prox and c[i - k] < pdl for k in (1, 2, 3)) and b == -1:
                    self.tapped["L"] = True
                    return [Order(side=-1, sl_dist=a, tp_dist=3 * a, risk_pct=1.0, tp1_r=1.5, tp1_frac=0.5,
                                  be_after_tp1=self.be)]
        return []


# =====================================================================================
# 5. mt5-gold-ai-trader (HillaryMurimi) — M5
# =====================================================================================
class AiTrader(Strategy):
    """Phần "AI" (LSTM, phân loại chiến lược huấn luyện trên 3 dòng dữ liệu bịa, tin tức) không ảnh hưởng gì đến
    tín hiệu ngoài việc chọn 1 trong 3 chiến lược, nên test riêng từng chiến lược:
      scalping: MA5 cắt lên MA20 và độ lệch chuẩn 10 nến < ½ độ lệch chuẩn 200 nến → mua; cắt xuống → bán
      day:      RSI(14, trung bình cộng) < 30 → mua, > 70 → bán
      swing:    MACD 12/26/9 cắt tín hiệu
    SL $10, TP $20. Lot gốc = 2% vốn / ($10 × 10): sai hệ số (1 lot vàng = $100 mỗi $1), tức rủi ro thật 20%/lệnh.
    main.py chỉ chạy một lần, không kiểm tra lệnh đang mở; ở đây giả định chạy mỗi nến M5 và chỉ giữ 1 lệnh."""

    def __init__(self, df, mode, real_risk=False, name=None):
        self.name, self.mode, self.real = name or f"AI {mode}", mode, real_risk
        c = df["close"]
        if mode == "scalping":
            f, s = sma(c, 5), sma(c, 20)
            vol, ref = c.rolling(10).std(), c.rolling(200).std() / 2
            up = (f > s) & (f.shift(1) <= s.shift(1)) & (vol < ref)
            dn = (f < s) & (f.shift(1) >= s.shift(1))
        elif mode == "day":
            d = c.diff()
            g, lo = d.where(d > 0, 0).rolling(14).mean(), (-d.where(d < 0, 0)).rolling(14).mean()
            r = 100 - 100 / (1 + g / lo)
            up, dn = r < 30, r > 70
        else:
            m = ema(c, 12) - ema(c, 26)
            sg = ema(m, 9)
            up = (m > sg) & (m.shift(1) <= sg.shift(1))
            dn = (m < sg) & (m.shift(1) >= sg.shift(1))
        self.sig = np.where(up, 1, np.where(dn, -1, 0))

    def on_bar(self, eng, i):
        s = self.sig[i]
        if not s or eng.pos or eng.market:
            return []
        lots = max(round(eng.balance * 0.02 / (10 * 10), 2), 0.01)
        qty = eng.risk_qty(2.0, 10.0) if self.real else lots * 100
        return [Order(side=int(s), sl_dist=10.0, tp_dist=20.0, qty=qty)]


# =====================================================================================
# 6. alpha_v3_enhanced.py (P1Piyush) — M1, vàng + DXY
# =====================================================================================
class AlphaV3(Strategy):
    """Parabolic SAR đổi chiều + giá cùng phía EMA13 + z-score 20 nến của DXY < −1,5 (mua) / > 1,5 (bán),
    ATR(14) ≥ 0,15. SL $1,6, TP $2,3, trailing $1,6 co lại khi đà chậm. Rủi ro 1%, lot 0,01–0,5.
    Repo chỉ test trên ~5 ngày nến 1 phút của Yahoo; ở đây cũng chỉ có ~7 ngày (Yahoo không cho tải xa hơn)."""

    def __init__(self, df, name="alpha_v3"):
        self.name = name
        self.c = df["close"].to_numpy()
        self.ema = ema(df["close"], 13).to_numpy()
        pc = df["close"].shift(1)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
        self.atr = tr.rolling(14).mean().to_numpy()
        z = df["close_dxy"]
        self.z = ((z - z.rolling(20).mean()) / z.rolling(20).std()).to_numpy()
        self.trend = self._psar(df["high"].to_numpy(), df["low"].to_numpy())
        self.start = df.index[50]
        self.capital = 1000.0                       # vốn mặc định của repo

    @staticmethod
    def _psar(high, low, step=0.03, mx=0.2):
        n = len(high)
        sar, tr, af, ep = np.zeros(n), np.zeros(n), np.full(n, step), np.zeros(n)
        tr[0] = 1 if high[1] > high[0] else -1
        sar[0] = low[0] if tr[0] == 1 else high[0]
        ep[0] = high[0] if tr[0] == 1 else low[0]
        for i in range(1, n):
            sar[i] = sar[i - 1] + af[i - 1] * (ep[i - 1] - sar[i - 1])
            if tr[i - 1] == 1:
                if low[i] < sar[i]:
                    tr[i], sar[i], ep[i], af[i] = -1, ep[i - 1], low[i], step
                else:
                    tr[i] = 1
                    ep[i], af[i] = (high[i], min(mx, af[i - 1] + step)) if high[i] > ep[i - 1] else (ep[i - 1], af[i - 1])
                    sar[i] = min(sar[i], low[i - 1], low[i - 2] if i > 1 else low[i - 1])
            else:
                if high[i] > sar[i]:
                    tr[i], sar[i], ep[i], af[i] = 1, ep[i - 1], high[i], step
                else:
                    tr[i] = -1
                    ep[i], af[i] = (low[i], min(mx, af[i - 1] + step)) if low[i] < ep[i - 1] else (ep[i - 1], af[i - 1])
                    sar[i] = max(sar[i], high[i - 1], high[i - 2] if i > 1 else high[i - 1])
        return tr

    def manage(self, eng, p, i):
        if i < 5:
            return
        v1, v2 = self.c[i] - self.c[i - 1], self.c[i - 1] - self.c[i - 2]
        a1 = v1 - v2
        f = np.clip(1 + p.side * a1 / self.atr[i], 0.3, 1.0)
        new = self.c[i] - p.side * 1.6 * f
        if (p.side > 0 and new > p.sl) or (p.side < 0 and new < p.sl):
            p.sl = new

    def on_bar(self, eng, i):
        if eng.pos or eng.market or i < 50 or self.atr[i] < 0.15:
            return []
        flip_up = self.trend[i] == 1 and self.trend[i - 1] == -1
        flip_dn = self.trend[i] == -1 and self.trend[i - 1] == 1
        lots = round(max(0.01, min(eng.balance * 0.01 / (1.6 * 100), 0.5)), 2)
        if flip_up and self.c[i] > self.ema[i] and self.z[i] < -1.5:
            return [Order(side=1, sl_dist=1.6, tp_dist=2.3, qty=lots * 100)]
        if flip_dn and self.c[i] < self.ema[i] and self.z[i] > 1.5:
            return [Order(side=-1, sl_dist=1.6, tp_dist=2.3, qty=lots * 100)]
        return []


def load_alpha_data():
    from gold_repos import FX
    g = pd.read_csv(FX / "1m" / "XAUUSD_GCF.csv", index_col=0, parse_dates=True)
    d = pd.read_csv(FX / "1m" / "DXY.csv", index_col=0, parse_dates=True)
    g["close_dxy"] = d["close"].reindex(g.index).ffill()
    return g.dropna(subset=["close_dxy"])


def variants(only, spread):
    from gold_repos import load_gold
    out = []
    need5 = any(k in only for k in ("bak", "pdh", "ai"))
    d5 = load_gold("5m") if need5 else None
    if "bak" in only:
        out += [("bak", "BAKOME · gốc (lot sai → sàn từ chối)", d5, Bakome(d5), 2),
                ("bak", "BAKOME · chỉ sửa lot (1% rủi ro), giữ các lỗi khác", d5, Bakome(d5, fix="lot"), 2),
                ("bak", "BAKOME · sửa hết theo README (hoà vốn, trailing, chốt 50%)", d5, Bakome(d5, fix="all"), 2)]
    if "pdh" in only:
        out += [("pdh", "PDH/PDL · phá đỉnh/đáy hôm qua + retest, 1% rủi ro", d5, PdhPdl(d5), 1),
                ("pdh", "PDH/PDL · + hoà vốn sau TP1", d5, PdhPdl(d5, be_after_tp1=True), 1)]
    if "ai" in only:
        for mode in ("scalping", "day", "swing"):
            out.append(("ai", f"AI trader · {mode} · lot gốc (rủi ro thật ~20%/lệnh)", d5, AiTrader(d5, mode), 1))
            out.append(("ai", f"AI trader · {mode} · rủi ro 2% đúng", d5, AiTrader(d5, mode, real_risk=True), 1))
    if "alpha" in only:
        d1 = load_alpha_data()
        out.append(("alpha", f"alpha_v3 · GC=F nến 1 phút {d1.index[0]:%d/%m}–{d1.index[-1]:%d/%m} (vốn $1000)",
                    d1, AlphaV3(d1), 1))
    return out
