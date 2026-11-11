"""Đọc/ghi CSV nến ngày, dùng chung cho mọi nguồn dữ liệu."""
from pathlib import Path

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


def symbol_to_file(symbol: str) -> str:
    return symbol.replace("/", "_") + ".csv"


def file_to_symbol(path: Path) -> str:
    return path.stem.replace("_", "/", 1)


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    return df[~df.index.duplicated(keep="last")]


def save_csv(df: pd.DataFrame, path: Path) -> None:
    df = df[COLUMNS].dropna(subset=["open", "high", "low", "close"])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_csv(path, index_label="date")


def load_all(data_dir) -> dict:
    frames = {}
    for path in sorted(Path(data_dir).glob("*.csv")):
        df = load_csv(path)
        if len(df):
            frames[file_to_symbol(path)] = df
    return frames
