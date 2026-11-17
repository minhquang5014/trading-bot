"""Logic vào/ra của từng repo, viết lại bằng pandas để chạy trên cùng dữ liệu nến ngày.

Mỗi hàm nhận `P` (bảng rộng open/high/low/close/volume, hàng = ngày, cột = mã) và `bench` (giá đóng cửa
BTC / SPY), trả về một Spec. Tín hiệu tính ở giá đóng cửa ngày t, lệnh khớp ở giá mở cửa ngày t+1,
giống freqtrade và các EA MT5 (chỉ xét khi có nến mới).

Code gốc nằm trong clone_repo/repos/ (chỉ đọc, không chạy). Những chỗ phải đổi khi chuyển sang nến ngày
được ghi trong `notes` của từng Spec.
"""
from dataclasses import dataclass, field

import pandas as pd

import indicators as ta


@dataclass
class Spec:
    key: str
    name: str
    repo: str
    entry: pd.DataFrame                  # True = mua ở giá mở cửa phiên sau
    exit: pd.DataFrame = None            # True = bán ở giá mở cửa phiên sau
    stop_pct: float = None               # cắt lỗ cố định, vd 0.2 = -20%
    stop_atr: float = None               # cắt lỗ = giá vào - x * ATR (ATR của nến tín hiệu)
    stop_level: pd.DataFrame = None      # cắt lỗ ở một mức giá tuyệt đối lấy từ nến tín hiệu (vd EMA200)
    tp_pct: float = None                 # chốt lời cố định
    tp_atr: float = None                 # chốt lời = giá vào + x * ATR
    tp_r: float = None                   # chốt lời = giá vào + x * khoảng cách cắt lỗ ban đầu
    roi: dict = None                     # bảng minimal_roi của freqtrade {phút: lợi nhuận tối thiểu}
    trail_pct: float = None              # trailing kiểu freqtrade: stop = đỉnh cao nhất * (1 - trail_pct)
    trail_offset: float = 0.0            # ...chỉ bật khi lời ≥ trail_offset
    trail_atr_start: float = None        # trailing kiểu MT5: bật khi lời ≥ x ATR
    trail_atr_dist: float = None         # ...stop = đỉnh cao nhất - y ATR
    entry_trigger: pd.DataFrame = None   # lệnh chờ mua (buy-stop): chỉ khớp nếu phiên sau chạm mức này
    atr: pd.DataFrame = None
    notes: list = field(default_factory=list)


def _common(P):
    return P["open"], P["high"], P["low"], P["close"], P["volume"]


# ---------------------------------------------------------------- Neyt/Paul-Tudor-Jones-200-EMA-strategy
def ptj_200ema(P, bench):
    o, h, l, c, v = _common(P)
    e200 = ta.ema(c, 200)
    return Spec("ptj_200ema", "PTJ: giá cắt EMA200", "Neyt/Paul-Tudor-Jones-200-EMA-strategy",
                entry=ta.crossed_above(c, e200), exit=ta.crossed_below(c, e200),
                notes=["Pine script gốc: mua khi close cắt lên EMA200, bán khi cắt xuống. Không có cắt lỗ."])


# ---------------------------------------------------------------- MayankBharati/Quant-Trading-Project
def golden_cross(P, bench):
    o, h, l, c, v = _common(P)
    up = ta.ema(c, 50) > ta.ema(c, 200)
    return Spec("golden_cross", "Golden Cross EMA50/200", "MayankBharati/Quant-Trading-Project",
                entry=up, exit=~up & ta.ema(c, 200).notna(),
                notes=["Notebook giữ lệnh khi EMA50 > EMA200 (trạng thái, không phải điểm cắt), thoát khi Death Cross.",
                       "Bản gốc chia đều vốn cho mọi mã cùng lúc; ở đây dùng chung cách chia 10 vị thế."])


# ---------------------------------------------------------------- paulcpk/freqtrade-strategies-that-work
def pc_double_ema(P, bench):
    o, h, l, c, v = _common(P)
    e9, e21, e200 = ta.ema(c, 9), ta.ema(c, 21), ta.ema(c, 200)
    return Spec("pc_double_ema", "EMA9/21 + low > EMA200", "paulcpk/freqtrade-strategies-that-work",
                entry=ta.crossed_above(e9, e21) & (l > e200) & (v > 0),
                exit=ta.crossed_below(e9, e21) | (l < e200), stop_pct=0.20,
                notes=["DoubleEMACrossoverWithTrend. Gốc chạy nến 1h."])


def pc_ema800(P, bench):
    o, h, l, c, v = _common(P)
    e800 = ta.ema(c, 800)
    return Spec("pc_ema800", "Giá cắt EMA800 (ngưỡng 1%)", "paulcpk/freqtrade-strategies-that-work",
                entry=ta.crossed_above(c, e800) & (v > 0), exit=ta.crossed_below(c, e800 * 0.99),
                stop_pct=0.15, trail_pct=0.15,
                notes=["EMAPriceCrossoverWithThreshold. Gốc nến 1h (EMA800 ≈ 33 ngày); giữ nguyên 800 nến ngày "
                       "nên chỉ mã có trên 800 ngày dữ liệu mới có tín hiệu.",
                       "trailing_stop=True không có positive: stop bám đỉnh cao nhất -15%."])


def pc_macd(P, bench):
    o, h, l, c, v = _common(P)
    m, s, _ = ta.macd(c)
    e100 = ta.ema(c, 100)
    return Spec("pc_macd", "MACD cắt lên dưới 0 + low > EMA100", "paulcpk/freqtrade-strategies-that-work",
                entry=(m < 0) & ta.crossed_above(m, s) & (l > e100) & (v > 0),
                exit=ta.crossed_below(m, 0) | (l < e100), stop_pct=0.20,
                notes=["MACDCrossoverWithTrend. Gốc nến 1h."])


def pc_rsi(P, bench):
    o, h, l, c, v = _common(P)
    r, e100 = ta.rsi(c, 4), ta.ema(c, 100)
    return Spec("pc_rsi", "RSI(4) bật lên từ 15 + low > EMA100", "paulcpk/freqtrade-strategies-that-work",
                entry=ta.crossed_above(r, 15) & (l > e100) & (v > 0),
                exit=ta.crossed_above(r, 85) | (l < e100), stop_pct=0.10, trail_pct=0.10,
                notes=["RSIDirectionalWithTrend. Gốc nến 1h, trailing_stop=True (bám đỉnh -10%)."])


def pc_rsi_slow(P, bench):
    o, h, l, c, v = _common(P)
    r, e600 = ta.rsi(c, 10), ta.ema(c, 600)
    return Spec("pc_rsi_slow", "RSI(10) bật lên từ 25 + low > EMA600", "paulcpk/freqtrade-strategies-that-work",
                entry=ta.crossed_above(r, 25) & (l > e600) & (v > 0),
                exit=ta.crossed_below(r, 20) | (l < e600), stop_pct=0.20, trail_pct=0.20,
                notes=["RSIDirectionalWithTrendSlow. Gốc nến 1h."])


# ---------------------------------------------------------------- MT5-Automation-Labs/mt5-ema-pullback-ea
def mt5_pullback(P, bench):
    o, h, l, c, v = _common(P)
    f, s, a = ta.ema(c, 21), ta.ema(c, 100), ta.atr(h, l, c, 14)
    slope = (s - s.shift(5)) / a
    entry = (f > s) & (slope >= 0.1) & (l <= f + 0.2 * a) & (l > s) & (c > f) & (c > o)
    return Spec("mt5_pullback", "MT5: hồi về EMA21 trong xu hướng EMA100", "MT5-Automation-Labs/mt5-ema-pullback-ea",
                entry=entry, stop_atr=1.5, tp_atr=3.0, trail_atr_start=1.0, trail_atr_dist=1.0, atr=a,
                notes=["Signal.mqh: EMA21 > EMA100, EMA100 dốc lên ≥ 0,1 ATR / 5 nến, low chạm EMA21 (±0,2 ATR) mà "
                       "không thủng EMA100, nến xanh đóng trên EMA21.",
                       "SL 1,5 ATR, TP 3 ATR, trailing 1 ATR sau khi lời 1 ATR (ATR lấy ở nến tín hiệu).",
                       "Gốc nến M15, chia vốn theo 1% rủi ro; ở đây chia đều vốn như các chiến lược khác. Bỏ lọc phiên/spread."])


# ---------------------------------------------------------------- srpo93/XAUUSD-bot
def xau_pullback(P, bench):
    o, h, l, c, v = _common(P)
    e200, f, s = ta.ema(c, 200), ta.ema(c, 21), ta.ema(c, 50)
    r, a = ta.rsi(c, 14), ta.atr(h, l, c, 14)
    trend = (c > e200) & (e200 > e200.shift(5)) & (f > s)
    pull = ((l <= f) & (r <= 45)).astype(float).rolling(5, min_periods=1).max().astype(bool)
    trigger = (c > o) & (c > f) & (r >= 50)
    near = (c - f).abs() <= 1.5 * a
    return Spec("xau_pullback", "XAUUSD: hồi về EMA21 + RSI reset", "srpo93/XAUUSD-bot",
                entry=trend & pull & trigger & near, stop_atr=1.5, tp_r=2.0, atr=a,
                notes=["Xu hướng: close > EMA200 và EMA200 dốc lên 5 nến (gốc ở khung H1, ở đây cùng khung ngày), EMA21 > EMA50.",
                       "Hồi: trong 5 nến gần nhất có nến low ≤ EMA21 với RSI ≤ 45. Vào: nến xanh đóng trên EMA21, RSI ≥ 50, "
                       "cách EMA21 không quá 1,5 ATR.",
                       "SL 1,5 ATR, TP 2R, không quản lý lệnh sau khi vào (preset 01-baseline). Gốc nến M15 vàng, bỏ lọc phiên."])


# ---------------------------------------------------------------- freqtrade/freqtrade-strategies
def ft_adx_momentum(P, bench):
    o, h, l, c, v = _common(P)
    _, _, adx = ta.dmi(h, l, c, 14)
    pdi, mdi, _ = ta.dmi(h, l, c, 25)
    mom = c - c.shift(14)
    return Spec("ft_adx_momentum", "ADXMomentum", "freqtrade/freqtrade-strategies",
                entry=(adx > 25) & (mom > 0) & (pdi > 25) & (pdi > mdi),
                exit=(adx > 25) & (mom < 0) & (mdi > 25) & (pdi < mdi),
                stop_pct=0.25, roi={0: 0.01},
                notes=["berlinguyinca/ADXMomentum. minimal_roi {0: 1%}: chốt lời ngay khi lời 1%. Gốc nến 1h."])


def ft_trendrider(P, bench):
    o, h, l, c, v = _common(P)
    e9, e16, e50, e200 = ta.ema(c, 9), ta.ema(c, 16), ta.ema(c, 50), ta.ema(c, 200)
    r = ta.rsi(c, 16)
    pdi, mdi, adx = ta.dmi(h, l, c, 14)
    _, _, hist = ta.macd(c)
    vr = v / (v.ewm(span=20, adjust=False, min_periods=20).mean() + 1e-10)
    ob = ta.obv(c, v)
    ob_ema = ob.ewm(span=20, adjust=False, min_periods=20).mean()
    btc_rsi = ta.rsi(bench, 14).reindex(c.index)
    is_bull = (c > e200) & (e50 > e200)
    pullback = (l <= e16 * 1.02) & (c > e16) & (c > o)
    entry = (is_bull & pullback & (r > 30) & (r < 65) & (adx > 18) & (vr > 0.7) & (pdi > mdi) & (ob > ob_ema)
             & (v > 0) & (r < 70)).mul(btc_rsi > 35, axis=0)
    exit_ = ((r > 78)
             | (ta.crossed_below(e9, e16) & (hist < 0) & (r > 50))
             | ((c < e200 * 0.99) & (c.shift(1) >= e200.shift(1)))
             | ((c < e200 * 0.995) & (r > 72) & (hist < hist.shift(1))))
    return Spec("ft_trendrider", "TrendRider: hồi về EMA16 (LONG 1)", "freqtrade/freqtrade-strategies",
                entry=entry.astype(bool), exit=exit_, stop_pct=0.06, trail_pct=0.03, trail_offset=0.05,
                roi={0: 0.229, 124: 0.136, 290: 0.044, 764: 0},
                notes=["Chỉ lấy tín hiệu LONG 1 (trend pullback) với tham số hyperopt trong file. Bỏ chỉ số Fear & Greed; "
                       "RSI BTC 1h thay bằng RSI(14) BTC ngày.",
                       "Gốc nến 1h: bảng ROI tính theo phút nên trên nến ngày, từ ngày thứ 2 cứ có lời là chốt.",
                       "Protections (Cooldown 20 phút, StoplossGuard 60 phút) quá ngắn so với nến ngày nên bỏ."])


def ft_ema_skip_pump(P, bench):
    o, h, l, c, v = _common(P)
    e5, e12 = ta.ema(c, 5), ta.ema(c, 12)
    tp = (h + l + c) / 3
    mid, sd = tp.rolling(20).mean(), tp.rolling(20).std(ddof=0)
    lo_, hi_ = mid - 2 * sd, mid + 2 * sd
    mn, mx = c.rolling(12).min(), c.rolling(12).max()
    entry = (v < v.rolling(30).mean().shift(1) * 20) & (c < e5) & (c < e12) & (c == mn) & (c <= lo_)
    exit_ = (c > e5) & (c > e12) & (c >= mx) & (c >= hi_)
    return Spec("ft_ema_skip_pump", "EMASkipPump (mua đáy BB, tránh pump)", "freqtrade/freqtrade-strategies",
                entry=entry, exit=exit_, stop_pct=0.05, roi={0: 0.10},
                notes=["berlinguyinca/EMASkipPump. Kiểu bắt đáy (ngược với momentum), gốc nến 5m."])


# ---------------------------------------------------------------- jimtin/algorithmic_trading_bot
def jimtin_ema_cross(P, bench):
    o, h, l, c, v = _common(P)
    e50, e200 = ta.ema(c, 50), ta.ema(c, 200)
    pos = e50 > e200
    cross = (pos != pos.shift(1)) & e200.notna() & e200.shift(1).notna()
    green = c > o
    entry = cross & green
    return Spec("jimtin_ema_cross", "jimtin: EMA50/200 cắt + lệnh chờ ở đỉnh nến", "jimtin/algorithmic_trading_bot",
                entry=entry, entry_trigger=h.where(entry), stop_level=e200.where(entry), tp_r=1.0,
                notes=["det_trade: khi EMA50/200 cắt nhau (bất kể chiều) và nến xanh: đặt buy-stop ở high của nến, "
                       "SL = EMA200, TP = 1R. Lệnh chờ chỉ sống 1 nến (main.py huỷ lệnh chờ mỗi vòng).",
                       "Gốc chạy MT5 khung tuỳ chọn, chia vốn theo 1% rủi ro."])


# ---------------------------------------------------------------- jcarenza67/alpaca-ema-bot
def alpaca_ema(P, bench):
    o, h, l, c, v = _common(P)
    e9, e15 = ta.ema(c, 9), ta.ema(c, 15)
    r = ta.rsi(c, 14)
    pivot = (h + l + c) / 3
    entry = ta.crossed_above(e9, e15) & (r < 30) & (v > 1.5 * v.rolling(20).mean()) & (c > pivot)
    return Spec("alpaca_ema", "Alpaca: EMA9/15 cắt + RSI < 30 + KL đột biến", "jcarenza67/alpaca-ema-bot",
                entry=entry, tp_pct=0.10, stop_pct=0.20,
                notes=["ema_test.py: nến ngày, chốt lời +10%, cắt lỗ -20%. RSI < 30 cùng lúc EMA9 cắt lên EMA15 rất hiếm."])


# ---------------------------------------------------------------- a2gs/EMA_Binance_BOT
def a2gs_ema(P, bench):
    o, h, l, c, v = _common(P)
    f, s = ta.ema(c, 9), ta.ema(c, 21)
    return Spec("a2gs_ema", "a2gs: EMA nhanh/chậm cắt nhau (9/21)", "a2gs/EMA_Binance_BOT",
                entry=ta.crossed_above(f, s), exit=ta.crossed_below(f, s),
                notes=["bot.py chỉ gửi thông báo BUY/SELL khi EMA nhanh/chậm đổi vị trí; chu kỳ truyền qua dòng lệnh, "
                       "repo không có giá trị mặc định nên dùng 9/21. Không có cắt lỗ."])


ALL = [ptj_200ema, golden_cross, pc_double_ema, pc_ema800, pc_macd, pc_rsi, pc_rsi_slow, mt5_pullback,
       xau_pullback, ft_adx_momentum, ft_trendrider, ft_ema_skip_pump, jimtin_ema_cross, alpaca_ema, a2gs_ema]
