"""Bước 2: lọc và xếp hạng altcoin ("bắn shotgun").

Bộ lọc cơ bản (luôn bật): thanh khoản, giá > MA25, MA5 > MA25.
Bộ lọc thêm (khoá `filters` trong config.yaml, để null / 0 / false là tắt):

  trend_ma          Mã phải đóng cửa trên EMA dài hạn của **chính nó** (PTJ áp cho từng mã, không chỉ BTC).
                    Repo Neyt/Paul-Tudor-Jones-200-EMA-strategy.
  golden_cross      [nhanh, chậm]: EMA nhanh > EMA chậm, tức xu hướng trung hạn đã lên.
                    Repo MayankBharati/Quant-Trading-Project.
  fresh_cross_days  MA5 phải vừa cắt lên MA25 trong N phiên gần nhất. Mua lúc tín hiệu mới xuất hiện,
                    không mua đi mua lại suốt thời gian "đang nằm trên" (crossover thay vì trạng thái).
  breakout_days     Giá đóng cửa là cao nhất N phiên (Bibirion mua mã đang lập đỉnh mới).
  rs_lookback       Mã phải tăng mạnh hơn chỉ số chuẩn (BTC / SPY) trong N phiên.
  min_vol_ratio     Khối lượng hôm nay ≥ x lần trung bình: dòng tiền thật sự vào.
  max_extension     Giá không cách MA25 quá x (vd 0.3 = 30%): không đuổi theo mã đã chạy quá xa.
  min_history       Mã phải có ít nhất N nến (tránh coin mới niêm yết, biến động hỗn loạn).

Các ý ở README mục 9 (lấy từ MT5 EA, XAUUSD-bot, freqtrade TrendRider / ADXMomentum / EMASkipPump):

  pullback          {fast, slow, lookback, rsi_max, rsi_min}: chờ giá hồi về EMA nhanh rồi mới vào.
                    Trong `lookback` phiên gần nhất có nến chạm EMA nhanh (low ≤ EMA21) mà vẫn đóng cửa trên
                    EMA chậm; hôm nay là nến xanh đóng cửa trên EMA nhanh. rsi_max: RSI lúc hồi ≤ x;
                    rsi_min: RSI hôm nay ≥ x (null = bỏ qua).
  max_dist_atr      Không đuổi giá: close − EMA nhanh ≤ x × ATR (EMA nhanh = pullback.fast, mặc định 21).
  ema_slope         [n, k]: EMA n phải cao hơn chính nó k phiên trước (xu hướng dài đang dốc lên).
  adx_min           ADX(14) ≥ x và +DI > −DI (có xu hướng, không đi ngang).
  max_rsi           RSI(14) < x (không mua lúc quá nóng).
  max_vol_ratio     Bỏ nến có khối lượng > x lần trung bình (cú pump đột biến).

Xếp hạng: `rank_by: momentum_volume` (gốc: đà tăng + đột biến khối lượng) hoặc `momentum` (chỉ đà tăng,
không thưởng điểm cho khối lượng đột biến).
"""
import pandas as pd

from strategy import indicators as ta

FIELDS = ("open", "high", "low", "close", "volume")


def build_panels(frames: dict, index=None) -> dict:
    """Ghép {symbol: DataFrame OHLCV} thành các bảng rộng: hàng = ngày, cột = mã."""
    panels = {f: pd.DataFrame({s: df[f] for s, df in frames.items()}) for f in FIELDS}
    if index is not None:
        panels = {f: p.reindex(index) for f, p in panels.items()}
    return panels


def _ema(df, n):
    return df.ewm(span=n, adjust=False, min_periods=n).mean()


def compute_signals(panels: dict, cfg: dict, bench_close: pd.Series = None) -> dict:
    close = panels["close"]
    vol_usd = close * panels["volume"]
    vlb = cfg["volume_lookback"]
    f = cfg.get("filters") or {}

    sma_fast = close.rolling(cfg["regime_fast_ma"]).mean()
    sma_slow = close.rolling(cfg["regime_slow_ma"]).mean()
    momentum = close / close.shift(cfg["momentum_lookback"]) - 1
    avg_vol = vol_usd.rolling(vlb).mean()
    # Khối lượng hôm nay so với trung bình 20 ngày trước đó (không tính hôm nay).
    vol_ratio = vol_usd / vol_usd.shift(1).rolling(vlb).mean()

    fast_above = sma_fast > sma_slow
    eligible = (avg_vol >= cfg["min_volume_usd"]) & (close > sma_slow) & fast_above

    if f.get("trend_ma"):
        eligible &= close > _ema(close, int(f["trend_ma"]))
    if f.get("golden_cross"):
        a, b = f["golden_cross"]
        eligible &= _ema(close, int(a)) > _ema(close, int(b))
    if f.get("fresh_cross_days"):
        crossed = fast_above & ~fast_above.shift(1, fill_value=False).astype(bool)
        eligible &= crossed.astype(int).rolling(int(f["fresh_cross_days"]), min_periods=1).max().astype(bool)
    if f.get("breakout_days"):
        eligible &= close >= close.rolling(int(f["breakout_days"])).max()
    if f.get("rs_lookback") and bench_close is not None:
        n = int(f["rs_lookback"])
        bench_ret = (bench_close / bench_close.shift(n) - 1).reindex(close.index)
        eligible &= (close / close.shift(n) - 1).gt(bench_ret, axis=0)
    if f.get("min_vol_ratio"):
        eligible &= vol_ratio >= float(f["min_vol_ratio"])
    if f.get("max_extension"):
        eligible &= close / sma_slow - 1 <= float(f["max_extension"])
    if f.get("min_history"):
        eligible &= close.notna().cumsum() >= int(f["min_history"])

    high, low, open_ = panels["high"], panels["low"], panels["open"]
    atr = ta.atr(high, low, close, int(cfg.get("atr_period", 14)))
    pb = f.get("pullback") or {}
    ema_fast_n = int(pb.get("fast", 21)) if isinstance(pb, dict) else 21
    need_rsi = f.get("max_rsi") or (isinstance(pb, dict) and (pb.get("rsi_max") or pb.get("rsi_min")))
    rsi = ta.rsi(close, 14) if need_rsi else None

    if pb:
        pb = pb if isinstance(pb, dict) else {}
        e_fast, e_slow = _ema(close, ema_fast_n), _ema(close, int(pb.get("slow", 50)))
        touched = (low <= e_fast) & (close > e_slow)
        if pb.get("rsi_max"):
            touched &= rsi <= float(pb["rsi_max"])
        n = int(pb.get("lookback", 5))
        recent = touched.astype(int).rolling(n, min_periods=1).max().astype(bool)
        trigger = (close > open_) & (close > e_fast) & (e_fast > e_slow)
        if pb.get("rsi_min"):
            trigger &= rsi >= float(pb["rsi_min"])
        eligible &= recent & trigger
    if f.get("max_dist_atr"):
        eligible &= close - _ema(close, ema_fast_n) <= float(f["max_dist_atr"]) * atr
    if f.get("ema_slope"):
        n, k = f["ema_slope"]
        e = _ema(close, int(n))
        eligible &= e > e.shift(int(k))
    if f.get("adx_min"):
        pdi, mdi, adx = ta.dmi(high, low, close, 14)
        eligible &= (adx >= float(f["adx_min"])) & (pdi > mdi)
    if f.get("max_rsi"):
        eligible &= rsi < float(f["max_rsi"])
    if f.get("max_vol_ratio"):
        eligible &= vol_ratio <= float(f["max_vol_ratio"])

    # Điểm = trung bình thứ hạng phần trăm của đà tăng và đột biến khối lượng, chỉ trong nhóm đủ điều kiện.
    mom_rank = momentum.where(eligible).rank(axis=1, pct=True)
    if cfg.get("rank_by", "momentum_volume") == "momentum":
        score = mom_rank
    else:
        vol_rank = vol_ratio.where(eligible).rank(axis=1, pct=True)
        score = (mom_rank + vol_rank) / 2

    # Đường MA dùng cho luật thoát "gãy đà" (mặc định MA5; đặt exit_ma lớn hơn để giữ lệnh lâu hơn, 0 = tắt).
    exit_n = cfg.get("exit_ma", cfg["regime_fast_ma"])
    sma_exit = close.rolling(int(exit_n)).mean() if exit_n else close * float("nan")

    return {
        "sma_fast": sma_fast,
        "sma_exit": sma_exit,
        "atr": atr,
        "sma_slow": sma_slow,
        "momentum": momentum,
        "vol_ratio": vol_ratio,
        "eligible": eligible,
        "score": score,
    }


def rank_candidates(signals: dict, date, exclude=()) -> list:
    row = signals["score"].loc[date].dropna()
    row = row[~row.index.isin(list(exclude))]
    return list(row.sort_values(ascending=False).index)
