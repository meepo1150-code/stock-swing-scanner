#!/bin/bash
# ==========================================================================
# publish_to_github.sh
# ==========================================================================
# สแกนหุ้น + copy ผลลัพธ์เข้าโฟลเดอร์ docs/ + push ขึ้น GitHub ในคำสั่งเดียว
# ใช้แทนการพิมพ์หลายคำสั่งทุกครั้งที่อยากอัปเดต dashboard ที่แชร์ให้เพื่อนดู
#
# ข้อกำหนดก่อนใช้ (ทำครั้งแรกครั้งเดียว):
#   1. ต้องมี git repo อยู่แล้ว (git init + git remote add origin ... ทำไปแล้ว)
#   2. ต้องเปิด GitHub Pages ใน repo settings โดยเลือก serve จากโฟลเดอร์ /docs
#      (Settings → Pages → Source: Deploy from a branch → Branch: main → /docs)
#
# วิธีใช้:
#   chmod +x publish_to_github.sh   (ทำครั้งแรกครั้งเดียว)
#   ./publish_to_github.sh
# ==========================================================================

cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

echo "📈 Publish Stock Scanner → GitHub Pages"
echo "========================================"
echo ""

# ---------- หา Python3 ----------
PYTHON_BIN=""
for candidate in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo "❌ ไม่พบ Python 3"
    exit 1
fi

# ---------- เช็คว่าเป็น git repo อยู่แล้วไหม ----------
if [ ! -d ".git" ]; then
    echo "❌ โฟลเดอร์นี้ยังไม่ได้ตั้งเป็น git repo"
    echo "   รันตามนี้ก่อน (ครั้งแรกครั้งเดียว):"
    echo "   git init"
    echo "   git remote add origin https://github.com/<username>/<repo-name>.git"
    exit 1
fi

# ---------- เลือกไฟล์ ticker (เหมือน scan_and_open.command) ----------
TOP_FILE=$(ls tickers_top*.csv 2>/dev/null | head -1)
if [ -n "$TOP_FILE" ]; then
    TICKERS_FILE="$TOP_FILE"
    echo "📋 ใช้รายชื่อหุ้นจาก $TOP_FILE"
elif [ -f "tickers_full.csv" ]; then
    TICKERS_FILE="tickers_full.csv"
    echo "📋 ใช้รายชื่อหุ้นจาก tickers_full.csv"
else
    TICKERS_FILE="tickers.csv"
    echo "📋 ใช้รายชื่อหุ้นจาก tickers.csv (16 ตัวอย่าง)"
fi
echo ""

# ---------- รัน scanner ----------
echo "🔍 กำลังสแกนหุ้น..."
echo "----------------------------------------------------------------------"
mkdir -p dashboard docs

"$PYTHON_BIN" stock_scanner.py --tickers-file "$TICKERS_FILE" --output dashboard/scanner_results.json
SCAN_EXIT_CODE=$?
echo "----------------------------------------------------------------------"

if [ $SCAN_EXIT_CODE -eq 2 ]; then
    echo ""
    echo "⏸️  การสแกนยังไม่ครบ (ติด rate limit) — ยังไม่ publish ขึ้น GitHub"
    echo "   รันคำสั่งนี้ซ้ำอีกครั้งเพื่อสแกนต่อให้ครบก่อน publish"
    exit 2
elif [ $SCAN_EXIT_CODE -ne 0 ]; then
    echo "❌ Scanner รันไม่สำเร็จ (exit code: $SCAN_EXIT_CODE) — ดู error ด้านบน"
    exit 1
fi

echo "✅ สแกนเสร็จแล้ว"
echo ""

# ---------- copy ผลลัพธ์ + dashboard เข้า docs/ ----------
echo "📂 กำลัง copy ไฟล์เข้า docs/ (โฟลเดอร์ที่ GitHub Pages serve)..."
cp dashboard/index.html docs/index.html
cp dashboard/scanner_results.json docs/scanner_results.json

# ไฟล์ใหม่ (เพิ่มเข้ามาทีหลัง — compare.html ใช้เทียบผลสแกน 2 วัน,
# scorecard.html ใช้ backtest win rate ของแต่ละ setup)
if [ -f "dashboard/compare.html" ]; then
    cp dashboard/compare.html docs/compare.html
fi
if [ -f "dashboard/scorecard.html" ]; then
    cp dashboard/scorecard.html docs/scorecard.html
fi

# history/ — compare.html และ scorecard.html อ่านข้อมูลจากโฟลเดอร์นี้โดยตรง
# (fetch ผ่าน relative path "history/YYYY-MM-DD.json") ถ้าไม่ copy ไปด้วย
# ทั้งสองหน้าจะเปิดได้แต่ไม่มีข้อมูลให้แสดงเลยตอนเปิดจาก GitHub Pages
if [ -d "dashboard/history" ]; then
    mkdir -p docs/history
    cp dashboard/history/*.json docs/history/ 2>/dev/null
    echo "🗂️  copy history snapshot ทั้งหมดเข้า docs/history/ ด้วย"
fi

# ⚠️ ไม่ copy institutional_ownership_cache.json ขึ้น public โดยตั้งใจ —
# เป็นแค่ cache ภายในสำหรับลด API call ไม่มีประโยชน์กับคนดู dashboard
# และไม่ควรอยู่ใน git history แบบไม่จำเป็น

echo "✅ copy เสร็จแล้ว"
echo ""

# ---------- git add / commit / push ----------
echo "📤 กำลัง push ขึ้น GitHub..."
git add docs/index.html docs/scanner_results.json
if [ -f "docs/compare.html" ]; then
    git add docs/compare.html
fi
if [ -f "docs/scorecard.html" ]; then
    git add docs/scorecard.html
fi
if [ -d "docs/history" ]; then
    git add docs/history/
fi
COMMIT_MSG="Update scan results $(date '+%Y-%m-%d %H:%M')"
git commit -m "$COMMIT_MSG"

if [ $? -ne 0 ]; then
    echo "ℹ️  ไม่มีอะไรเปลี่ยนแปลง (ผลลัพธ์เหมือนรอบที่แล้ว) — ข้าม commit"
else
    git push
    if [ $? -ne 0 ]; then
        echo "❌ Push ไม่สำเร็จ — เช็ค internet connection หรือ git remote ตั้งถูกไหม"
        echo "   ลองรัน: git remote -v   เพื่อเช็คว่าตั้ง remote ไว้ถูกต้องหรือยัง"
        exit 1
    fi
    echo "✅ Push สำเร็จ!"
fi

echo ""
echo "========================================================================"
echo "✅ เสร็จสมบูรณ์ — เพื่อนที่เปิดลิงก์ GitHub Pages ไว้ refresh จะเห็นข้อมูลใหม่"
echo "========================================================================"
