"""Backtest các repo EA vàng (XAUUSD) viết lại bằng Python, chạy bằng gold_engine.py.

    .venv/bin/python clone_repo/gold_repos.py                 # tất cả
    .venv/bin/python clone_repo/gold_repos.py --only fvg,gte  # chọn repo
    .venv/bin/python clone_repo/gold_repos.py --spread 0.5

Repo (đọc code trong clone_repo/repos/, không chạy code của họ):
  fvg   foeed/FvgGold-EA              M15: FVG + Order Block có chấm điểm, lệnh limit, RR 1.5
  bak   BAKOME-Hub/BAKOMEGoldScalper  M5: giờ "Silver Bullet" + EMA200 H4, SL 2 ATR / TP 3 ATR
  gte   mehdi-jahani/GoldTraderEA     H1: cộng điểm xác nhận từ nhiều module mô hình giá
  pdh   P1Piyush/XAUUSD-Trading-Bots  M5: phá đỉnh/đáy ngày hôm trước rồi retest (pdh_bot.py)
  alpha P1Piyush/XAUUSD-Trading-Bots  M1: PSAR đảo chiều + EMA13 + z-score DXY (alpha_v3_enhanced.py)
  ai    HillaryMurimi/mt5-gold-ai-trader M5: MA cross / RSI / MACD, SL $10 TP $20
egade369/ea-xauusd chỉ có file .ex5 đã biên dịch, không có mã nguồn nên không backtest được.

Dữ liệu vàng: Binance PAXGUSDT (xem fetch_fx.py), bỏ nến cuối tuần theo giờ server (New York + 7).
Mỗi biến thể chạy liền 2021→nay và chạy riêng từng năm (mỗi năm bắt đầu lại từ vốn ban đầu).
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from gold_engine import (Engine, Order, Strategy, atr_mt5, ema, htf_value, resample, rsi_wilder,  # noqa: E402
                         server_time, sma, stats)

FX = ROOT / "data" / "csv" / "fx"


def load_gold(interval, start="2020-09-15"):
    df = pd.read_csv(FX / interval / "PAXGUSDT.csv", index_col=0, parse_dates=True)
    df = df[df.index >= start]
    st = server_time(df.index)
    return df[np.asarray(st.dayofweek) < 5]          # sàn MT5 đóng cửa cuối tuần


# =====================================================================================
# 1. FvgGold (foeed/FvgGold-EA) — M15
# =====================================================================================
class FvgGold(Strategy):
    """Phát hiện FVG 3 nến (gap ≥ 25 điểm = $0.25, tuổi 2–20 nến), chấm điểm 0–100:
    gap/ATR (30) + thân/biên độ nến giữa (30) + cùng hướng EMA50/200 H1 (20) + độ mới (10) + mua dưới EMA50 H1 (10)
    + Order Block trùng (+20). Điểm ≥ 50 và cùng hướng H1 trong killzone GMT 7–10, 12–21 thì đặt lệnh limit:
    mua ở đáy FVG + $1.5, SL đáy − $3, TP 1.5R. Tối đa 1 lệnh (tính cả lệnh chờ). Hoà vốn khi lời 0.5 ATR.
    Ngừng trong ngày khi lỗ $5. Lot cố định 0.01 (mặc định của repo)."""

    def __init__(self, df, fixed_oz=1.0, risk_pct=0.5, cancel_stale=False, use_kz=True, name="FvgGold"):
        self.name, self.fixed_oz, self.risk_pct, self.cancel_stale, self.use_kz = name, fixed_oz, risk_pct, cancel_stale, use_kz
        self.MIN_GAP, self.MAX_AGE, self.MIN_AGE, self.BUF, self.MIN_SCORE = 0.25, 20, 2, 3.0, 50.0
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.atr = atr_mt5(df, 14).to_numpy()
        h1 = resample(df, "1h")
        self.ef = htf_value(df, ema(h1["close"], 50), "1h")
        self.es = htf_value(df, ema(h1["close"], 200), "1h")
        now = df.index + pd.Timedelta("15min")       # thời điểm ra quyết định = lúc nến đóng
        self.hour = np.asarray(now.hour)
        self.sday = np.asarray(server_time(now).normalize())
        n = len(df)
        o, h, l, c = self.o, self.h, self.l, self.c
        self.bull = np.full(n, np.nan)
        self.bear = np.full(n, np.nan)
        self.bull[1:-1] = l[2:] - h[:-2]             # gap tại nến giữa m: low[m+1] - high[m-1]
        self.bear[1:-1] = l[:-2] - h[2:]
        rng = h - l
        self.disp = np.where(rng > 0, np.abs(c - o) / np.where(rng > 0, rng, 1), 0)
        # Order Block: nến k ngược màu ngay trước nến bùng nổ k+1
        body2 = np.full(n, np.nan)
        body2[:-1] = np.abs(c[1:] - o[1:])
        self.ob_body2 = body2
        t = np.zeros(n, int)
        t[:-1] = np.where((c[1:] > o[1:]) & (c[:-1] < o[:-1]), 1, np.where((c[1:] < o[1:]) & (c[:-1] > o[:-1]), -1, 0))
        self.ob_type = t
        self.ob_top, self.ob_bot = np.maximum(o, c), np.minimum(o, c)

    def reset(self, eng, i0):
        self.zones, self.day, self.day_pl = {}, None, 0.0

    def on_trade_closed(self, eng, p, i):
        if self.sday[i] == self.day:
            self.day_pl += p.pnl

    def _ob_confluence(self, i, typ, top, bot):
        atr = self.atr[i]
        for k in range(max(i - 49, 1), i - 1):
            if self.ob_type[k] != typ or not (self.ob_body2[k] >= atr * 1.5):
                continue
            ot, ob = self.ob_top[k], self.ob_bot[k]
            if ot - ob < 0.10:
                continue
            if (typ == 1 and self.c[i] < ob) or (typ == -1 and self.c[i] + self.sp > ot):
                continue                              # đã bị phá (mitigated)
            if top >= ob and bot <= ot:
                return True
            dist = bot - ot if typ == 1 else ob - top
            if 0 <= dist <= 2.0:
                return True
        return False

    def _bias(self, i):
        f, s = self.ef[i], self.es[i]
        return 0 if np.isnan(s) else (1 if f > s else -1 if f < s else 0)

    def manage(self, eng, p, i):
        a = self.atr[i]
        if p.side > 0 and self.h[i] - p.entry >= 0.5 * a and p.sl < p.entry:
            p.sl = p.entry + 0.02
        elif p.side < 0 and p.entry - (self.l[i] + self.sp) >= 0.5 * a and p.sl > p.entry:
            p.sl = p.entry - 0.02

    def on_bar(self, eng, i):
        self.sp = eng.spread
        if self.sday[i] != self.day:
            self.day, self.day_pl = self.sday[i], 0.0
        if self.day_pl <= -5.0:
            return []
        c, atr, bias = self.c[i], self.atr[i], self._bias(i)
        if np.isnan(atr) or atr <= 0:
            return []
        # Phát hiện (tuổi = i + 1 - m, trong [2, 20])
        for m in range(i - self.MAX_AGE + 1, i):
            if m < 1 or m in self.zones:
                continue
            if self.bull[m] >= self.MIN_GAP:
                typ, top, bot = 1, self.l[m + 1], self.h[m - 1]
            elif self.bear[m] >= self.MIN_GAP:
                typ, top, bot = -1, self.l[m - 1], self.h[m + 1]
            else:
                continue
            age = i + 1 - m
            score = min((top - bot) / atr, 1.0) * 30 + self.disp[m] * 30 + (20 if typ == bias else 0)
            score += (1 - age / self.MAX_AGE) * 10
            if not np.isnan(self.ef[i]) and ((typ == 1 and c < self.ef[i]) or (typ == -1 and c > self.ef[i])):
                score += 10
            if self._ob_confluence(i, typ, top, bot):
                score += 20
            if score >= self.MIN_SCORE:
                self.zones[m] = {"type": typ, "top": top, "bot": bot, "mid": (top + bot) / 2, "placed": False}
        # Dọn vùng quá tuổi / đã bị lấp quá nửa
        for m in list(self.zones):
            z = self.zones[m]
            old = m <= i - self.MAX_AGE
            mitig = (z["type"] == 1 and c < z["mid"]) or (z["type"] == -1 and c + self.sp > z["mid"])
            if old or mitig:
                del self.zones[m]
                if old and self.cancel_stale:
                    eng.pending[:] = [od for od in eng.pending if od.tag != m]
        if bias == 0 or (self.use_kz and not (7 <= self.hour[i] < 10 or 12 <= self.hour[i] < 21)):
            return []
        out = []
        for m, z in self.zones.items():
            if z["placed"] or z["type"] != bias:
                continue
            if len(eng.pos) + len(eng.pending) + len(out) >= 1:
                break
            if any(od.tag == m for od in eng.pending) or any(p.tag == m for p in eng.pos):
                continue
            if z["type"] == 1:
                entry, sl = z["bot"] + self.BUF / 2, z["bot"] - self.BUF
                if c + self.sp - entry < self.sp:
                    continue
            else:
                entry, sl = z["top"] - self.BUF / 2, z["top"] + self.BUF
                if entry - c < self.sp:
                    continue
            d = abs(entry - sl)
            if self.fixed_oz:
                qty = self.fixed_oz
            else:
                lots = eng.balance * self.risk_pct / 100 / (d * 100)
                qty = max(1.0, np.floor(min(lots, 0.10, eng.balance * 0.01) * 100 + 1e-9))
            out.append(Order(side=z["type"], kind="limit", price=entry, sl=sl, tp=entry + z["type"] * d * 1.5,
                             qty=qty, tag=m))
            z["placed"] = True
        return out


# =====================================================================================
# 2. GoldTraderEA (mehdi-jahani) — H1
# =====================================================================================
class GoldTraderEA(Strategy):
    """Điều kiện chặn: giá > SMA200, "tilt" 5 nến, không phải "ngày xấu", trong phiên (GMT trừ 6h, 7h, 21h).
    Điểm xác nhận (cần ≥ 7): chỉ báo (≥3/9 điều kiện) ×1, mô hình nến ×1, price action ×2, mô hình giá ×2,
    hỗ trợ/kháng cự ×3. Các module MA cross, phân kỳ, khối lượng luôn = 0 do lỗi kích thước mảng trong code.
    Lệnh bán không bao giờ mở được (so giá với handle của iMA). Lot cố định 0.1, SL 2 ATR, TP 4 ATR, 1 lệnh.
    fix=True: sửa các lỗi trên (cho bán, mức hỗ trợ/kháng cự cập nhật mỗi nến) để xem ý tưởng gốc."""

    def __init__(self, df, fix=False, name="GoldTraderEA"):
        self.name, self.fix = name, fix
        self.o, self.h, self.l, self.c, self.v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        cl = df["close"]
        self.rsi = rsi_wilder(cl, 14).to_numpy()
        macd = ema(cl, 12) - ema(cl, 26)
        self.macd, self.msig = macd.to_numpy(), sma(macd, 9).to_numpy()     # tín hiệu MACD của MT5 là SMA
        ll, hh = df["low"].rolling(5).min(), df["high"].rolling(5).max()
        k = (cl - ll).rolling(3).sum() / (hh - ll).rolling(3).sum() * 100
        self.sk, self.sd = k.to_numpy(), sma(k, 3).to_numpy()
        up, dn = df["high"].diff(), -df["low"].diff()
        pdm = up.where((up > dn) & (up > 0), 0.0)
        mdm = dn.where((dn > up) & (dn > 0), 0.0)
        pc = cl.shift(1)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
        a = 2 / 15
        pdi = 100 * pdm.ewm(alpha=a, adjust=False).mean() / tr.ewm(alpha=a, adjust=False).mean()
        mdi = 100 * mdm.ewm(alpha=a, adjust=False).mean() / tr.ewm(alpha=a, adjust=False).mean()
        self.adx = (100 * (pdi - mdi).abs() / (pdi + mdi)).ewm(alpha=a, adjust=False).mean().to_numpy()
        self.ef, self.es = ema(cl, 20).to_numpy(), ema(cl, 50).to_numpy()
        mid, sd = sma(cl, 20), cl.rolling(20).std(ddof=0)
        self.bbm, self.bbl, self.bbu = mid.to_numpy(), (mid - 2 * sd).to_numpy(), (mid + 2 * sd).to_numpy()
        self.ma200 = sma(cl, 200).to_numpy()
        self.atr = atr_mt5(df, 14).to_numpy()
        now = df.index + pd.Timedelta("1h")
        self.gmt_h, self.gmt_dow = np.asarray(now.hour), np.asarray(now.dayofweek)
        st = server_time(now)
        self.s_dow, self.s_day, self.s_mon = np.asarray(st.dayofweek), np.asarray(st.day), np.asarray(st.month)

    # ---- tiện ích: R(i, n) = mảng theo kiểu MQL "series" (0 = nến hiện tại) ----
    def R(self, i, n):
        lo = max(i - n + 1, 0)
        s = slice(lo, i + 1)
        return tuple(a[s][::-1] for a in (self.o, self.h, self.l, self.c))

    def reset(self, eng, i0):
        self.sup, self.res = self._sr_levels(i0)

    def _sr_levels(self, i):
        o, h, l, c = self.R(i, 200)
        if len(l) < 100:
            return [], []
        sup, res = [], []
        for k in range(10, 100):
            if len(sup) < 10 and l[k] < l[k - 1] and l[k] < l[k + 1] and l[k] < l[k - 2] and l[k] < l[k + 2]:
                if (np.abs(l - l[k]) <= 0.0005 * l[k]).sum() >= 2:
                    sup.append(l[k])
            if len(res) < 10 and h[k] > h[k - 1] and h[k] > h[k + 1] and h[k] > h[k - 2] and h[k] > h[k + 2]:
                if (np.abs(h - h[k]) <= 0.0005 * h[k]).sum() >= 2:
                    res.append(h[k])
        return sorted(sup), sorted(res, reverse=True)

    # ---- mô hình (chỉ chiều mua; chiều bán đối xứng khi fix=True) ----
    @staticmethod
    def _candles(o, h, l, c):
        body = abs(c[0] - o[0]); up = h[0] - max(o[0], c[0]); dn = min(o[0], c[0]) - l[0]; tot = h[0] - l[0]
        pin = dn > 2 * body and dn > up and dn > 0.6 * tot
        inside = h[0] < h[1] and l[0] > l[1] and c[0] > o[0]
        hammer = pin and c[0] > o[0]
        b1, b2 = abs(c[1] - o[1]), abs(c[0] - o[0])
        engulf = c[1] < o[1] and c[0] > o[0] and ((c[0] >= o[1] and o[0] <= c[1]) or b2 > b1 * 0.8)
        mb1, mb2 = abs(c[2] - o[2]), abs(c[1] - o[1])
        morning = c[2] < o[2] and mb2 < mb1 * 0.5 and c[0] > o[0] and c[0] > (o[2] + c[2]) / 2 * 0.9
        return pin, inside, hammer, engulf, morning

    @staticmethod
    def _trend_breakout(h, c):
        size, touches, tv, slope = len(h), 0, 0.0, 0.0
        for k in range(3, size - 3):
            if h[k] > h[k + 1] and h[k] > h[k - 1]:
                if touches == 0:
                    tv, touches = h[k], 1
                elif touches == 1:
                    slope, tv, touches = (h[k] - tv) / k, h[k], 2
                elif abs(h[k] - (tv - slope * k)) < 0.20:
                    tv, touches = h[k], touches + 1
        if touches < 3:
            return False
        tv -= slope * size
        return c[0] > tv and c[1] <= tv

    @staticmethod
    def _three_white(o, h, l, c):
        return (c[2] > o[2] and c[1] > o[1] and c[0] > o[0] and c[1] > c[2] and c[0] > c[1] and o[1] > o[2]
                and o[0] > o[1] and all(c[k] - o[k] > 0.7 * (h[k] - l[k]) for k in range(3)))

    @staticmethod
    def _price_formation(h, l):
        pl, ph, hl, hh = l[9], h[9], 0, 0
        for k in (8, 6, 4, 2, 0):
            if l[k] > pl:
                hl, pl = hl + 1, l[k]
            if k > 0 and h[k - 1] > ph:
                hh, ph = hh + 1, h[k - 1]
        return hl >= 3 or hh >= 3

    @staticmethod
    def _support_holding(o, l, c):
        for k in range(5, 0, -1):
            if l[k] < l[k + 1] and l[k] < l[k - 1]:
                return c[0] > l[k] and c[0] > o[0]
        return False

    @staticmethod
    def _double_bottom(h, l, c):
        size = len(l)
        fb, fbi = np.inf, -1
        for k in range(min(19, size - 2), 14, -1):
            if l[k] < fb and l[k] < l[k + 1] and l[k] < l[k - 1]:
                fb, fbi = l[k], k
        if fbi < 0:
            return False
        mp, mpi = 0.0, -1
        for k in range(fbi - 1, 9, -1):
            if h[k] > mp and h[k] > h[k + 1] and h[k] > h[k - 1]:
                mp, mpi = h[k], k
                break
        if mpi < 0:
            return False
        sb, sbi = np.inf, -1
        for k in range(mpi - 1, 2, -1):
            if l[k] < sb and l[k] < l[k + 1] and l[k] < l[k - 1]:
                sb, sbi = l[k], k
                break
        return sbi >= 0 and fbi > mpi > sbi and abs(fb - sb) < 0.01 * fb and c[0] > mp

    @staticmethod
    def _inv_hs(h, l, c):
        h, l, c = h[:50], l[:50], c[:50]
        if len(l) < 50:
            return False
        ls, lsi = np.inf, -1
        for k in range(29, 23, -1):
            if l[k] < ls and l[k] < l[k + 1] and l[k] < l[k - 1]:
                ls, lsi = l[k], k
        if lsi < 0:
            return False
        hd, hdi = np.inf, -1
        for k in range(lsi - 1, 14, -1):
            if l[k] < hd and l[k] < l[k + 1] and l[k] < l[k - 1]:
                hd, hdi = l[k], k
        if hdi < 0 or hd >= ls:
            return False
        rs, rsi_ = np.inf, -1
        for k in range(hdi - 1, 0, -1):
            if l[k] < rs and l[k] < l[k + 1] and l[k] < l[k - 1]:
                rs, rsi_ = l[k], k
        if rsi_ < 0:
            return False
        lp, lpi, rp, rpi = 0.0, -1, 0.0, -1
        for k in range(lsi - 1, hdi, -1):
            if h[k] > lp:
                lp, lpi = h[k], k
        for k in range(hdi - 1, rsi_, -1):
            if h[k] > rp:
                rp, rpi = h[k], k
        if lpi < 0 or rpi < 0:
            return False
        neck = lp + (rp - lp) / (lpi - rpi) * lpi
        return hd < ls and hd < rs and abs(ls - rs) <= 0.01 * ls and c[0] > neck

    @staticmethod
    def _asc_triangle(h, l, c):
        size = len(h)
        res = 0.0
        for k in range(50, min(90, size - 2)):
            if h[k] > h[k + 1] and h[k] > h[k - 1]:
                res = h[k]
                break
        if res == 0 or (np.abs(h[5:min(50, size)] - res) < 0.001 * res).sum() < 2:
            return False
        prev = 0.0
        for k in range(50, 5, -1):
            if k + 1 >= size:
                continue
            if l[k] < l[k + 1] and l[k] < l[k - 1]:
                if prev != 0 and l[k] <= prev:
                    return False
                prev = l[k]
        return c[0] > res

    @staticmethod
    def _cup_handle(h, l, c):
        size = len(h)
        if size < 80:
            return False
        p1 = next((k for k in range(80, min(99, size - 2)) if h[k] > h[k + 1] and h[k] > h[k - 1]), -1)
        if p1 < 0:
            return False
        p2 = next((k for k in range(10, min(30, size - 1)) if h[k] > h[k + 1] and h[k] > h[k - 1]), -1)
        if p2 < 0:
            return False
        seg = l[p2 + 5:p1 - 5]
        if len(seg) == 0 or abs(h[p1] - h[p2]) > 0.1 * h[p1] or p2 > (p1 - (p2 + 5 + int(np.argmin(seg)))) // 2:
            return False
        return c[0] > max(h[p1], h[p2])

    @staticmethod
    def _wedge(h, l, c):
        size = len(h)
        if size < 20:
            return False
        mx = min(size - 1, 49)
        mn = min(10, mx // 2)
        lows, highs = [], []
        for k in range(mx, mn - 1, -1):
            if k + 1 >= size:
                continue
            if len(lows) < 3 and l[k] < l[k + 1] and l[k] < l[k - 1]:
                lows.append((l[k], k))
            if len(highs) < 3 and h[k] > h[k + 1] and h[k] > h[k - 1]:
                highs.append((h[k], k))
            if len(lows) >= 2 and len(highs) >= 2:
                break
        if len(lows) < 2 or len(highs) < 2:
            return False
        (l0, i0), (l1, i1) = lows[0], lows[1]
        (h0, j0), (h1, j1) = highs[0], highs[1]
        if not (l0 < l1 and h0 < h1):
            return False
        ls, us = (l0 - l1) / (i0 - i1), (h0 - h1) / (j0 - j1)
        if us >= ls:
            return False
        return c[0] > h0 + us * j0

    def _score_buy(self, i, o, h, l, c):
        r0, r1, r2 = self.rsi[i], self.rsi[i - 1], self.rsi[i - 2]
        pin, inside, hammer, engulf, morning = self._candles(o, h, l, c)
        wedge = self._wedge(h, l, c)
        conds = [r0 > r1 > r2 and r0 < 70 and r1 < 30,
                 self.macd[i] > self.msig[i] and self.macd[i - 1] < self.msig[i - 1],
                 self.sk[i] > self.sd[i] and self.sk[i - 1] < self.sd[i - 1] and self.sk[i] < 80,
                 self.adx[i] > 25,
                 self.ef[i] > self.es[i] and self.ef[i - 1] < self.es[i - 1],
                 c[0] < self.bbl[i] or (c[0] > self.bbm[i] and c[1] < self.bbm[i - 1]),
                 self.v[i] > self.v[i - 1] > self.v[i - 2],
                 engulf or hammer or morning, wedge]
        score = 1 if sum(map(bool, conds)) >= 3 else 0
        score += pin + inside + hammer + engulf + morning
        pa = (self._trend_breakout(h, c) + self._three_white(o, h, l, c) + (c[0] > h[1:11].max())
              + self._price_formation(h, l) + self._support_holding(o, l, c))
        score += 2 * pa
        ch = (self._double_bottom(h, l, c) + self._inv_hs(h, l, c) + self._asc_triangle(h, l, c)
              + self._cup_handle(h, l, c) + wedge)
        score += 2 * ch
        bid = c[0]
        sr = any(abs(bid - s) < 0.01 * bid and bid > s for s in self.sup) + any(r < bid < r * 1.01 for r in self.res)
        return score + 3 * sr

    def _bad_day(self, i):
        sc = (self.s_dow[i] in (0, 4)) + (self.s_day[i] <= 2 or self.s_day[i] >= 28)   # MQL: 1 = thứ Hai, 5 = thứ Sáu
        if (self.s_mon[i] == 12 and self.s_day[i] >= 23) or (self.s_mon[i] == 1 and self.s_day[i] <= 5):
            sc += 2
        if self.atr[i] > 1.5 * (self.atr[i - 1] + self.atr[i - 2]) / 2:
            sc += 3
        if self.s_dow[i] == 4 and self.s_day[i] <= 7:
            sc += 3
        if self.s_mon[i] in (1, 3, 5, 6, 7, 9, 11) and 15 <= self.s_day[i] <= 17:
            sc += 2
        return sc >= 3

    def on_bar(self, eng, i):
        if i < 210 or eng.pos or np.isnan(self.ma200[i]):
            return []
        if self.gmt_dow[i] >= 5 or self.gmt_h[i] in (6, 7, 21):
            return []
        if self.fix:
            self.sup, self.res = self._sr_levels(i)
        elif not self.sup:
            self.sup, self.res = self._sr_levels(i)
        o, h, l, c = self.R(i, 100)
        above = c[0] > self.ma200[i]
        bull = (c[:5] > o[:5]).sum()
        bear = (c[:5] < o[:5]).sum()
        buy_ok = above and (bull >= 3 or bull == bear)
        sell_ok = self.fix and not above and (bear >= 3 or bull == bear)
        if not (buy_ok or sell_ok) or self._bad_day(i):
            return []
        a = self.atr[i]
        if buy_ok and self._score_buy(i, o, h, l, c) >= 7:
            return [Order(side=1, sl_dist=2 * a, tp_dist=4 * a, qty=10.0)]
        if sell_ok:
            # Đối xứng: đảo giá (−giá) để dùng lại đúng các hàm mô hình chiều mua.
            score = self._score_sell(i, o, h, l, c)
            if score >= 7:
                return [Order(side=-1, sl_dist=2 * a, tp_dist=4 * a, qty=10.0)]
        return []

    def _score_sell(self, i, o, h, l, c):
        # Gương giá qua trục 0: nến giảm thành nến tăng, đỉnh thành đáy. Chỉ báo dùng điều kiện ngược.
        O, H, L, C = -o, -l, -h, -c
        pin, inside, hammer, engulf, morning = self._candles(O, H, L, C)
        wedge = self._wedge(H, L, C)
        r0, r1, r2 = self.rsi[i], self.rsi[i - 1], self.rsi[i - 2]
        conds = [r0 < r1 < r2 and r0 > 30 and r1 > 70,
                 self.macd[i] < self.msig[i] and self.macd[i - 1] > self.msig[i - 1],
                 self.sk[i] < self.sd[i] and self.sk[i - 1] > self.sd[i - 1] and self.sk[i] > 20,
                 self.adx[i] > 25,
                 self.ef[i] < self.es[i] and self.ef[i - 1] > self.es[i - 1],
                 c[0] > self.bbu[i] or (c[0] < self.bbm[i] and c[1] > self.bbm[i - 1]),
                 self.v[i] > self.v[i - 1] > self.v[i - 2], engulf or hammer or morning, wedge]
        score = 1 if sum(map(bool, conds)) >= 3 else 0
        score += pin + inside + hammer + engulf + morning
        score += 2 * (self._trend_breakout(H, C) + self._three_white(O, H, L, C) + (C[0] > H[1:11].max())
                      + self._price_formation(H, L) + self._support_holding(O, L, C))
        score += 2 * (self._double_bottom(H, L, C) + self._inv_hs(H, L, C) + self._asc_triangle(H, L, C)
                      + self._cup_handle(H, L, C) + wedge)
        bid = c[0]
        sr = any(abs(bid - r) < 0.01 * bid and bid < r for r in self.res) + any(s * 0.99 < bid < s for s in self.sup)
        return score + 3 * sr


# =====================================================================================
# Chạy
# =====================================================================================
def evaluate(df, strat, spread, capital, start="2021-01-01", max_positions=1, sub=None):
    eng = Engine(df, spread=spread, capital=capital, max_positions=max_positions, sub=sub)
    cur, tr = eng.run(strat, start=start)
    s = stats(cur, tr, capital)
    s["Bị từ chối"] = eng.rejected
    years = range(pd.Timestamp(start).year, df.index[-1].year + 1)
    yr = {}
    for y in years:
        cy, ty = eng.run(strat, start=f"{y}-01-01", end=f"{y}-12-31 23:59")
        yr[y] = stats(cy, ty, capital)
    return s, yr, tr


def bench_years(df, start="2021-01-01"):
    c = df["close"]
    return {y: c[str(y)].iloc[-1] / c[str(y)].iloc[0] - 1 for y in range(pd.Timestamp(start).year, c.index[-1].year + 1)}


def variants(only, spread):
    out = []
    if "fvg" in only:
        d = load_gold("15m")
        m5 = load_gold("5m")        # thứ tự giá trong nến M15 khớp lệnh limit
        out += [("fvg", "FvgGold · gốc (lot 0.01, lệnh chờ không bao giờ huỷ)", d, FvgGold(d), 1, m5),
                ("fvg", "FvgGold · rủi ro 0.5%/lệnh (tối đa 0.1 lot)", d, FvgGold(d, fixed_oz=0), 1, m5),
                ("fvg", "FvgGold · 0.5% + huỷ lệnh chờ khi FVG quá 20 nến", d, FvgGold(d, fixed_oz=0, cancel_stale=True), 1, m5)]
    if "gte" in only:
        d = resample(load_gold("15m"), "1h")
        out += [("gte", "GoldTraderEA · gốc (lot 0.1, chỉ mua do lỗi)", d, GoldTraderEA(d), 1),
                ("gte", "GoldTraderEA · sửa lỗi (cho bán, S/R cập nhật)", d, GoldTraderEA(d, fix=True), 1)]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="fvg,gte,bak,pdh,ai,alpha")
    ap.add_argument("--spread", type=float, default=0.30)
    ap.add_argument("--capital", type=float, default=10_000)
    args = ap.parse_args()
    only = args.only.split(",")
    out_dir = HERE / "results" / "gold"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, yrows = [], {}
    import gold_repos_m5 as m5                      # các EA khung M5 / M1
    vs = variants(only, args.spread) + m5.variants(only, args.spread)
    bench = None
    for v in vs:
        key, name, df, strat, maxpos = v[:5]
        sub = v[5] if len(v) > 5 else None
        cap = getattr(strat, "capital", args.capital)
        s, yr, tr = evaluate(df, strat, args.spread, cap, start=getattr(strat, "start", "2021-01-01"),
                             max_positions=maxpos, sub=sub)
        if bench is None and len(yr) > 1:
            bench = bench_years(df)
        rows.append({"Repo": key, "Biến thể": name, "Vốn cuối $": round(cap * (1 + s["ROI"])),
                     "ROI": s["ROI"], "MDD": s["MDD"], "Lệnh": s["Lệnh"], "Thắng": s["Thắng"],
                     "R TB": s["R TB"], "PF": s["PF"], "Bị từ chối": s["Bị từ chối"],
                     "Năm lời": f"{sum(v['ROI'] > 0 for v in yr.values())}/{len(yr)}",
                     "Năm tệ nhất": min(v["ROI"] for v in yr.values())})
        yrows[name] = {y: v["ROI"] for y, v in yr.items()}
        tr.to_csv(out_dir / f"trades_{key}_{len(rows)}.csv", index=False)
        print(f"  {name}: {s['Lệnh']} lệnh, ROI {s['ROI']:+.1%}", flush=True)

    def pct(v):
        return "-" if pd.isna(v) else f"{v:+.1%}"

    t = pd.DataFrame(rows)
    show = t.copy()
    for col in ("ROI", "MDD", "Năm tệ nhất"):
        show[col] = show[col].map(pct)
    show["Thắng"] = show["Thắng"].map(lambda v: "-" if pd.isna(v) else f"{v:.0%}")
    show["R TB"] = show["R TB"].map(lambda v: "-" if pd.isna(v) else f"{v:+.2f}")
    show["PF"] = show["PF"].map(lambda v: "-" if pd.isna(v) else f"{v:.2f}")
    yt = pd.DataFrame(yrows).T
    if bench:
        yt.loc["Giữ vàng"] = pd.Series(bench)
    yt = yt.apply(lambda col: col.map(pct))
    print("\n" + show.to_string(index=False))
    print("\n" + yt.to_string())
    from run import to_md
    with open(out_dir / f"gold_repos_spread{args.spread}.md", "w") as f:
        f.write(f"# Backtest repo EA vàng · vốn ${args.capital:,.0f} · spread ${args.spread}\n\n"
                "Dữ liệu PAXGUSDT (Binance), 2021 → nay. ROI/MDD: chạy liền. Năm lời / năm tệ nhất: chạy riêng từng năm.\n\n")
        f.write(to_md(show.set_index("Repo")) + "\n\n## ROI từng năm (chạy riêng)\n\n" + to_md(yt) + "\n")
    print(f"\nĐã lưu {out_dir / f'gold_repos_spread{args.spread}.md'}")


if __name__ == "__main__":
    main()
