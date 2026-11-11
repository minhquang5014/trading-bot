"""Tải nến ngày cho một hoặc nhiều thị trường trong config.yaml, mỗi mã một file CSV.

    python data/fetch.py                          # thị trường mặc định (crypto)
    python data/fetch.py --market us_stocks
    python data/fetch.py --market all
    python data/fetch.py --market crypto --symbols SOL/USDT,DOGE/USDT --since 2023-01-01
    python data/fetch.py --market us_stocks --max-symbols 20     # thử nhanh
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.storage import load_all, load_csv, symbol_to_file  # noqa: E402,F401  (re-export)
from strategy import list_markets, load_config  # noqa: E402


def get_source(cfg: dict):
    src = cfg.get("source", "ccxt")
    if src == "ccxt":
        from data.sources import ccxt_src as mod
    elif src == "yfinance":
        from data.sources import yf_src as mod
    else:
        raise SystemExit(f"Nguồn dữ liệu không hỗ trợ: {src}")
    return mod


def update_all(cfg: dict, symbols=None, since=None, include_inactive=True, max_symbols=None, verbose=True):
    Path(cfg["data_dir"]).mkdir(parents=True, exist_ok=True)
    symbols, failed = get_source(cfg).update(cfg, symbols=symbols, since=since, include_inactive=include_inactive,
                                             max_symbols=max_symbols, verbose=verbose)
    if verbose:
        print(f"[{cfg['market']}] xong {len(symbols) - len(failed)}/{len(symbols)} mã → {cfg['data_dir']}")
        if failed:
            print(f"  Thất bại {len(failed)} mã: {', '.join(failed[:20])}{' ...' if len(failed) > 20 else ''}")
    return symbols, failed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--market", help="Tên thị trường trong config, hoặc 'all'")
    ap.add_argument("--since", help="YYYY-MM-DD, mặc định lấy từ config")
    ap.add_argument("--symbols", help="Danh sách mã, cách nhau bởi dấu phẩy")
    ap.add_argument("--active-only", action="store_true", help="Crypto: bỏ các cặp đã ngừng giao dịch")
    ap.add_argument("--max-symbols", type=int, help="Giới hạn số mã (để thử nhanh)")
    args = ap.parse_args()

    markets = list_markets(args.config) if args.market == "all" else [args.market]
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    for m in markets:
        cfg = load_config(args.config, market=m)
        update_all(cfg, symbols=symbols, since=args.since,
                   include_inactive=not args.active_only, max_symbols=args.max_symbols)


if __name__ == "__main__":
    main()
