"""Vẽ biểu đồ nến (PNG) cho Telegram: nến + EMA50/200, đường PDH/PDL, lệnh đang mở (giá vào / SL / TP)."""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

UP, DOWN, BG, GRID, TXT = "#26a69a", "#ef5350", "#131722", "#2a2e39", "#d1d4dc"


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def render(df, title, levels=None, positions=None, show=150, tz_label="UTC"):
    """df: OHLC (index UTC), có thể dài hơn `show` để EMA200 đủ dữ liệu. levels: {tên: giá}.
    positions: [(nhãn, side, giá vào, sl, tp)]."""
    e50, e200 = ema(df["close"], 50), ema(df["close"], 200)
    d = df.iloc[-show:]
    x = np.arange(len(d))
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    col = np.where(c >= o, UP, DOWN)

    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.vlines(x, l, h, colors=col, linewidth=0.8)
    ax.bar(x, np.maximum(np.abs(c - o), (h.max() - l.min()) * 0.0008), bottom=np.minimum(o, c), color=col, width=0.7)
    ax.plot(x, e50.iloc[-show:].to_numpy(), color="#f0b90b", lw=1.1, label="EMA50")
    if len(df) >= 200:
        ax.plot(x, e200.iloc[-show:].to_numpy(), color="#7e57c2", lw=1.1, label="EMA200")

    for name, price in (levels or {}).items():
        if price is not None and np.isfinite(price):
            ax.axhline(price, color="#888", ls="--", lw=0.8)
            ax.text(len(d) - 0.5, price, f" {name} {price:.2f}", color="#aaa", fontsize=8, va="bottom", ha="right")
    for label, side, entry, sl, tp in positions or []:
        ax.axhline(entry, color="#2962ff", lw=1)
        ax.text(1, entry, f"{label} {entry:.2f}", color="#5b8cff", fontsize=8, va="bottom")
        if sl:
            ax.axhline(sl, color=DOWN, ls=":", lw=1)
            ax.text(1, sl, f"SL {sl:.2f}", color=DOWN, fontsize=8, va="bottom")
        if tp:
            ax.axhline(tp, color=UP, ls=":", lw=1)
            ax.text(1, tp, f"TP {tp:.2f}", color=UP, fontsize=8, va="bottom")

    last = c[-1]
    ax.axhline(last, color=col[-1], lw=0.6, alpha=0.6)
    ax.annotate(f"{last:.2f}", xy=(len(d) - 1, last), xytext=(6, 0), textcoords="offset points",
                color="white", fontsize=9, va="center", bbox=dict(boxstyle="round,pad=0.2", fc=col[-1], ec="none"))

    ticks = np.linspace(0, len(d) - 1, 7).astype(int)
    fmt = "%d/%m %H:%M" if (d.index[-1] - d.index[0]) < pd.Timedelta(days=60) else "%d/%m/%y"
    ax.set_xticks(ticks)
    ax.set_xticklabels([d.index[i].strftime(fmt) for i in ticks], color=TXT, fontsize=8)
    ax.tick_params(axis="y", colors=TXT, labelsize=8)
    ax.yaxis.tick_right()
    ax.grid(color=GRID, lw=0.6)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.set_xlim(-1, len(d) + 3)
    ax.set_title(f"{title}   ({tz_label})", color=TXT, fontsize=11, loc="left")
    leg = ax.legend(loc="upper left", fontsize=8, facecolor=BG, edgecolor=GRID)
    for t in leg.get_texts():
        t.set_color(TXT)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()
