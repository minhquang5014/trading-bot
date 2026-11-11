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

- [ ] So với chỉ mua giữ BTC, chiến lược có thắng sau khi trừ phí không?
- [ ] Sụt giảm lớn nhất có chấp nhận được không (mục tiêu dưới 25%)?
- [ ] Lời trung bình / lỗ trung bình có đạt ≥ 3 không?
- [ ] Bỏ riêng bộ lọc 200 ngày thì kết quả tệ hơn bao nhiêu?
- [ ] Đổi 10 mã thành 5 hoặc 15 mã thì kết quả có ổn định không, hay chỉ đẹp ở đúng một con số?
- [ ] Test riêng từng giai đoạn: thị trường lên 2021, xuống 2022, đi ngang 2023.
- [ ] Danh sách altcoin phải gồm cả những mã **đã bị gỡ khỏi sàn**, nếu không kết quả sẽ đẹp giả (lỗi "chỉ nhìn kẻ sống sót").

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
