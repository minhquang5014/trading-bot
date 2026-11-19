"""Kết nối tài khoản MT5 qua MetaApi (metaapi.cloud): MT5 chạy trên cloud của MetaApi, bot chỉ gọi API qua internet,
nên bot chạy được trên Linux / macOS / cloud miễn phí, không cần Windows hay terminal MT5.

Cùng giao diện với broker.mt5_client.MT5Client (bot.py không cần biết đang dùng loại nào). Biến môi trường (.env):

    BROKER=metaapi
    METAAPI_TOKEN=...           # metaapi.cloud → API access → token
    METAAPI_ACCOUNT_ID=...      # metaapi.cloud → Accounts → id của tài khoản MT5 đã thêm (dạng uuid)
    METAAPI_REGION=             # tuỳ chọn, vd london / new-york (để trống = theo tài khoản)

SDK của MetaApi là async; lớp này chạy một event loop riêng trong luồng nền và gọi đồng bộ, hợp với vòng lặp của bot.
Thời gian MetaApi trả về là UTC nên không cần đổi giờ server.

An toàn: giống MT5Client, mọi lệnh bị từ chối nếu tài khoản không phải demo, trừ khi ALLOW_LIVE_TRADING=true.
"""
import asyncio
import math
import os
import threading
import time
from types import SimpleNamespace as NS

import pandas as pd

from broker.mt5_client import GOLD_CANDIDATES, TF_MINUTES, MT5Error, OrderResult

TF_METAAPI = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "4h", "D1": "1d", "W1": "1w"}
OK_CODES = {"ERR_NO_ERROR", "TRADE_RETCODE_PLACED", "TRADE_RETCODE_DONE", "TRADE_RETCODE_DONE_PARTIAL",
            "TRADE_RETCODE_NO_CHANGES"}
DEAL_ENTRY = {"DEAL_ENTRY_IN": 0, "DEAL_ENTRY_OUT": 1, "DEAL_ENTRY_INOUT": 2, "DEAL_ENTRY_OUT_BY": 3}
DEAL_REASON = {"DEAL_REASON_CLIENT": 0, "DEAL_REASON_MOBILE": 1, "DEAL_REASON_WEB": 2, "DEAL_REASON_EXPERT": 3,
               "DEAL_REASON_SL": 4, "DEAL_REASON_TP": 5, "DEAL_REASON_SO": 6, "DEAL_REASON_ROLLOVER": 7,
               "DEAL_REASON_VMARGIN": 8, "DEAL_REASON_SPLIT": 9}
CALL_TIMEOUT = 60


def _utc(ts):
    """datetime (có hoặc không có múi giờ) → pd.Timestamp UTC không múi giờ."""
    t = pd.Timestamp(ts)
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t


class MetaApiClient:
    def __init__(self, token=None, account_id=None, region=None, allow_live=None):
        try:
            from metaapi_cloud_sdk import MetaApi  # noqa: F401
        except ImportError as e:
            raise MT5Error("Chưa cài SDK MetaApi: pip install metaapi-cloud-sdk") from e
        self.token = token or os.environ.get("METAAPI_TOKEN")
        self.account_id = account_id or os.environ.get("METAAPI_ACCOUNT_ID")
        self.region = region or os.environ.get("METAAPI_REGION") or None
        if not self.token or not self.account_id:
            raise MT5Error("Thiếu METAAPI_TOKEN hoặc METAAPI_ACCOUNT_ID trong .env")
        if allow_live is None:
            allow_live = os.environ.get("ALLOW_LIVE_TRADING", "false").lower() in ("1", "true", "yes")
        self.allow_live = allow_live
        self.api = self._acct = self.conn = None
        self._spec = {}           # symbol -> NS(digits, trade_contract_size, volume_step, volume_min, volume_max)
        self._candles = {}        # (symbol, tf) -> DataFrame đã tải (bộ nhớ đệm, chỉ tải phần mới)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="metaapi-loop", daemon=True)
        self._thread.start()

    # ----- chạy coroutine trên loop nền -----
    def _run(self, coro, timeout=CALL_TIMEOUT):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # ----- kết nối -----
    async def _connect(self):
        from metaapi_cloud_sdk import MetaApi
        if self.api is None:
            opts = {"region": self.region} if self.region else {}
            self.api = MetaApi(self.token, opts)
        self._acct = await self.api.metatrader_account_api.get_account(self.account_id)
        if self._acct.state not in ("DEPLOYING", "DEPLOYED"):
            await self._acct.deploy()       # MetaApi bật terminal trên cloud (có thể mất 1–2 phút)
        await self._acct.wait_connected(timeout_in_seconds=300)
        self.conn = self._acct.get_rpc_connection()
        await self.conn.connect()
        await self.conn.wait_synchronized(timeout_in_seconds=300)

    def connect(self):
        try:
            self._run(self._connect(), timeout=660)
        except Exception as e:          # noqa: BLE001
            raise MT5Error(f"Không kết nối được MetaApi: {e}. Kiểm tra token, account id, và trạng thái tài khoản "
                           f"trên metaapi.cloud (login/server/mật khẩu master đúng chưa).") from e
        return self.account_info_ns()

    def ensure(self):
        if self.conn is None or self._acct is None or self._acct.connection_status != "CONNECTED":
            self.connect()

    def shutdown(self):
        async def _close():
            if self.conn is not None:
                await self.conn.close()
            if self.api is not None:
                self.api.close()
        try:
            self._run(_close(), timeout=30)
        except Exception:               # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ----- thông tin -----
    def _info(self):
        return self._run(self.conn.get_account_information())

    def account_info_ns(self, info=None):
        a = info or self._info()
        login = a.get("login") or 0
        return NS(login=login, server=a.get("server", ""), name=a.get("name", ""), currency=a.get("currency", ""),
                  balance=a.get("balance", 0.0), equity=a.get("equity", 0.0),
                  profit=a.get("equity", 0.0) - a.get("balance", 0.0) - a.get("credit", 0.0),
                  margin=a.get("margin", 0.0), margin_free=a.get("freeMargin", 0.0),
                  margin_level=a.get("marginLevel") or 0.0, leverage=int(a.get("leverage") or 0),
                  trade_mode_str=a.get("type", ""))

    def account(self):
        return self.account_info_ns()

    def is_demo(self):
        return self._info().get("type") == "ACCOUNT_TRADE_MODE_DEMO"

    def resolve_symbol(self, name="XAUUSD"):
        symbols = set(self._run(self.conn.get_symbols()) or [])
        names = [name] + [c for c in GOLD_CANDIDATES if c != name] if name.upper().startswith(("XAU", "GOLD")) else [name]
        for n in names:
            if n in symbols:
                return n
        found = sorted(s for s in symbols if name[:3].upper() in s.upper())[:10]
        raise MT5Error(f"Không tìm thấy mã {name}. Mã gần giống trên broker: {', '.join(found) or 'không có'}")

    def symbol_info(self, symbol):
        if symbol not in self._spec:
            s = self._run(self.conn.get_symbol_specification(symbol))
            if not s:
                raise MT5Error(f"Không có mã {symbol}")
            tick = s.get("tickSize") or 0.01
            digits = s.get("digits")
            if digits is None:
                digits = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
            self._spec[symbol] = NS(digits=int(digits), trade_contract_size=s.get("contractSize") or 100.0,
                                    volume_step=s.get("volumeStep") or 0.01, volume_min=s.get("minVolume") or 0.01,
                                    volume_max=s.get("maxVolume") or 100.0)
        return self._spec[symbol]

    def tick(self, symbol):
        p = self._run(self.conn.get_symbol_price(symbol))
        if not p or not p.get("bid"):
            raise MT5Error(f"Chưa có giá cho {symbol} (thị trường đóng cửa?)")
        return NS(bid=p["bid"], ask=p["ask"], time=int(_utc(p["time"]).timestamp()))

    def server_offset(self, symbol):
        return 0                       # MetaApi trả giờ UTC

    def time_utc(self, ts, symbol):
        return pd.to_datetime(ts, unit="s")

    # ----- nến -----
    async def _fetch(self, symbol, tf, start, limit):
        return await self._acct.get_historical_candles(symbol, tf, start, limit)

    def _page(self, symbol, tf, start=None, limit=1000):
        rows = self._run(self._fetch(symbol, tf, start, limit)) or []
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame({"time": [_utc(r["time"]) for r in rows], "open": [r["open"] for r in rows],
                           "high": [r["high"] for r in rows], "low": [r["low"] for r in rows],
                           "close": [r["close"] for r in rows], "volume": [r.get("tickVolume", 0) for r in rows]})
        return df.set_index("time").sort_index()

    def rates(self, symbol, timeframe="M15", count=300, closed_only=True):
        """Nến OHLC (index UTC). MetaApi trả tối đa 1000 nến mỗi lần nên lấy lùi nhiều trang; lần sau chỉ tải phần mới."""
        tf_key = timeframe.upper()
        tf = TF_METAAPI[tf_key]
        step = pd.Timedelta(minutes=TF_MINUTES[tf_key])
        key = (symbol, tf)
        cached = self._candles.get(key)
        newest = self._page(symbol, tf)
        if cached is not None and len(cached) and len(newest) and newest.index[0] <= cached.index[-1]:
            df = pd.concat([cached, newest])
        else:
            df = newest
        # lấy lùi tới khi đủ số nến cần (có dư cho nến đang chạy)
        guard = 0
        while len(df) < count + 1 and guard < 50:
            guard += 1
            # phải có múi giờ: SDK gọi astimezone(), datetime không múi giờ bị hiểu là giờ máy (VN = UTC+7)
            older = self._page(symbol, tf, start=(df.index[0] - pd.Timedelta(seconds=1)).tz_localize("UTC").to_pydatetime(),
                               limit=min(1000, count + 1 - len(df)))
            older = older[older.index < df.index[0]]
            if older.empty:
                break
            df = pd.concat([older, df])
        df = df[~df.index.duplicated(keep="last")].sort_index()
        self._candles[key] = df.iloc[-max(count + 1, 2000):]
        if df.empty:
            raise MT5Error(f"Không lấy được nến {symbol} {timeframe}")
        if closed_only and df.index[-1] + step > pd.Timestamp.now("UTC").tz_localize(None):
            df = df.iloc[:-1]
        return df.iloc[-count:][["open", "high", "low", "close", "volume"]]

    # ----- lệnh / lịch sử -----
    def positions(self, symbol=None):
        out = []
        for p in self._run(self.conn.get_positions()) or []:
            if symbol and p.get("symbol") != symbol:
                continue
            out.append(NS(ticket=int(p["id"]), type=0 if p.get("type") == "POSITION_TYPE_BUY" else 1,
                          symbol=p.get("symbol"), volume=p.get("volume", 0.0), price_open=p.get("openPrice", 0.0),
                          sl=p.get("stopLoss") or 0.0, tp=p.get("takeProfit") or 0.0,
                          profit=p.get("unrealizedProfit", p.get("profit", 0.0)), magic=p.get("magic") or 0,
                          comment=p.get("comment") or ""))
        return out

    def deals(self, since_utc, until_utc=None):
        start = (pd.Timestamp(since_utc) - pd.Timedelta(days=1)).tz_localize("UTC").to_pydatetime()
        end = (pd.Timestamp(until_utc or pd.Timestamp.now("UTC").tz_localize(None))
               + pd.Timedelta(days=1)).tz_localize("UTC").to_pydatetime()
        out, offset = [], 0
        while True:
            res = self._run(self.conn.get_deals_by_time_range(start, end, offset, 1000)) or {}
            batch = res.get("deals", []) if isinstance(res, dict) else list(res)
            for d in batch:
                if d.get("type") not in ("DEAL_TYPE_BUY", "DEAL_TYPE_SELL"):
                    continue                 # nạp/rút, phí... bỏ qua
                out.append(NS(ticket=int(d["id"]), position_id=int(d.get("positionId") or 0), symbol=d.get("symbol"),
                              type=0 if d["type"] == "DEAL_TYPE_BUY" else 1,
                              entry=DEAL_ENTRY.get(d.get("entryType"), -1), reason=DEAL_REASON.get(d.get("reason"), -1),
                              volume=d.get("volume") or 0.0, price=d.get("price") or 0.0, profit=d.get("profit") or 0.0,
                              commission=d.get("commission") or 0.0, swap=d.get("swap") or 0.0, fee=0.0,
                              magic=d.get("magic") or 0, comment=d.get("comment") or d.get("brokerComment") or "",
                              time=int(_utc(d["time"]).timestamp())))
            if len(batch) < 1000:
                break
            offset += 1000
        return sorted(out, key=lambda d: d.ticket)

    def lot_for_risk(self, symbol, side, entry, sl, risk_money):
        info = self.symbol_info(symbol)
        loss1 = abs(entry - sl) * info.trade_contract_size      # tài khoản USD, mã báo giá USD (XAUUSD)
        if loss1 <= 0:
            return 0.0
        step = info.volume_step
        lots = math.floor(risk_money / loss1 / step + 1e-9) * step
        return round(lots, 8) if lots >= info.volume_min else 0.0

    def _guard(self):
        if not self.is_demo() and not self.allow_live:
            raise MT5Error("Tài khoản THẬT: đặt lệnh bị khoá. Đặt ALLOW_LIVE_TRADING=true trong .env nếu chắc chắn.")

    def _trade(self, coro):
        from metaapi_cloud_sdk.clients.metaapi.trade_exception import TradeException
        try:
            r = self._run(coro)
        except TradeException as e:
            return OrderResult(False, f"Broker từ chối ({e.stringCode}): {e}")
        except Exception as e:          # noqa: BLE001
            return OrderResult(False, f"MetaApi lỗi: {e}")
        if r.get("stringCode") not in OK_CODES:
            return OrderResult(False, f"Broker từ chối ({r.get('stringCode')}): {r.get('message')}")
        return OrderResult(True, "OK", ticket=int(r.get("positionId") or r.get("orderId") or 0))

    def _fill_details(self, res, symbol, fallback_price, volume):
        """MetaApi không trả giá khớp trong phản hồi lệnh: đọc lại từ vị thế, nếu không có thì dùng giá hiện tại."""
        if not res.ok:
            return res
        for _ in range(5):
            for p in self.positions(symbol):
                if p.ticket == res.ticket:
                    res.price, res.volume = p.price_open, p.volume
                    return res
            time.sleep(0.5)
        res.price, res.volume = fallback_price, volume
        return res

    def market_order(self, symbol, side, volume, sl=None, tp=None, comment="tg-bot", magic=50140, deviation=30):
        self._guard()
        info = self.symbol_info(symbol)
        volume = round(math.floor(volume / info.volume_step + 1e-9) * info.volume_step, 8)
        if volume < info.volume_min or volume > info.volume_max:
            return OrderResult(False, f"Khối lượng {volume} ngoài khoảng {info.volume_min}–{info.volume_max}")
        t = self.tick(symbol)
        opts = {"comment": comment, "magic": magic, "slippage": deviation}
        sl = round(sl, info.digits) if sl else None
        tp = round(tp, info.digits) if tp else None
        fn = self.conn.create_market_buy_order if side > 0 else self.conn.create_market_sell_order
        res = self._trade(fn(symbol, volume, sl, tp, opts))
        return self._fill_details(res, symbol, t.ask if side > 0 else t.bid, volume)

    def close_position(self, ticket, volume=None, deviation=30):
        self._guard()
        ps = [p for p in self.positions() if p.ticket == int(ticket)]
        if not ps:
            return OrderResult(False, f"Không có lệnh #{ticket}")
        p = ps[0]
        info = self.symbol_info(p.symbol)
        t = self.tick(p.symbol)
        price = t.bid if p.type == 0 else t.ask
        opts = {"comment": "tg-close", "slippage": deviation}
        if volume is not None and volume < p.volume:
            vol = round(math.floor(volume / info.volume_step + 1e-9) * info.volume_step, 8)
            if vol < info.volume_min:
                return OrderResult(False, f"Khối lượng đóng {vol} nhỏ hơn mức tối thiểu {info.volume_min}")
            res = self._trade(self.conn.close_position_partially(str(p.ticket), vol, opts))
        else:
            vol = p.volume
            res = self._trade(self.conn.close_position(str(p.ticket), opts))
        if res.ok:
            res.ticket, res.price, res.volume = p.ticket, price, vol
        return res

    def modify_sltp(self, ticket, sl=None, tp=None):
        self._guard()
        ps = [p for p in self.positions() if p.ticket == int(ticket)]
        if not ps:
            return OrderResult(False, f"Không có lệnh #{ticket}")
        p = ps[0]
        info = self.symbol_info(p.symbol)
        sl = round(sl if sl is not None else p.sl, info.digits) or None
        tp = round(tp if tp is not None else p.tp, info.digits) or None
        return self._trade(self.conn.modify_position(str(p.ticket), sl, tp))
