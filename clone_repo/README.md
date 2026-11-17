# clone_repo: backtest phương pháp của các repo tham khảo

Mục đích: trước khi sửa bot, chạy thử phương pháp của từng repo GitHub trên **cùng dữ liệu nến ngày đã tải** (`data/csv/`) với **cùng điều kiện**, để xem phương pháp nào thật sự có lời sau phí.

```
clone_repo/
├── repos/          # bản clone các repo (git clone --depth 1), chỉ để đọc, không chạy code của họ; bị .gitignore
├── indicators.py   # EMA, RSI, ATR, ADX/DI, MACD, OBV theo cách tính của TA-Lib / MetaTrader
├── strategies.py   # logic vào/ra của từng repo, viết lại bằng pandas
├── sim.py          # mô phỏng danh mục dùng chung
├── run.py          # chạy tất cả, in bảng, lưu kết quả và biểu đồ
└── results/        # kết quả (bị .gitignore)
```

## Chạy

```bash
.venv/bin/python clone_repo/run.py --market crypto --chart
.venv/bin/python clone_repo/run.py --market us_stocks --chart
.venv/bin/python clone_repo/run.py --market crypto --only xau_pullback,mt5_pullback --start 2022-01-01
```

Kết quả nằm trong `clone_repo/results/<market>/`:
- `summary.md`: bảng xếp hạng, ROI theo năm, ghi chú cách chuyển từng repo.
- `trades_<key>.csv`: từng lệnh.
- `chart_<key>.html`: biểu đồ tương tác (cùng giao diện với `backtest/chart_template.html`).

## Điều kiện chung cho mọi chiến lược

- Vốn $1000, tối đa 10 lệnh cùng lúc, mỗi lệnh 1/10 vốn. Spot, không đòn bẩy, chỉ mua (không bán khống).
- Tín hiệu ở giá đóng cửa ngày t, khớp ở giá mở cửa ngày t+1.
- Phí và trượt giá lấy từ `config.yaml`: crypto 0,1% + 0,2% mỗi chiều, cổ phiếu Mỹ 0,05% + 0,05%.
- Chỉ mua mã có khối lượng tiền trung bình 20 phiên ≥ `min_volume_usd` của thị trường. Nhiều tín hiệu hơn số chỗ trống thì ưu tiên mã thanh khoản cao nhất.
- Nếu trong cùng một phiên giá chạm cả cắt lỗ lẫn chốt lời, giả định chạm cắt lỗ trước (bảo thủ).
- Bot hiện tại cũng được chạy lại với cùng $1000 và 1/10 vốn mỗi lệnh để so sánh. Code của bot không bị sửa.

## Những điểm khác với repo gốc

- **Khung thời gian**: phần lớn repo chạy nến 1h, 15 phút hoặc 5 phút; ở đây tất cả chạy nến ngày với **đúng tham số gốc**. Ví dụ EMA800 trên nến 1h chỉ khoảng 33 ngày, nhưng trên nến ngày là hơn 2 năm. Kết quả vì vậy cho biết *logic* đó có hợp với nến ngày không, không phải kết quả của repo trên khung gốc.
- **Bảng ROI của freqtrade** tính theo phút. Trên nến ngày, các mốc dưới 1 ngày gộp lại, nên TrendRider (ROI = 0 sau 764 phút) sẽ chốt ngay từ ngày thứ 2 khi đang có lời.
- **Lọc phiên giao dịch và spread** của các EA MT5 bị bỏ (không áp dụng cho nến ngày).
- **Chia vốn theo % rủi ro** của các EA MT5 và jimtin được thay bằng chia đều vốn, để so sánh công bằng.
- Chi tiết cho từng repo nằm ở phần ghi chú của `summary.md`.

## Lưu ý khi đọc kết quả

- **Cổ phiếu Mỹ bị lỗi "chỉ nhìn kẻ sống sót"**: danh sách mã là S&P 500 + Nasdaq 100 *hiện tại*, nên những mã đã rớt khỏi chỉ số không có trong dữ liệu. Lỗi này làm đẹp kết quả của các chiến lược giữ lệnh lâu.
- Crypto có cả những cặp đã bị gỡ khỏi sàn. Khi một mã hết dữ liệu, lệnh đang mở được bán ở giá cuối cùng.
- Một năm thắng chưa đủ để kết luận. Xem bảng ROI theo năm, đặc biệt 2021 (lên), 2022 (xuống), 2023 (đi ngang).

## Vàng và forex (XAUUSD-bot)

```bash
.venv/bin/python clone_repo/fetch_fx.py                 # tải dữ liệu vào data/csv/fx/{1d,1h,15m}/
.venv/bin/python clone_repo/xau_ea.py --fx              # mô phỏng EA trên vàng M15 + forex H1
.venv/bin/python clone_repo/xau_ea.py --spread 0.5 --capital 1000
```

- **Dữ liệu vàng M15:** Binance PAXGUSDT (token bảo chứng 1 oz vàng), từ 08/2020. So với hợp đồng tương lai GC=F: tương quan lợi nhuận nến 15 phút là 0,977, lợi nhuận theo năm chênh 1–2%. Test bắt đầu từ 2021 vì năm 2020 PAXG còn ít người giao dịch. Dukascopy chặn tải tự động (HTTP 429). Yahoo chỉ có 60 ngày nến 15m và khoảng 2 năm nến 1h.
- **Forex:** Yahoo, nến 1h khoảng 2 năm, nến ngày từ 2003. Vì không đủ nến M15, EA được chạy ở khung H1, xu hướng H4.
- **`xau_ea.py`** mô phỏng sát code MQL5 (xem docstring): giờ server = giờ New York + 7, bỏ nến cuối tuần, mua ở ask / bán ở bid với spread cố định, ATR tính kiểu MT5, khối lượng làm tròn xuống 0,01 lot. Với vốn $1000, nhiều lệnh vàng bị bỏ vì 1% rủi ro không đủ cho 0,01 lot; mặc định vì vậy là $10,000.
- Kết quả ở `clone_repo/results/xau/`.

## Repo EA vàng (FvgGold, BAKOME, GoldTraderEA, XAUUSD-Trading-Bots, mt5-gold-ai-trader)

```bash
.venv/bin/python clone_repo/fetch_fx.py binance5m yahoo1m    # nến PAXG M5 + vàng/DXY 1 phút (Yahoo, ~7 ngày)
.venv/bin/python clone_repo/gold_repos.py                    # tất cả; --only fvg,gte,bak,pdh,ai,alpha; --spread 0.5
```

- `gold_engine.py`: engine kiểu MT5 dùng chung (bid/ask, lot 0,01 = 1 oz, ký quỹ 1:100, lệnh limit, chốt một phần). Nến khớp lệnh limit được xét lại bằng nến M5 bên trong để biết giá chạm SL hay TP trước.
- `gold_repos.py` (FvgGold M15, GoldTraderEA H1) và `gold_repos_m5.py` (BAKOME, PDH/PDL, AI trader M5, alpha_v3 M1): logic viết lại theo đúng code, **kể cả lỗi**. Mỗi repo có thêm bản "sửa lỗi" để xem ý tưởng gốc.
- egade369/ea-xauusd chỉ có file `.ex5` đã biên dịch, không có mã nguồn, nên không test được.
- Kết quả: `clone_repo/results/gold/gold_repos_spread*.md`. Vốn $10,000 (alpha_v3: $1,000), 2021 → 11/2026, spread $0,30.

| Repo | Kết quả | Ghi chú |
|---|---|---|
| FvgGold | PF 0,92–0,99, ROI −0,1% đến −31% | Spread 0 vẫn chỉ hoà vốn. README ghi +48,7%/6 tháng với lot 0,01 là không thể: mỗi lệnh thắng chỉ được ~$6,75. |
| GoldTraderEA | +43% (gốc), +180% (sửa lỗi), MDD −54% | Lời đến từ lệnh mua năm 2025; lệnh bán lỗ. Giữ 10 oz vàng cùng kỳ lời +209%. |
| BAKOME | 0 lệnh (gốc), −98% (sửa hết) | Code không dùng FVG/OB để vào lệnh, chỉ giờ + EMA200 H4. Lot sai nên sàn từ chối, bộ đếm ngày không reset. |
| PDH/PDL | +5,6%, PF 1,28, nhưng chỉ 36 lệnh / 6 năm | Spread 0,5 thì PF 1,07. Quá ít lệnh để kết luận. |
| mt5-gold-ai-trader | −95% đến −100% | "AI" không ảnh hưởng tín hiệu; lot sai hệ số (rủi ro thật ~20%/lệnh). |
| alpha_v3 | −6,5% / 7 ngày | Chỉ có 7 ngày nến 1 phút (Yahoo). |

## Bot 2 chiều vàng + forex (`two_way.py`)

```bash
.venv/bin/python clone_repo/two_way.py      # → clone_repo/results/two_way/two_way.md
```

Bản ghép đang lời với cổ phiếu Mỹ (hồi về EMA21, cắt lỗ 2 ATR, hoà vốn 1R, chốt 2,5R), thêm chiều bán, chỉ vào lệnh khi xu hướng rõ (EMA200 dốc, EMA50/200, ADX ≥ 25). Rủi ro 1%/lệnh, mỗi mã chạy riêng, kết quả tính bằng R.

**Lỗi dữ liệu đã sửa:** nến ngày forex của Yahoo từ ~2010 có `close` là giá lúc *đầu* ngày (≈ open). `repair_fx_daily()` dựng lại open/close; so với nến ngày dựng từ dữ liệu 1h: tương quan lợi nhuận 0,94–0,98, cùng màu nến 94–97%. Vàng (GC=F), DXY và dữ liệu 1h không bị lỗi.

| Khung | 2 chiều + lọc | 2 chiều, không lọc | Chỉ mua + lọc |
|---|---|---|---|
| D1 (2004–2026, vàng + 10 cặp) | PF 1,02, +7R, 11/23 năm lời | PF 0,95, −47R | PF 0,90, −18R |
| H4 (vàng 2021–, forex ~2 năm) | PF 0,79, −51R | PF 0,87, −69R | PF 1,03, +3R |
| H1 | PF 0,83, −171R | PF 0,86, −320R | PF 0,89, −50R |
| M15 (chỉ vàng) | PF 0,74, −249R | PF 0,73, −595R | PF 0,83, −84R |

- Bộ lọc "không đánh khi đi ngang" giảm lỗ rõ (bớt ~2/3 số lệnh), nhưng không tự tạo ra lợi thế.
- Khung càng ngắn càng lỗ: spread chiếm phần lớn hơn của 1R.
- Lưới 24 tham số D1: forex lỗ hoặc hoà ở hầu hết; vàng lời ở cả 24, nhưng lời gần như chỉ từ 3 lệnh mua giữ nhiều tháng (2006, 2011, 2026), lệnh bán ≈ 0R, tức là ăn theo đà tăng dài của vàng.
- Chưa tính swap qua đêm.

## Bot hồi về trung bình 2 chiều (mean_reversion.py)

`.venv/bin/python clone_repo/mean_reversion.py` → `results/mean_reversion/mean_reversion.md`

Luật: RSI(2) < 10 thì mua, RSI(2) > 90 thì bán, khớp ở giá mở nến sau. Thoát khi giá đóng cửa vượt lại SMA5, hoặc sau 10 nến, hoặc chạm cắt lỗ 2,5 ATR (1R = 1% vốn).

| Khung | Chỉ khi đi ngang (ADX<20) | Không lọc | Thuận xu hướng dài (SMA200) |
|---|---|---|---|
| D1 (2003–2026) | PF 0,96, −30R | PF 1,01, +12R | **PF 1,07, +41R**, lời 12/23 năm |
| H4 | PF 0,85, −63R | PF 0,87, −128R | PF 0,97, −10R |
| H1 | PF 0,83, −263R | PF 0,86, −548R | PF 0,88, −185R |

- Biến thể **thuận xu hướng dài trên D1** có lời ở 17/18 bộ tham số (PF 0,99–1,48). Bộ tốt nhất là RSI(3) 10/90, thoát ở SMA10: forex +54R, PF 1,26.
  - Lợi thế này còn khi spread ×3 (PF 1,20).
  - Lợi thế yếu dần: PF 1,60 giai đoạn 2003–2010, chỉ còn PF 1,15 từ 2011.
  - Mỗi lệnh chỉ lời trung bình +0,07R và giữ khoảng 8 ngày, nên swap có thể ăn bớt một phần.
- Lọc "chỉ khi đi ngang" theo ADX lại làm kết quả **kém hơn**.
- Vàng hoà ở mọi biến thể. Khung H4 và H1 lỗ vì chi phí giao dịch.

Kiểm tra dữ liệu PAXG so với GC=F (2021–2026): tương quan lợi nhuận ngày 0,84, chênh giá trung bình 0,75%, biên độ ngày 1,76% so với 1,66%. PAXG chỉ là dữ liệu thay thế cho XAUUSD. Các EA vào lệnh theo giờ trong ngày (killzone, FVG trên M5) nên được kiểm lại bằng MT5 Strategy Tester với tick thật của broker.
