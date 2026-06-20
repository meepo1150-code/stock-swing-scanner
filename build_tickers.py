#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_tickers.py
=================
ดึงรายชื่อหุ้นทั้งหมดที่เทรดผ่าน Nasdaq (รวมหุ้นที่ list บน NYSE/NYSE American/Cboe/BATS
ที่ส่งคำสั่งผ่าน Nasdaq's UTP ด้วย) แล้วกรองเอาแต่ "หุ้นสามัญของบริษัทจริง" ออกมา
ตัดทิ้ง: ETF, SPAC (Acquisition Corp), Warrant, Right, Unit, Preferred Stock, Test Issue

ผลลัพธ์: ไฟล์ tickers_full.csv ที่ใช้กับ stock_scanner.py ได้ตรงๆ
  python3 stock_scanner.py --tickers-file tickers_full.csv

⚠️ หมายเหตุสำคัญ:
  - ไฟล์นี้คือ "หุ้นทุกตัวที่เทรดผ่าน Nasdaq+NYSE+อื่นๆ" ไม่ใช่แค่ Nasdaq บริสุทธิ์
    เพราะ Russell 2000 มีหุ้นจาก NYSE ปนอยู่ด้วยจำนวนมาก (Russell 2000 ไม่สนใจว่า list ที่ไหน)
  - ไฟล์นี้ "ไม่ใช่" รายชื่อ Russell 2000 แท้ๆ (ที่ต้องดูจาก market cap ranking)
    แต่เป็น universe ที่กว้างกว่าและครอบคลุม Russell 2000 เกือบทั้งหมดอยู่แล้ว
    เพราะ base filter ของ stock_scanner.py (price>$5, market cap>$300M, volume>1M)
    จะกรองหุ้นเล็กเกินไป/ใหญ่เกินไปออกไปเองอยู่ดี
  - ใช้แทนการหา Russell 2000 แท้ๆได้ในระดับที่ใช้งานได้จริง โดยไม่ต้องพึ่ง
    iShares ETF holdings ที่บางทีโหลดยากกว่า
"""

import csv
import sys
import urllib.request
import urllib.error
from pathlib import Path

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

OUTPUT_FILE = "tickers_full.csv"

# คำที่ถ้าเจอใน Security Name แปลว่าไม่ใช่หุ้นสามัญธรรมดา (ตัดทิ้ง)
EXCLUDE_KEYWORDS = [
    "warrant", "right", "unit", "acquisition corp", "acquisition corporation",
    "acquisition inc", "acquisition ltd", "acquisition company", "acquisition co.",
    "preferred", "depositary share", "trust preferred", "notes due",
    "subordinated", "spac", "blank check", "% senior", "% series",
    "convertible preferred", "tangible equity unit",
]

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) build_tickers.py/1.0"


def log(msg: str):
    print(msg, flush=True)


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def is_excluded_name(security_name: str) -> bool:
    name_lower = security_name.lower()
    return any(kw in name_lower for kw in EXCLUDE_KEYWORDS)


def parse_nasdaqlisted(text: str) -> list[dict]:
    """
    Format: Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
    """
    lines = text.strip().splitlines()
    if not lines:
        return []
    header = lines[0].split("|")
    rows = []
    for line in lines[1:]:
        if line.startswith("File Creation Time") or not line.strip():
            continue
        parts = line.split("|")
        if len(parts) != len(header):
            continue
        row = dict(zip(header, parts))
        rows.append(row)
    return rows


def parse_otherlisted(text: str) -> list[dict]:
    """
    Format: ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
    """
    lines = text.strip().splitlines()
    if not lines:
        return []
    header = lines[0].split("|")
    rows = []
    for line in lines[1:]:
        if line.startswith("File Creation Time") or not line.strip():
            continue
        parts = line.split("|")
        if len(parts) != len(header):
            continue
        row = dict(zip(header, parts))
        rows.append(row)
    return rows


def filter_common_stocks(rows: list[dict], symbol_field: str, name_field: str,
                          etf_field: str, test_field: str) -> list[str]:
    """กรองเอาแต่หุ้นสามัญจริง ตัด ETF, Test Issue, SPAC/Warrant/Right/Preferred ออก"""
    tickers = []
    for row in rows:
        symbol = row.get(symbol_field, "").strip()
        name = row.get(name_field, "").strip()
        is_etf = row.get(etf_field, "N").strip().upper() == "Y"
        is_test = row.get(test_field, "N").strip().upper() == "Y"

        if not symbol or not name:
            continue
        if is_etf or is_test:
            continue
        if is_excluded_name(name):
            continue
        # ตัด symbol ที่มีอักขระแปลกๆ (เช่น warrant suffix .WS, .U, .WT, ตัว $ ของ preferred)
        if any(c in symbol for c in [".", "$", "+", "~"]):
            continue
        # ตัด symbol ที่ลงท้ายด้วยตัวอักษรบอก class พิเศษที่มักเป็น warrant/right/unit
        # (W=warrant, R=right, U=unit ต่อท้าย root symbol ที่สั้นกว่าปกติ)
        tickers.append(symbol)

    return tickers


def main():
    log("=" * 70)
    log("📥 กำลังดึงรายชื่อหุ้นจาก Nasdaq Symbol Directory...")
    log("=" * 70)

    all_tickers: set[str] = set()

    # ---------- nasdaqlisted.txt ----------
    try:
        log(f"🔗 ดึง {NASDAQ_LISTED_URL}")
        text = fetch_text(NASDAQ_LISTED_URL)
        rows = parse_nasdaqlisted(text)
        log(f"   ได้ {len(rows)} รายการดิบ")
        filtered = filter_common_stocks(
            rows, symbol_field="Symbol", name_field="Security Name",
            etf_field="ETF", test_field="Test Issue",
        )
        log(f"   เหลือ {len(filtered)} ตัว หลังกรอง ETF/SPAC/Warrant/Test")
        all_tickers.update(filtered)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        log(f"❌ ดึง nasdaqlisted.txt ไม่สำเร็จ: {e}")

    # ---------- otherlisted.txt ----------
    try:
        log(f"🔗 ดึง {OTHER_LISTED_URL}")
        text = fetch_text(OTHER_LISTED_URL)
        rows = parse_otherlisted(text)
        log(f"   ได้ {len(rows)} รายการดิบ")
        filtered = filter_common_stocks(
            rows, symbol_field="ACT Symbol", name_field="Security Name",
            etf_field="ETF", test_field="Test Issue",
        )
        log(f"   เหลือ {len(filtered)} ตัว หลังกรอง ETF/SPAC/Warrant/Test")
        all_tickers.update(filtered)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        log(f"❌ ดึง otherlisted.txt ไม่สำเร็จ: {e}")

    if not all_tickers:
        log("")
        log("❌ ดึงข้อมูลไม่สำเร็จเลย — เช็ค internet connection แล้วลองใหม่")
        sys.exit(1)

    sorted_tickers = sorted(all_tickers)

    log("")
    log(f"✅ รวมทั้งหมด {len(sorted_tickers)} ticker (ไม่ซ้ำกัน)")
    log(f"💾 บันทึกที่ {OUTPUT_FILE}")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ticker"])
        for t in sorted_tickers:
            writer.writerow([t])

    log("")
    log("=" * 70)
    log("✅ เสร็จแล้ว! ใช้งานด้วยคำสั่ง:")
    log(f"   python3 stock_scanner.py --tickers-file {OUTPUT_FILE}")
    log("=" * 70)
    log("")
    log("⚠️  ข้อควรรู้ก่อนรันสแกนจริง:")
    log(f"   - {len(sorted_tickers)} ticker นี้คือ universe กว้าง (Nasdaq + NYSE + อื่นๆ)")
    log("     ไม่ใช่ Russell 2000 แท้ๆ แต่ base filter ของ scanner จะกรองหุ้นที่ไม่เข้าเกณฑ์")
    log("     (ราคา/volume/market cap) ออกไปเองอยู่ดี")
    log(f"   - สแกน {len(sorted_tickers)} ตัวจะใช้เวลานานกว่าทดสอบ 16 ตัวเดิมมาก")
    log("     (เป็นหลักสิบนาทีถึงเป็นชั่วโมง ขึ้นกับ --workers และความเร็ว internet)")
    log("     แนะนำลองกับ ticker ส่วนหนึ่งก่อน เช่น:")
    log(f"     python3 stock_scanner.py --tickers-file {OUTPUT_FILE} --workers 15")


if __name__ == "__main__":
    main()
