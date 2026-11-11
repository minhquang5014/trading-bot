"""Cổ phiếu Mỹ, vàng, forex... qua yfinance (Yahoo Finance).

Giá đã điều chỉnh (chia tách, cổ tức) thay đổi ngược về quá khứ mỗi khi có cổ tức, nên mỗi lần
cập nhật tải lại toàn bộ lịch sử thay vì nối thêm, để chuỗi giá không bị gãy.

Lưu ý survivorship: danh sách S&P 500 / Nasdaq-100 là thành phần HIỆN TẠI, chưa gồm các mã đã bị
loại khỏi chỉ số hoặc phá sản, nên kết quả backtest sẽ đẹp hơn thực tế.
"""
import urllib.request
from io import StringIO
from pathlib import Path

import pandas as pd

from data.storage import COLUMNS, save_csv, symbol_to_file

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_DIR = ROOT / "data" / "universe"
WIKI = {
    "sp500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol"),
    "nasdaq100": ("https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies", "Ticker"),
}
USER_AGENT = "Mozilla/5.0 (shotgun-momentum research script)"


def _fetch_index_members(name: str) -> list:
    url, col = WIKI[name]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
    # Chọn bảng lớn nhất có cột mã (id của bảng mỗi trang mỗi khác).
    tables = [t for t in pd.read_html(StringIO(html), flavor="lxml") if col in t.columns]
    if not tables:
        raise ValueError(f"Không tìm thấy bảng có cột '{col}' ở {url}")
    table = max(tables, key=len)
    # Yahoo dùng dấu gạch ngang: BRK.B -> BRK-B
    return sorted({str(t).strip().replace(".", "-") for t in table[col].dropna()})


def index_members(name: str, refresh: bool = True) -> list:
    """Lấy danh sách thành phần chỉ số từ Wikipedia, lưu cache để dùng khi mất mạng."""
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    cache = UNIVERSE_DIR / f"{name}.txt"
    if refresh or not cache.exists():
        try:
            members = _fetch_index_members(name)
            cache.write_text("\n".join(members) + "\n")
            return members
        except Exception as e:
            if not cache.exists():
                raise
            print(f"Không tải được {name} ({type(e).__name__}), dùng bản cache.")
    return [s for s in cache.read_text().split() if s]


def resolve_universe(cfg: dict) -> list:
    uni = cfg.get("universe", [])
    if isinstance(uni, str):
        out = set()
        for name in uni.split("+"):
            out.update(index_members(name.strip()))
        return sorted(out)
    return list(uni)


def _drop_incomplete(df: pd.DataFrame) -> pd.DataFrame:
    """Bỏ nến hôm nay nếu thị trường Mỹ chưa đóng cửa."""
    now = pd.Timestamp.now(tz="America/New_York")
    if now.hour < 17:  # cho Yahoo ~1 giờ sau 16:00 để chốt nến
        df = df[df.index < now.normalize().tz_localize(None)]
    return df


def _download(tickers: list, since: str) -> dict:
    import yfinance as yf
    raw = yf.download(tickers, start=since, auto_adjust=True, progress=False,
                      group_by="ticker", threads=True)
    if raw is None or raw.empty:
        return {}
    if not isinstance(raw.columns, pd.MultiIndex):
        raw.columns = pd.MultiIndex.from_product([[tickers[0]], raw.columns])
    out = {}
    for t in raw.columns.get_level_values(0).unique():
        df = raw[t].rename(columns=str.lower)
        if not set(COLUMNS) <= set(df.columns):
            continue
        df = df[COLUMNS].dropna(subset=["close"])
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        if len(df):
            out[t] = _drop_incomplete(df)
    return out


def update(cfg: dict, symbols=None, since=None, max_symbols=None, verbose=True, batch_size=100, **_):
    data_dir = Path(cfg["data_dir"])
    if symbols is None:
        symbols = resolve_universe(cfg)
        if max_symbols:
            symbols = symbols[:max_symbols]
    symbols = [cfg["benchmark"]] + [s for s in symbols if s != cfg["benchmark"]]
    since = since or cfg.get("since", "2015-01-01")

    failed = []
    for i in range(0, len(symbols), batch_size):
        chunk = symbols[i:i + batch_size]
        frames = _download(chunk, since)
        for t in chunk:
            df = frames.get(t)
            if df is None or df.empty:
                failed.append(t)
                continue
            save_csv(df, data_dir / symbol_to_file(t))
        if verbose:
            print(f"[{min(i + batch_size, len(symbols))}/{len(symbols)}] đã tải {len(chunk) - sum(t in failed for t in chunk)}"
                  f"/{len(chunk)} mã", flush=True)
    return symbols, failed
