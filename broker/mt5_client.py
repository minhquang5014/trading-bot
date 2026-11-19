"""Kết nối MetaTrader 5 (PF Markets hoặc broker MT5 bất kỳ) qua thư viện chính thức `MetaTrader5`.

Yêu cầu: Windows + terminal MT5 của broker đã cài (thư viện điều khiển terminal, không gọi API web).
Thông tin đăng nhập lấy từ biến môi trường (file .env, không commit):

    MT5_LOGIN=12345678
    MT5_PASSWORD=...
    MT5_SERVER=PFMarkets-Demo        # đúng tên server hiện trong MT5 (File → Login to Trade Account)
    MT5_PATH=C:\\Program Files\\PF Markets MT5 Terminal\\terminal64.exe   # tuỳ chọn

Thời gian nến/tick của MT5 là **giờ server** của broker. Lớp này đổi hết sang UTC (offset đo từ tick mới nhất).

An toàn: mọi lệnh đặt/đóng bị từ chối nếu tài khoản không phải demo, trừ khi đặt ALLOW_LIVE_TRADING=true.
"""
import math
import os
import time
from dataclasses import dataclass

import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:          # Linux / macOS: không có thư viện MT5
    mt5 = None

TIMEFRAMES = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
              "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1"}
TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440, "W1": 10080}
GOLD_CANDIDATES = ("XAUUSD", "XAUUSD.", "XAUUSDm", "XAUUSD.r", "XAUUSD.raw", "XAUUSD+", "GOLD", "XAUUSD.pro")


class MT5Error(RuntimeError):
    pass


@dataclass
class OrderResult:
    ok: bool
    message: str
    ticket: int = 0
    price: float = 0.0
    volume: float = 0.0


class MT5Client:
    def __init__(self, login=None, password=None, server=None, path=None, allow_live=None):
        if mt5 is None:
            raise MT5Error("Chưa cài thư viện MetaTrader5 (chỉ có trên Windows): pip install MetaTrader5")
        self.login = int(login or os.environ.get("MT5_LOGIN") or 0) or None
        self.password = password or os.environ.get("MT5_PASSWORD")
        self.server = server or os.environ.get("MT5_SERVER")
        self.path = path or os.environ.get("MT5_PATH") or None
        if allow_live is None:
            allow_live = os.environ.get("ALLOW_LIVE_TRADING", "false").lower() in ("1", "true", "yes")
        self.allow_live = allow_live
        self._offset = None          # giờ server - UTC (giây)
        self._offset_at = 0.0

    # ----- kết nối -----
    def connect(self):
        kw = {}
        if self.path:
            kw["path"] = self.path
        if self.login:
            kw.update(login=self.login, password=self.password, server=self.server)
        if not mt5.initialize(**kw):
            err = mt5.last_error()
            mt5.shutdown()
            raise MT5Error(f"Không kết nối được MT5: {err}. Kiểm tra terminal đã mở, login/password/server đúng, "
                           f"và Tools → Options → Expert Advisors → 'Allow algorithmic trading' đã bật.")
        return self.account()

    def ensure(self):
        """Gọi trước mỗi thao tác: terminal có thể mất kết nối (máy ngủ, đổi mạng)."""
        if mt5.terminal_info() is None or mt5.account_info() is None:
            self.connect()

    def shutdown(self):
        mt5.shutdown()

    # ----- thông tin -----
    def account(self):
        a = mt5.account_info()
        if a is None:
            raise MT5Error(f"account_info lỗi: {mt5.last_error()}")
        return a

    def is_demo(self):
        return self.account().trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO

    def resolve_symbol(self, name="XAUUSD"):
        """Tên mã vàng mỗi broker một kiểu (XAUUSD, XAUUSD.r, GOLD...): thử tên được đưa trước, rồi các biến thể."""
        names = [name] + [c for c in GOLD_CANDIDATES if c != name] if name.upper().startswith(("XAU", "GOLD")) else [name]
        for n in names:
            info = mt5.symbol_info(n)
            if info is not None:
                if not info.visible:
                    mt5.symbol_select(n, True)
                return n
        found = [s.name for s in (mt5.symbols_get(f"*{name[:3]}*") or [])][:10]
        raise MT5Error(f"Không tìm thấy mã {name}. Mã gần giống trên broker: {', '.join(found) or 'không có'}")

    def symbol_info(self, symbol):
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"Không có mã {symbol}")
        return info

    def tick(self, symbol):
        t = mt5.symbol_info_tick(symbol)
        if t is None or t.time == 0:
            raise MT5Error(f"Chưa có giá cho {symbol} (thị trường đóng cửa?)")
        return t

    def server_offset(self, symbol):
        """Chênh lệch giờ server so với UTC, làm tròn 30 phút. Đo lại mỗi giờ (đổi giờ mùa hè)."""
        if self._offset is None or time.time() - self._offset_at > 3600:
            t = self.tick(symbol)
            raw = t.time - time.time()
            if abs(raw) < 15 * 3600:              # tick cũ (cuối tuần) sẽ cho số vô nghĩa
                self._offset = round(raw / 1800) * 1800
                self._offset_at = time.time()
            elif self._offset is None:
                self._offset = 0
        return self._offset

    def rates(self, symbol, timeframe="M15", count=300, closed_only=True):
        """Nến OHLC, index là giờ UTC. closed_only bỏ nến đang chạy."""
        tf = getattr(mt5, TIMEFRAMES[timeframe.upper()])
        start = 1 if closed_only else 0
        arr = mt5.copy_rates_from_pos(symbol, tf, start, count)
        if arr is None or len(arr) == 0:
            raise MT5Error(f"Không lấy được nến {symbol} {timeframe}: {mt5.last_error()}")
        df = pd.DataFrame(arr)
        off = self.server_offset(symbol)
        df.index = pd.to_datetime(df["time"] - off, unit="s")
        df.index.name = "time"
        return df.rename(columns={"tick_volume": "volume"})[["open", "high", "low", "close", "volume"]]

    def deals(self, since_utc, until_utc=None):
        """Lịch sử deal (khớp lệnh) trong khoảng giờ UTC, gồm cả lệnh bị SL/TP đóng và lệnh đặt tay trong MT5.
        MT5 lọc theo giờ server nên nới khoảng thêm 1 ngày mỗi bên; người gọi tự lọc theo ticket."""
        start = pd.Timestamp(since_utc) - pd.Timedelta(days=1)
        end = pd.Timestamp(until_utc or pd.Timestamp.now("UTC").tz_localize(None)) + pd.Timedelta(days=1)
        ds = mt5.history_deals_get(start.to_pydatetime(), end.to_pydatetime())
        if ds is None:
            raise MT5Error(f"history_deals_get lỗi: {mt5.last_error()}")
        return sorted(ds, key=lambda d: d.ticket)

    def time_utc(self, server_ts, symbol):
        """Đổi giờ server (giây) của deal/tick sang UTC."""
        return pd.to_datetime(server_ts - self.server_offset(symbol), unit="s")

    def positions(self, symbol=None):
        ps = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        return list(ps or [])

    # ----- khối lượng theo rủi ro -----
    def lot_for_risk(self, symbol, side, entry, sl, risk_money):
        """Số lot sao cho chạm SL thì lỗ ≈ risk_money (làm tròn xuống theo bước lot của broker)."""
        info = self.symbol_info(symbol)
        typ = mt5.ORDER_TYPE_BUY if side > 0 else mt5.ORDER_TYPE_SELL
        loss1 = mt5.order_calc_profit(typ, symbol, 1.0, entry, sl)
        if loss1 is None or loss1 >= 0:
            loss1 = -abs(entry - sl) * info.trade_contract_size
        lots = risk_money / abs(loss1)
        step = info.volume_step or 0.01
        lots = math.floor(lots / step + 1e-9) * step
        return round(lots, 8) if lots >= info.volume_min else 0.0

    # ----- đặt / đóng lệnh -----
    def _guard(self):
        if not self.is_demo() and not self.allow_live:
            raise MT5Error("Tài khoản THẬT: đặt lệnh bị khoá. Đặt ALLOW_LIVE_TRADING=true trong .env nếu chắc chắn.")

    @staticmethod
    def _filling(info):
        fm = info.filling_mode            # bit 1 = FOK, bit 2 = IOC
        if fm & 1:
            return mt5.ORDER_FILLING_FOK
        if fm & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _round_price(self, info, p):
        return None if p is None else round(p, info.digits)

    def market_order(self, symbol, side, volume, sl=None, tp=None, comment="tg-bot", magic=50140, deviation=30):
        self._guard()
        info = self.symbol_info(symbol)
        volume = round(math.floor(volume / info.volume_step + 1e-9) * info.volume_step, 8)
        if volume < info.volume_min or volume > info.volume_max:
            return OrderResult(False, f"Khối lượng {volume} ngoài khoảng {info.volume_min}–{info.volume_max}")
        t = self.tick(symbol)
        price = t.ask if side > 0 else t.bid
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if side > 0 else mt5.ORDER_TYPE_SELL,
            "price": price, "deviation": deviation, "magic": magic, "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(info),
        }
        if sl:
            req["sl"] = self._round_price(info, sl)
        if tp:
            req["tp"] = self._round_price(info, tp)
        return self._send(req)

    def close_position(self, ticket, volume=None, deviation=30):
        """Đóng lệnh; volume < khối lượng lệnh = đóng một phần."""
        self._guard()
        ps = mt5.positions_get(ticket=int(ticket))
        if not ps:
            return OrderResult(False, f"Không có lệnh #{ticket}")
        p = ps[0]
        info = self.symbol_info(p.symbol)
        t = self.tick(p.symbol)
        buy = p.type == mt5.POSITION_TYPE_BUY
        vol = p.volume
        if volume is not None:
            vol = round(math.floor(min(volume, p.volume) / info.volume_step + 1e-9) * info.volume_step, 8)
            if vol < info.volume_min:
                return OrderResult(False, f"Khối lượng đóng {vol} nhỏ hơn mức tối thiểu {info.volume_min}")
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": vol, "position": p.ticket,
            "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
            "price": t.bid if buy else t.ask, "deviation": deviation, "magic": p.magic, "comment": "tg-close",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(info),
        }
        return self._send(req)

    def modify_sltp(self, ticket, sl=None, tp=None):
        self._guard()
        ps = mt5.positions_get(ticket=int(ticket))
        if not ps:
            return OrderResult(False, f"Không có lệnh #{ticket}")
        p = ps[0]
        info = self.symbol_info(p.symbol)
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol, "position": p.ticket,
               "sl": self._round_price(info, sl if sl is not None else p.sl),
               "tp": self._round_price(info, tp if tp is not None else p.tp)}
        return self._send(req)

    def _send(self, req):
        check = mt5.order_check(req)
        if check is None or check.retcode not in (0, mt5.TRADE_RETCODE_DONE):
            msg = check.comment if check else mt5.last_error()
            return OrderResult(False, f"Kiểm tra lệnh thất bại: {msg}")
        r = mt5.order_send(req)
        if r is None:
            return OrderResult(False, f"order_send lỗi: {mt5.last_error()}")
        if r.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            return OrderResult(False, f"Broker từ chối ({r.retcode}): {r.comment}")
        return OrderResult(True, "OK", ticket=r.order, price=r.price, volume=r.volume)
