"""Crypto qua ccxt (mặc định Binance spot). Lấy cả cặp đã ngừng giao dịch để giảm lỗi survivorship."""
import time
from pathlib import Path

import pandas as pd

from data.storage import load_csv, save_csv, symbol_to_file

DAY_MS = 86_400_000
LEVERAGED_MAJORS = {"BTC", "ETH", "BNB", "ADA", "XRP", "DOT", "LINK", "TRX", "XTZ", "EOS", "LTC",
                    "BCH", "FIL", "SXP", "UNI", "SUSHI", "AAVE", "YFI", "1INCH", "XLM"}


def is_excluded(base: str, cfg: dict) -> bool:
    if base in set(cfg.get("exclude_bases", [])):
        return True
    # Token đòn bẩy của Binance: BTCUP, ETHDOWN, BNBBULL, ...
    if base.endswith(("DOWN", "BULL", "BEAR")) and len(base) > 4:
        return True
    if base.endswith("UP") and base[:-2] in LEVERAGED_MAJORS:
        return True
    return False


def make_exchange(cfg: dict):
    import ccxt
    ex = getattr(ccxt, cfg["exchange"])({"enableRateLimit": True, "timeout": 120000,  # exchangeInfo ~17MB
                                         "options": {"fetchMarkets": ["spot"]}})
    # Một số mạng chặn api.binance.com; data-api.binance.vision là endpoint dữ liệu công khai của Binance.
    if cfg.get("public_api_url"):
        ex.urls["api"]["public"] = cfg["public_api_url"]
    return ex


def load_markets(ex, retries: int = 3) -> dict:
    """exchangeInfo nặng ~17MB, mạng chập chờn hay timeout, nên thử lại vài lần."""
    for i in range(retries):
        try:
            return ex.load_markets()
        except Exception as e:
            if i == retries - 1:
                raise
            print(f"Tải danh sách thị trường lỗi ({type(e).__name__}), thử lại {i + 2}/{retries}...", flush=True)
            time.sleep(5)


def list_universe(ex, cfg: dict, include_inactive: bool = True) -> list:
    out = []
    for m in load_markets(ex).values():
        if not m.get("spot") or m.get("quote") != cfg["quote"]:
            continue
        if not include_inactive and not m.get("active", True):
            continue
        if is_excluded(m["base"], cfg):
            continue
        out.append(m["symbol"])
    return sorted(out)


def fetch_ohlcv_all(ex, symbol: str, since_ms: int, timeframe: str = "1d") -> list:
    rows = []
    now_ms = int(time.time() * 1000)
    while since_ms < now_ms:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        next_since = batch[-1][0] + DAY_MS
        if next_since <= since_ms:
            break
        since_ms = next_since
    return [r for r in rows if r[0] + DAY_MS <= now_ms]  # bỏ nến hôm nay (chưa đóng)


def update_symbol(ex, symbol: str, data_dir: Path, since: str, timeframe: str = "1d") -> int:
    path = data_dir / symbol_to_file(symbol)
    old = load_csv(path) if path.exists() else None
    if old is not None and len(old):
        since_ms = int(old.index[-1].timestamp() * 1000) + DAY_MS
    else:
        since_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)

    rows = fetch_ohlcv_all(ex, symbol, since_ms, timeframe)
    if not rows:
        return 0
    new = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    new["date"] = pd.to_datetime(new["ts"], unit="ms").dt.normalize()
    new = new.drop(columns="ts").set_index("date")
    save_csv(new if old is None else pd.concat([old, new]), path)
    return len(new)


def update(cfg: dict, symbols=None, since=None, include_inactive=True, max_symbols=None, verbose=True):
    data_dir = Path(cfg["data_dir"])
    ex = make_exchange(cfg)
    if symbols is None:
        symbols = list_universe(ex, cfg, include_inactive=include_inactive)
        if max_symbols:
            symbols = symbols[:max_symbols]
    else:
        load_markets(ex)
    symbols = [cfg["benchmark"]] + [s for s in symbols if s != cfg["benchmark"]]

    since = since or cfg.get("since", "2020-01-01")
    failed = []
    for i, sym in enumerate(symbols, 1):
        try:
            n = update_symbol(ex, sym, data_dir, since, cfg.get("timeframe", "1d"))
            if verbose:
                print(f"[{i}/{len(symbols)}] {sym}: +{n} nến", flush=True)
        except Exception as e:  # mạng, mã bị gỡ hẳn, ...
            failed.append(sym)
            if verbose:
                print(f"[{i}/{len(symbols)}] {sym}: lỗi {type(e).__name__}: {e}", flush=True)
    return symbols, failed
