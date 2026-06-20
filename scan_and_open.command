#!/bin/bash
# ==========================================================================
# scan_and_open.command
# ==========================================================================
# ดับเบิลคลิกไฟล์นี้บน macOS เพื่อ:
#   1. รัน stock_scanner.py (ดึงข้อมูลหุ้นจริง + เช็ค 6 setup + fundamental)
#   2. เปิด local server เสิร์ฟ dashboard
#   3. เปิดเบราว์เซอร์ไปที่ dashboard ให้อัตโนมัติ
#   4. ปิด server ให้เรียบร้อยตอนปิดหน้าต่าง Terminal นี้ (กด Ctrl+C หรือปิดหน้าต่าง)
#
# วิธีติดตั้งครั้งแรก (ทำครั้งเดียว):
#   1. เปิด Terminal แล้วรัน: chmod +x scan_and_open.command
#   2. ถ้า macOS เด้งเตือน "ไม่รู้จัก developer" ตอนดับเบิลคลิกครั้งแรก:
#      คลิกขวาที่ไฟล์ → เลือก "Open" → กด "Open" อีกครั้งในป๊อปอัพ
#      (ทำครั้งเดียวเท่านั้น ครั้งต่อไปดับเบิลคลิกได้ปกติ)
# ==========================================================================

# cd ไปที่โฟลเดอร์ที่ไฟล์นี้อยู่ (สำคัญมาก ไม่งั้นจะหาไฟล์อื่นไม่เจอ)
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

echo "📈 Stock Swing Scanner — เริ่มทำงาน..."
echo "📂 โฟลเดอร์ทำงาน: $(pwd)"
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
    echo "❌ ไม่พบ Python 3 ในเครื่อง"
    echo "   ติดตั้งได้จาก https://www.python.org/downloads/ แล้วลองใหม่"
    echo ""
    read -p "กด Enter เพื่อปิดหน้าต่างนี้..."
    exit 1
fi

echo "✅ พบ Python: $PYTHON_BIN ($($PYTHON_BIN --version))"
echo ""

# ---------- เช็คว่ามี yfinance ติดตั้งหรือยัง ----------
if ! "$PYTHON_BIN" -c "import yfinance" >/dev/null 2>&1; then
    echo "⚠️ ยังไม่ได้ติดตั้ง yfinance — กำลังติดตั้งให้อัตโนมัติ..."
    "$PYTHON_BIN" -m pip install --quiet yfinance pandas
    if [ $? -ne 0 ]; then
        echo "❌ ติดตั้ง library ไม่สำเร็จ — ลองรันคำสั่งนี้เองดู:"
        echo "   $PYTHON_BIN -m pip install yfinance pandas"
        read -p "กด Enter เพื่อปิดหน้าต่างนี้..."
        exit 1
    fi
    echo "✅ ติดตั้งเสร็จแล้ว"
    echo ""
fi

# ---------- เลือกไฟล์รายชื่อ ticker ----------
# ลำดับความสำคัญ: tickers_top*.csv (กรองตาม market cap แล้ว, เร็วสุด)
#                > tickers_full.csv (รายชื่อทั้งหมดจาก build_tickers.py, ช้าแต่ครอบคลุม)
#                > tickers.csv (16 ตัวอย่างสำหรับทดสอบ)
TOP_FILE=$(ls tickers_top*.csv 2>/dev/null | head -1)

if [ -n "$TOP_FILE" ]; then
    TICKERS_FILE="$TOP_FILE"
    echo "📋 ใช้รายชื่อหุ้นจาก $TOP_FILE (กรองตาม market cap แล้ว)"
elif [ -f "tickers_full.csv" ]; then
    TICKERS_FILE="tickers_full.csv"
    echo "📋 ใช้รายชื่อหุ้นจาก tickers_full.csv (รายชื่อจริงทั้งหมด — อาจใช้เวลานาน)"
    echo "   💡 อยากให้เร็วขึ้น รันคำสั่ง: $PYTHON_BIN rank_tickers_by_marketcap.py"
else
    TICKERS_FILE="tickers.csv"
    echo "📋 ใช้รายชื่อหุ้นจาก tickers.csv (16 ตัวอย่าง — ยังไม่ได้รัน build_tickers.py)"
    echo "   💡 อยากสแกนกว้างกว่านี้ รันคำสั่ง: $PYTHON_BIN build_tickers.py"
fi
echo ""

# ---------- รัน scanner ----------
echo "🔍 กำลังสแกนหุ้น (อาจใช้เวลาสักครู่ ขึ้นกับจำนวน ticker ใน $TICKERS_FILE)..."
echo "----------------------------------------------------------------------"

mkdir -p dashboard

"$PYTHON_BIN" stock_scanner.py --tickers-file "$TICKERS_FILE" --output dashboard/scanner_results.json

SCAN_EXIT_CODE=$?
echo "----------------------------------------------------------------------"

if [ $SCAN_EXIT_CODE -ne 0 ]; then
    echo "❌ Scanner รันไม่สำเร็จ (exit code: $SCAN_EXIT_CODE) — ดู error ด้านบน"
    read -p "กด Enter เพื่อปิดหน้าต่างนี้..."
    exit 1
fi

echo "✅ สแกนเสร็จแล้ว"
echo ""

# ---------- เปิด local server ----------
PORT=8765

# เช็คว่า port นี้มีใครใช้อยู่ไหม ถ้ามีให้ลอง port อื่น (ใช้ Python เช็คแทน lsof เพื่อความทนทาน)
while ! "$PYTHON_BIN" -c "
import socket, sys
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    try:
        s.bind(('127.0.0.1', $PORT))
        sys.exit(0)
    except OSError:
        sys.exit(1)
" >/dev/null 2>&1; do
    PORT=$((PORT + 1))
done

echo "🌐 กำลังเปิด local server ที่ port $PORT..."
cd dashboard || exit 1

"$PYTHON_BIN" -m http.server "$PORT" >/tmp/stock_scanner_server.log 2>&1 &
SERVER_PID=$!

# รอ server พร้อมก่อนเปิดเบราว์เซอร์
sleep 1

DASHBOARD_URL="http://localhost:$PORT/index.html"
echo "✅ Server พร้อมแล้วที่: $DASHBOARD_URL"
echo ""
echo "🚀 กำลังเปิดเบราว์เซอร์..."
open "$DASHBOARD_URL"

echo ""
echo "========================================================================"
echo "✅ เสร็จสมบูรณ์ — Dashboard เปิดในเบราว์เซอร์แล้ว"
echo ""
echo "⚠️  อย่าปิดหน้าต่าง Terminal นี้ ถ้ายังอยากดู dashboard อยู่"
echo "    (server จะหยุดทำงานทันทีที่ปิดหน้าต่างนี้)"
echo ""
echo "    ปิดหน้าต่างนี้เมื่อดูเสร็จแล้ว หรือกด Ctrl+C เพื่อหยุด server"
echo "========================================================================"
echo ""

# trap ให้ kill server ตอนปิดหน้าต่างหรือกด Ctrl+C
trap "echo ''; echo '🛑 ปิด server แล้ว'; kill $SERVER_PID 2>/dev/null; exit 0" INT TERM EXIT

# รอจนกว่าจะปิดหน้าต่างหรือกด Ctrl+C
wait $SERVER_PID
