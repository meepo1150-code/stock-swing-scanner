#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rank_tickers_by_marketcap.py
=============================
อ่านรายชื่อ ticker จาก tickers_full.csv (ที่ได้จาก build_tickers.py)
แล้วดึง market cap ของแต่ละตัว จากนั้นเรียงจากใหญ่ไปเล็ก ตัดให้เหลือ N ตัวบนสุด

เป้าหมาย: ลดจำนวน ticker จากหลักพันให้เหลือหลักร้อย-พัน ที่เป็นบริษัทใหญ่/เทรดคล่อง
ซึ่งน่าจะผ่าน base filter ของ stock_scanner.py อยู่แล้ว (price>$5, market cap>$300M, volume>1M)
ทำให้การสแกนจริงรอบต่อไปเร็วขึ้นมาก โดยไม่เสียโอกาสเจอหุ้นที่น่าสนใจไปมาก

วิธีดึงข้อมูล: ลองทาง yfinance fast_info ก่อน (เบา เร็ว) ถ้าได้ None
(บางเครื่อง/บาง yfinance เวอร์ชัน fast_info คืนค่า None เงียบๆโดยไม่ raise exception —
เจอจริงบน macOS + yfinance บางเวอร์ชัน) จะ fallback ไปใช้ .info เต็มรูปแบบ
ซึ่งช้ากว่าแต่พิสูจน์แล้วว่าใช้งานได้แน่นอน (เป็น endpoint เดียวกับที่ stock_scanner.py ใช้)

⚠️ หมายเหตุ: ขั้นตอนนี้ต้องเรียก yfinance ทุก ticker เหมือนกัน (เพื่อรู้ market cap)
ดังนั้นอาจใช้เวลาพอสมควร (ใกล้เคียงการสแกนเต็มถ้าต้อง fallback ไป .info บ่อย)
ทำครั้งนี้แล้วเก็บผลไว้ใช้ซ้ำได้นาน (market cap ไม่เปลี่ยนเร็วขนาดต้องทำใหม่ทุกวัน)
มี retry + checkpoint/resume เหมือน stock_scanner.py กัน Yahoo Finance rate limit

วิธีใช้:
  python3 rank_tickers_by_marketcap.py
  python3 rank_tickers_by_marketcap.py --top 500
  python3 rank_tickers_by_marketcap.py --input tickers_full.csv --top 1000 --workers 10
  python3 rank_tickers_by_marketcap.py --no-resume    # ไม่ resume จาก checkpoint เดิม
"""

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yfinance as yf
except ImportError:
    print("❌ ไม่พบ yfinance — รัน: pip install yfinance")
    sys.exit(1)

DEFAULT_INPUT = "tickers_full.csv"
DEFAULT_TOP_N = 1000
DEFAULT_WORKERS = 10  # ลดจาก 20 เป็น 10 เพราะ fallback ไป .info ทำให้แต่ละ request หนักขึ้น

# ใช้เกณฑ์เดียวกับ base filter ของ stock_scanner.py เพื่อตัดหุ้นที่ไม่ผ่านอยู่ดีออกไปตั้งแต่ตอนนี้
MIN_PRICE = 5.0
MIN_MARKET_CAP = 300_000_000
MIN_AVG_VOLUME = 1_000_000

# --- Rate limit handling (ค่าเดียวกับ stock_scanner.py เพื่อความสม่ำเสมอ) ---
MAX_RETRIES = 4
RETRY_BASE_DELAY_SEC = 3.0
RETRY_BACKOFF_MULTIPLIER = 2.5
RATE_LIMIT_KEYWORDS = [
    "429", "too many requests", "rate limit", "rate-limit",
    "exceeded", "throttle", "temporarily blocked",
]
REQUEST_STAGGER_DELAY_SEC = 0.15

CHECKPOINT_SUFFIX = ".rankcheckpoint.json"
CHECKPOINT_SAVE_EVERY = 100


@dataclass
class TickerInfo:
    ticker: str
    market_cap: Optional[float] = None
    price: Optional[float] = None
    avg_volume: Optional[float] = None
    error: str = ""


def log(msg: str):
    print(msg, flush=True)


def is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in RATE_LIMIT_KEYWORDS)


def load_ticker_list(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        log(f"❌ ไม่พบไฟล์ {path}")
        log(f"   รัน build_tickers.py ก่อนเพื่อสร้างไฟล์นี้")
        sys.exit(1)

    tickers = []
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        col = "ticker" if reader.fieldnames and "ticker" in reader.fieldnames else reader.fieldnames[0]
        for row in reader:
            val = row.get(col, "").strip()
            if val and not val.startswith("#"):
                tickers.append(val.upper())
    return tickers


def fetch_market_cap_one(ticker: str) -> TickerInfo:
    """
    ดึง market cap / price / avg volume ของ ticker เดียว พร้อม retry ถ้าโดน rate limit
    ลองทาง fast_info ก่อน (เบา เร็ว) ถ้าไม่ได้ข้อมูล fallback ไป .info เต็มรูปแบบ
    """
    info = TickerInfo(ticker=ticker)

    if REQUEST_STAGGER_DELAY_SEC > 0:
        time.sleep(REQUEST_STAGGER_DELAY_SEC)

    delay = RETRY_BASE_DELAY_SEC
    last_exc = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            tk = yf.Ticker(ticker)

            # ---------- ลองทาง fast_info ก่อน (เบา เร็ว) ----------
            mcap = price = avg_vol = None
            try:
                fi = tk.fast_info
                mcap = fi.get("market_cap") if hasattr(fi, "get") else fi.market_cap
                price = fi.get("last_price") if hasattr(fi, "get") else fi.last_price
                avg_vol = fi.get("ten_day_average_volume") if hasattr(fi, "get") else fi.ten_day_average_volume
            except Exception as e:
                if is_rate_limit_error(e):
                    raise  # ส่งต่อให้ retry loop รอบนอกจัดการ ไม่กลืนเงียบๆ
                # error อื่นที่ไม่ใช่ rate limit (เช่น property ใช้ไม่ได้) -> แค่ fallback ไป .info ต่อ

            # ---------- Fallback ไป .info เต็มรูปแบบ ถ้า fast_info ไม่ได้ market cap ----------
            if not mcap:
                full_info = tk.info or {}
                mcap = full_info.get("marketCap")
                price = price or full_info.get("currentPrice") or full_info.get("regularMarketPrice")
                avg_vol = avg_vol or full_info.get("averageVolume") or full_info.get("averageVolume10days")

            info.market_cap = float(mcap) if mcap else None
            info.price = float(price) if price else None
            info.avg_volume = float(avg_vol) if avg_vol else None

            if info.market_cap is None:
                info.error = "ไม่มีข้อมูล market cap (ลองทั้ง fast_info และ .info แล้ว)"

            return info  # สำเร็จ (มีหรือไม่มี market cap ก็ตาม) ไม่ต้อง retry ต่อ

        except Exception as e:
            last_exc = e
            if not is_rate_limit_error(e):
                info.error = f"{type(e).__name__}: {e}"
                return info  # error ที่ไม่ใช่ rate limit retry ไปก็ไม่ช่วย
            if attempt < MAX_RETRIES:
                time.sleep(delay)
                delay *= RETRY_BACKOFF_MULTIPLIER
            # ถ้าเป็น attempt สุดท้ายแล้ว loop จะจบแล้วตกไปด้านล่าง

    # ลองครบ MAX_RETRIES แล้วยังโดน rate limit อยู่
    info.error = f"rate limit: {last_exc}"
    return info


# =========================================================================
# 💾 Checkpoint helpers
# =========================================================================

def checkpoint_path_for(output_file: str) -> str:
    return output_file + CHECKPOINT_SUFFIX


def save_checkpoint(output_file: str, results: list[TickerInfo], remaining: list[str]):
    ckpt_path = checkpoint_path_for(output_file)
    data = {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "completed_results": [asdict(r) for r in results],
        "remaining_tickers": remaining,
    }
    try:
        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log(f"⚠️ บันทึก checkpoint ไม่สำเร็จ (ไม่กระทบการทำงานต่อ): {e}")


def load_checkpoint(output_file: str) -> Optional[dict]:
    ckpt_path = checkpoint_path_for(output_file)
    if not Path(ckpt_path).exists():
        return None
    try:
        with open(ckpt_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ อ่าน checkpoint ไม่สำเร็จ ({e}) — จะเริ่มใหม่ทั้งหมด")
        return None


def delete_checkpoint(output_file: str):
    try:
        Path(checkpoint_path_for(output_file)).unlink(missing_ok=True)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="จัดอันดับ ticker ตาม market cap แล้วตัดให้เหลือ N ตัวบนสุด"
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help=f"ไฟล์ input (default: {DEFAULT_INPUT})")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help=f"จำนวน ticker ที่จะเก็บ (default: {DEFAULT_TOP_N})")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"จำนวน thread พร้อมกัน (default: {DEFAULT_WORKERS})")
    parser.add_argument("--output", default=None, help="ไฟล์ output (default: tickers_top{N}.csv)")
    parser.add_argument("--no-resume", action="store_true", help="ไม่ resume จาก checkpoint เดิม แม้จะมีอยู่")
    args = parser.parse_args()

    output_file = args.output or f"tickers_top{args.top}.csv"

    all_tickers = load_ticker_list(args.input)
    log(f"📋 โหลด {len(all_tickers)} ticker จาก {args.input}")

    results: list[TickerInfo] = []
    tickers_to_fetch = list(all_tickers)

    if args.no_resume:
        delete_checkpoint(output_file)
        log("⚠️ ไม่ resume จาก checkpoint เดิม (--no-resume)")
    else:
        ckpt = load_checkpoint(output_file)
        if ckpt:
            prev_results = [TickerInfo(**d) for d in ckpt.get("completed_results", [])]
            prev_remaining = ckpt.get("remaining_tickers", [])
            prev_all = {r.ticker for r in prev_results} | set(prev_remaining)
            if prev_all == set(all_tickers):
                results = prev_results
                tickers_to_fetch = prev_remaining
                log(f"♻️  พบ checkpoint เดิม — ดึงไปแล้ว {len(results)} ตัว เหลืออีก {len(tickers_to_fetch)} ตัว")
            else:
                log("ℹ️  พบ checkpoint แต่รายชื่อ ticker ไม่ตรงกับรอบนี้ — เริ่มใหม่ทั้งหมด")
                delete_checkpoint(output_file)

    if tickers_to_fetch:
        log(f"🔍 กำลังดึง market cap ด้วย {args.workers} workers (ลอง fast_info ก่อน, fallback .info ถ้าจำเป็น)...")
        log(f"   (ขั้นตอนนี้ต้องเรียก yfinance ทุกตัว จะใช้เวลาพอสมควร — ทำครั้งนี้แล้วเก็บผลไว้ใช้ซ้ำได้)")
        log("-" * 70)

        start = time.time()
        completed = 0
        total_target = len(results) + len(tickers_to_fetch)

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_ticker = {executor.submit(fetch_market_cap_one, t): t for t in tickers_to_fetch}
            pending = set(tickers_to_fetch)

            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                completed += 1
                res = future.result()

                if res.error and is_rate_limit_error(Exception(res.error)):
                    # ยัง rate limit อยู่แม้ retry ครบแล้ว — เก็บไว้ใน remaining ให้ resume รอบหน้า
                    pass
                else:
                    pending.discard(ticker)
                    results.append(res)

                if completed % CHECKPOINT_SAVE_EVERY == 0 or completed == len(tickers_to_fetch):
                    elapsed = time.time() - start
                    n_err = sum(1 for r in results if r.error)
                    log(f"   [{len(results)}/{total_target}] ดึงแล้ว ({n_err} error/ไม่มีข้อมูล) — {elapsed:.0f}s")
                    save_checkpoint(output_file, results, sorted(pending))

        elapsed = time.time() - start
        log("-" * 70)

        if pending:
            log(f"⚠️  เหลือ {len(pending)} ตัวที่ยังโดน rate limit อยู่ — บันทึก checkpoint ไว้แล้ว")
            log(f"   รันคำสั่งเดิมอีกครั้งเพื่อดึงต่อ (จะ resume อัตโนมัติ): python3 {Path(__file__).name} --input {args.input} --top {args.top} --output {output_file}")
            log(f"⏱️ ใช้เวลาไป {elapsed:.0f} วินาทีในรอบนี้")
            return  # ยังไม่ครบ ไม่ต้องเขียนผลลัพธ์สุดท้ายตอนนี้

        log(f"⏱️ ดึงข้อมูลครบใน {elapsed:.0f} วินาที (รอบนี้)")
    else:
        log("✅ ดึงข้อมูลครบจาก checkpoint แล้ว ไม่ต้องดึงเพิ่ม")

    n_err = sum(1 for r in results if r.error)
    log(f"📊 รวมทั้งหมด {len(results)} ตัว ({n_err} ตัว error/ไม่มีข้อมูล)")

    # กรองตาม base filter เดียวกับ stock_scanner.py ก่อนเรียง (ตัดของที่ไม่ผ่านอยู่ดีออกไปเลย)
    qualified = [
        r for r in results
        if r.market_cap and r.price and r.avg_volume
        and r.price > MIN_PRICE
        and r.market_cap > MIN_MARKET_CAP
        and r.avg_volume > MIN_AVG_VOLUME
    ]
    log(f"✅ ผ่านเกณฑ์ base filter (price>${MIN_PRICE}, mcap>${MIN_MARKET_CAP:,}, vol>{MIN_AVG_VOLUME:,}): {len(qualified)} ตัว")

    if not qualified:
        log("")
        log("❌ ไม่มี ticker ตัวไหนผ่านเกณฑ์เลย — เช็คว่า fetch_market_cap_one ทำงานถูกต้องไหม")
        log("   (ลองรัน: python3 -c \"import yfinance as yf; print(yf.Ticker('AAPL').info.get('marketCap'))\")")
        delete_checkpoint(output_file)
        sys.exit(1)

    # เรียงจาก market cap ใหญ่ไปเล็ก
    qualified.sort(key=lambda r: r.market_cap, reverse=True)

    top_n = qualified[: args.top]
    log(f"📊 ตัดเหลือ Top {len(top_n)} ตัว (ใหญ่สุดก่อน)")

    if top_n:
        log(f"   ใหญ่สุด: {top_n[0].ticker} (${top_n[0].market_cap:,.0f})")
        log(f"   เล็กสุดในกลุ่มที่เลือก: {top_n[-1].ticker} (${top_n[-1].market_cap:,.0f})")

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ticker"])
        for r in top_n:
            writer.writerow([r.ticker])

    delete_checkpoint(output_file)  # สำเร็จครบแล้ว ไม่ต้องเก็บ checkpoint ไว้อีก

    log("")
    log("=" * 70)
    log(f"✅ บันทึกที่ {output_file}")
    log(f"   ใช้งานด้วย: python3 stock_scanner.py --tickers-file {output_file}")
    log("=" * 70)


if __name__ == "__main__":
    main()
