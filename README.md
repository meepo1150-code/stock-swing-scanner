# 📈 Stock Swing Scanner — คู่มือติดตั้งและใช้งาน

> ⚠️ **สำคัญ:** โค้ดนี้เขียนและทดสอบ logic เสร็จสมบูรณ์แล้ว (ผ่าน unit test ทุกเงื่อนไข รวม fundamental layer ใหม่)
> แต่ **ยังไม่ได้ทดสอบดึงข้อมูลจริงจาก Yahoo Finance** เพราะ sandbox ที่ใช้พัฒนาบล็อก network
> ไปที่ `query1/query2.finance.yahoo.com` — ต้องรันบนเครื่องของคุณเองเพื่อทดสอบกับข้อมูลจริง

## 🆕 มีอะไรใหม่ในเวอร์ชันนี้

1. **Fundamental Filter** — กรองหุ้นที่งบการเงินแย่ผิดปกติออกก่อนเข้า setup (debt/equity สูงเกิน, current ratio ต่ำเกิน)
2. **Fundamental Score (0-100)** — คะแนนเชิงคุณภาพจาก profitability, growth, financial health, valuation แสดงคู่กับ setup ที่ match
3. **Setup ที่ 6: Earnings Surprise Momentum** — จับช่วงหุ้นที่เพิ่ง beat earnings estimate ชัดเจนและราคายัง react บวกอยู่
4. **Dashboard HTML ที่ host ได้จริง** — ไฟล์อยู่ในโฟลเดอร์ `dashboard/` พร้อม deploy ขึ้น GitHub Pages ได้ (ดู `DEPLOY_GITHUB_PAGES.md`)
5. **ดับเบิลคลิกแล้วรันได้เลย (macOS)** — `scan_and_open.command` สแกน + เปิด dashboard ให้อัตโนมัติ ไม่ต้องพิมพ์ terminal เอง
6. **หารายชื่อหุ้นจริงอัตโนมัติ** — `build_tickers.py` ดึงจาก Nasdaq, `rank_tickers_by_marketcap.py` ตัดเหลือ Top N ตาม market cap
7. **กัน Yahoo Finance rate limit** — retry แบบ backoff อัตโนมัติ + checkpoint/resume ถ้าสแกนค้างกลางทางไม่ต้องเริ่มใหม่หมด

> ⚠️ **เรื่องสำคัญที่ควรเข้าใจ:** การเพิ่มเงื่อนไขเชิงพื้นฐานไม่ได้ทำให้ระบบนี้ "ชนะตลาด" ได้
> ข้อมูลพื้นฐาน (P/E, debt, growth) เป็นข้อมูล public ที่ทุกคนเข้าถึงได้เหมือนกัน — Fundamental layer ในนี้
> ทำหน้าที่เป็น **risk filter** ลดความเสี่ยงเจอหุ้นงบแย่ ไม่ใช่เครื่องมือหา "หุ้นที่ดีกว่าตลาด"
> สำหรับ swing trade 1-5 วัน price action/volume/momentum ยังมีน้ำหนักมากกว่า fundamental มาก

---

## 📁 ไฟล์ที่ได้รับ

```
stock_scanner.py             → โค้ดหลัก สแกนหุ้นตาม 6 เงื่อนไข Setup + Fundamental layer
build_tickers.py              → ดึงรายชื่อหุ้นจริงจาก Nasdaq มาแทน 16 ตัวอย่าง (รันครั้งเดียว/อัปเดตเป็นระยะ)
rank_tickers_by_marketcap.py  → ตัดรายชื่อหุ้นให้เหลือ Top N ตาม market cap (ทำให้สแกนรายวันเร็วขึ้น)
scan_and_open.command         → (macOS) ดับเบิลคลิกเพื่อสแกน + เปิด dashboard อัตโนมัติ
test_logic.py                 → unit test (ทดสอบ logic ด้วยข้อมูลจำลอง — รันผ่านแล้ว ✅)
tickers.csv                   → รายชื่อ ticker ตัวอย่าง 16 ตัว (ไม่ใช่ Nasdaq+Russell 2000 ฉบับเต็ม)
dashboard/
  ├── index.html               → Dashboard เว็บ (host บน GitHub Pages ได้)
  └── scanner_results.json     → ข้อมูลตัวอย่าง (demo data จำลอง ไม่ใช่ข้อมูลจริง)
DEPLOY_GITHUB_PAGES.md        → คู่มือ deploy dashboard ขึ้น GitHub Pages
README.md                     → ไฟล์นี้
```

---

## 🛠️ ขั้นตอนติดตั้ง (ทำครั้งเดียว)

### 1. เช็คว่ามี Python 3.9+ ติดตั้งอยู่
```bash
python3 --version
```

### 2. ติดตั้ง dependencies
```bash
pip3 install yfinance pandas
```

### 3. ทดสอบว่า yfinance ดึงข้อมูลได้จริงไหม (สำคัญมาก — ทำก่อนรันจริง)
```bash
python3 -c "
import yfinance as yf
hist = yf.Ticker('AAPL').history(period='5d')
print(hist)
"
```
ถ้าเห็นตารางราคาหุ้น AAPL ออกมา = ใช้งานได้ปกติ ✅
ถ้า error → เช็ค internet connection หรือ firewall/VPN ที่อาจบล็อก Yahoo Finance

---

## 🚀 วิธีใช้งาน

### ทางลัด (macOS) — ใช้ทุกวันด้วยดับเบิลคลิกเดียว ⭐ แนะนำ

**ติดตั้งครั้งแรก (ทำครั้งเดียว):**
1. เปิด Terminal → `cd` ไปที่โฟลเดอร์ที่มีไฟล์ทั้งหมด
2. รัน: `chmod +x scan_and_open.command`
3. ดับเบิลคลิกไฟล์ `scan_and_open.command` ครั้งแรก macOS จะเตือน "ไม่รู้จัก developer" — **คลิกขวาที่ไฟล์ → เลือก Open → กด Open อีกครั้งในป๊อปอัพ** (ทำครั้งเดียวพอ)

**ใช้งานทุกวัน:**
ดับเบิลคลิกไฟล์ `scan_and_open.command` — มันจะ:
1. รัน scanner ดึงข้อมูลหุ้นล่าสุดให้อัตโนมัติ
2. เปิด dashboard ในเบราว์เซอร์ให้เลย ไม่ต้องพิมพ์คำสั่งอะไรเอง

ปิดหน้าต่าง Terminal เมื่อดูเสร็จแล้ว (server จะหยุดทำงานเองตอนนั้น)

> 💡 ถ้าอยากให้สแกนรายชื่อ ticker ที่ครบกว่าตัวอย่าง 16 ตัว ให้แก้ไฟล์ `tickers.csv` ก่อน (ดูหัวข้อ "หารายชื่อ Nasdaq + Russell 2000 ฉบับเต็ม" ด้านล่าง) — `scan_and_open.command` จะใช้ไฟล์นี้โดยอัตโนมัติ

### วิธี manual (ใช้เพื่อทดสอบ/debug หรือ control เพิ่มเติม)

ทดสอบเบื้องต้นด้วย ticker ตัวอย่าง 16 ตัว (เร็ว ~10-20 วินาที):
```bash
cd stock_scanner
python3 stock_scanner.py
```

ทดสอบเฉพาะบาง ticker:
```bash
python3 stock_scanner.py --tickers AAPL,NVDA,TSLA
```

รัน unit test logic (ไม่ต้องใช้ internet):
```bash
python3 test_logic.py
```

### ดูผลลัพธ์
ผลลัพธ์จะถูกบันทึกที่ `scanner_results.json` — เปิดดูตรงๆได้ หรือเปิดผ่าน `dashboard/index.html`

---

## 📋 ขั้นตอนถัดไป — หารายชื่อหุ้นฉบับเต็ม (ไม่ใช่แค่ 16 ตัวอย่าง)

ไฟล์ `tickers.csv` ที่ให้มามีแค่ 16 ตัวอย่าง (ใช้ทดสอบโค้ดเท่านั้น) ก่อนใช้งานจริงควรสแกนรายชื่อที่กว้างกว่านี้

### วิธีที่ง่ายที่สุด — รัน `build_tickers.py` (แนะนำ) ⭐

```bash
python3 build_tickers.py
```

สคริปต์นี้จะ:
1. ดึงรายชื่อหุ้นทั้งหมดที่เทรดผ่าน Nasdaq (รวมหุ้น NYSE/NYSE American/อื่นๆที่ route ผ่าน Nasdaq's UTP ด้วย) จากไฟล์ทางการของ Nasdaq — ฟรี ไม่ต้อง API key
2. กรองทิ้งสิ่งที่ไม่ใช่หุ้นสามัญจริง: ETF, SPAC, Warrant, Right, Unit, Preferred Stock, Test Issue
3. บันทึกผลเป็น `tickers_full.csv` (รวมแล้วหลักพันตัว)

แล้วใช้กับ scanner ได้เลย:
```bash
python3 stock_scanner.py --tickers-file tickers_full.csv --workers 15
```

> 💡 **เรื่องสำคัญที่ควรเข้าใจ:** `tickers_full.csv` ที่ได้ **ไม่ใช่ Russell 2000 แท้ๆ** (ซึ่งต้องดูจาก market-cap ranking ที่ Nasdaq ไม่ได้เปิดข้อมูลนี้ให้ฟรีตรงๆ) แต่เป็น universe ที่กว้างกว่า (หุ้นทุกตัวที่เทรดผ่าน Nasdaq+NYSE) ซึ่ง**ครอบคลุม Russell 2000 ส่วนใหญ่อยู่แล้วในทางปฏิบัติ** เพราะ base filter ของ `stock_scanner.py` (ราคา > $5, market cap > $300M, volume > 1M) จะกรองหุ้นเล็กเกินไป/ใหญ่เกินไปออกไปเองอยู่ดี — ถ้าอยาก Russell 2000 ที่ตรงเป๊ะ ต้องไปโหลด holdings จาก iShares ETF (ดูวิธี manual ด้านล่าง)

### ขั้นต่อไป (แนะนำ) — ตัดให้เหลือ Top N ตัวตาม market cap ด้วย `rank_tickers_by_marketcap.py`

`tickers_full.csv` มักมีหลักพันตัว สแกนเต็มจะใช้เวลานาน (หลักสิบนาทีถึงเป็นชั่วโมง) วิธีที่คุ้มกว่าคือกรองเอาแต่บริษัทใหญ่/เทรดคล่องไว้ก่อน:

```bash
python3 rank_tickers_by_marketcap.py --top 1000
```

สคริปต์นี้จะ:
1. ดึง market cap แบบเบา (ใช้ `fast_info` ของ yfinance ไม่ใช่ `.info` เต็มรูปแบบ — เร็วกว่าการสแกนเต็ม)
2. กรองตามเกณฑ์เดียวกับ base filter ของ `stock_scanner.py` (ราคา/market cap/volume) ออกไปเลย
3. เรียงจาก market cap ใหญ่ไปเล็ก ตัดเหลือ Top N (default 1000)
4. บันทึกเป็น `tickers_top1000.csv`

```bash
python3 stock_scanner.py --tickers-file tickers_top1000.csv
```

> ⚠️ **ข้อควรเข้าใจ:** ขั้นตอนนี้ก็ต้องเรียก yfinance ทุก ticker เหมือนกัน (เพื่อรู้ market cap) ดังนั้นยังต้องใช้เวลาพอสมควรตอนรันครั้งนี้ — **แต่ทำครั้งเดียวแล้วเก็บผลไว้ใช้ซ้ำได้นาน** เพราะ market cap ไม่เปลี่ยนเร็วขนาดต้องทำใหม่ทุกวัน (อัปเดตทุก 1-2 สัปดาห์ก็พอ) ส่วนการสแกนหา setup รายวันหลังจากนี้จะเร็วขึ้นมาก เพราะเหลือ ticker น้อยลง
>
> ปรับจำนวนได้ตามต้องการ เช่น `--top 500` หรือ `--top 2000` — ยิ่งมากยิ่งครอบคลุม แต่สแกนรายวันจะช้าขึ้นตามไปด้วย

`scan_and_open.command` จะเช็คหาไฟล์ `tickers_top*.csv` ก่อนเสมอ ถ้ามีจะใช้ไฟล์นี้อัตโนมัติ (ลำดับความสำคัญ: `tickers_top*.csv` > `tickers_full.csv` > `tickers.csv`)

### วิธี manual (ถ้าอยากได้ Russell 2000 ที่ตรงเป๊ะ หรือ build_tickers.py ใช้ไม่ได้)

**Russell 2000 จาก iShares (ทางการที่สุด):**
- เข้า https://www.ishares.com/us/products/239710/ishares-russell-2000-etf
- กดปุ่ม "Download Holdings" → จะได้ CSV รายชื่อหุ้นทั้งหมดใน Russell 2000
- แปลงเป็นไฟล์ CSV column เดียวชื่อ `ticker` แล้วใช้กับ `--tickers-file`

> 💡 **คำแนะนำเรื่องเวลาที่ใช้:** สแกนหลักพันตัวจะใช้เวลานานกว่าทดสอบ 16 ตัวเดิมมาก
> (yfinance ดึงทีละ ticker ผ่าน HTTP request) แนะนำให้:
> - ใช้ `rank_tickers_by_marketcap.py` ตัดให้เหลือ Top N ก่อน (วิธีข้างบน)
> - ปรับ `--workers` ตามความเหมาะสม (ระวัง rate limit ของ Yahoo Finance ถ้าตั้งสูงเกินไป)
> - รันตอนเช้าหลังตลาดอเมริกาปิด ตามที่ออกแบบไว้ใน Project Instructions

---

## ⚙️ การปรับแต่งเงื่อนไข

พารามิเตอร์ของแต่ละเงื่อนไขอยู่ที่ด้านบนของ `stock_scanner.py` ปรับได้ตรงนี้:

```python
MIN_PRICE = 5.0                   # Base filter: ราคาขั้นต่ำ
MIN_AVG_VOLUME = 1_000_000        # Base filter: volume เฉลี่ยขั้นต่ำ
MIN_MARKET_CAP = 300_000_000      # Base filter: market cap ขั้นต่ำ
MIN_DOLLAR_VOLUME = 5_000_000     # Base filter: dollar volume ขั้นต่ำ

GAP_UP_PCT_MIN = 4.0               # เงื่อนไข 1: gap ขั้นต่ำ
VOLUME_BREAKOUT_MULTIPLIER = 2.0   # เงื่อนไข 2: volume ต้องมากกว่ากี่เท่า
RS_MIN_STREAK_DAYS = 3             # เงื่อนไข 3: ต้องแรงกว่าตลาดกี่วันติด
PULLBACK_TOLERANCE_PCT = 2.0       # เงื่อนไข 4: ระยะห่างจาก EMA ที่ถือว่า "แตะ"
SECTOR_ROTATION_LOOKBACK = 5       # เงื่อนไข 5: ดูข้อมูล sector กี่วันหลังสุด
EARNINGS_SURPRISE_MIN_PCT = 5.0    # เงื่อนไข 6: ต้อง beat estimate เกินกี่% ถึงนับ
```

---

## 🖥️ การใช้ Dashboard

ถ้าใช้ `scan_and_open.command` (macOS) ไม่ต้องทำอะไรเพิ่ม — มันเปิด dashboard ให้อัตโนมัติแล้ว

ถ้าอยากรันแบบ manual หรืออยู่บน OS อื่น:
```bash
# 1. รัน scanner แล้วให้ output ไปที่โฟลเดอร์ dashboard
python3 stock_scanner.py --output dashboard/scanner_results.json

# 2. เปิดดูบนเครื่องตัวเอง (ห้าม double-click ไฟล์ตรงๆ ต้องผ่าน local server)
cd dashboard
python3 -m http.server 8000
# แล้วเปิดเบราว์เซอร์ไปที่ http://localhost:8000
```

อยากแชร์ให้เพื่อนเปิดดูผ่านลิงก์ได้เลย → ดูคู่มือเต็มใน `DEPLOY_GITHUB_PAGES.md`

Dashboard มีฟีเจอร์:
- กรอง "ทั้งหมด" vs "Match Setup" เท่านั้น
- เรียงตาม setup ที่ match มากสุด / fundamental score / dollar volume / ชื่อ ticker
- คลิกที่การ์ดเพื่อดูรายละเอียดเต็มของทุก setup + fundamental score breakdown

---

## ⚙️ การปรับแต่งเงื่อนไข Fundamental (ใหม่)

```python
FUNDAMENTAL_FILTER_ENABLED = True   # ปิดได้ด้วย --no-fundamental-filter ตอนรัน
MAX_DEBT_TO_EQUITY = 300.0          # debt/equity ไม่เกิน 300%
MIN_CURRENT_RATIO = 0.5             # current ratio ขั้นต่ำ
EARNINGS_SURPRISE_LOOKBACK_DAYS = 5 # earnings surprise ต้องเกิดภายในกี่วัน
```

ปิด fundamental filter ชั่วคราว (ยังคำนวณ score ให้ดูอยู่ แค่ไม่กรองออก):
```bash
python3 stock_scanner.py --no-fundamental-filter
```

> 💡 **ทำไม fundamental filter "ปล่อยผ่าน" เวลาไม่มีข้อมูล?**
> yfinance บางตัวข้อมูลงบไม่ครบ (โดยเฉพาะหุ้นขนาดเล็กใน Russell 2000) ถ้าตัดทิ้งทันทีที่ไม่มีข้อมูล
> จะเสียโอกาสดูหุ้นที่อาจจะดีจริงๆไปเยอะ ระบบเลยเลือก "ไม่ตัดทิ้งทั้งที่ไม่รู้ข้อมูลจริง" แทน

---

## 🔁 การกัน Yahoo Finance Rate Limit + Resume

ถ้าสแกน ticker จำนวนมาก (หลักร้อย-พัน) Yahoo Finance อาจบล็อกชั่วคราวเพราะคิดว่ามี request เยอะ/เร็วเกินไป — ระบบมีการป้องกัน 3 ชั้น:

1. **Retry แบบ backoff อัตโนมัติ** — ถ้าโดน rate limit จะรอแล้วลองใหม่เอง (สูงสุด 4 ครั้ง รอนานขึ้นทุกรอบ) ไม่ต้องทำอะไรเพิ่ม
2. **Stagger delay** — หน่วงเวลาเล็กน้อย (0.15 วินาที) ระหว่าง request แต่ละตัว ลดโอกาสโดน rate limit ตั้งแต่ต้น
3. **Checkpoint/Resume** — ถ้าโดน rate limit หนักจนต้อง retry ครบแล้วยังไม่ผ่าน ระบบจะบันทึกความคืบหน้าไว้ที่ไฟล์ `<output>.checkpoint.json` แล้ว**รันคำสั่งเดิมซ้ำอีกครั้งได้เลย** — ระบบจะ resume ต่อจากจุดที่ค้าง ไม่ต้องสแกนตัวที่ทำไปแล้วซ้ำ

```bash
# รันครั้งแรก สแกนไป 500 จาก 1000 ตัวแล้วโดน rate limit
python3 stock_scanner.py --tickers-file tickers_top1000.csv

# รอสักพัก (15-30 นาที) แล้วรันคำสั่งเดิมซ้ำ — จะ resume ต่อจาก 500 ตัวที่เหลือ
python3 stock_scanner.py --tickers-file tickers_top1000.csv
```

ถ้าอยากสแกนใหม่ทั้งหมดโดยไม่ resume:
```bash
python3 stock_scanner.py --tickers-file tickers_top1000.csv --no-resume
```

> 💡 **ลด `--workers` ถ้าโดน rate limit บ่อย** — ค่า default คือ 8 ถ้ายังโดนบ่อยลองลดเป็น 3-5 จะช้าลงแต่โดนบล็อกน้อยลง

---

## 🐛 ถ้าเจอปัญหา

| ปัญหา | สาเหตุที่เป็นไปได้ | วิธีแก้ |
|---|---|---|
| `ModuleNotFoundError: No module named 'yfinance'` | ยังไม่ติดตั้ง | `pip3 install yfinance` (หรือดับเบิลคลิก `scan_and_open.command` ซึ่งติดตั้งให้อัตโนมัติ) |
| ดึงข้อมูลไม่ได้ / timeout / "ส่งข้อมูลเยอะเกินไป" | Yahoo Finance rate-limit หรือ internet มีปัญหา | ระบบจะ retry อัตโนมัติแล้ว ถ้ายังไม่ผ่าน รอ 15-30 นาทีแล้วรันคำสั่งเดิมซ้ำ (จะ resume ต่อจาก checkpoint อัตโนมัติ) หรือลด `--workers` |
| Ticker บางตัว error "ไม่มีข้อมูลราคา" | หุ้นนั้นอาจ delist/halt หรือพิมพ์ ticker ผิด | ปกติ ระบบจะ skip ตัวนั้นแล้วทำตัวอื่นต่อ ไม่กระทบตัวอื่น |
| ผลลัพธ์ดูแปลก/ไม่ match อะไรเลย | อาจเป็นเพราะตลาดเงียบ/ไม่มี setup ในวันนั้นจริงๆ | ลองปรับพารามิเตอร์ให้หลวมขึ้นเพื่อทดสอบ หรือลองวันอื่น |
| ดับเบิลคลิก `.command` แล้วไม่มีอะไรเกิดขึ้น/เปิดด้วย text editor | ยังไม่ได้ตั้ง permission ให้รันได้ | เปิด Terminal แล้วรัน `chmod +x scan_and_open.command` |
| macOS เตือน "ไม่สามารถเปิดได้เพราะมาจาก developer ที่ไม่รู้จัก" | ระบบความปลอดภัย Gatekeeper ของ macOS | คลิกขวาที่ไฟล์ → เลือก **Open** → กด **Open** อีกครั้งในป๊อปอัพ (ทำครั้งเดียว) |
| `scan_and_open.command` เปิดแล้วปิด Terminal ทันที โดยไม่เห็น error | อาจเกิดจาก path มีช่องว่างหรืออักขระพิเศษ | ลองย้ายโฟลเดอร์ทั้งหมดไปไว้ที่ path ง่ายๆ เช่น `~/Documents/stock_scanner/` |

---

## ⚠️ ย้ำเตือนสำคัญ (ตาม Project Instructions)

- ระบบนี้คือ **Scan + Dashboard เท่านั้น** — ไม่มี Auto Order หุ้นใดๆทั้งสิ้น
- ทุกการตัดสินใจซื้อขายเป็น **Manual decision** ของคุณเอง
- ต้อง verify กับแอป **Dime!/Webull** ก่อนเสมอว่าหุ้นที่เจอซื้อได้จริงไหม (เต็มหุ้น/fractional)
- ผลลัพธ์ไม่ใช่คำแนะนำการลงทุน ไม่การันตีผลกำไร
- หุ้นซิ่ง/Small-cap มีความเสี่ยงสูงกว่าหุ้นใหญ่มาก — ไม่เสี่ยงเงินที่รับไม่ได้ถ้าหาย
