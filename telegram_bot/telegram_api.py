"""Gọi Telegram Bot API trực tiếp bằng requests (long polling, một luồng — hợp với thư viện MT5 không an toàn đa luồng)."""
import json

import requests


class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}"
        self.offset = None
        self.s = requests.Session()

    def _call(self, method, timeout=30, files=None, **params):
        if files:
            r = self.s.post(f"{self.base}/{method}", data=params, files=files, timeout=timeout)
        else:
            r = self.s.post(f"{self.base}/{method}", json=params, timeout=timeout)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data["result"]

    def me(self):
        return self._call("getMe")

    def updates(self, wait=10):
        params = {"timeout": wait, "allowed_updates": ["message", "callback_query"]}
        if self.offset is not None:
            params["offset"] = self.offset
        res = self._call("getUpdates", timeout=wait + 10, **params)
        if res:
            self.offset = res[-1]["update_id"] + 1
        return res

    def send(self, chat_id, text, buttons=None):
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        return self._call("sendMessage", **params)

    def photo(self, chat_id, png_bytes, caption=""):
        return self._call("sendPhoto", timeout=60, files={"photo": ("chart.png", png_bytes, "image/png")},
                          chat_id=chat_id, caption=caption[:1024], parse_mode="HTML")

    def answer_callback(self, cb_id, text=""):
        return self._call("answerCallbackQuery", callback_query_id=cb_id, text=text)

    def edit_buttons(self, chat_id, message_id, buttons=None):
        return self._call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                          reply_markup=json.loads(json.dumps({"inline_keyboard": buttons or []})))

    def set_commands(self, commands):
        return self._call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])
