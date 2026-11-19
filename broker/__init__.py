"""Chọn cách kết nối broker theo biến BROKER trong .env:

    BROKER=metaapi   MT5 chạy trên cloud của MetaApi; bot chạy được ở mọi nơi (Linux, cloud miễn phí)  [mặc định]
    BROKER=mt5       terminal MT5 trên chính máy này (chỉ Windows), qua thư viện MetaTrader5
"""
import os


def make_client():
    kind = (os.environ.get("BROKER") or "metaapi").strip().lower()
    if kind == "mt5":
        from broker.mt5_client import MT5Client
        return MT5Client()
    if kind == "metaapi":
        from broker.metaapi_client import MetaApiClient
        return MetaApiClient()
    raise SystemExit(f"BROKER={kind} không hợp lệ (chọn metaapi hoặc mt5)")
