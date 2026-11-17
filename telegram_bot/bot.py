"""Bot Telegram điều khiển tài khoản MT5 (PF Markets): xem giá, biểu đồ, tài khoản, đặt/đóng lệnh, cảnh báo tín hiệu.

    .venv\\Scripts\\python telegram_bot\\bot.py            # chạy bot (để cửa sổ mở; terminal MT5 phải đang chạy)
    .venv\\Scripts\\python telegram_bot\\bot.py --check    # chỉ kiểm tra kết nối MT5 + Telegram rồi thoát

Cài đặt: xem mục "Bot Telegram + MT5" trong README.md. Bí mật nằm trong .env, tham số trong config.yaml (khoá mt5_bot).

Lệnh Telegram:
    /chart [M5|M15|H1|H4|D1] [số nến] [mã]   biểu đồ mới nhất từ broker
    /price [mã]                              giá bid/ask, spread
    /account                                 số dư, equity, margin
    /positions                               lệnh đang mở
    /buy <lot | 1%> [sl] [tp]                mua thị trường (có nút xác nhận). "1%" = tính lot theo rủi ro, cần SL
    /sell <lot | 1%> [sl] [tp]               bán thị trường
    /close <ticket | all>                    đóng lệnh
    /signals [on|off]                        bật/tắt cảnh báo tín hiệu PDH/PDL (M5)
    /id                                      xem chat id của bạn

Chỉ các chat id trong TELEGRAM_ALLOWED_CHAT_IDS được dùng bot. Mọi lệnh giao dịch đều phải bấm nút xác nhận.
"""
import argparse
import html
import json
import logging
import os
import secrets
import sys
import time
import traceback
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from broker.mt5_client import TF_MINUTES, TIMEFRAMES, MT5Client, MT5Error  # noqa: E402
from telegram_bot import chart as chartlib  # noqa: E402
from telegram_bot.signals import pdh_pdl_signal, prev_day_levels  # noqa: E402
from telegram_bot.telegram_api import Telegram  # noqa: E402

STATE_FILE = ROOT / "live" / "state_mt5_bot.json"
log = logging.getLogger("mt5bot")

DEFAULTS = {
    "symbol": "XAUUSD",
    "display_tz": "Asia/Ho_Chi_Minh",
    "chart_tf": "M15",
    "chart_bars": 150,
    "max_lot": 0.10,            # chặn lệnh tay quá cỡ
    "signals": True,            # bật cảnh báo khi khởi động
    "signal_risk_pct": 1.0,     # % số dư rủi ro cho lệnh từ tín hiệu
    "signal_confirm_minutes": 5,
    "confirm_seconds": 60,
    "magic": 50140,
}

COMMANDS = [
    ("chart", "Biểu đồ: /chart M15 150"), ("price", "Giá bid/ask"), ("account", "Số dư, equity"),
    ("positions", "Lệnh đang mở"), ("buy", "Mua: /buy 0.01 [sl] [tp] hoặc /buy 1% sl"),
    ("sell", "Bán: /sell 0.01 [sl] [tp]"), ("close", "Đóng: /close <ticket|all>"),
    ("signals", "Cảnh báo PDH/PDL: /signals on|off"), ("id", "Chat id của bạn"), ("help", "Hướng dẫn"),
]


def load_env(path=ROOT / ".env"):
    """Đọc .env đơn giản (KEY=VALUE), không ghi đè biến đã có."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_settings():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return {**DEFAULTS, **(raw.get("mt5_bot") or {})}


def esc(s):
    return html.escape(str(s))


class Bot:
    def __init__(self, cfg, mt, tg, allowed):
        self.cfg, self.mt, self.tg, self.allowed = cfg, mt, tg, allowed
        self.symbol = mt.resolve_symbol(cfg["symbol"])
        self.pending = {}        # token -> dict(action, chat, expires, ...)
        self.state = {"signals": bool(cfg["signals"]), "last_signal_bar": None, "tp1": {}}
        if STATE_FILE.exists():
            try:
                self.state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
            except ValueError:
                pass
        self.last_m5_check = None

    # ----- tiện ích -----
    def save(self):
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.state, indent=1, default=str), encoding="utf-8")

    def local(self, ts):
        return pd.Timestamp(ts).tz_localize("UTC").tz_convert(self.cfg["display_tz"])

    def fmt_time(self, ts):
        return self.local(ts).strftime("%d/%m %H:%M")

    def digits(self):
        return self.mt.symbol_info(self.symbol).digits

    def broadcast(self, text, buttons=None):
        for cid in self.allowed:
            try:
                self.tg.send(cid, text, buttons)
            except Exception as e:      # noqa: BLE001
                log.warning("gửi tới %s lỗi: %s", cid, e)

    def ask_confirm(self, chat, text, action, ttl=None):
        token = secrets.token_hex(4)
        action.update(chat=chat, expires=time.time() + (ttl or self.cfg["confirm_seconds"]))
        self.pending[token] = action
        self.tg.send(chat, text, [[{"text": "✅ Xác nhận", "callback_data": f"ok:{token}"},
                                   {"text": "✖ Huỷ", "callback_data": f"no:{token}"}]])

    # ----- vòng lặp -----
    def run(self):
        self.tg.set_commands(COMMANDS)
        a = self.mt.account()
        self.broadcast(f"🤖 Bot đã chạy. Tài khoản <b>{a.login}</b> ({'DEMO' if self.mt.is_demo() else 'THẬT'}) "
                       f"· {esc(a.server)} · mã {esc(self.symbol)}\nCảnh báo tín hiệu: "
                       f"{'bật' if self.state['signals'] else 'tắt'}. Gõ /help để xem lệnh.")
        while True:
            try:
                for u in self.tg.updates(wait=10):
                    self.handle_update(u)
                self.mt.ensure()
                self.manage_tp1()
                self.check_signals()
                self.pending = {k: v for k, v in self.pending.items() if v["expires"] > time.time()}
            except KeyboardInterrupt:
                raise
            except Exception as e:      # noqa: BLE001 — không để một lỗi làm chết bot
                log.error("vòng lặp lỗi: %s\n%s", e, traceback.format_exc())
                time.sleep(5)

    def handle_update(self, u):
        if "callback_query" in u:
            cb = u["callback_query"]
            chat = cb["message"]["chat"]["id"]
            if chat not in self.allowed:
                return
            self.on_callback(cb, chat)
            return
        msg = u.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = (msg.get("chat") or {}).get("id")
        if not text.startswith("/") or chat is None:
            return
        cmd, *args = text.split()
        cmd = cmd[1:].split("@")[0].lower()
        if cmd == "id":
            self.tg.send(chat, f"Chat id của bạn: <code>{chat}</code>")
            return
        if chat not in self.allowed:
            log.warning("chat %s không được phép (lệnh /%s)", chat, cmd)
            return
        fn = getattr(self, f"cmd_{cmd}", None)
        if fn is None:
            self.tg.send(chat, "Không có lệnh này. Gõ /help.")
            return
        try:
            fn(chat, args)
        except MT5Error as e:
            self.tg.send(chat, f"⚠️ {esc(e)}")
        except Exception as e:          # noqa: BLE001
            log.error("/%s lỗi: %s\n%s", cmd, e, traceback.format_exc())
            self.tg.send(chat, f"⚠️ Lỗi: {esc(e)}")

    # ----- lệnh xem -----
    def cmd_start(self, chat, args):
        self.cmd_help(chat, args)

    def cmd_help(self, chat, args):
        self.tg.send(chat, "<b>Lệnh</b>\n" + "\n".join(f"/{c} — {esc(d)}" for c, d in COMMANDS)
                     + "\n\nVí dụ: <code>/chart H1 200</code>, <code>/buy 0.01 4050 4120</code>, "
                       "<code>/sell 1% 4120</code> (lot theo 1% rủi ro, SL 4120)")

    def cmd_price(self, chat, args):
        sym = self.mt.resolve_symbol(args[0]) if args else self.symbol
        t = self.mt.tick(sym)
        d = self.mt.symbol_info(sym).digits
        self.tg.send(chat, f"<b>{esc(sym)}</b>\nBid {t.bid:.{d}f} · Ask {t.ask:.{d}f} · spread {t.ask - t.bid:.{d}f}\n"
                           f"{self.fmt_time(pd.Timestamp(time.time(), unit='s'))}")

    def cmd_chart(self, chat, args):
        tf, bars, sym = self.cfg["chart_tf"], int(self.cfg["chart_bars"]), self.symbol
        for a in args:
            if a.upper() in TIMEFRAMES:
                tf = a.upper()
            elif a.isdigit():
                bars = max(20, min(int(a), 1000))
            else:
                sym = self.mt.resolve_symbol(a)
        df = self.mt.rates(sym, tf, bars + 250, closed_only=False)
        levels = {}
        if sym == self.symbol:
            try:
                pdh, pdl = prev_day_levels(self.mt.rates(sym, "M5", 700))
                levels = {"PDH": pdh, "PDL": pdl}
            except MT5Error:
                pass
        poss = [(f"{'BUY' if p.type == 0 else 'SELL'} {p.volume} #{p.ticket}", 1 if p.type == 0 else -1,
                 p.price_open, p.sl, p.tp) for p in self.mt.positions(sym)]
        local = df.copy()
        local.index = local.index.tz_localize("UTC").tz_convert(self.cfg["display_tz"]).tz_localize(None)
        png = chartlib.render(local, f"{sym} {tf}", levels, poss, show=bars, tz_label=self.cfg["display_tz"])
        t = self.mt.tick(sym)
        d = self.mt.symbol_info(sym).digits
        self.tg.photo(chat, png, f"<b>{esc(sym)} {tf}</b> · bid {t.bid:.{d}f} · ask {t.ask:.{d}f}")

    def cmd_account(self, chat, args):
        a = self.mt.account()
        lvl = f"{a.margin_level:.0f}%" if a.margin else "–"
        self.tg.send(chat, f"<b>{a.login}</b> · {esc(a.server)} · {'DEMO' if self.mt.is_demo() else 'THẬT'}\n"
                           f"Số dư {a.balance:,.2f} {esc(a.currency)}\nEquity {a.equity:,.2f} · lời/lỗ mở {a.profit:+,.2f}\n"
                           f"Margin {a.margin:,.2f} · free {a.margin_free:,.2f} · level {lvl} · đòn bẩy 1:{a.leverage}")

    def cmd_positions(self, chat, args):
        ps = self.mt.positions()
        if not ps:
            self.tg.send(chat, "Không có lệnh đang mở.")
            return
        lines = []
        for p in ps:
            d = self.mt.symbol_info(p.symbol).digits
            lines.append(f"#{p.ticket} {'BUY' if p.type == 0 else 'SELL'} {p.volume} {esc(p.symbol)} @ {p.price_open:.{d}f}"
                         f" · SL {p.sl:.{d}f} · TP {p.tp:.{d}f} · <b>{p.profit:+,.2f}</b>")
        self.tg.send(chat, "\n".join(lines))

    def cmd_signals(self, chat, args):
        if args and args[0].lower() in ("on", "off"):
            self.state["signals"] = args[0].lower() == "on"
            self.save()
        self.tg.send(chat, f"Cảnh báo PDH/PDL (M5, {esc(self.symbol)}): <b>{'bật' if self.state['signals'] else 'tắt'}</b>.\n"
                           "Backtest 2021–2026 (spread $0,30): +4,5%, PF 1,21 nhưng chỉ 37 lệnh — chưa đủ để tin.")

    # ----- lệnh giao dịch -----
    @staticmethod
    def _parse_trade_args(args):
        """['0.01'|'1%', sl?, tp?] hoặc dạng sl=.. tp=.."""
        if not args:
            raise ValueError("thiếu khối lượng")
        size, rest, sl, tp = args[0], args[1:], None, None
        pos = []
        for a in rest:
            k, _, v = a.partition("=")
            if v and k.lower() == "sl":
                sl = float(v)
            elif v and k.lower() == "tp":
                tp = float(v)
            else:
                pos.append(float(a))
        if pos:
            sl = sl if sl is not None else pos[0]
        if len(pos) > 1:
            tp = tp if tp is not None else pos[1]
        return size, sl, tp

    def _trade(self, chat, args, side):
        name = "MUA" if side > 0 else "BÁN"
        try:
            size, sl, tp = self._parse_trade_args(args)
        except ValueError as e:
            self.tg.send(chat, f"Sai cú pháp ({esc(e)}). Ví dụ: <code>/{'buy' if side > 0 else 'sell'} 0.01 4050 4120</code>")
            return
        t = self.mt.tick(self.symbol)
        entry = t.ask if side > 0 else t.bid
        for lvl, nm, bad in ((sl, "SL", lambda v: (v >= entry) if side > 0 else (v <= entry)),
                             (tp, "TP", lambda v: (v <= entry) if side > 0 else (v >= entry))):
            if lvl is not None and bad(lvl):
                self.tg.send(chat, f"{nm} {lvl} nằm sai phía giá hiện tại {entry}.")
                return
        if size.endswith("%"):
            if sl is None:
                self.tg.send(chat, "Lot theo % rủi ro cần SL: <code>/buy 1% 4050</code>")
                return
            risk = self.mt.account().balance * float(size[:-1]) / 100
            lot = self.mt.lot_for_risk(self.symbol, side, entry, sl, risk)
            if lot == 0:
                self.tg.send(chat, f"Rủi ro {size} quá nhỏ so với khoảng SL (dưới lot tối thiểu).")
                return
        else:
            lot = float(size)
        if lot > self.cfg["max_lot"]:
            self.tg.send(chat, f"Lot {lot} vượt giới hạn max_lot = {self.cfg['max_lot']} (config.yaml).")
            return
        d = self.digits()
        risk_txt = ""
        if sl is not None:
            risk_txt = f" · rủi ro ≈ {abs(lot * (entry - sl) * self.mt.symbol_info(self.symbol).trade_contract_size):,.2f}"
        self.ask_confirm(chat, f"{name} <b>{lot}</b> lot {esc(self.symbol)} @ ~{entry:.{d}f}\n"
                               f"SL {sl if sl else '–'} · TP {tp if tp else '–'}{risk_txt}",
                         {"action": "open", "side": side, "lot": lot, "sl": sl, "tp": tp})

    def cmd_buy(self, chat, args):
        self._trade(chat, args, 1)

    def cmd_sell(self, chat, args):
        self._trade(chat, args, -1)

    def cmd_close(self, chat, args):
        if not args:
            self.tg.send(chat, "Dùng: <code>/close 123456</code> hoặc <code>/close all</code>")
            return
        ps = self.mt.positions()
        if args[0].lower() == "all":
            tickets = [p.ticket for p in ps]
        else:
            tickets = [int(a) for a in args if a.isdigit()]
        tickets = [t for t in tickets if any(p.ticket == t for p in ps)]
        if not tickets:
            self.tg.send(chat, "Không có lệnh nào khớp.")
            return
        pnl = sum(p.profit for p in ps if p.ticket in tickets)
        self.ask_confirm(chat, f"Đóng {len(tickets)} lệnh ({', '.join(f'#{t}' for t in tickets)}) · lời/lỗ hiện tại {pnl:+,.2f}",
                         {"action": "close", "tickets": tickets})

    def on_callback(self, cb, chat):
        verb, _, token = cb.get("data", "").partition(":")
        act = self.pending.pop(token, None)
        msg = cb["message"]
        try:
            self.tg.edit_buttons(chat, msg["message_id"])
        except Exception:               # noqa: BLE001
            pass
        if act is None or act["expires"] < time.time():
            self.tg.answer_callback(cb["id"], "Hết hạn, gửi lại lệnh.")
            return
        if verb != "ok":
            self.tg.answer_callback(cb["id"], "Đã huỷ")
            self.tg.send(chat, "Đã huỷ.")
            return
        self.tg.answer_callback(cb["id"], "Đang gửi lệnh…")
        try:
            self.execute(chat, act)
        except MT5Error as e:
            self.tg.send(chat, f"⚠️ {esc(e)}")

    def execute(self, chat, act):
        d = self.digits()
        if act["action"] == "open":
            r = self.mt.market_order(self.symbol, act["side"], act["lot"], act["sl"], act["tp"],
                                     comment="tg-manual", magic=self.cfg["magic"])
            self.tg.send(chat, f"✅ Đã khớp #{r.ticket}: {r.volume} lot @ {r.price:.{d}f}" if r.ok else f"❌ {esc(r.message)}")
        elif act["action"] == "close":
            out = []
            for t in act["tickets"]:
                r = self.mt.close_position(t)
                out.append(f"#{t}: {'đã đóng @ ' + format(r.price, f'.{d}f') if r.ok else esc(r.message)}")
                self.state["tp1"].pop(str(t), None)
            self.save()
            self.tg.send(chat, "\n".join(out))
        elif act["action"] == "signal":
            sig = act["signal"]
            t = self.mt.tick(self.symbol)
            entry = t.ask if sig.side > 0 else t.bid
            sl, tp1, tp2 = sig.levels(entry)
            risk = self.mt.account().balance * self.cfg["signal_risk_pct"] / 100
            lot = min(self.mt.lot_for_risk(self.symbol, sig.side, entry, sl, risk), self.cfg["max_lot"])
            if lot == 0:
                self.tg.send(chat, "❌ Rủi ro quá nhỏ cho lot tối thiểu.")
                return
            r = self.mt.market_order(self.symbol, sig.side, lot, sl, tp2, comment="tg-pdh", magic=self.cfg["magic"])
            if not r.ok:
                self.tg.send(chat, f"❌ {esc(r.message)}")
                return
            # SL/TP tính lại theo giá khớp thật; TP1 (chốt 50%) do bot tự quản
            sl, tp1, tp2 = sig.levels(r.price)
            self.mt.modify_sltp(r.ticket, sl, tp2)
            self.state["tp1"][str(r.ticket)] = {"price": tp1, "side": sig.side, "volume": r.volume}
            self.save()
            self.tg.send(chat, f"✅ #{r.ticket} {'BUY' if sig.side > 0 else 'SELL'} {r.volume} @ {r.price:.{d}f}\n"
                               f"SL {sl:.{d}f} · TP1 {tp1:.{d}f} (chốt 50%) · TP2 {tp2:.{d}f}")

    # ----- tín hiệu -----
    def check_signals(self):
        if not self.state["signals"]:
            return
        now = pd.Timestamp.now("UTC").tz_localize(None)
        bar = now.floor("5min")
        if self.last_m5_check == bar or now - bar < pd.Timedelta(seconds=5):   # đợi nến đóng hẳn
            return
        self.last_m5_check = bar
        m5 = self.mt.rates(self.symbol, "M5", 1000, closed_only=True)
        if now - m5.index[-1] > pd.Timedelta(minutes=15):      # thị trường đóng cửa
            return
        sig = pdh_pdl_signal(m5)
        if sig is None or str(sig.bar_time) == str(self.state.get("last_signal_bar")):
            return
        self.state["last_signal_bar"] = str(sig.bar_time)
        self.save()
        d = self.digits()
        t = self.mt.tick(self.symbol)
        entry = t.ask if sig.side > 0 else t.bid
        sl, tp1, tp2 = sig.levels(entry)
        risk = self.mt.account().balance * self.cfg["signal_risk_pct"] / 100
        lot = min(self.mt.lot_for_risk(self.symbol, sig.side, entry, sl, risk), self.cfg["max_lot"])
        text = (f"📣 <b>Tín hiệu {sig.strategy}: {'MUA' if sig.side > 0 else 'BÁN'} {esc(self.symbol)}</b>\n"
                f"{esc(sig.note)} · nến M5 {self.fmt_time(sig.bar_time)}\n"
                f"Giá ~{entry:.{d}f} · SL {sl:.{d}f} · TP1 {tp1:.{d}f} (50%) · TP2 {tp2:.{d}f}\n"
                f"Lot {lot} ({self.cfg['signal_risk_pct']}% rủi ro). Bấm xác nhận trong {self.cfg['signal_confirm_minutes']} phút.")
        for cid in self.allowed:
            if lot > 0:
                self.ask_confirm(cid, text, {"action": "signal", "signal": sig}, ttl=self.cfg["signal_confirm_minutes"] * 60)
            else:
                self.tg.send(cid, text + "\n(Lot = 0: vốn quá nhỏ cho rủi ro này, chỉ cảnh báo.)")

    def manage_tp1(self):
        """Lệnh từ tín hiệu: giá chạm TP1 thì đóng 50% (giống backtest PDH/PDL)."""
        if not self.state["tp1"]:
            return
        open_t = {p.ticket: p for p in self.mt.positions(self.symbol)}
        t = self.mt.tick(self.symbol)
        changed = False
        for k, v in list(self.state["tp1"].items()):
            p = open_t.get(int(k))
            if p is None:
                self.state["tp1"].pop(k)
                changed = True
                continue
            px = t.bid if v["side"] > 0 else t.ask
            if (px - v["price"]) * v["side"] >= 0:
                r = self.mt.close_position(p.ticket, volume=p.volume / 2)
                self.state["tp1"].pop(k)
                changed = True
                self.broadcast(f"🎯 #{p.ticket} chạm TP1: đã chốt {r.volume} lot @ {r.price:.{self.digits()}f}" if r.ok
                               else f"⚠️ #{p.ticket} chạm TP1 nhưng chốt 50% thất bại: {esc(r.message)}")
        if changed:
            self.save()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="kiểm tra kết nối rồi thoát")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()
    cfg = load_settings()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Thiếu TELEGRAM_BOT_TOKEN trong .env (lấy từ @BotFather).")
    allowed = {int(x) for x in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x}
    tg = Telegram(token)
    me = tg.me()
    print(f"Telegram OK: @{me['username']}")

    if not allowed:
        print("Chưa có TELEGRAM_ALLOWED_CHAT_IDS. Nhắn /id cho bot để lấy chat id, rồi thêm vào .env.")
        while True:      # chế độ chỉ trả lời /id
            for u in tg.updates(wait=20):
                m = u.get("message") or {}
                if (m.get("text") or "").startswith("/"):
                    cid = m["chat"]["id"]
                    tg.send(cid, f"Chat id của bạn: <code>{cid}</code>\nThêm vào .env: TELEGRAM_ALLOWED_CHAT_IDS={cid}")
                    print(f"chat id: {cid}")

    mt = MT5Client()
    a = mt.connect()
    print(f"MT5 OK: {a.login} @ {a.server} · {'DEMO' if mt.is_demo() else 'THẬT'} · số dư {a.balance} {a.currency}")
    bot = Bot(cfg, mt, tg, allowed)
    t = mt.tick(bot.symbol)
    print(f"Mã {bot.symbol}: bid {t.bid} ask {t.ask} · giờ server lệch UTC {mt.server_offset(bot.symbol) / 3600:+.1f}h")
    if args.check:
        mt.shutdown()
        return
    try:
        bot.run()
    finally:
        mt.shutdown()


if __name__ == "__main__":
    main()
