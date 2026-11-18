# Shotgun Momentum Bot

Chiến lược giao dịch kết hợp hai phong cách:

- **Bibirion** (trader cá nhân Nhật Bản, đầu những năm 2000): "Emerging Shotgun". Khi nhóm cổ phiếu nhỏ đang nóng, rải tiền vào khoảng 10 mã cùng lúc, cắt nhanh mã yếu và để mã mạnh tự gánh lời.
- **Paul Tudor Jones**: chỉ chơi khi giá nằm trên đường trung bình 200 ngày, mỗi lệnh phải có tiềm năng lời gấp nhiều lần mức lỗ, và cắt lỗ không do dự.

Thị trường mặc định: **altcoin trên Binance (spot, không đòn bẩy)**. Altcoin là thứ gần nhất với "cổ phiếu nhỏ tăng trưởng" mà Bibirion từng chơi. Có thể đổi sang cổ phiếu Việt Nam hoặc Nhật bằng cách thay phần lấy dữ liệu.

> ⚠️ Đây là dự án học tập và nghiên cứu. Luôn backtest, sau đó chạy giấy (paper trading) ít nhất 1–2 tháng trước khi dùng tiền thật. Kết quả quá khứ không đảm bảo tương lai.

---

## 1. Bộ luật giao dịch

### Bước 1: Xem "thời tiết" thị trường (bộ lọc chế độ)

Chỉ được mở lệnh mới khi **cả hai** điều kiện đều đúng:

| Luật | Nguồn | Điều kiện |
|---|---|---|
| Xu hướng dài | Paul Tudor Jones | Chỉ số chung (mặc định BTC) đóng cửa **trên** đường trung bình 200 ngày |
| Đà ngắn hạn | Bibirion | Đường trung bình 5 ngày của chỉ số chung **nằm trên** đường 25 ngày |

Nếu một trong hai sai thì không mở lệnh mới. Lệnh đang có vẫn được quản lý theo luật thoát bên dưới.

### Bước 2: Chọn mã ("bắn shotgun")

Mỗi ngày, từ danh sách altcoin đủ thanh khoản:

1. Loại các mã có khối lượng giao dịch trung bình 20 ngày dưới `MIN_VOLUME_USD`.
2. Chỉ giữ mã có giá trên đường 25 ngày **và** đường 5 ngày nằm trên đường 25 ngày.
3. Xếp hạng theo **mức tăng 5 ngày** và **khối lượng hôm nay so với trung bình 20 ngày**.
4. Lấy top `MAX_POSITIONS` mã (mặc định 10).

Bộ lọc thêm (khoá `filters` trong `config.yaml`, mỗi thị trường ghi đè được). Lấy ý từ hai repo ở mục 8 cộng với cách chọn mã của Bibirion:

| Bộ lọc | Nguồn | Điều kiện |
|---|---|---|
| `trend_ma: 200` | Repo PTJ 200 EMA | Chính mã đó đóng cửa trên EMA200 của nó, không chỉ BTC |
| `golden_cross: [50, 200]` | Repo Golden Cross | EMA50 > EMA200 của mã |
| `rs_lookback: 20` | PTJ / momentum | Mã tăng mạnh hơn chỉ số chuẩn trong 20 phiên |
| `cooldown_days: 10` | Bibirion "bỏ mã yếu" | Vừa lỗ ở mã nào thì 10 ngày không mua lại |
| `breakout_days: 20` | Bibirion | Đóng cửa cao nhất 20 phiên |
| `min_vol_ratio: 1.5` | Bibirion | Khối lượng hôm nay ≥ 1,5 lần trung bình |
| `max_extension: 0.25` | | Không đuổi mã đã cách MA25 quá 25% |
| `min_history: 200` | | Bỏ coin mới niêm yết (< 200 nến) |
| `fresh_cross_days` | Repo PTJ (crossover) | MA5 vừa cắt lên MA25 trong N phiên |

Crypto bật cả 8 bộ lọc đầu, cổ phiếu Mỹ bật 4 bộ lọc đầu.

### Bước 3: Chia vốn

- Không dùng đòn bẩy.
- Mỗi mã được tối đa `POSITION_PCT` số vốn (mặc định 2%, tức 1/50; để `null` thì dùng `1 / MAX_POSITIONS`). Với 10 mã, tối đa 20% vốn nằm trong thị trường.
- Mỗi lệnh chỉ được lỗ tối đa `RISK_PER_TRADE` (mặc định 1%) tổng vốn. Khối lượng mua được tính ngược từ khoảng cách đến điểm cắt lỗ:

```
số tiền mua = (vốn × RISK_PER_TRADE) / % khoảng cách đến cắt lỗ
(không vượt quá vốn / MAX_POSITIONS)
```

### Bước 4: Thoát lệnh (cắt nhanh, để lời chạy)

Thoát ngay khi gặp **bất kỳ** điều kiện nào:

| Điều kiện | Mặc định | Ý nghĩa |
|---|---|---|
| Cắt lỗ cứng | -4% | Sai là ra, không gồng |
| Hết thời gian | 2 ngày mà chưa lời ≥ +3% | Bibirion: mã không chạy thì bỏ |
| Gãy đà | Giá đóng cửa dưới đường 5 ngày | Hành vi giá đi ngược kỳ vọng |
| Chốt lời động | Khi lời ≥ +12% (gấp 3 lần mức cắt lỗ), dời cắt lỗ lên hòa vốn, rồi bám theo đỉnh cao nhất -6% | Để mã mạnh chạy xa |
| Thị trường xấu | Chỉ số chung đóng cửa dưới đường 200 ngày | Luật của PTJ: đóng hết |

Mục tiêu: tỷ lệ thắng có thể chỉ 30–40%, nhưng lệnh thắng trung bình phải lớn gấp 3–5 lần lệnh thua trung bình.

---

## 2. Tham số (config)

```yaml
exchange: binance
quote: USDT
benchmark: BTC/USDT
timeframe: 1d

# Bộ lọc thị trường
regime_long_ma: 200      # PTJ
regime_fast_ma: 5        # Bibirion
regime_slow_ma: 25       # Bibirion

# Chọn mã
min_volume_usd: 5000000
max_positions: 10
momentum_lookback: 5
volume_lookback: 20

# Quản lý rủi ro
risk_per_trade: 0.01
stop_loss_pct: 0.04
time_stop_days: 2
time_stop_min_gain: 0.03
trail_trigger_pct: 0.12
trail_pct: 0.06

# Chi phí (tính vào backtest)
fee_pct: 0.001
slippage_pct: 0.002
```

---

## 3. Cấu trúc dự án

```
shotgun-momentum/
├── config.yaml
├── data/
│   └── fetch.py          # tải nến ngày từ Binance qua ccxt, lưu CSV
├── strategy/
│   ├── regime.py         # bộ lọc thị trường (200 / 5 / 25)
│   ├── scanner.py        # lọc và xếp hạng altcoin
│   ├── sizing.py         # tính khối lượng mỗi lệnh
│   └── exits.py          # các luật thoát lệnh
├── backtest/
│   ├── engine.py         # chạy mô phỏng nhiều mã cùng lúc
│   └── report.py         # lợi nhuận, sụt giảm lớn nhất, tỷ lệ thắng, lời/lỗ TB
├── live/
│   └── paper.py          # chạy giấy, ghi log, chưa đặt lệnh thật
└── README.md
```

## 4. Công nghệ

| Phần | Thư viện | Lý do |
|---|---|---|
| Lấy dữ liệu | `ccxt` | Hỗ trợ Binance và hầu hết sàn crypto |
| Tính toán | `pandas`, `numpy` | Đường trung bình, xếp hạng |
| Backtest nhiều mã | `vectorbt` hoặc engine tự viết | `backtesting.py` chỉ chạy được một mã mỗi lần, không hợp với kiểu rải 10 mã |
| Báo cáo | `quantstats` | Biểu đồ vốn, sụt giảm, thống kê |

## 5. Cài đặt và chạy

```bash
git clone <repo-url> shotgun-momentum
cd shotgun-momentum
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt             # thêm `pip install quantstats` nếu muốn báo cáo HTML

python data/fetch.py --since 2020-01-01     # tải dữ liệu (gồm cả cặp đã ngừng giao dịch)
python backtest/engine.py                   # chạy backtest
python backtest/engine.py --experiments     # các bài kiểm tra ở mục 6
python backtest/engine.py --html report.html
python backtest/chart.py --market us_stocks --open   # biểu đồ tương tác kiểu TradingView (HTML thuần)
python live/paper.py                        # chạy giấy, mỗi ngày một lần sau 00:00 UTC
```

Ghi chú triển khai:

- Engine tự viết (`backtest/engine.py`), không dùng vectorbt, vì các luật thoát phụ thuộc vào đường đi của giá. `live/paper.py` dùng lại đúng engine này.
- Tín hiệu tính ở giá đóng cửa ngày t, lệnh mua/bán khớp ở giá mở cửa ngày t+1. Cắt lỗ cứng và trailing khớp trong phiên; nếu giá mở cửa đã nhảy qua điểm cắt lỗ thì khớp ở giá mở cửa.
- Time stop xét liên tục: từ ngày giữ thứ 2 trở đi, giá đóng cửa nào chưa lời ≥ +3% thì thoát.
- Nếu mạng chặn `api.binance.com`, đặt `public_api_url` trong `config.yaml` (mặc định đang dùng `data-api.binance.vision`).

## 6. Cần kiểm tra trong backtest

Kết quả với cấu hình hiện tại trong `config.yaml`, chạy bằng `python backtest/engine.py --market <thị trường> --experiments`.
Crypto tính từ 2020, cổ phiếu Mỹ từ 2015.

- [x] **Có thắng chỉ mua giữ sau phí không?** Không, ở cả hai thị trường.
  - Crypto: −14,6%, trong khi giữ BTC +925%.
  - Cổ phiếu Mỹ: +21,7%, trong khi giữ SPY +345%.
  - Bù lại, bot sụt giảm ít hơn nhiều: −3,4% so với −33,7% của SPY.
- [x] **Sụt giảm lớn nhất dưới 25%?** Có. Crypto −15%, cổ phiếu Mỹ −3,4%.
- [x] **Lời TB / lỗ TB ≥ 3?** Chưa đạt. Crypto 2,31, cổ phiếu Mỹ 2,97.
- [x] **Bỏ riêng bộ lọc 200 ngày thì sao?** Kết quả kém hơn một chút.
  - Crypto: −15,8% so với −14,6%.
  - Cổ phiếu Mỹ: +19,0% so với +21,7%, MDD −4,8% so với −3,4%.
- [x] **Đổi số mã có ổn định không?**
  - Crypto lỗ với cả 5, 10 và 15 mã.
  - Cổ phiếu Mỹ lời với cả ba: +8% (5 mã), +22% (10 mã), +24% (15 mã), MDD đều dưới 5%.
- [x] **Test riêng từng giai đoạn:**
  - Crypto: 2021 −3,1%, 2022 không có lệnh (bộ lọc chặn hết), 2023 −1,3%.
  - Cổ phiếu Mỹ: lời khoảng +1% mỗi năm 2021, 2022 và 2023.
  - Kết quả chạy riêng từng năm ở mục 9.
- [ ] **Chưa làm: danh sách mã phải gồm cả mã đã bị gỡ khỏi sàn.** Hiện chỉ có các mã còn sống, nên kết quả có thể đẹp hơn thực tế (lỗi "chỉ nhìn kẻ sống sót"). Với cổ phiếu Mỹ, cần danh sách thành phần S&P 500 lịch sử.

### Kết quả thử bộ lọc ($1000, 2% mỗi lệnh, phí đã trừ)

| | Lệnh | Vốn cuối | MDD | Profit factor |
|---|---|---|---|---|
| Crypto, chưa lọc | 6420 | $229 | -78% | 0.70 |
| Crypto, 8 bộ lọc | 859 | $854 | -15% | 0.72 |
| Cổ phiếu Mỹ, chưa lọc | 8006 | $925 | -16% | 0.96 |
| Cổ phiếu Mỹ, 4 bộ lọc + thoát nới (-8%, 10 ngày, MA25, trailing 20/10) | 1329 | $1268 | -5% | 1.35 |

Bộ lọc giảm mạnh số lệnh và sụt giảm, nhưng với luật thoát gốc thì trung bình mỗi lệnh vẫn lỗ (profit factor < 1). Với crypto, 601/859 lệnh dính cắt lỗ -4% ngay trong ngày đầu: mua ở giá mở cửa sau một cây nến bứt phá thường là mua đúng đỉnh của cú pump.

## 7. Rủi ro đã biết

- Altcoin có thể sập 30–50% qua một đêm, khi đó lệnh cắt lỗ -4% có thể khớp xa hơn nhiều.
- Mọi altcoin thường lên xuống cùng BTC, nên 10 mã chưa chắc đã là 10 cơ hội độc lập.
- Phong cách giữ 1–2 ngày sinh ra nhiều lệnh, nên phí giao dịch ảnh hưởng lớn.
- Bibirion chơi cổ phiếu Nhật đầu những năm 2000. Thị trường crypto hiện nay khác, nên phải tự kiểm chứng.

## 8. Tham khảo

- Paul Tudor Jones 200 EMA backtest: https://github.com/Neyt/Paul-Tudor-Jones-200-EMA-strategy
- Golden Cross / Death Cross nhiều loại tài sản: https://github.com/MayankBharati/Quant-Trading-Project
- ccxt: https://github.com/ccxt/ccxt
- vectorbt: https://github.com/polakowo/vectorbt
- EMA pullback + ATR stop (MT5): https://github.com/MT5-Automation-Labs/mt5-ema-pullback-ea
- XAUUSD trend pullback (MT5): https://github.com/srpo93/XAUUSD-bot
- Freqtrade strategies: https://github.com/freqtrade/freqtrade-strategies
- Freqtrade strategies that work: https://github.com/paulcpk/freqtrade-strategies-that-work
- Bot EMA đơn giản: https://github.com/jcarenza67/alpaca-ema-bot, https://github.com/a2gs/EMA_Binance_BOT, https://github.com/jimtin/algorithmic_trading_bot

## 9. Đề xuất cải tiến (đã đưa vào bot)

Lỗi lớn nhất hiện tại: với crypto, 601/859 lệnh dính cắt lỗ -4% ngay trong ngày đầu. Bot mua ở giá mở cửa sau một cây nến bứt phá, tức là thường mua đúng đỉnh cú pump. Các repo ở mục 8 xử lý chuyện này như sau:

| Ý tưởng | Lấy từ | Bot hiện tại | Giải quyết |
|---|---|---|---|
| Chờ giá hồi về EMA nhanh rồi mới vào: nến chạm EMA21 mà không thủng EMA chậm, sau đó nến xanh đóng lại trên EMA21 (có thể thêm RSI ≤ 45 lúc hồi, ≥ 50 lúc vào) | MT5 EA, XAUUSD, TrendRider | Mua ngay sau nến bứt phá | Mua đỉnh pump |
| Cắt lỗ theo ATR (1,5–2 ATR) thay vì -4% cố định | MT5 EA, XAUUSD, FixedRiskRewardLoss | -4% | Mã biến động mạnh bị quét stop. Chia vốn theo rủi ro nên mã càng biến động thì tự mua càng ít |
| EMA dài phải dốc lên trong N nến | MT5 EA, XAUUSD | Chỉ cần giá nằm trên | Giá lình xình quanh một đường EMA phẳng |
| ADX > 20–25 và +DI > −DI | ADXMomentum, TrendRider | Không có | Thị trường đi ngang |
| RSI < 70, bỏ nến có khối lượng tăng quá đột biến | TrendRider, EMASkipPump | Xếp hạng còn thưởng điểm cho khối lượng đột biến | Mua lúc quá nóng |
| Không đuổi giá: bỏ lệnh nếu giá cách EMA21 quá 1,5 ATR | XAUUSD | `max_extension` theo % | Đuổi mã đã chạy xa |
| Ngừng mở lệnh mới sau 3 lần cắt lỗ trong một khoảng thời gian (StoplossGuard), hoặc khi tài khoản sụt quá 10% (MaxDrawdown) | freqtrade TrendRider | Chỉ nghỉ theo từng mã | Chuỗi thua liên tục |
| Dời stop về hoà vốn ở 1R, chốt lời theo R (2–3,5R) | XAUUSD, FixedRiskRewardLoss | Trailing kích hoạt ở +12% | Lời rồi lại thành lỗ |

(R = khoảng cách từ giá vào đến điểm cắt lỗ ban đầu.)

Cách làm:

1. Trước tiên, backtest riêng từng phương pháp của các repo trên cùng dữ liệu đã tải (thư mục `clone_repo/`) để biết phương pháp nào thật sự có lời sau phí.
2. Đưa vào bot **từng ý một**, dưới dạng tuỳ chọn trong `config.yaml` (giữ nguyên luật gốc), và so với bản hiện tại trên cả crypto lẫn cổ phiếu Mỹ. Thứ tự: vào lệnh kiểu hồi về EMA → cắt lỗ ATR → ADX / EMA dốc lên → StoplossGuard + MaxDrawdown → hoà vốn ở 1R và chốt lời theo R.
3. Ghép những ý có ích, rồi kiểm tra riêng từng năm 2021/2022/2023 để tránh kết quả chỉ đẹp nhờ chỉnh tham số khớp quá khứ.

### Kết quả

Mỗi ý là một tuỳ chọn trong `config.yaml` (mặc định tắt): `filters.pullback`, `filters.max_dist_atr`, `filters.ema_slope`, `filters.adx_min`, `filters.max_rsi`, `filters.max_vol_ratio`, `rank_by`, `stop_atr`, `breakeven_r`, `take_profit_r`, `stoploss_guard`, `drawdown_guard`. Script `backtest/ablation.py` bật **từng ý một** trên nền cấu hình gốc, rồi ghép lại. Mỗi năm được backtest riêng từ $1000, tối đa 10 lệnh, trần 10% vốn mỗi lệnh, đã trừ phí.

```bash
.venv/bin/python backtest/ablation.py --market us_stocks   # → backtest/results/<market>/ablation.md
```

**Cổ phiếu Mỹ, 2015–2026 (12 năm):**

| Biến thể | Năm lời | ROI trung vị | Năm tệ nhất | MDD tệ nhất | Lệnh | PF |
|---|---|---|---|---|---|---|
| Bot gốc | 5/12 | -4% | -22% | -28% | 8047 | 0.94 |
| Hồi về EMA21 | 2/12 | -4% | -20% | -23% | 7873 | 0.94 |
| Cắt lỗ 2 ATR | 4/12 | -1% | -23% | -27% | 7914 | 0.97 |
| StoplossGuard + MaxDrawdown | 4/12 | -1% | -15% | -18% | 5981 | 0.97 |
| Hoà vốn 1R + chốt 2,5R, giữ time stop 2 ngày | 4/12 | -8% | -23% | -33% | 8595 | 0.92 |
| Chỉ thoát theo R (bỏ time stop và MA5) | 9/12 | +4% | -6% | -19% | 2066 | 1.14 |
| Hồi EMA21 + 2 ATR + thoát theo R | 8/12 | +8% | -7% | -19% | 1307 | 1.25 |
| **Ghép tất cả** (+ EMA200 dốc, ADX ≥ 25, RSI < 70, guards) | **11/12** | **+6%** | **-5%** | **-14%** | 1148 | **1.34** |
| Giữ SPY | 10/12 | +18% | -19% | -34% | – | – |

- Từng ý riêng lẻ gần như không giúp được gì. Nguyên nhân chính là **time stop 2 ngày + thoát dưới MA5** cắt lệnh trước khi kịp lời. Khi bỏ hai luật đó và thoát theo R, các ý còn lại mới có tác dụng.
- Bản ghép giữ lệnh trung bình 14 phiên (trung vị 10), vẫn là đánh ngắn.
- Kiểm tra độ bền của bản ghép: trượt giá ×3, 5 hoặc 20 lệnh, chốt 2R hoặc 3,5R, cắt lỗ 1,5 ATR, bỏ hoà vốn. Mọi biến thể đều lời 8–10/12 năm, và lời trung bình ở cả hai nửa 2015–2020 và 2021–2026.
- Lãi mỗi năm thấp hơn giữ SPY (trung vị +6% so với +18%). Bù lại, năm tệ nhất chỉ -5% (SPY -19%) và bot chỉ có lệnh khi thị trường tốt.
- **Bản ghép đã được bật cho `us_stocks`** trong `config.yaml`. Chạy liên tục 2015–2026 với cấu hình thật (2% vốn/lệnh): $1000 → $1217, MDD -3,4%, PF 1,37, 1103 lệnh.

**Crypto, 2020–2026:** không ý nào, kể cả bản ghép, lời quá 2/7 năm. PF tốt nhất là 0,86 (ADX). Cắt lỗ ATR giảm tỷ lệ dính stop ngày đầu từ 69% xuống 9%, nhưng vẫn lỗ. **Cấu hình crypto vì vậy giữ nguyên.**

**Lưu ý:**
- Danh sách cổ phiếu Mỹ vẫn bị lỗi "chỉ nhìn kẻ sống sót" (mục 6).
- Biểu đồ `backtest/chart.py` đã có đủ các tuỳ chọn mới.
  - **Chế độ backtest:** vẽ đúng mức cắt lỗ ATR và mức chốt lời theo R của từng lệnh.
  - **Chế độ live:** phần mô phỏng JS có các bộ lọc mới (hồi EMA21, ATR, EMA dốc, ADX, RSI, khối lượng), cắt lỗ ATR, hoà vốn, chốt lời theo R và các guard. Có thêm ô chọn bộ luật: "Luật crypto" hoặc "Ghép mục 9".
  - Đã đối chiếu từng lệnh giữa JS và engine Python trên 8 cổ phiếu và 4 coin, chạy riêng từng mã: trùng 100%.
