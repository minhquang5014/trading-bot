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

## 3b. Bot Telegram + MT5 (PF Markets)

> 🚧 **Đang làm, chưa chạy thử với broker thật.** Code mới chỉ qua kiểm tra cú pháp. Bước tiếp theo: cài MT5 của PF Markets, chạy `--check`, rồi test từng lệnh trên tài khoản **demo**.

Bot Telegram điều khiển tài khoản MT5: xem giá, xem biểu đồ vàng mới nhất từ broker, xem tài khoản, đặt/đóng lệnh và nhận cảnh báo tín hiệu PDH/PDL.

```
broker/mt5_client.py       # kết nối MT5 (thư viện MetaTrader5): nến, tick, tài khoản, đặt/đóng/sửa lệnh, tính lot theo rủi ro
telegram_bot/bot.py        # vòng lặp bot, các lệnh /chart /buy ..., xác nhận bằng nút bấm
telegram_bot/telegram_api.py  # gọi Telegram Bot API (long polling, một luồng)
telegram_bot/chart.py      # vẽ nến PNG: EMA50/200, PDH/PDL, giá vào / SL / TP của lệnh đang mở
telegram_bot/signals.py    # tín hiệu PDH/PDL trên nến M5, dùng lại lớp PdhPdl đã backtest trong clone_repo/
.env.example               # mẫu file bí mật (login MT5, token Telegram)
```

### Cài đặt (Windows)

1. Cài **terminal MT5 của PF Markets**, đăng nhập tài khoản demo, bật *Tools → Options → Expert Advisors → Allow algorithmic trading*. Terminal phải luôn mở khi bot chạy (thư viện `MetaTrader5` điều khiển terminal, broker không có API web riêng).
2. Tạo bot Telegram: nhắn `@BotFather` → `/newbot` → lấy token.
3. `copy .env.example .env` rồi điền `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (đúng tên server trong MT5) và `TELEGRAM_BOT_TOKEN`. File `.env` đã nằm trong `.gitignore`.
4. Cài thư viện. Trên Windows cần `PYTHONUTF8=1` vì `requirements.txt` có chữ tiếng Việt:
   ```powershell
   $env:PYTHONUTF8 = "1"
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
5. Lấy chat id: để trống `TELEGRAM_ALLOWED_CHAT_IDS`, chạy bot, nhắn `/id`, rồi điền số nhận được vào `.env`.
6. Kiểm tra kết nối, sau đó chạy bot:
   ```powershell
   .venv\Scripts\python telegram_bot\bot.py --check
   .venv\Scripts\python telegram_bot\bot.py
   ```

### Lệnh Telegram

| Lệnh | Việc làm |
|---|---|
| `/chart [M5\|M15\|H1\|H4\|D1] [số nến] [mã]` | Biểu đồ mới nhất từ broker (mặc định M15, 150 nến), giờ Việt Nam |
| `/price [mã]` | Bid / ask / spread |
| `/account` | Số dư, equity, margin |
| `/positions` | Lệnh đang mở |
| `/buy 0.01 [sl] [tp]`, `/sell ...` | Đặt lệnh thị trường. `/buy 1% 4050` thì lot được tính theo 1% rủi ro (bắt buộc có SL) |
| `/close <ticket\|all>` | Đóng lệnh |
| `/signals on\|off` | Bật/tắt cảnh báo PDH/PDL M5 |
| `/id` | Xem chat id |

Tham số không bí mật nằm trong `config.yaml`, khoá `mt5_bot` (mã, múi giờ, `max_lot`, % rủi ro cho tín hiệu...).

### Cảnh báo tín hiệu

Mỗi khi một nến M5 đóng, bot chạy logic PDH/PDL (phá đỉnh/đáy hôm qua rồi retest, xem `clone_repo/README.md`). Khi có tín hiệu, bot gửi giá vào, SL (1 ATR), TP1 (1,5R) và TP2 (3R), kèm lot tính theo `signal_risk_pct`. Bot **không tự vào lệnh**: muốn vào thì bấm *Xác nhận* trong 5 phút. Sau khi khớp, bot tính lại SL/TP theo giá khớp thật và tự chốt 50% khi giá chạm TP1. Lưu ý: backtest chiến lược này chỉ có 37 lệnh trong 6 năm (PF 1,21 ở spread $0,30, PF 1,02 ở spread $0,50), quá ít để tin là có lợi thế.

### An toàn

- Chỉ các chat id trong `TELEGRAM_ALLOWED_CHAT_IDS` dùng được bot; chat khác chỉ nhận được `/id`.
- Mọi lệnh đặt/đóng đều phải bấm nút xác nhận, hết hạn sau 60 giây.
- Tài khoản **thật** bị khoá đặt lệnh, trừ khi đặt `ALLOW_LIVE_TRADING=true`.
- `max_lot` chặn mọi lệnh vượt cỡ.

### Việc còn lại

- [ ] Cài MT5 PF Markets, chạy `--check` (tên mã vàng, độ lệch giờ server, kiểu khớp lệnh).
- [ ] Test `/chart`, `/price`, `/account`, rồi `/buy 0.01` + `/close` trên demo.
- [ ] So tín hiệu PDH/PDL live với backtest. Bài test offline (tín hiệu live có khớp đúng các nến của backtest không) đang dở.
- [ ] Chưa có: tự chạy lại khi máy khởi động, log lệnh ra CSV.

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

- [ ] So với chỉ mua giữ BTC, chiến lược có thắng sau khi trừ phí không?
- [ ] Sụt giảm lớn nhất có chấp nhận được không (mục tiêu dưới 25%)?
- [ ] Lời trung bình / lỗ trung bình có đạt ≥ 3 không?
- [ ] Bỏ riêng bộ lọc 200 ngày thì kết quả tệ hơn bao nhiêu?
- [ ] Đổi 10 mã thành 5 hoặc 15 mã thì kết quả có ổn định không, hay chỉ đẹp ở đúng một con số?
- [ ] Test riêng từng giai đoạn: thị trường lên 2021, xuống 2022, đi ngang 2023.
- [ ] Danh sách altcoin phải gồm cả những mã **đã bị gỡ khỏi sàn**, nếu không kết quả sẽ đẹp giả (lỗi "chỉ nhìn kẻ sống sót").

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
- Biểu đồ `backtest/chart.py` mô phỏng lại bằng JavaScript và **chưa có** các tuỳ chọn mới, nên với `us_stocks` biểu đồ sẽ khác backtest.
