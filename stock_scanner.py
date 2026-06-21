#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_scanner.py
================
Nasdaq + Russell 2000 Swing Trading Scanner

สแกนหุ้นตามรายชื่อที่กำหนด (Nasdaq + Russell 2000) แล้วเช็ค 5 เงื่อนไข Setup:
  1. Gap & Hold
  2. Volume Breakout
  3. Relative Strength Leader
  4. Pullback to Support (in uptrend)
  5. Sector Hype Rotation

ผลลัพธ์จะถูก export เป็น scanner_results.json สำหรับใช้กับ stock_dashboard.html

⚠️ หมายเหตุสำคัญ:
  - นี่คือ Scan + Dashboard เท่านั้น ไม่ใช่ Auto Trading Bot
  - ไม่มีการส่งคำสั่งซื้อขายอัตโนมัติใดๆ ทั้งสิ้น
  - ต้อง verify หุ้นที่เจอกับแอป Dime!/Webull ก่อนตัดสินใจซื้อขายจริงเสมอ
  - ผลลัพธ์ไม่ใช่คำแนะนำการลงทุน และไม่การันตีผลกำไร

วิธีใช้:
  python3 stock_scanner.py                      # สแกนทั้งหมด ใช้ tickers.csv
  python3 stock_scanner.py --tickers AAPL,NVDA   # สแกนเฉพาะบาง ticker (ทดสอบ)
  python3 stock_scanner.py --workers 10          # ปรับจำนวน thread พร้อมกัน
"""

import argparse
import csv
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("❌ ไม่พบ yfinance — รัน: pip install yfinance")
    sys.exit(1)


# =========================================================================
# 🛡️ FILTER พื้นฐาน (บังคับทุกเงื่อนไข) — ตาม Project Instructions
# =========================================================================
MIN_PRICE = 5.0                  # ราคา > $5 (เลี่ยง penny stock)
MIN_AVG_VOLUME = 1_000_000       # Avg Volume > 1,000,000 shares/วัน
MIN_MARKET_CAP = 300_000_000     # Market Cap > $300M
MIN_DOLLAR_VOLUME = 5_000_000    # Avg Dollar Volume > $5M/วัน

# พารามิเตอร์ของแต่ละเงื่อนไข Setup (ปรับได้ตามต้องการ)
GAP_UP_PCT_MIN = 4.0             # Gap & Hold: gap up > 4%
VOLUME_BREAKOUT_MULTIPLIER = 2.0  # Volume Breakout: volume > 2x average
VOLUME_BREAKOUT_LOOKBACK = 20     # break 20-day high
RS_LOOKBACK_DAYS = 5               # Relative Strength: เทียบ 5 วันหลังสุด
RS_MIN_STREAK_DAYS = 3             # ต้องแรงกว่าตลาดต่อเนื่องอย่างน้อย 3 วัน
PULLBACK_EMA_SHORT = 10
PULLBACK_EMA_LONG = 20
PULLBACK_TOLERANCE_PCT = 2.0       # ถือว่า "แตะ" EMA ถ้าห่างไม่เกิน 2%
SECTOR_ROTATION_LOOKBACK = 5       # Sector Hype: เทียบ sector ETF 5 วัน
EARNINGS_SURPRISE_LOOKBACK_DAYS = 5   # เงื่อนไข 6: เพิ่งมี earnings surprise ภายในกี่วัน
EARNINGS_SURPRISE_MIN_PCT = 5.0       # ต้อง beat estimate เกินกี่% ถึงนับว่า surprise

# =========================================================================
# 📅 EARNINGS CALENDAR WARNING — เตือนล่วงหน้าก่อนหุ้นประกาศผลประกอบการ
# =========================================================================
# ⚠️ ดึงเฉพาะ ticker ที่ match setup อย่างน้อย 1 ข้อ (second-pass เหมือน insider
# buying) เพื่อไม่เพิ่ม API call ให้ทั้ง 1000 ticker — ดึงแค่ตัวที่กำลังพิจารณาซื้อจริง
#
# Severity ขยับตามจำนวนวันที่เหลือก่อนประกาศ เพราะ swing trade ถือแค่ 1-5 วัน:
#   - red: 1-2 วัน   -> เสี่ยงสูงถ้าวางแผนถือเกินวันประกาศ
#   - yellow: 3-5 วัน -> เช็คแผน exit ให้ชัดก่อนถึงวันนั้น
#   - white: 6-7 วัน  -> ข้อมูลอ้างอิง ไม่กระทบ position สั้นๆมากนัก
EARNINGS_WARNING_WINDOW_DAYS = 7   # แสดงเตือนถ้า earnings อยู่ภายในกี่วันข้างหน้า
EARNINGS_WARNING_RED_MAX_DAYS = 2     # 1-2 วัน = red
EARNINGS_WARNING_YELLOW_MAX_DAYS = 5  # 3-5 วัน = yellow (6-7 วัน = white)

# =========================================================================
# 👔 INSIDER BUYING — second-pass confirmation layer (เหมือน earnings calendar)
# =========================================================================
# ⚠️ Insider buying คือ "confirmation" ไม่ใช่ "pre-filter" — บอกว่าคนข้างในเชื่อมั่น
# ระยะยาว ไม่ได้แปลว่าหุ้นจะ breakout วันนี้พรุ่งนี้ จึงเช็คทีหลังจาก 6 setup เดิม
# match แล้วเท่านั้น (second-pass) เพื่อไม่เพิ่ม API call ให้ทั้ง 1000 ticker
#
# ⚠️ yfinance ส่ง column "Transaction" เป็นข้อความบรรยาย (เช่น "Purchase at price..")
# ไม่ใช่ raw SEC code ตัวอักษรเดียว ("P","S","A",...) ดังนั้นต้องจับคำแบบเข้มงวด:
# ต้องมีคำที่บอกว่าเป็นการซื้อในตลาดเปิด (purchase/buy) "และ" ต้องไม่มีคำที่บอกว่า
# เป็น sale/gift/award/grant/option ปนอยู่ในข้อความเดียวกัน ลด false positive
INSIDER_BUYING_LOOKBACK_DAYS = 30   # นับเฉพาะ purchase ภายในกี่วันที่ผ่านมา
INSIDER_BUYING_PURCHASE_KEYWORDS = ("purchase", "buy", "bought")
INSIDER_BUYING_EXCLUDE_KEYWORDS = ("sale", "sold", "gift", "award", "grant", "option exercise", "exercise")

# =========================================================================
# 🏦 INSTITUTIONAL OWNERSHIP — second-pass, cache 30 วัน (13F รายงานช้า)
# =========================================================================
# ข้อมูล 13F รายงานทุกไตรมาส เปลี่ยนช้ามาก ไม่จำเป็นต้องดึงใหม่ทุกวัน — cache ไว้
# 30 วันต่อ ticker ลด API call ลงมาก (เดือนละครั้งพอ ไม่ใช่ทุกวัน)
INSTITUTIONAL_CACHE_FILE_NAME = "institutional_ownership_cache.json"
INSTITUTIONAL_CACHE_MAX_AGE_DAYS = 30

# =========================================================================
# 🧮 FUNDAMENTAL FILTER — กันหุ้นงบแย่ก่อนเข้า 6 setup (risk filter ไม่ใช่ alpha)
# =========================================================================
# หมายเหตุ: นี่คือการกรองความเสี่ยงพื้นฐานเบื้องต้น ไม่ใช่เครื่องมือ "ชนะตลาด"
# ข้อมูลเหล่านี้ public ทุกคนเข้าถึงได้เหมือนกัน จุดประสงค์คือลดความเสี่ยงเจอ
# หุ้นที่งบแย่มากๆ (หนี้สูงผิดปกติ, ขาดทุนสะสมหนัก, ใกล้ delist) ไม่ใช่หาหุ้น "ดีกว่าตลาด"
FUNDAMENTAL_FILTER_ENABLED = True   # ปิดได้ถ้าไม่ต้องการกรองชั้นนี้ (--no-fundamental-filter)
MAX_DEBT_TO_EQUITY = 300.0          # debt/equity ไม่เกิน 300% (หนี้ไม่เกิน 3 เท่าทุน)
MIN_CURRENT_RATIO = 0.5             # current ratio ขั้นต่ำ (สภาพคล่องระยะสั้น) — ผ่อนปรนเพราะ growth stock มักต่ำกว่า 1
MAX_NEGATIVE_EARNINGS_STREAK = None  # ตั้งเป็นตัวเลขถ้าต้องการกันหุ้นขาดทุนติดต่อกันหลายปี (None = ไม่เช็ค)

# น้ำหนักของ fundamental score (รวมแล้ว 100 คะแนน) — ปรับได้ตามมุมมอง
FUNDAMENTAL_SCORE_WEIGHTS = {
    "profitability": 25,    # profit margin, ROE
    "growth": 25,            # revenue growth, earnings growth
    "financial_health": 25,  # debt/equity, current ratio
    "valuation": 25,         # P/E เทียบ sector (ถ้าไม่มีข้อมูล sector PE จะข้ามและกระจายน้ำหนักให้ข้ออื่น)
}

# Sector ETF ที่ใช้เทียบ (เพิ่ม/ลดได้ตามต้องการ)
SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
    "Biotechnology": "XBI",
}

MARKET_BENCHMARK = "^IXIC"  # Nasdaq Composite ใช้เป็น benchmark สำหรับ Relative Strength

HISTORY_PERIOD = "3mo"   # ดึงข้อมูลย้อนหลัง 3 เดือน (พอสำหรับทุกเงื่อนไข)
HISTORY_INTERVAL = "1d"

DEFAULT_TICKERS_FILE = "tickers.csv"
DEFAULT_OUTPUT_FILE = "scanner_results.json"

# =========================================================================
# 🗂️ HISTORY SNAPSHOT — เก็บผลสแกนแต่ละวันไว้สำหรับ compare.html
# =========================================================================
# ทุกครั้งที่สแกนสำเร็จครบ (is_complete=True) จะเซฟสำเนาผลลัพธ์เป็น snapshot
# แยกตามวันที่ไว้ในโฟลเดอร์ history/ ข้าง output file หลัก
# เก็บย้อนหลังตามจำนวนวันที่กำหนด ไฟล์เก่ากว่านั้นจะถูกลบอัตโนมัติ
HISTORY_DIR_NAME = "history"
HISTORY_RETENTION_DAYS = 30

# =========================================================================
# 🩺 DATA FRESHNESS CHECK — เช็คข้อมูลผิดปกติก่อนเอาไป generate setup
# =========================================================================
# ป้องกัน garbage-in-garbage-out: ถ้า yfinance ส่งข้อมูลเพี้ยนมา (ราคา 0, volume หาย,
# ราคากระโดดผิดปกติ, ข้อมูลเก่าเกินจริง) ไม่ควรเอาไปคำนวณ setup เงียบๆโดยไม่เตือน
# หมายเหตุ: นี่คือการ "flag เตือน" เท่านั้น ไม่ตัด ticker ออกจากผลลัพธ์ — เพราะบางครั้ง
# ราคาที่ดูเหมือนผิดปกติ (เช่น กระโดดแรงมาก) อาจเป็นข่าวจริงๆก็ได้ ต้องให้คนตัดสินใจเอง
DATA_FRESHNESS_MAX_DAILY_MOVE_PCT = 50.0   # ราคาเปลี่ยนเกินกี่% ใน 1 วัน ถึงถือว่าน่าสงสัย
DATA_FRESHNESS_MAX_STALE_TRADING_DAYS = 5  # ข้อมูลล่าสุดเก่ากว่ากี่วันทำการ ถึงถือว่า "ล้า"

# =========================================================================
# 🔁 RATE LIMIT HANDLING — กัน Yahoo Finance บล็อกตอนสแกนจำนวนมาก
# =========================================================================
# Yahoo Finance ไม่มี API key ทางการ ถ้ายิง request เร็ว/เยอะเกินไปจาก IP เดียว
# จะถูกบล็อกชั่วคราว (rate limit) ไม่ใช่ bug ของโค้ด — ป้องกันด้วย retry + backoff
MAX_RETRIES = 4                   # ลองใหม่สูงสุดกี่ครั้งต่อ ticker ถ้าเจอ error ที่น่าจะเป็น rate limit
RETRY_BASE_DELAY_SEC = 3.0        # หน่วงเวลาเริ่มต้นก่อน retry ครั้งแรก (วินาที)
RETRY_BACKOFF_MULTIPLIER = 2.5    # แต่ละครั้งที่ retry ใหม่ หน่วงเวลานานขึ้นกี่เท่า
RATE_LIMIT_KEYWORDS = [
    "429", "too many requests", "rate limit", "rate-limit",
    "exceeded", "throttle", "temporarily blocked",
]

# Checkpoint: เซฟผลที่สแกนได้แล้วเป็นพักๆ กัน scan ค้างกลางทางแล้วต้องเริ่มใหม่หมด
CHECKPOINT_FILE_SUFFIX = ".checkpoint.json"
CHECKPOINT_SAVE_EVERY = 50         # เซฟ checkpoint ทุกๆ N ticker ที่สแกนเสร็จ

# หน่วงเวลาเล็กน้อยระหว่าง request ของแต่ละ worker (ลดโอกาสโดน rate limit ตั้งแต่ต้น)
REQUEST_STAGGER_DELAY_SEC = 0.15


# =========================================================================
# 📦 Data classes
# =========================================================================

@dataclass
class SetupMatch:
    setup_name: str
    matched: bool
    detail: str = ""
    suggested_hold_days: str = ""


@dataclass
class ScanResult:
    ticker: str
    company_name: str = ""
    sector: str = ""
    price: float = 0.0
    avg_volume: int = 0
    market_cap: float = 0.0
    avg_dollar_volume: float = 0.0
    passed_base_filter: bool = False
    base_filter_fail_reason: str = ""
    # --- Data freshness (เพิ่มใหม่) — flag เตือนเฉยๆ ไม่ตัด ticker ออก ---
    data_quality_flags: list = field(default_factory=list)
    # --- Fundamental layer (เพิ่มใหม่) ---
    passed_fundamental_filter: bool = True
    fundamental_filter_fail_reason: str = ""
    fundamental_score: Optional[float] = None  # 0-100, None ถ้าข้อมูลไม่พอคำนวณ
    fundamental_breakdown: dict = field(default_factory=dict)
    fundamentals_raw: dict = field(default_factory=dict)  # ตัวเลขดิบสำหรับโชว์ใน dashboard
    # --- Setup matching (เดิม + setup ใหม่) ---
    setups_matched: list = field(default_factory=list)
    # --- Earnings calendar warning (เพิ่มใหม่) — เช็คเฉพาะตัวที่ match setup แล้ว ---
    upcoming_earnings_warning: Optional[dict] = None
    # --- Insider buying (เพิ่มใหม่) — second-pass confirmation layer ---
    insider_buying_signal: Optional[dict] = None
    # --- Institutional ownership (เพิ่มใหม่) — second-pass, cache 30 วัน ---
    institutional_ownership: Optional[dict] = None
    error: str = ""
    # --- Internal flag เท่านั้น (ไม่ export ลง JSON) — บอก run_scan ว่า ticker นี้ดึง
    # institutional ownership ใหม่จริงไหม หรือใช้ cache เดิม (ถ้าใช้ cache เดิม ไม่ต้อง
    # เขียน cache ทับใหม่ จะได้ไม่เสีย cached_at เดิมไปโดยไม่จำเป็น) ---
    _institutional_cache_hit: bool = False

    def to_dict(self):
        d = asdict(self)
        d["setups_matched"] = [asdict(s) for s in self.setups_matched]
        d.pop("_institutional_cache_hit", None)  # internal field เท่านั้น ไม่ใช่ข้อมูลที่ dashboard ต้องเห็น
        return d


# =========================================================================
# 🔧 Helper functions
# =========================================================================

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_tickers(path: str) -> list[str]:
    """
    โหลดรายชื่อ ticker จากไฟล์ CSV
    รูปแบบไฟล์: หนึ่ง ticker ต่อบรรทัด หรือมี column ชื่อ 'ticker'/'symbol'
    """
    p = Path(path)
    if not p.exists():
        log(f"⚠️ ไม่พบไฟล์ {path} — ใช้รายชื่อตัวอย่างแทน (ไม่ครบ Nasdaq+Russell 2000 จริง)")
        return _fallback_sample_tickers()

    tickers = []
    header_names = {"ticker", "symbol"}
    with open(p, newline="", encoding="utf-8") as f:
        sample_lines = f.read(2048).splitlines()
        f.seek(0)
        has_comma_header = bool(sample_lines) and "," in sample_lines[0]

        if has_comma_header:
            reader = csv.DictReader(f)
            col = None
            for candidate in ("ticker", "symbol", "Ticker", "Symbol"):
                if reader.fieldnames and candidate in reader.fieldnames:
                    col = candidate
                    break
            if col is None:
                col = reader.fieldnames[0] if reader.fieldnames else None
            for row in reader:
                if col and row.get(col):
                    val = row[col].strip()
                    if val and not val.startswith("#"):
                        tickers.append(val.upper())
        else:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower() in header_names:
                    continue  # ข้าม header แบบ single-column (เช่น "ticker")
                tickers.append(line.upper())

    tickers = sorted(set(t for t in tickers if t))
    log(f"✅ โหลด {len(tickers)} tickers จาก {path}")
    return tickers


def _fallback_sample_tickers() -> list[str]:
    """รายชื่อตัวอย่างเล็กๆ สำหรับทดสอบโค้ด (ไม่ใช่ Nasdaq+Russell 2000 ฉบับเต็ม)"""
    return [
        "AAPL", "NVDA", "AMD", "TSLA", "MSFT", "AMZN", "META", "GOOGL",
        "AVGO", "NFLX", "COIN", "PLTR", "SMCI", "MARA", "RIOT", "SOFI",
    ]


def safe_float(x, default=0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def pct_change(a: float, b: float) -> float:
    """% เปลี่ยนแปลงจาก a ไป b"""
    if a == 0:
        return 0.0
    return (b - a) / a * 100.0


# =========================================================================
# 🩺 Data freshness check
# =========================================================================

def check_data_freshness(hist: pd.DataFrame) -> list[str]:
    """
    ตรวจข้อมูลราคาล่าสุดว่าผิดปกติไหมก่อนเอาไปคำนวณ base filter / setup ใดๆ
    คืนค่า: list ของคำเตือน (ว่างเปล่า = ไม่มีปัญหาที่เจอ)

    หมายเหตุ: เป็นการ "flag" เท่านั้น ไม่ตัด ticker ออก — เพราะบางอย่างที่ดูผิดปกติ
    (เช่น ราคากระโดดแรงมาก) อาจเป็นข่าวจริงก็ได้ ต้องให้คนตัดสินใจเองว่าเชื่อถือได้ไหม
    """
    flags: list[str] = []

    if hist is None or hist.empty:
        flags.append("ไม่มีข้อมูลราคาเลย")
        return flags

    last = hist.iloc[-1]

    # --- 1. ราคา/volume เป็น NaN ในแถวล่าสุด ---
    ohlcv_cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in hist.columns]
    nan_cols = [c for c in ohlcv_cols if pd.isna(last.get(c))]
    if nan_cols:
        flags.append(f"ข้อมูลวันล่าสุดมีค่าหายไป (NaN) ในคอลัมน์: {', '.join(nan_cols)}")

    # --- 2. ราคาเป็น 0 หรือติดลบ (เป็นไปไม่ได้จริง) ---
    for col in ("Open", "High", "Low", "Close"):
        if col in hist.columns:
            val = safe_float(last.get(col), default=None) if last.get(col) is not None else None
            if val is not None and val <= 0:
                flags.append(f"ราคา {col} = {val} (≤ 0 ผิดปกติ)")

    # --- 3. Volume เป็น 0 ในวันล่าสุด ---
    if "Volume" in hist.columns:
        last_volume = safe_float(last.get("Volume"))
        if last_volume <= 0:
            flags.append("Volume วันล่าสุด = 0 (อาจ halt หรือข้อมูลขาด)")

    # --- 4. ราคากระโดดผิดปกติเทียบวันก่อนหน้า ---
    if "Close" in hist.columns and len(hist) >= 2:
        prev_close = safe_float(hist["Close"].iloc[-2])
        last_close = safe_float(last.get("Close"))
        if prev_close > 0:
            move_pct = abs(pct_change(prev_close, last_close))
            if move_pct > DATA_FRESHNESS_MAX_DAILY_MOVE_PCT:
                flags.append(
                    f"ราคาเปลี่ยน {move_pct:.0f}% ใน 1 วัน (prev ${prev_close:.2f} → ${last_close:.2f}) "
                    f"— เกิน {DATA_FRESHNESS_MAX_DAILY_MOVE_PCT:.0f}% เช็คให้แน่ใจว่าไม่ใช่ data error "
                    f"ก่อนเชื่อตัวเลขนี้ (อาจเป็นข่าวจริง หรือ stock split ที่ไม่ได้ adjust)"
                )

    # --- 5. ข้อมูลล้า (วันล่าสุดใน hist เก่ากว่าที่ควร) ---
    try:
        last_date = hist.index[-1]
        if last_date.tzinfo is not None:
            now = pd.Timestamp.now(tz=last_date.tzinfo)
        else:
            now = pd.Timestamp.now()
        days_stale = (now.normalize() - last_date.normalize()).days
        if days_stale > DATA_FRESHNESS_MAX_STALE_TRADING_DAYS:
            flags.append(
                f"ข้อมูลล่าสุดเก่ากว่า {days_stale} วัน (วันที่ {last_date.date()}) "
                f"— อาจ delist/halt หรือ data feed มีปัญหา"
            )
    except Exception:
        pass  # ถ้าเช็ควันที่ไม่ได้ ไม่ critical พอจะ fail ทั้งฟังก์ชัน

    return flags


# =========================================================================
# 🛡️ Base filter
# =========================================================================

def apply_base_filter(hist: pd.DataFrame, info: dict) -> tuple[bool, str, dict]:
    """
    เช็ค filter พื้นฐาน 5 ข้อ
    คืนค่า: (passed: bool, fail_reason: str, metrics: dict)
    """
    if hist is None or hist.empty:
        return False, "ไม่มีข้อมูลราคา", {}

    last_close = safe_float(hist["Close"].iloc[-1])
    avg_volume = safe_float(hist["Volume"].tail(20).mean())
    dollar_volume = last_close * avg_volume
    market_cap = safe_float(info.get("marketCap"))

    metrics = {
        "price": last_close,
        "avg_volume": int(avg_volume),
        "market_cap": market_cap,
        "avg_dollar_volume": dollar_volume,
    }

    if last_close <= MIN_PRICE:
        return False, f"ราคา ${last_close:.2f} ≤ ${MIN_PRICE} (penny stock)", metrics
    if avg_volume <= MIN_AVG_VOLUME:
        return False, f"Avg Volume {avg_volume:,.0f} ≤ {MIN_AVG_VOLUME:,}", metrics
    if market_cap and market_cap <= MIN_MARKET_CAP:
        return False, f"Market Cap ${market_cap:,.0f} ≤ ${MIN_MARKET_CAP:,}", metrics
    if dollar_volume <= MIN_DOLLAR_VOLUME:
        return False, f"Dollar Volume ${dollar_volume:,.0f} ≤ ${MIN_DOLLAR_VOLUME:,}", metrics

    return True, "", metrics


# =========================================================================
# 🧮 Fundamental filter + Fundamental score
# =========================================================================
# ⚠️ ข้อควรเข้าใจ: ข้อมูลพื้นฐาน (P/E, debt, growth) เป็นข้อมูล public ที่ทุกคน
# เข้าถึงได้เหมือนกัน — ส่วนนี้ทำหน้าที่เป็น "risk filter" ลดความเสี่ยงเจอหุ้นงบแย่
# ไม่ใช่เครื่องมือค้นหา "หุ้นที่ดีกว่าตลาด" สำหรับ swing trade 1-5 วัน
# price action / volume / momentum ยังเป็นตัวตัดสินหลักเหมือนเดิม

def apply_fundamental_filter(info: dict) -> tuple[bool, str]:
    """
    กรองหุ้นที่งบการเงินแย่ผิดปกติออกก่อนเข้า setup
    คืนค่า: (passed: bool, fail_reason: str)
    หมายเหตุ: ถ้าไม่มีข้อมูลฟิลด์ใดฟิลด์หนึ่ง จะ "ปล่อยผ่าน" ฟิลด์นั้น (ไม่ใช่ตัด)
    เพราะ yfinance บางตัวข้อมูลไม่ครบ ไม่ควรตัดทิ้งทั้งที่ไม่รู้ข้อมูลจริง
    """
    if not FUNDAMENTAL_FILTER_ENABLED:
        return True, ""

    debt_to_equity = info.get("debtToEquity")  # yfinance คืนเป็น % อยู่แล้ว เช่น 150 = 150%
    if debt_to_equity is not None:
        try:
            if float(debt_to_equity) > MAX_DEBT_TO_EQUITY:
                return False, f"Debt/Equity {float(debt_to_equity):.0f}% > {MAX_DEBT_TO_EQUITY:.0f}% (หนี้สูงผิดปกติ)"
        except (TypeError, ValueError):
            pass

    current_ratio = info.get("currentRatio")
    if current_ratio is not None:
        try:
            if float(current_ratio) < MIN_CURRENT_RATIO:
                return False, f"Current Ratio {float(current_ratio):.2f} < {MIN_CURRENT_RATIO} (สภาพคล่องระยะสั้นน่ากังวล)"
        except (TypeError, ValueError):
            pass

    return True, ""


def calculate_fundamental_score(info: dict, sector_pe_cache: dict) -> tuple[Optional[float], dict, dict]:
    """
    คำนวณ fundamental score 0-100 จาก 4 มุม: profitability, growth, financial_health, valuation
    คืนค่า: (score: float|None, breakdown: dict, raw_numbers: dict)

    หมายเหตุ: เป็นคะแนนเชิงคุณภาพหยาบๆ สำหรับให้ดูคู่กับ setup ที่ match เท่านั้น
    ไม่ใช่การประเมินมูลค่าหุ้นแบบมืออาชีพ และไม่ควรใช้ตัดสินใจซื้อขายเพียงอย่างเดียว
    """
    raw = {
        "profit_margin": info.get("profitMargins"),
        "roe": info.get("returnOnEquity"),
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),
        "debt_to_equity": info.get("debtToEquity"),
        "current_ratio": info.get("currentRatio"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "sector": info.get("sector"),
    }

    sub_scores = {}
    weights_used = {}

    # --- 1. Profitability (profit margin + ROE) ---
    profit_pts = []
    if raw["profit_margin"] is not None:
        pm = safe_float(raw["profit_margin"]) * 100  # แปลงเป็น %
        # 0% margin = 0 คะแนน, 20%+ margin = คะแนนเต็ม
        profit_pts.append(max(0.0, min(100.0, pm / 20.0 * 100)))
    if raw["roe"] is not None:
        roe = safe_float(raw["roe"]) * 100
        # ROE 0% = 0 คะแนน, 25%+ = คะแนนเต็ม
        profit_pts.append(max(0.0, min(100.0, roe / 25.0 * 100)))
    if profit_pts:
        sub_scores["profitability"] = sum(profit_pts) / len(profit_pts)
        weights_used["profitability"] = FUNDAMENTAL_SCORE_WEIGHTS["profitability"]

    # --- 2. Growth (revenue growth + earnings growth) ---
    growth_pts = []
    if raw["revenue_growth"] is not None:
        rg = safe_float(raw["revenue_growth"]) * 100
        # 0% growth = 50 คะแนน (กลางๆ), 30%+ = คะแนนเต็ม, ติดลบ = คะแนนต่ำ
        growth_pts.append(max(0.0, min(100.0, 50 + rg / 30.0 * 50)))
    if raw["earnings_growth"] is not None:
        eg = safe_float(raw["earnings_growth"]) * 100
        growth_pts.append(max(0.0, min(100.0, 50 + eg / 30.0 * 50)))
    if growth_pts:
        sub_scores["growth"] = sum(growth_pts) / len(growth_pts)
        weights_used["growth"] = FUNDAMENTAL_SCORE_WEIGHTS["growth"]

    # --- 3. Financial health (debt/equity + current ratio) ---
    health_pts = []
    if raw["debt_to_equity"] is not None:
        de = safe_float(raw["debt_to_equity"])
        # debt/equity 0% = คะแนนเต็ม, 300%+ = 0 คะแนน
        health_pts.append(max(0.0, min(100.0, 100 - de / 300.0 * 100)))
    if raw["current_ratio"] is not None:
        cr = safe_float(raw["current_ratio"])
        # current ratio 2.0+ = คะแนนเต็ม, 0 = 0 คะแนน
        health_pts.append(max(0.0, min(100.0, cr / 2.0 * 100)))
    if health_pts:
        sub_scores["financial_health"] = sum(health_pts) / len(health_pts)
        weights_used["financial_health"] = FUNDAMENTAL_SCORE_WEIGHTS["financial_health"]

    # --- 4. Valuation (เทียบ trailing PE กับ sector PE เฉลี่ย ถ้ามี) ---
    sector = raw["sector"]
    sector_avg_pe = sector_pe_cache.get(sector) if sector else None
    if raw["trailing_pe"] is not None and raw["trailing_pe"] > 0:
        pe = safe_float(raw["trailing_pe"])
        if sector_avg_pe and sector_avg_pe > 0:
            # PE ต่ำกว่า sector เฉลี่ย = คะแนนสูง (relative valuation)
            ratio = pe / sector_avg_pe
            val_score = max(0.0, min(100.0, 100 - (ratio - 1) * 50))
        else:
            # ไม่มีข้อมูล sector PE -> ใช้เกณฑ์หยาบๆ: PE 15 = กลางๆ, ต่ำกว่า/สูงกว่าปรับตามนั้น
            val_score = max(0.0, min(100.0, 100 - (pe - 15) / 30.0 * 50))
        sub_scores["valuation"] = val_score
        weights_used["valuation"] = FUNDAMENTAL_SCORE_WEIGHTS["valuation"]

    if not sub_scores:
        return None, {}, raw  # ไม่มีข้อมูลพอจะคำนวณเลย

    # Normalize น้ำหนักตามที่มีข้อมูลจริง (ถ้าขาดมุมไหนไป กระจายน้ำหนักให้มุมที่เหลือ)
    total_weight = sum(weights_used.values())
    final_score = sum(
        sub_scores[k] * (weights_used[k] / total_weight) for k in sub_scores
    )

    breakdown = {k: round(v, 1) for k, v in sub_scores.items()}
    breakdown["_weights_used"] = weights_used
    breakdown["_data_completeness"] = f"{len(sub_scores)}/4 มุม"

    return round(final_score, 1), breakdown, raw


# =========================================================================
# 🔍 6 เงื่อนไข Setup (5 เดิม + Earnings Surprise Momentum)
# =========================================================================

def check_gap_and_hold(hist: pd.DataFrame) -> SetupMatch:
    """
    1. Gap & Hold
       - Gap up > 4% + ราคาไม่หลุด Gap ภายในวันแรก
       - Hold: 1-3 วัน
    """
    name = "Gap & Hold"
    if len(hist) < 2:
        return SetupMatch(name, False, "ข้อมูลไม่พอ")

    prev_close = safe_float(hist["Close"].iloc[-2])
    today_open = safe_float(hist["Open"].iloc[-1])
    today_close = safe_float(hist["Close"].iloc[-1])
    today_low = safe_float(hist["Low"].iloc[-1])

    gap_pct = pct_change(prev_close, today_open)

    if gap_pct <= GAP_UP_PCT_MIN:
        return SetupMatch(name, False, f"Gap {gap_pct:.1f}% ≤ {GAP_UP_PCT_MIN}%")

    # เช็คว่าราคาไม่หลุดกลับไปต่ำกว่าจุด gap (prev_close) ภายในวันแรก
    held = today_low >= prev_close

    if held:
        return SetupMatch(
            name, True,
            f"Gap up {gap_pct:.1f}% และ hold ได้ (low ${today_low:.2f} ≥ prev close ${prev_close:.2f}, close ${today_close:.2f})",
            "1-3 วัน",
        )
    return SetupMatch(name, False, f"Gap up {gap_pct:.1f}% แต่หลุด gap กลับมา (low ${today_low:.2f} < prev close ${prev_close:.2f})")


def check_volume_breakout(hist: pd.DataFrame) -> SetupMatch:
    """
    2. Volume Breakout
       - Break 20-day High + Volume > 2x average
       - Hold: 2-5 วัน
    """
    name = "Volume Breakout"
    if len(hist) < VOLUME_BREAKOUT_LOOKBACK + 1:
        return SetupMatch(name, False, "ข้อมูลไม่พอ")

    lookback_high = safe_float(hist["High"].iloc[-(VOLUME_BREAKOUT_LOOKBACK + 1):-1].max())
    today_close = safe_float(hist["Close"].iloc[-1])
    today_volume = safe_float(hist["Volume"].iloc[-1])
    avg_volume_20 = safe_float(hist["Volume"].iloc[-(VOLUME_BREAKOUT_LOOKBACK + 1):-1].mean())

    broke_high = today_close > lookback_high
    volume_surge = avg_volume_20 > 0 and today_volume >= VOLUME_BREAKOUT_MULTIPLIER * avg_volume_20

    if broke_high and volume_surge:
        ratio = today_volume / avg_volume_20 if avg_volume_20 else 0
        return SetupMatch(
            name, True,
            f"Break {VOLUME_BREAKOUT_LOOKBACK}-day high (${lookback_high:.2f}) ด้วย volume {ratio:.1f}x average",
            "2-5 วัน",
        )

    reasons = []
    if not broke_high:
        reasons.append(f"close ${today_close:.2f} ไม่ break high ${lookback_high:.2f}")
    if not volume_surge:
        ratio = today_volume / avg_volume_20 if avg_volume_20 else 0
        reasons.append(f"volume {ratio:.1f}x < {VOLUME_BREAKOUT_MULTIPLIER}x")
    return SetupMatch(name, False, "; ".join(reasons))


def check_relative_strength(hist: pd.DataFrame, benchmark_hist: Optional[pd.DataFrame]) -> SetupMatch:
    """
    3. Relative Strength Leader
       - แรงกว่า sector/market ติดต่อกัน 3-5 วัน
       - Hold: 3-5 วัน
    (ใช้ Nasdaq Composite เป็น market benchmark)
    """
    name = "Relative Strength Leader"
    if benchmark_hist is None or benchmark_hist.empty:
        return SetupMatch(name, False, "ไม่มีข้อมูล benchmark")
    if len(hist) < RS_LOOKBACK_DAYS + 1 or len(benchmark_hist) < RS_LOOKBACK_DAYS + 1:
        return SetupMatch(name, False, "ข้อมูลไม่พอ")

    stock_closes = hist["Close"].tail(RS_LOOKBACK_DAYS + 1).values
    bench_closes = benchmark_hist["Close"].tail(RS_LOOKBACK_DAYS + 1).values

    streak = 0
    max_streak = 0
    for i in range(1, len(stock_closes)):
        stock_ret = pct_change(stock_closes[i - 1], stock_closes[i])
        bench_ret = pct_change(bench_closes[i - 1], bench_closes[i])
        if stock_ret > bench_ret:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    if max_streak >= RS_MIN_STREAK_DAYS:
        total_stock_ret = pct_change(stock_closes[0], stock_closes[-1])
        total_bench_ret = pct_change(bench_closes[0], bench_closes[-1])
        return SetupMatch(
            name, True,
            f"แรงกว่า Nasdaq ติดต่อกัน {max_streak} วัน (หุ้น {total_stock_ret:+.1f}% vs Nasdaq {total_bench_ret:+.1f}% ใน {RS_LOOKBACK_DAYS} วัน)",
            "3-5 วัน",
        )
    return SetupMatch(name, False, f"แรงกว่าตลาดต่อเนื่องสูงสุด {max_streak} วัน < {RS_MIN_STREAK_DAYS} วัน")


def check_pullback_to_support(hist: pd.DataFrame) -> SetupMatch:
    """
    4. Pullback to Support (in uptrend)
       - Uptrend แรง + pullback มาแตะ EMA10/20
       - Hold: 2-4 วัน
    """
    name = "Pullback to Support (in uptrend)"
    if len(hist) < PULLBACK_EMA_LONG + 5:
        return SetupMatch(name, False, "ข้อมูลไม่พอ")

    closes = hist["Close"]
    ema_short = closes.ewm(span=PULLBACK_EMA_SHORT, adjust=False).mean()
    ema_long = closes.ewm(span=PULLBACK_EMA_LONG, adjust=False).mean()

    last_close = safe_float(closes.iloc[-1])
    last_ema_short = safe_float(ema_short.iloc[-1])
    last_ema_long = safe_float(ema_long.iloc[-1])

    # Uptrend: EMA10 > EMA20 และราคาปัจจุบันสูงกว่าราคา 20 วันก่อน
    price_20d_ago = safe_float(closes.iloc[-21]) if len(closes) >= 21 else safe_float(closes.iloc[0])
    is_uptrend = last_ema_short > last_ema_long and last_close > price_20d_ago

    if not is_uptrend:
        return SetupMatch(name, False, "ไม่อยู่ใน uptrend (EMA10 ไม่อยู่เหนือ EMA20 หรือราคาไม่สูงกว่า 20 วันก่อน)")

    dist_to_ema10 = abs(pct_change(last_ema_short, last_close))
    dist_to_ema20 = abs(pct_change(last_ema_long, last_close))
    near_ema10 = dist_to_ema10 <= PULLBACK_TOLERANCE_PCT
    near_ema20 = dist_to_ema20 <= PULLBACK_TOLERANCE_PCT

    if near_ema10 or near_ema20:
        which = "EMA10" if near_ema10 else "EMA20"
        dist = dist_to_ema10 if near_ema10 else dist_to_ema20
        return SetupMatch(
            name, True,
            f"Uptrend + pullback แตะ {which} (ห่าง {dist:.1f}%, close ${last_close:.2f}, EMA10 ${last_ema_short:.2f}, EMA20 ${last_ema_long:.2f})",
            "2-4 วัน",
        )
    return SetupMatch(
        name, False,
        f"Uptrend อยู่ แต่ยังไม่ pullback มาแตะ EMA (ห่าง EMA10 {dist_to_ema10:.1f}%, EMA20 {dist_to_ema20:.1f}%)",
    )


def check_sector_hype_rotation(hist: pd.DataFrame, sector_etf_hist: Optional[pd.DataFrame], sector_name: str) -> SetupMatch:
    """
    5. Sector Hype Rotation
       - เงินหมุนเข้าทั้งกลุ่ม (เช็คจาก sector ETF)
       - Hold: 3-5 วัน
    """
    name = "Sector Hype Rotation"
    if sector_etf_hist is None or sector_etf_hist.empty:
        return SetupMatch(name, False, f"ไม่มีข้อมูล sector ETF สำหรับ '{sector_name}'")
    if len(sector_etf_hist) < SECTOR_ROTATION_LOOKBACK + 1 or len(hist) < SECTOR_ROTATION_LOOKBACK + 1:
        return SetupMatch(name, False, "ข้อมูลไม่พอ")

    sector_ret = pct_change(
        safe_float(sector_etf_hist["Close"].iloc[-(SECTOR_ROTATION_LOOKBACK + 1)]),
        safe_float(sector_etf_hist["Close"].iloc[-1]),
    )
    stock_ret = pct_change(
        safe_float(hist["Close"].iloc[-(SECTOR_ROTATION_LOOKBACK + 1)]),
        safe_float(hist["Close"].iloc[-1]),
    )

    # เกณฑ์: sector ETF ต้องเป็นบวกแรง (>2%) และหุ้นวิ่งตามหรือแรงกว่า sector
    sector_hot = sector_ret > 2.0
    stock_participates = stock_ret >= sector_ret * 0.8  # หุ้นวิ่งตามกลุ่มอย่างน้อย 80% ของ sector move

    if sector_hot and stock_participates:
        return SetupMatch(
            name, True,
            f"Sector '{sector_name}' ร้อน (+{sector_ret:.1f}% ใน {SECTOR_ROTATION_LOOKBACK} วัน) และหุ้นวิ่งตาม ({stock_ret:+.1f}%)",
            "3-5 วัน",
        )

    reasons = []
    if not sector_hot:
        reasons.append(f"sector '{sector_name}' ยังไม่ร้อน ({sector_ret:+.1f}% < 2%)")
    if not stock_participates:
        reasons.append(f"หุ้นไม่วิ่งตาม sector (หุ้น {stock_ret:+.1f}% vs sector {sector_ret:+.1f}%)")
    return SetupMatch(name, False, "; ".join(reasons))


def check_earnings_surprise_momentum(earnings_history: Optional[pd.DataFrame], hist: pd.DataFrame) -> SetupMatch:
    """
    6. Earnings Surprise Momentum (เงื่อนไขใหม่ — เชิงพื้นฐาน)
       - บริษัทเพิ่ง report earnings ที่ beat estimate ชัดเจน (ภายใน ~5 วันซื้อขาย)
       - ราคายังตอบสนองเป็นบวกอยู่ (ไม่ได้ sell-the-news กลับลง)
       - Hold: 2-5 วัน (มักมี momentum ต่อจาก earnings surprise)

       ⚠️ หมายเหตุ: earnings surprise เป็นข้อมูล public ที่รู้พร้อมกันทั้งตลาดทันทีที่ประกาศ
       เงื่อนไขนี้จับช่วงที่ตลาดยัง "digest" ข่าวไม่เต็มที่ ไม่ใช่การรู้ข้อมูลก่อนใคร
    """
    name = "Earnings Surprise Momentum"
    if earnings_history is None or earnings_history.empty:
        return SetupMatch(name, False, "ไม่มีข้อมูล earnings history")

    try:
        latest = earnings_history.iloc[-1]
        earnings_date = earnings_history.index[-1]
    except (IndexError, KeyError):
        return SetupMatch(name, False, "อ่านข้อมูล earnings history ไม่ได้")

    # หา surprise % — yfinance ใช้ชื่อ column 'surprisePercent' (หรือบางเวอร์ชัน 'Surprise(%)')
    surprise_pct = None
    for col in ("surprisePercent", "Surprise(%)", "epsSurprisePercent"):
        if col in earnings_history.columns:
            surprise_pct = safe_float(latest.get(col))
            break

    if surprise_pct is None:
        return SetupMatch(name, False, "ไม่พบ column surprise % ใน earnings history")

    if surprise_pct < EARNINGS_SURPRISE_MIN_PCT:
        return SetupMatch(name, False, f"Earnings surprise {surprise_pct:+.1f}% < {EARNINGS_SURPRISE_MIN_PCT}%")

    # เช็คว่า earnings date อยู่ในช่วง lookback ที่กำหนดไหม (เทียบกับวันที่ล่าสุดใน price history)
    try:
        last_price_date = hist.index[-1]
        earnings_ts = pd.Timestamp(earnings_date)
        if earnings_ts.tzinfo is not None and last_price_date.tzinfo is None:
            earnings_ts = earnings_ts.tz_localize(None)
        elif earnings_ts.tzinfo is None and last_price_date.tzinfo is not None:
            last_price_date = last_price_date.tz_localize(None)
        days_since = (last_price_date - earnings_ts).days
    except Exception:
        return SetupMatch(name, False, "เทียบวันที่ earnings ไม่ได้")

    if days_since < 0:
        return SetupMatch(name, False, "earnings date อยู่ในอนาคต (ยังไม่ report จริง)")
    if days_since > EARNINGS_SURPRISE_LOOKBACK_DAYS:
        return SetupMatch(name, False, f"Earnings surprise เกิดมา {days_since} วันแล้ว > {EARNINGS_SURPRISE_LOOKBACK_DAYS} วัน (เก่าเกินไป)")

    # เช็คว่าราคายังตอบสนองบวกอยู่ไหม (ไม่ sell-the-news)
    price_reaction_positive = True
    try:
        recent_closes = hist["Close"].tail(min(days_since + 1, len(hist)))
        if len(recent_closes) >= 2:
            reaction_pct = pct_change(safe_float(recent_closes.iloc[0]), safe_float(recent_closes.iloc[-1]))
            price_reaction_positive = reaction_pct > -2.0  # ยอมให้ขยับลงเล็กน้อยได้ ไม่เกิน -2%
    except Exception:
        pass

    if not price_reaction_positive:
        return SetupMatch(
            name, False,
            f"Earnings beat +{surprise_pct:.1f}% เมื่อ {days_since} วันก่อน แต่ราคา sell-the-news กลับลง",
        )

    return SetupMatch(
        name, True,
        f"Earnings beat estimate +{surprise_pct:.1f}% เมื่อ {days_since} วันก่อน และราคายัง react บวกอยู่",
        "2-5 วัน",
    )


# =========================================================================
# 📅 Earnings calendar warning
# =========================================================================

def check_upcoming_earnings(calendar: Optional[dict]) -> Optional[dict]:
    """
    เช็คว่าหุ้นนี้กำลังจะประกาศผลประกอบการภายใน EARNINGS_WARNING_WINDOW_DAYS วันไหม
    คืนค่า: dict {days_until, earnings_date, severity, message} หรือ None ถ้าไม่มีคำเตือน
    (ไม่มีคำเตือน = ไม่มี earnings ใกล้ๆ หรือไม่มีข้อมูลให้เช็ค — ทั้งสองกรณีคืน None เหมือนกัน)

    หมายเหตุ: yfinance Ticker.calendar เปลี่ยน schema บ่อยในอดีต (เคยมีปัญหา KeyError
    กับ field อื่นที่คล้ายกัน) ฟังก์ชันนี้จึงต้อง "ทนทาน" ต่อ schema ที่ไม่คาดคิด —
    ถ้าอ่านไม่ได้ด้วยเหตุผลใดก็ตาม ให้คืน None เงียบๆ ไม่ throw exception ออกไป
    เพราะนี่เป็นแค่ข้อมูลเสริม ไม่ควรทำให้ ticker นั้น scan ไม่ผ่านเพราะเหตุนี้
    """
    if not calendar or not isinstance(calendar, dict):
        return None

    raw_date = calendar.get("Earnings Date")
    if raw_date is None:
        return None

    # "Earnings Date" อาจเป็น list ของวันที่ (ช่วงคาดการณ์), หรือวันที่เดียว, หรือ
    # datetime/date object ตรงๆ — ทุกแบบเคยเจอมาจาก yfinance ขึ้นกับเวอร์ชัน/ticker
    candidate_dates = []
    if isinstance(raw_date, (list, tuple)):
        candidate_dates = list(raw_date)
    else:
        candidate_dates = [raw_date]

    if not candidate_dates:
        return None

    # เอาวันที่ "เร็วที่สุด" ในกลุ่มที่ยังไม่ผ่านไป (ถ้ามีช่วงคาดการณ์ เช่น 24-28 ก.ค.
    # เอาวันแรกของช่วงมาคำนวณ severity เพราะเป็น worst-case ที่ใกล้ที่สุด)
    today = datetime.now(timezone.utc).date()
    parsed_dates = []
    for d in candidate_dates:
        try:
            if hasattr(d, "date"):  # datetime object
                parsed_dates.append(d.date())
            elif hasattr(d, "year"):  # date object อยู่แล้ว
                parsed_dates.append(d)
            else:
                parsed_dates.append(pd.Timestamp(d).date())
        except Exception:
            continue  # parse ไม่ได้ ข้ามตัวนี้ไป ไม่ทำให้ทั้งฟังก์ชัน fail

    if not parsed_dates:
        return None

    future_dates = [d for d in parsed_dates if d >= today]
    if not future_dates:
        return None  # earnings date ที่มีทั้งหมดผ่านไปแล้ว (ข้อมูลเก่า ไม่ update)

    earnings_date = min(future_dates)
    days_until = (earnings_date - today).days

    if days_until > EARNINGS_WARNING_WINDOW_DAYS:
        return None  # ไกลเกินไป ไม่ต้องเตือน (เกินหน้าต่าง 7 วัน)

    if days_until <= EARNINGS_WARNING_RED_MAX_DAYS:
        severity = "red"
        message = f"🔴 Earnings ใน {days_until} วัน — เสี่ยงสูงถ้าวางแผนถือเกินวันประกาศ"
    elif days_until <= EARNINGS_WARNING_YELLOW_MAX_DAYS:
        severity = "yellow"
        message = f"🟡 Earnings ใน {days_until} วัน — เช็คแผน exit ให้ชัดก่อนถึงวันนั้น"
    else:
        severity = "white"
        message = f"⚪ Earnings ใน {days_until} วัน — ข้อมูลอ้างอิง ไม่กระทบ position สั้นมากนัก"

    return {
        "earnings_date": earnings_date.isoformat(),
        "days_until": days_until,
        "severity": severity,
        "message": message,
    }


# =========================================================================
# 👔 Insider buying check
# =========================================================================

def _is_open_market_purchase_text(transaction_text: str) -> bool:
    """
    เช็คว่าข้อความ transaction (จาก yfinance) บอกว่าเป็น 'ซื้อในตลาดเปิด' จริงไหม
    ใช้เกณฑ์เข้มงวด: ต้องมีคำที่บอกว่าซื้อ "และ" ต้องไม่มีคำที่บอกว่าเป็น sale/gift/
    award/grant/option exercise ปนอยู่ในข้อความเดียวกัน — ลด false positive เพราะ
    yfinance ส่งมาเป็นข้อความบรรยายอิสระ ไม่ใช่ raw SEC transaction code ตัวอักษรเดียว
    """
    if not transaction_text or not isinstance(transaction_text, str):
        return False
    text_lower = transaction_text.lower()
    has_purchase_word = any(kw in text_lower for kw in INSIDER_BUYING_PURCHASE_KEYWORDS)
    has_exclude_word = any(kw in text_lower for kw in INSIDER_BUYING_EXCLUDE_KEYWORDS)
    return has_purchase_word and not has_exclude_word


def check_insider_buying(insider_transactions: Optional[pd.DataFrame]) -> Optional[dict]:
    """
    เช็คว่ามีผู้บริหาร/director ซื้อหุ้นตัวเองในตลาดเปิดจริงไหม ภายใน
    INSIDER_BUYING_LOOKBACK_DAYS วันที่ผ่านมา
    คืนค่า: dict {count, total_value, latest_date, insiders} หรือ None ถ้าไม่มีสัญญาณ

    หมายเหตุ: insider_transactions จาก yfinance เคยพบว่าคืน DataFrame ว่างบ่อย
    (แม้กับหุ้นใหญ่อย่าง AAPL) และชื่อ column อาจต่างกันไปตามเวอร์ชัน/ticker —
    ฟังก์ชันนี้จึงต้องทนทานสุดๆ ไม่ throw exception ออกไปไม่ว่า schema จะเป็นแบบไหน
    """
    if insider_transactions is None or not isinstance(insider_transactions, pd.DataFrame):
        return None
    if insider_transactions.empty:
        return None

    # หา column ที่บอก "ประเภท transaction" — ลองหลายชื่อที่เป็นไปได้
    transaction_col = None
    for col in ("Transaction", "Text", "Transaction Type"):
        if col in insider_transactions.columns:
            transaction_col = col
            break
    if transaction_col is None:
        return None  # อ่าน schema นี้ไม่ได้ — ไม่มี column ที่บอกประเภท transaction เลย

    # หา column วันที่ — ลองหลายชื่อ
    date_col = None
    for col in ("Start Date", "Date"):
        if col in insider_transactions.columns:
            date_col = col
            break

    # หา column จำนวนเงิน/มูลค่า (ไม่บังคับมี — ถ้าไม่มีก็ยังนับจำนวน transaction ได้)
    value_col = None
    for col in ("Value", "Shares"):
        if col in insider_transactions.columns:
            value_col = col
            break

    cutoff_date = None
    if date_col is not None:
        cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=INSIDER_BUYING_LOOKBACK_DAYS)

    matching_rows = []
    for _, row in insider_transactions.iterrows():
        text = row.get(transaction_col)
        if not _is_open_market_purchase_text(str(text) if text is not None else ""):
            continue

        # กรองตามวันที่ ถ้ามี column วันที่ให้เช็ค (ถ้าไม่มี ก็ยังนับรวมไว้ — ดีกว่าตัดทิ้งเงียบๆ)
        if cutoff_date is not None and date_col is not None:
            try:
                row_date = pd.Timestamp(row.get(date_col)).date()
                if row_date < cutoff_date:
                    continue
            except Exception:
                pass  # parse วันที่ไม่ได้ ไม่ตัดทิ้ง เผื่อข้อมูลมีค่าแต่ format แปลก

        matching_rows.append(row)

    if not matching_rows:
        return None

    total_value = 0.0
    if value_col is not None:
        for row in matching_rows:
            total_value += safe_float(row.get(value_col))

    insider_names = []
    if "Insider" in insider_transactions.columns:
        insider_names = list({str(row.get("Insider")) for row in matching_rows if row.get("Insider")})

    return {
        "count": len(matching_rows),
        "total_value": total_value if value_col is not None else None,
        "insiders": insider_names[:5],  # จำกัดไว้ไม่ให้ยาวเกินไปถ้ามีหลายคน
        "lookback_days": INSIDER_BUYING_LOOKBACK_DAYS,
    }


# =========================================================================
# 🏦 Institutional ownership check (มี cache 30 วัน)
# =========================================================================

def _institutional_cache_path(output_path: str) -> Path:
    return Path(output_path).resolve().parent / INSTITUTIONAL_CACHE_FILE_NAME


def load_institutional_cache(output_path: str) -> dict:
    """โหลด cache ทั้งไฟล์ (ทุก ticker) — คืน dict ว่างถ้าไม่มีไฟล์หรืออ่านไม่ได้"""
    path = _institutional_cache_path(output_path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ อ่าน institutional ownership cache ไม่สำเร็จ ({e}) — เริ่มใหม่จาก cache ว่าง")
        return {}


def save_institutional_cache(output_path: str, cache: dict):
    """เซฟ cache ทั้งไฟล์ — ไม่ critical พอจะทำให้สแกนทั้งรอบ fail ถ้าเซฟไม่สำเร็จ"""
    path = _institutional_cache_path(output_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"⚠️ เซฟ institutional ownership cache ไม่สำเร็จ: {e}")


def is_institutional_cache_entry_fresh(entry: Optional[dict]) -> bool:
    """เช็คว่า cache entry ของ ticker นี้ยังใหม่พอไม่ต้องดึงซ้ำไหม"""
    if not entry or "cached_at" not in entry:
        return False
    try:
        cached_at = datetime.fromisoformat(entry["cached_at"])
        age_days = (datetime.now(timezone.utc) - cached_at).days
        return age_days < INSTITUTIONAL_CACHE_MAX_AGE_DAYS
    except Exception:
        return False  # parse วันที่ cache ไม่ได้ — ปลอดภัยสุดคือถือว่า "ไม่ fresh" แล้วดึงใหม่


def summarize_institutional_holders(holders: Optional[pd.DataFrame]) -> Optional[dict]:
    """
    สรุปข้อมูล institutional_holders ดิบจาก yfinance ให้เป็น dict สั้นๆเก็บใน cache/result
    คืนค่า None ถ้าอ่านไม่ได้ — ไม่ throw exception (เหตุผลเดียวกับ insider buying)
    """
    if holders is None or not isinstance(holders, pd.DataFrame) or holders.empty:
        return None

    pct_col = None
    for col in ("pctHeld", "% Out", "pctOut"):
        if col in holders.columns:
            pct_col = col
            break

    total_pct = None
    if pct_col is not None:
        try:
            total_pct = float(holders[pct_col].apply(safe_float).sum()) * (100 if holders[pct_col].max() <= 1 else 1)
        except Exception:
            total_pct = None

    holder_names = []
    if "Holder" in holders.columns:
        try:
            holder_names = holders["Holder"].head(5).tolist()
        except Exception:
            holder_names = []

    return {
        "num_institutions_reported": len(holders),
        "top_holders_pct_combined": round(total_pct, 1) if total_pct is not None else None,
        "top_holder_names": holder_names,
    }


# =========================================================================
# 🔁 Retry helper — กัน Yahoo Finance rate limit
# =========================================================================

def is_rate_limit_error(exc: Exception) -> bool:
    """เช็คว่า exception นี้น่าจะเป็น rate limit จาก Yahoo Finance ไหม"""
    msg = str(exc).lower()
    return any(kw in msg for kw in RATE_LIMIT_KEYWORDS)


def fetch_with_retry(fetch_fn, ticker: str, what: str = "ข้อมูล"):
    """
    เรียก fetch_fn() พร้อม retry แบบ exponential backoff ถ้าเจอ error ที่ดูเหมือน rate limit
    ถ้า error ไม่เกี่ยวกับ rate limit (เช่น ticker ไม่มีจริง) จะไม่ retry เพราะลองใหม่ก็ไม่ช่วย
    """
    last_exc = None
    delay = RETRY_BASE_DELAY_SEC

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fetch_fn()
        except Exception as e:
            last_exc = e
            if not is_rate_limit_error(e):
                raise  # error อื่นที่ไม่ใช่ rate limit ไม่ต้อง retry ส่งต่อทันที
            if attempt < MAX_RETRIES:
                log(f"   ⏳ {ticker}: โดน rate limit ตอนดึง{what} — รอ {delay:.0f}s แล้วลองใหม่ (ครั้งที่ {attempt}/{MAX_RETRIES})")
                time.sleep(delay)
                delay *= RETRY_BACKOFF_MULTIPLIER
            else:
                log(f"   ❌ {ticker}: โดน rate limit ตอนดึง{what} — ลองครบ {MAX_RETRIES} ครั้งแล้วยังไม่ผ่าน")

    raise last_exc


# =========================================================================
# 🔬 Scan logic ต่อ 1 ticker
# =========================================================================

def scan_one_ticker(
    ticker: str,
    benchmark_hist: Optional[pd.DataFrame],
    sector_hist_cache: dict,
    sector_pe_cache: dict,
    institutional_cache: Optional[dict] = None,
) -> ScanResult:
    result = ScanResult(ticker=ticker)

    # หน่วงเวลาเล็กน้อยก่อนยิง request (ลดโอกาสโดน rate limit ตั้งแต่ต้น)
    if REQUEST_STAGGER_DELAY_SEC > 0:
        time.sleep(REQUEST_STAGGER_DELAY_SEC)

    try:
        tk = yf.Ticker(ticker)

        try:
            hist = fetch_with_retry(
                lambda: tk.history(period=HISTORY_PERIOD, interval=HISTORY_INTERVAL, auto_adjust=True),
                ticker, "ราคาย้อนหลัง",
            )
        except Exception as e:
            result.error = f"ดึงราคาไม่สำเร็จ: {type(e).__name__}: {e}"
            return result

        if hist is None or hist.empty:
            result.error = "ไม่มีข้อมูลราคา (อาจ delist/halt หรือ ticker ผิด)"
            return result

        # --- Data freshness check (เพิ่มใหม่) — flag เตือนก่อนเอา hist ไปคำนวณอะไรต่อ ---
        result.data_quality_flags = check_data_freshness(hist)
        if result.data_quality_flags:
            log(f"   🩺 {ticker}: data quality flag — {'; '.join(result.data_quality_flags)}")

        try:
            info = fetch_with_retry(lambda: tk.info or {}, ticker, "ข้อมูลบริษัท")
        except Exception:
            info = {}

        result.company_name = info.get("shortName", "") or info.get("longName", "")
        result.sector = info.get("sector", "") or "Unknown"

        passed, fail_reason, metrics = apply_base_filter(hist, info)
        result.passed_base_filter = passed
        result.base_filter_fail_reason = fail_reason
        result.price = metrics.get("price", 0.0)
        result.avg_volume = metrics.get("avg_volume", 0)
        result.market_cap = metrics.get("market_cap", 0.0)
        result.avg_dollar_volume = metrics.get("avg_dollar_volume", 0.0)

        if not passed:
            # ไม่ผ่าน base filter -> ไม่ต้องเช็คต่อ (ตาม instruction: filter บังคับทุกเงื่อนไข)
            return result

        # --- Fundamental filter (เพิ่มใหม่) — กรองงบแย่ก่อนเข้า setup ---
        fund_passed, fund_fail_reason = apply_fundamental_filter(info)
        result.passed_fundamental_filter = fund_passed
        result.fundamental_filter_fail_reason = fund_fail_reason

        # --- Fundamental score (เพิ่มใหม่) — คำนวณเสมอเพื่อโชว์คู่กับ setup แม้ filter ไม่ผ่าน ---
        score, breakdown, raw_numbers = calculate_fundamental_score(info, sector_pe_cache)
        result.fundamental_score = score
        result.fundamental_breakdown = breakdown
        result.fundamentals_raw = raw_numbers

        if not fund_passed:
            # ไม่ผ่าน fundamental filter -> ไม่เช็ค setup ต่อ (เหมือน base filter)
            return result

        # เช็คทั้ง 6 เงื่อนไข
        setups = []
        setups.append(check_gap_and_hold(hist))
        setups.append(check_volume_breakout(hist))
        setups.append(check_relative_strength(hist, benchmark_hist))
        setups.append(check_pullback_to_support(hist))

        sector_etf_symbol = SECTOR_ETFS.get(result.sector)
        sector_etf_hist = sector_hist_cache.get(sector_etf_symbol) if sector_etf_symbol else None
        setups.append(check_sector_hype_rotation(hist, sector_etf_hist, result.sector))

        try:
            earnings_hist = fetch_with_retry(lambda: tk.earnings_history, ticker, "earnings history")
        except Exception:
            earnings_hist = None
        setups.append(check_earnings_surprise_momentum(earnings_hist, hist))

        result.setups_matched = setups

        # --- Second-pass confirmation layer (เพิ่มใหม่) — เช็คเฉพาะตัวที่ match
        # setup อย่างน้อย 1 ข้อแล้วเท่านั้น กัน API call เพิ่มให้ทั้ง 1000 ticker ---
        if any(s.matched for s in setups):
            # 1) Earnings calendar warning
            try:
                calendar = fetch_with_retry(lambda: tk.calendar, ticker, "earnings calendar")
            except Exception:
                calendar = None
            try:
                result.upcoming_earnings_warning = check_upcoming_earnings(calendar)
            except Exception as e:
                # เผื่อ schema ของ calendar แปลกจนเกิน try/except ภายใน check_upcoming_earnings เอง
                # (เป็น defense ชั้นที่ 2 — ไม่ควรเกิดขึ้นจริง แต่กันไว้ไม่ให้กระทบ ticker นี้ทั้งตัว)
                log(f"   ⚠️ {ticker}: เช็ค earnings calendar ไม่สำเร็จ — {type(e).__name__}: {e}")
                result.upcoming_earnings_warning = None

            # 2) Insider buying — confirmation ว่าคนข้างในซื้อหุ้นตัวเองในตลาดเปิดไหม
            try:
                insider_tx = fetch_with_retry(lambda: tk.insider_transactions, ticker, "insider transactions")
            except Exception:
                insider_tx = None
            try:
                result.insider_buying_signal = check_insider_buying(insider_tx)
                if result.insider_buying_signal:
                    log(f"   👔 {ticker}: insider buying — {result.insider_buying_signal['count']} purchase(s) ภายใน {INSIDER_BUYING_LOOKBACK_DAYS} วัน")
            except Exception as e:
                log(f"   ⚠️ {ticker}: เช็ค insider buying ไม่สำเร็จ — {type(e).__name__}: {e}")
                result.insider_buying_signal = None

            # 3) Institutional ownership — ใช้ cache 30 วัน ไม่ดึงใหม่ทุกวัน (13F รายงานช้า)
            cache_entry = (institutional_cache or {}).get(ticker)
            if is_institutional_cache_entry_fresh(cache_entry):
                result.institutional_ownership = cache_entry.get("data")
                result._institutional_cache_hit = True  # บอก run_scan ว่าไม่ต้องเขียน cache ใหม่
            else:
                try:
                    holders = fetch_with_retry(lambda: tk.institutional_holders, ticker, "institutional holders")
                except Exception:
                    holders = None
                try:
                    result.institutional_ownership = summarize_institutional_holders(holders)
                except Exception as e:
                    log(f"   ⚠️ {ticker}: สรุป institutional ownership ไม่สำเร็จ — {type(e).__name__}: {e}")
                    result.institutional_ownership = None
                result._institutional_cache_hit = False  # ดึงใหม่ -> run_scan ต้องเขียน cache กลับ

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        log(f"   ⚠️ Error ที่ {ticker}: {result.error}")
        log(traceback.format_exc(limit=2))

    return result


# =========================================================================
# 🚀 Main scan orchestration
# =========================================================================

def fetch_benchmark_and_sector_data() -> tuple[Optional[pd.DataFrame], dict, dict]:
    """ดึงข้อมูล Nasdaq Composite (benchmark), sector ETF, และ sector average PE ล่วงหน้าครั้งเดียว"""
    log(f"📊 ดึงข้อมูล market benchmark ({MARKET_BENCHMARK})...")
    benchmark_hist = None
    try:
        benchmark_hist = yf.Ticker(MARKET_BENCHMARK).history(period=HISTORY_PERIOD, interval=HISTORY_INTERVAL)
    except Exception as e:
        log(f"⚠️ ดึง benchmark ไม่สำเร็จ: {e}")

    log(f"📊 ดึงข้อมูล sector ETF ({len(SECTOR_ETFS)} ตัว)...")
    sector_hist_cache = {}
    sector_pe_cache = {}
    for sector_name, etf_symbol in SECTOR_ETFS.items():
        try:
            sector_hist_cache[etf_symbol] = yf.Ticker(etf_symbol).history(
                period=HISTORY_PERIOD, interval=HISTORY_INTERVAL
            )
        except Exception as e:
            log(f"   ⚠️ ดึง sector ETF {etf_symbol} ({sector_name}) ไม่สำเร็จ: {e}")
            sector_hist_cache[etf_symbol] = None

        # ดึง P/E เฉลี่ยของ sector ETF (ใช้ trailingPE ของ ETF เป็นตัวแทน sector valuation โดยประมาณ)
        try:
            etf_info = yf.Ticker(etf_symbol).info or {}
            etf_pe = etf_info.get("trailingPE")
            if etf_pe:
                sector_pe_cache[sector_name] = safe_float(etf_pe)
        except Exception:
            pass  # ไม่มี sector PE ก็ไม่เป็นไร — valuation score จะใช้เกณฑ์ทั่วไปแทน

    return benchmark_hist, sector_hist_cache, sector_pe_cache


# =========================================================================
# 💾 Checkpoint helpers — กัน scan ค้างกลางทางแล้วต้องเริ่มใหม่หมด
# =========================================================================

def checkpoint_path_for(output_path: str) -> str:
    return str(output_path) + CHECKPOINT_FILE_SUFFIX


def save_checkpoint(output_path: str, results: list[ScanResult], remaining_tickers: list[str]):
    """บันทึกผลที่สแกนได้แล้ว + รายชื่อ ticker ที่ยังไม่ได้สแกน"""
    ckpt_path = checkpoint_path_for(output_path)
    data = {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "completed_results": [r.to_dict() for r in results],
        "remaining_tickers": remaining_tickers,
    }
    try:
        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log(f"⚠️ บันทึก checkpoint ไม่สำเร็จ (ไม่กระทบการสแกนต่อ): {e}")


def load_checkpoint(output_path: str) -> Optional[dict]:
    """โหลด checkpoint ถ้ามี คืนค่า None ถ้าไม่มีหรืออ่านไม่ได้"""
    ckpt_path = checkpoint_path_for(output_path)
    if not Path(ckpt_path).exists():
        return None
    try:
        with open(ckpt_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ อ่าน checkpoint ไม่สำเร็จ ({e}) — จะสแกนใหม่ทั้งหมด")
        return None


def delete_checkpoint(output_path: str):
    ckpt_path = checkpoint_path_for(output_path)
    try:
        Path(ckpt_path).unlink(missing_ok=True)
    except Exception:
        pass


def dict_to_scan_result(d: dict) -> ScanResult:
    """แปลง dict (จาก checkpoint JSON) กลับเป็น ScanResult object"""
    setups = [SetupMatch(**s) for s in d.get("setups_matched", [])]
    r = ScanResult(
        ticker=d.get("ticker", ""),
        company_name=d.get("company_name", ""),
        sector=d.get("sector", ""),
        price=d.get("price", 0.0),
        avg_volume=d.get("avg_volume", 0),
        market_cap=d.get("market_cap", 0.0),
        avg_dollar_volume=d.get("avg_dollar_volume", 0.0),
        passed_base_filter=d.get("passed_base_filter", False),
        base_filter_fail_reason=d.get("base_filter_fail_reason", ""),
        passed_fundamental_filter=d.get("passed_fundamental_filter", True),
        fundamental_filter_fail_reason=d.get("fundamental_filter_fail_reason", ""),
        fundamental_score=d.get("fundamental_score"),
        fundamental_breakdown=d.get("fundamental_breakdown", {}),
        fundamentals_raw=d.get("fundamentals_raw", {}),
        error=d.get("error", ""),
    )
    r.setups_matched = setups
    return r


def run_scan(tickers: list[str], max_workers: int = 8, output_path: str = DEFAULT_OUTPUT_FILE,
             resume: bool = True) -> tuple[list[ScanResult], bool]:
    """
    คืนค่า (results, is_complete)
    is_complete=False หมายความว่ายังมี ticker ที่โดน rate limit ค้างอยู่ ไม่ควร export ผลลัพธ์
    เพราะจะทำให้ scanner_results.json ดูเหมือนสแกนครบทั้งที่จริงขาดไปบางตัว
    """
    results: list[ScanResult] = []
    tickers_to_scan = list(tickers)

    # ---------- เช็ค checkpoint ก่อน ถ้ามีและอยาก resume ----------
    if resume:
        ckpt = load_checkpoint(output_path)
        if ckpt:
            prev_results = [dict_to_scan_result(d) for d in ckpt.get("completed_results", [])]
            prev_remaining = ckpt.get("remaining_tickers", [])
            # resume ได้เฉพาะตอนรายชื่อ ticker เดิมตรงกับที่ขอสแกนรอบนี้ (กันสแกนผิดไฟล์)
            prev_all = {r.ticker for r in prev_results} | set(prev_remaining)
            current_all = set(tickers)
            if prev_all == current_all:
                results = prev_results
                tickers_to_scan = prev_remaining
                log(f"♻️  พบ checkpoint เดิม — สแกนไปแล้ว {len(results)} ตัว เหลืออีก {len(tickers_to_scan)} ตัว")
                log(f"   (ถ้าอยากสแกนใหม่ทั้งหมด ลบไฟล์ {checkpoint_path_for(output_path)} ก่อน)")
            else:
                log("ℹ️  พบ checkpoint แต่รายชื่อ ticker ไม่ตรงกับรอบนี้ — เริ่มสแกนใหม่ทั้งหมด")
                delete_checkpoint(output_path)

    if not tickers_to_scan:
        log("✅ ไม่มี ticker เหลือให้สแกน (สแกนครบจาก checkpoint แล้ว)")
        return results, True

    benchmark_hist, sector_hist_cache, sector_pe_cache = fetch_benchmark_and_sector_data()

    # โหลด institutional ownership cache ครั้งเดียวก่อนเริ่ม (read-only ระหว่าง thread ทำงาน
    # กันปัญหา race condition — เขียน cache กลับไฟล์ทีหลังจาก main thread เท่านั้น)
    institutional_cache = load_institutional_cache(output_path)
    log(f"🏦 โหลด institutional ownership cache: {len(institutional_cache)} ticker (cache ไว้สูงสุด {INSTITUTIONAL_CACHE_MAX_AGE_DAYS} วัน)")

    log(f"🔍 เริ่มสแกน {len(tickers_to_scan)} tickers ด้วย {max_workers} workers...")
    completed = 0
    total_target = len(results) + len(tickers_to_scan)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {
            executor.submit(scan_one_ticker, t, benchmark_hist, sector_hist_cache, sector_pe_cache, institutional_cache): t
            for t in tickers_to_scan
        }
        pending_tickers = set(tickers_to_scan)
        unrecoverable_tickers: set[str] = set()  # ticker ที่ error แบบไม่ใช่ rate limit (retry ไปก็ไม่ช่วย)

        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            completed += 1

            try:
                res = future.result()
                # scan_one_ticker จัดการ retry ภายในตัวเองแล้ว ถ้า error ออกมาถึงตรงนี้
                # แปลว่าลองครบ MAX_RETRIES แล้วไม่ผ่าน หรือเป็น error ที่ไม่ใช่ rate limit
                if res.error and is_rate_limit_error(Exception(res.error)):
                    # ยังเป็น rate limit อยู่แม้ retry ครบแล้ว — เก็บไว้ใน remaining ให้ resume รอบหน้า
                    log(f"   🔁 [{completed}/{total_target}] {ticker} — ยัง rate limit อยู่ จะลองใหม่ตอน resume รอบหน้า")
                else:
                    pending_tickers.discard(ticker)
                    results.append(res)
                    n_matched = sum(1 for s in res.setups_matched if s.matched)
                    status = "✅" if n_matched > 0 else ("⏭️" if res.passed_base_filter else "❌")
                    log(f"   [{len(results)}/{total_target}] {status} {ticker} — matched {n_matched} setup(s)")

                    # อัปเดต institutional ownership cache เฉพาะตอนที่ดึงใหม่จริง (ไม่ใช่ cache hit)
                    # ทำใน main thread เท่านั้น (นอก ThreadPoolExecutor) กัน race condition
                    if res.institutional_ownership is not None and not res._institutional_cache_hit:
                        institutional_cache[ticker] = {
                            "data": res.institutional_ownership,
                            "cached_at": datetime.now(timezone.utc).isoformat(),
                        }
            except Exception as e:
                if is_rate_limit_error(e):
                    log(f"   🔁 [{completed}/{total_target}] {ticker} — exception rate limit จะลองใหม่ตอน resume รอบหน้า")
                else:
                    pending_tickers.discard(ticker)
                    unrecoverable_tickers.add(ticker)
                    log(f"   [{len(results) + 1}/{total_target}] ⚠️ {ticker} — exception: {e}")
                    results.append(ScanResult(ticker=ticker, error=str(e)))

            # เซฟ checkpoint เป็นพักๆ
            if completed % CHECKPOINT_SAVE_EVERY == 0:
                save_checkpoint(output_path, results, sorted(pending_tickers))
                log(f"   💾 บันทึก checkpoint แล้ว ({len(results)}/{total_target})")

    # เซฟ checkpoint รอบสุดท้ายเสมอ (เผื่อมี ticker ที่ยังเป็น rate limit ค้างอยู่ตอนจบ loop)
    if pending_tickers:
        save_checkpoint(output_path, results, sorted(pending_tickers))
        log(f"💾 บันทึก checkpoint สุดท้าย — เหลือ {len(pending_tickers)} ตัวที่ยังโดน rate limit อยู่")
        log(f"   รันคำสั่งเดิมอีกครั้งเพื่อสแกนต่อ (ระบบจะ resume จากจุดนี้อัตโนมัติ)")
        # เซฟ institutional cache ที่อัปเดตไปแล้วด้วย แม้สแกนยังไม่ครบ (ของที่ดึงมาแล้วไม่ควรเสีย)
        save_institutional_cache(output_path, institutional_cache)
        return results, False

    save_institutional_cache(output_path, institutional_cache)
    log(f"🏦 บันทึก institutional ownership cache แล้ว ({len(institutional_cache)} ticker)")

    return results, True


# =========================================================================
# 🗂️ History snapshot — สำหรับ compare.html
# =========================================================================

def save_history_snapshot(output_data: dict, output_path: str):
    """
    เซฟสำเนาผลสแกนของวันนี้ไว้ใน <output_path's parent>/history/YYYY-MM-DD.json
    ใช้ข้อมูลชุดเดียวกันกับที่ export ไปเป็น output หลัก (output_data คือ dict
    ที่ export_results เตรียมไว้แล้ว ไม่ใช่ results list ดิบ — กันคำนวณซ้ำ/ไม่ตรงกัน)

    เซฟเฉพาะตอนสแกนสำเร็จครบเท่านั้น (เรียกจาก main() หลัง is_complete=True)
    ถ้าวันนี้สแกนหลายรอบ (เช่นรันซ้ำ) ไฟล์ของวันนั้นจะถูกเขียนทับด้วยผลล่าสุด
    """
    output_dir = Path(output_path).resolve().parent
    history_dir = output_dir / HISTORY_DIR_NAME

    # ใช้วันที่ UTC ให้สอดคล้องกับ scan_timestamp_utc ที่อยู่ใน output_data อยู่แล้ว
    # (กันความสับสนเรื่อง timezone ถ้าสแกนใกล้เที่ยงคืนพอดี)
    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot_path = history_dir / f"{scan_date}.json"

    try:
        history_dir.mkdir(parents=True, exist_ok=True)
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        log(f"🗂️  บันทึก history snapshot ที่ {snapshot_path}")
    except Exception as e:
        # ไม่ critical พอจะทำให้ scan ทั้งรอบ fail — แค่เตือนแล้วไปต่อ (รวมถึง mkdir fail ด้วย)
        log(f"⚠️ บันทึก history snapshot ไม่สำเร็จ: {e}")
        return

    _prune_old_snapshots(history_dir)


def _prune_old_snapshots(history_dir: Path):
    """ลบไฟล์ snapshot ที่เก่ากว่า HISTORY_RETENTION_DAYS วันออกอัตโนมัติ"""
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=HISTORY_RETENTION_DAYS)
    removed = 0
    for f in history_dir.glob("*.json"):
        try:
            file_date = datetime.strptime(f.stem, "%Y-%m-%d").date()
        except ValueError:
            continue  # ไฟล์ที่ชื่อไม่ตรงรูปแบบวันที่ ข้ามไป ไม่ลบ
        if file_date < cutoff:
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
    if removed:
        log(f"🧹 ลบ history snapshot เก่ากว่า {HISTORY_RETENTION_DAYS} วัน ออก {removed} ไฟล์")


def export_results(results: list[ScanResult], output_path: str) -> dict:
    """Export ผลลัพธ์เป็น JSON สำหรับ dashboard — คืนค่า output dict ด้วย
    (ให้ main() เอาไปใช้ save history snapshot ต่อได้โดยไม่ต้องคำนวณซ้ำ)"""
    # เรียงผลลัพธ์: ที่ match setup เยอะสุดอยู่บนสุด
    def sort_key(r: ScanResult):
        n_matched = sum(1 for s in r.setups_matched if s.matched)
        return (-n_matched, -r.avg_dollar_volume if r.avg_dollar_volume else 0)

    sorted_results = sorted(results, key=sort_key)

    output = {
        "scan_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scan_timestamp_local_note": "เวลาด้านบนเป็น UTC — แปลงเป็นเวลาไทยใน dashboard (+7 ชม.)",
        "total_scanned": len(results),
        "total_passed_base_filter": sum(1 for r in results if r.passed_base_filter),
        "total_passed_fundamental_filter": sum(
            1 for r in results if r.passed_base_filter and r.passed_fundamental_filter
        ),
        "total_matched_any_setup": sum(
            1 for r in results if any(s.matched for s in r.setups_matched)
        ),
        "total_with_data_quality_flags": sum(
            1 for r in results if r.data_quality_flags
        ),
        "total_with_upcoming_earnings": sum(
            1 for r in results if r.upcoming_earnings_warning
        ),
        "total_with_insider_buying": sum(
            1 for r in results if r.insider_buying_signal
        ),
        "total_with_institutional_data": sum(
            1 for r in results if r.institutional_ownership
        ),
        "base_filter_config": {
            "min_price": MIN_PRICE,
            "min_avg_volume": MIN_AVG_VOLUME,
            "min_market_cap": MIN_MARKET_CAP,
            "min_dollar_volume": MIN_DOLLAR_VOLUME,
        },
        "fundamental_filter_config": {
            "enabled": FUNDAMENTAL_FILTER_ENABLED,
            "max_debt_to_equity": MAX_DEBT_TO_EQUITY,
            "min_current_ratio": MIN_CURRENT_RATIO,
        },
        "fundamental_score_note": (
            "Fundamental score เป็นคะแนนเชิงคุณภาพหยาบๆ (0-100) จากข้อมูล public ทั่วไป "
            "ใช้เป็นข้อมูลประกอบการพิจารณาเท่านั้น ไม่ใช่สัญญาณซื้อขาย และไม่ได้แปลว่าหุ้นคะแนนสูง "
            "จะทำผลตอบแทนดีกว่าตลาด — โดยเฉพาะกับ swing trade ระยะสั้น 1-5 วัน ที่ price action "
            "มีน้ำหนักมากกว่า fundamental มาก"
        ),
        "setup_names": [
            "Gap & Hold",
            "Volume Breakout",
            "Relative Strength Leader",
            "Pullback to Support (in uptrend)",
            "Sector Hype Rotation",
            "Earnings Surprise Momentum",
        ],
        "disclaimer": (
            "ระบบนี้เป็นเครื่องมือช่วยกรองหุ้นเบื้องต้นเท่านั้น ไม่ใช่คำแนะนำการลงทุน "
            "ไม่การันตีผลกำไร ต้อง verify ข้อมูลและความสามารถในการซื้อขายกับ Dime!/Webull "
            "ก่อนตัดสินใจซื้อขายจริงเสมอ — Manual decision เท่านั้น ไม่มี Auto Order "
            "การเพิ่มเงื่อนไขเชิงพื้นฐานไม่ได้ทำให้ระบบนี้ \"ชนะตลาด\" — ข้อมูลพื้นฐานเป็นข้อมูล "
            "public ที่ทุกคนเข้าถึงได้เหมือนกัน ใช้เป็น risk filter เสริมเท่านั้น"
        ),
        "results": [r.to_dict() for r in sorted_results],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log(f"💾 บันทึกผลลัพธ์ที่ {output_path}")
    log(
        f"📈 สรุป: สแกน {output['total_scanned']} ตัว | "
        f"ผ่าน base filter {output['total_passed_base_filter']} ตัว | "
        f"match อย่างน้อย 1 setup {output['total_matched_any_setup']} ตัว"
    )

    return output


# =========================================================================
# 🏁 Entry point
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Nasdaq + Russell 2000 Swing Trading Scanner (Scan + Dashboard เท่านั้น ไม่ Auto Order)"
    )
    parser.add_argument(
        "--tickers-file", default=DEFAULT_TICKERS_FILE,
        help=f"ไฟล์ CSV รายชื่อ ticker (default: {DEFAULT_TICKERS_FILE})",
    )
    parser.add_argument(
        "--tickers", default=None,
        help="ระบุ ticker เองตรงๆ คั่นด้วย comma เช่น AAPL,NVDA (ถ้าระบุจะข้าม --tickers-file)",
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT_FILE,
        help=f"ไฟล์ output JSON (default: {DEFAULT_OUTPUT_FILE})",
    )
    parser.add_argument(
        "--workers", type=int, default=8,
        help="จำนวน thread พร้อมกันตอนดึงข้อมูล (default: 8, อย่าตั้งสูงเกินไปจะโดน rate limit)",
    )
    parser.add_argument(
        "--no-fundamental-filter", action="store_true",
        help="ปิด fundamental filter (ยังคำนวณ fundamental score แสดงผลอยู่ แค่ไม่ใช้ filter)",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="ไม่ resume จาก checkpoint เดิม แม้จะมีอยู่ (สแกนใหม่ทั้งหมด)",
    )
    args = parser.parse_args()

    if args.no_fundamental_filter:
        global FUNDAMENTAL_FILTER_ENABLED
        FUNDAMENTAL_FILTER_ENABLED = False
        log("⚠️ ปิด fundamental filter ตามที่ระบุ (--no-fundamental-filter)")

    start_time = time.time()

    if args.tickers:
        tickers = sorted(set(t.strip().upper() for t in args.tickers.split(",") if t.strip()))
        log(f"✅ ใช้รายชื่อ ticker ที่ระบุเอง {len(tickers)} ตัว")
    else:
        tickers = load_tickers(args.tickers_file)

    if not tickers:
        log("❌ ไม่มี ticker ให้สแกน — จบการทำงาน")
        sys.exit(1)

    if args.no_resume:
        delete_checkpoint(args.output)
        log("⚠️ ไม่ resume จาก checkpoint เดิม (--no-resume) — สแกนใหม่ทั้งหมด")

    try:
        results, is_complete = run_scan(tickers, max_workers=args.workers, output_path=args.output, resume=not args.no_resume)
    except KeyboardInterrupt:
        log("")
        log("⏸️  หยุดกลางทาง (Ctrl+C) — ผลที่สแกนได้แล้วถูกบันทึกใน checkpoint")
        log(f"   รันคำสั่งเดิมอีกครั้งเพื่อสแกนต่อจากจุดที่ค้าง: python3 {sys.argv[0]} ...")
        sys.exit(130)

    elapsed = time.time() - start_time

    if not is_complete:
        # ยังมี ticker ที่โดน rate limit ค้างอยู่ — "ห้าม" export ผลลัพธ์ตอนนี้
        # เพราะจะทำให้ scanner_results.json ดูเหมือนสแกนครบทั้งที่จริงขาดไปบางตัว
        # (เช่น ticker ที่ดี/match setup มากอาจอยู่ในกลุ่มที่ยังไม่ได้สแกน)
        log("")
        log("=" * 70)
        log(f"⏸️  การสแกนยังไม่ครบ — มี ticker ที่ยังโดน rate limit ค้างอยู่")
        log(f"   สแกนได้แล้ว {len(results)} ตัว จาก {len(tickers)} ตัว")
        log(f"   ⚠️ ยังไม่เขียนไฟล์ {args.output} ทับของเดิม (กันผลลัพธ์ไม่ครบหลอกว่าสแกนจบแล้ว)")
        log(f"   รันคำสั่งเดิมอีกครั้งเพื่อสแกนต่อให้ครบ ระบบจะ resume จากจุดนี้อัตโนมัติ")
        log("=" * 70)
        log(f"⏱️ ใช้เวลาไป {elapsed:.1f} วินาทีในรอบนี้")
        sys.exit(2)

    output_data = export_results(results, args.output)
    save_history_snapshot(output_data, args.output)
    delete_checkpoint(args.output)  # สแกนสำเร็จครบแล้ว ไม่ต้องเก็บ checkpoint ไว้อีก

    log(f"⏱️ เสร็จสิ้นใน {elapsed:.1f} วินาที")
    log("⚠️ อย่าลืม: ผลลัพธ์นี้ต้อง verify กับแอป Dime!/Webull ก่อนซื้อขายจริงเสมอ (Manual decision เท่านั้น)")


if __name__ == "__main__":
    main()
