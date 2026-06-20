# 🌐 คู่มือ Deploy Dashboard ขึ้น GitHub Pages

> เป้าหมาย: ได้ลิงก์เดียวที่เพื่อนเปิดดูผล scan ได้เลย ไม่ต้องส่งไฟล์ให้กันเอง

## ภาพรวมวิธีทำงาน (สำคัญ — ต้องเข้าใจก่อนเริ่ม)

```
[เครื่องคุณ]                              [GitHub]                        [เพื่อนคุณ]
stock_scanner.py                                                          
      ↓ รันแล้วได้                                                        
scanner_results.json   ──── push ขึ้น repo ────→  GitHub Pages   ──────→  เปิดลิงก์ดู
                                                  (serve index.html
                                                   + scanner_results.json)
```

**สิ่งสำคัญที่ต้องเข้าใจ:** GitHub Pages เสิร์ฟไฟล์ static เท่านั้น (HTML/CSS/JS) — มันรัน Python หรือดึงข้อมูลหุ้นเองไม่ได้
ดังนั้น**คุณยังต้องรัน `stock_scanner.py` บนเครื่องตัวเองแล้ว push ผลขึ้น repo เป็นระยะ** ถึงจะอัปเดต dashboard ได้
ไม่ใช่ระบบ auto-update เองทั้งหมด — เพื่อนจะเห็นข้อมูล ณ ครั้งล่าสุดที่คุณ push เท่านั้น

---

## ขั้นตอนที่ 1 — สร้าง GitHub repo (ทำครั้งเดียว)

1. เข้า https://github.com → กด "New repository"
2. ตั้งชื่อ เช่น `stock-swing-scanner` → เลือก Public (จำเป็น ถ้าใช้ GitHub Pages แบบฟรี)
3. กด "Create repository"

## ขั้นตอนที่ 2 — เตรียมโครงสร้างไฟล์ในเครื่องคุณ

จัดไฟล์ให้หน้าตาแบบนี้ในโฟลเดอร์ repo:

```
stock-swing-scanner/
├── stock_scanner.py
├── tickers.csv
├── test_logic.py
└── docs/                      ← โฟลเดอร์นี้คือสิ่งที่ GitHub Pages จะเอาไป serve
    ├── index.html             ← ไฟล์ dashboard (จากที่ให้ไปในโฟลเดอร์ dashboard/)
    └── scanner_results.json   ← ผลสแกนล่าสุด (รันแล้ว copy มาวางตรงนี้)
```

> 💡 ใช้ชื่อโฟลเดอร์ `docs/` เพราะ GitHub Pages มีตัวเลือก "serve จากโฟลเดอร์ docs" ในตั้งค่าได้ตรงๆ ไม่ต้องสร้าง branch แยก

## ขั้นตอนที่ 3 — Push ขึ้น GitHub

```bash
cd stock-swing-scanner
git init
git add .
git commit -m "Initial scanner + dashboard"
git branch -M main
git remote add origin https://github.com/<username>/stock-swing-scanner.git
git push -u origin main
```

## ขั้นตอนที่ 4 — เปิดใช้ GitHub Pages

1. ไปที่ repo บน GitHub → แท็บ **Settings** → เมนูซ้าย **Pages**
2. ที่ "Build and deployment" → Source เลือก **Deploy from a branch**
3. Branch เลือก **main** และโฟลเดอร์เลือก **/docs**
4. กด **Save**
5. รอ 1-2 นาที จะมีลิงก์ขึ้นมาแบบ:
   ```
   https://<username>.github.io/stock-swing-scanner/
   ```
6. เอาลิงก์นี้ส่งให้เพื่อนได้เลย ✅

---

## วิธีอัปเดตข้อมูลรอบใหม่ (ทำซ้ำทุกครั้งที่ scan)

```bash
# 1. รัน scanner ตามปกติ
python3 stock_scanner.py --tickers-file tickers_full.csv --output docs/scanner_results.json

# 2. push ขึ้น GitHub
git add docs/scanner_results.json
git commit -m "Update scan results $(date +%Y-%m-%d)"
git push
```

หลัง push ไม่กี่นาที เพื่อนที่เปิดลิงก์ไว้แล้ว refresh หน้าเว็บ จะเห็นข้อมูลใหม่ทันที

> 💡 **ทำเป็น routine:** สร้าง shell script เล็กๆรวม 2 คำสั่งนี้ไว้ด้วยกัน จะสะดวกกว่าพิมพ์ทุกครั้ง
> ```bash
> #!/bin/bash
> # scan_and_publish.sh
> python3 stock_scanner.py --tickers-file tickers_full.csv --output docs/scanner_results.json
> git add docs/scanner_results.json
> git commit -m "Update scan results $(date +%Y-%m-%d_%H:%M)"
> git push
> ```

---

## ทดสอบก่อน push จริง (แนะนำ)

เปิด dashboard ดูบนเครื่องตัวเองก่อน เพื่อเช็คว่าหน้าตาถูกต้อง:

```bash
cd docs
python3 -m http.server 8000
```

แล้วเปิดเบราว์เซอร์ไปที่ `http://localhost:8000` — **ห้ามเปิดไฟล์ `index.html` ตรงๆแบบ double-click**
(เบราว์เซอร์จะ block การโหลด `scanner_results.json` เพราะนโยบายความปลอดภัยของ browser กับ `file://` protocol)
ต้องเปิดผ่าน local server แบบนี้เท่านั้นถึงจะเห็นข้อมูลขึ้นจริง

---

## ความเป็นส่วนตัว — เพื่อนเห็นอะไรบ้าง

เนื่องจาก repo เป็น Public และ GitHub Pages เป็นเว็บสาธารณะ **ใครก็เข้าลิงก์ได้ ไม่ใช่แค่เพื่อนที่คุณส่งให้**
ถ้าไม่ต้องการให้คนอื่นเห็น มีทางเลือก:
- ใช้ GitHub Pages กับ Private repo (ต้องมี GitHub Pro หรือ org plan — ฟรี tier ทำไม่ได้กับ private repo)
- หรือยอมรับว่าข้อมูลเป็น public (ข้อมูลที่แสดงเป็นแค่ราคาหุ้น/setup ที่ match ไม่ใช่ข้อมูลส่วนตัวของคุณ ความเสี่ยงต่ำ)

---

## ⚠️ ย้ำเตือนสำหรับเพื่อนที่จะใช้ dashboard นี้

ส่งข้อความนี้ไปด้วยตอนแชร์ลิงก์ให้เพื่อน:

> นี่คือเครื่องมือกรองหุ้นเบื้องต้นเท่านั้น ไม่ใช่คำแนะนำการลงทุน ไม่การันตีผลกำไร
> ก่อนซื้อขายจริงต้อง verify ข้อมูลและตรวจสอบว่าซื้อได้จริงกับ broker ที่ใช้
> การตัดสินใจทั้งหมดเป็นความเสี่ยงของแต่ละคนเอง ระบบนี้ไม่มี Auto Order ใดๆ
