from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path=None, market=None, **overrides):
    """Gộp tham số chung + tham số của một thị trường + ghi đè từ dòng lệnh."""
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path) as f:
        raw = yaml.safe_load(f)
    markets = raw.pop("markets", {}) or {}
    market = market or raw.get("default_market") or next(iter(markets), None)
    if markets and market not in markets:
        raise SystemExit(f"Không có thị trường '{market}' trong config. Có: {', '.join(markets)}")

    cfg = {**raw, **(markets.get(market) or {}), "market": market}
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    data_dir = Path(cfg.get("data_dir", f"data/csv/{market}"))
    cfg["data_dir"] = str(data_dir if data_dir.is_absolute() else ROOT / data_dir)
    return cfg


def list_markets(path=None):
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path) as f:
        return list((yaml.safe_load(f).get("markets") or {}).keys())
