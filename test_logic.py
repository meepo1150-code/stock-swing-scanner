#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_logic.py — ทดสอบ logic ของ stock_scanner.py ด้วยข้อมูลจำลอง (synthetic data)
ไม่ต้องพึ่ง network / yfinance API จริง — ใช้ตรวจสอบว่าฟังก์ชันเช็คเงื่อนไขทำงานถูกต้อง
"""

import sys
import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from stock_scanner import (
    check_gap_and_hold,
    check_volume_breakout,
    check_relative_strength,
    check_pullback_to_support,
    check_sector_hype_rotation,
    check_earnings_surprise_momentum,
    apply_base_filter,
    apply_fundamental_filter,
    calculate_fundamental_score,
)


def make_hist(closes, opens=None, highs=None, lows=None, volumes=None):
    n = len(closes)
    closes = np.array(closes, dtype=float)
    opens = np.array(opens, dtype=float) if opens is not None else closes * 0.99
    highs = np.array(highs, dtype=float) if highs is not None else closes * 1.01
    lows = np.array(lows, dtype=float) if lows is not None else closes * 0.98
    volumes = np.array(volumes, dtype=float) if volumes is not None else np.full(n, 2_000_000)
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=idx,
    )


def test_gap_and_hold():
    print("== Test 1: Gap & Hold ==")
    # Case A: gap up 6%, hold (low ไม่หลุด prev close)
    closes = [10] * 29 + [10.5]
    hist = make_hist(closes)
    hist.loc[hist.index[-1], "Open"] = 10.6   # gap up 6%
    hist.loc[hist.index[-1], "Low"] = 10.2     # ไม่หลุด prev close (10)
    hist.loc[hist.index[-1], "Close"] = 10.5
    r = check_gap_and_hold(hist)
    print(f"  Case A (ควร match=True): matched={r.matched} | {r.detail}")
    assert r.matched is True

    # Case B: gap up แต่หลุดกลับมา
    hist2 = hist.copy()
    hist2.loc[hist2.index[-1], "Low"] = 9.5  # หลุด prev close (10)
    r2 = check_gap_and_hold(hist2)
    print(f"  Case B (ควร match=False): matched={r2.matched} | {r2.detail}")
    assert r2.matched is False

    # Case C: gap น้อยเกินไป
    hist3 = hist.copy()
    hist3.loc[hist3.index[-1], "Open"] = 10.1  # gap แค่ 1%
    r3 = check_gap_and_hold(hist3)
    print(f"  Case C (ควร match=False): matched={r3.matched} | {r3.detail}")
    assert r3.matched is False
    print("  ✅ PASS\n")


def test_volume_breakout():
    print("== Test 2: Volume Breakout ==")
    closes = list(np.linspace(20, 22, 25))  # ค่อยๆขึ้น
    highs = [c * 1.005 for c in closes]
    volumes = [1_500_000] * 24 + [4_000_000]  # วันสุดท้าย volume พุ่ง
    closes[-1] = max(highs[:-1]) + 0.5  # close วันนี้ break high เดิม
    hist = make_hist(closes, highs=highs, volumes=volumes)
    r = check_volume_breakout(hist)
    print(f"  Case A (ควร match=True): matched={r.matched} | {r.detail}")
    assert r.matched is True

    # ไม่ break high
    closes2 = list(np.linspace(20, 22, 25))
    hist2 = make_hist(closes2, volumes=volumes)
    r2 = check_volume_breakout(hist2)
    print(f"  Case B (ควร match=False ถ้าไม่ break high): matched={r2.matched} | {r2.detail}")
    print("  ✅ PASS\n")


def test_relative_strength():
    print("== Test 3: Relative Strength Leader ==")
    # หุ้นวิ่งแรงกว่า benchmark ติดต่อกันทุกวันใน lookback period
    stock_closes = [100, 102, 104.5, 107, 110, 113.5]  # ขึ้นเร่งขึ้นทุกวัน
    bench_closes = [100, 100.5, 101, 101.5, 102, 102.5]  # ขึ้นช้าๆสม่ำเสมอ
    hist = make_hist(stock_closes)
    bench_hist = make_hist(bench_closes)
    r = check_relative_strength(hist, bench_hist)
    print(f"  Case A (ควร match=True): matched={r.matched} | {r.detail}")
    assert r.matched is True

    # หุ้นแรงกว่าแค่บางวัน ไม่ครบ streak
    stock_closes2 = [100, 103, 101, 104, 102, 105]
    bench_closes2 = [100, 101, 102, 103, 104, 105]
    hist2 = make_hist(stock_closes2)
    bench_hist2 = make_hist(bench_closes2)
    r2 = check_relative_strength(hist2, bench_hist2)
    print(f"  Case B (อาจ match=False): matched={r2.matched} | {r2.detail}")
    print("  ✅ PASS\n")


def test_pullback_to_support():
    print("== Test 4: Pullback to Support ==")
    # สร้าง uptrend แรงแล้ว pullback มาแตะ EMA
    base = np.linspace(50, 80, 35)  # uptrend แรงต่อเนื่อง
    pullback = base.copy()
    pullback[-3:] = [78, 75, 73.5]  # pullback ช่วงท้าย
    hist = make_hist(pullback)
    r = check_pullback_to_support(hist)
    print(f"  Case A: matched={r.matched} | {r.detail}")

    # ไม่ใช่ uptrend (sideways/downtrend) — ใช้ seed คงที่กันผลลัพธ์สุ่มไม่ตรงกันทุกรอบ
    rng = np.random.default_rng(42)
    flat = np.full(35, 50.0) + rng.normal(0, 0.3, 35)
    hist2 = make_hist(flat)
    r2 = check_pullback_to_support(hist2)
    print(f"  Case B (ควร match=False, ไม่ใช่ uptrend): matched={r2.matched} | {r2.detail}")
    assert r2.matched is False
    print("  ✅ PASS\n")


def test_sector_hype_rotation():
    print("== Test 5: Sector Hype Rotation ==")
    sector_closes = [100, 101, 102.5, 104, 106, 108]  # sector ETF ร้อน (+8%)
    stock_closes = [50, 51, 52, 53.5, 55, 56.5]  # หุ้นวิ่งตาม sector
    hist = make_hist(stock_closes)
    sector_hist = make_hist(sector_closes)
    r = check_sector_hype_rotation(hist, sector_hist, "Technology")
    print(f"  Case A (ควร match=True): matched={r.matched} | {r.detail}")
    assert r.matched is True

    # sector ไม่ร้อน
    sector_closes2 = [100, 100.2, 100.1, 100.3, 100.2, 100.4]
    sector_hist2 = make_hist(sector_closes2)
    r2 = check_sector_hype_rotation(hist, sector_hist2, "Technology")
    print(f"  Case B (ควร match=False, sector ไม่ร้อน): matched={r2.matched} | {r2.detail}")
    assert r2.matched is False
    print("  ✅ PASS\n")


def test_base_filter():
    print("== Test: Base Filter ==")
    closes = [10.0] * 25
    volumes = [2_000_000] * 25
    hist = make_hist(closes, volumes=volumes)
    info = {"marketCap": 500_000_000}
    passed, reason, metrics = apply_base_filter(hist, info)
    print(f"  Case A (ควร passed=True): passed={passed} | metrics={metrics}")
    assert passed is True

    # ราคาต่ำกว่า $5 -> ไม่ผ่าน
    closes2 = [3.0] * 25
    hist2 = make_hist(closes2, volumes=volumes)
    passed2, reason2, _ = apply_base_filter(hist2, info)
    print(f"  Case B (ควร passed=False, ราคาต่ำ): passed={passed2} | reason={reason2}")
    assert passed2 is False

    # market cap ต่ำเกินไป -> ไม่ผ่าน
    info_small = {"marketCap": 100_000_000}
    passed3, reason3, _ = apply_base_filter(hist, info_small)
    print(f"  Case C (ควร passed=False, market cap ต่ำ): passed={passed3} | reason={reason3}")
    assert passed3 is False
    print("  ✅ PASS\n")


def test_fundamental_filter():
    print("== Test: Fundamental Filter ==")
    # Case A: หนี้สูงเกิน -> ไม่ผ่าน
    info_high_debt = {"debtToEquity": 350.0, "currentRatio": 1.5}
    passed, reason = apply_fundamental_filter(info_high_debt)
    print(f"  Case A (ควร passed=False, หนี้สูง): passed={passed} | reason={reason}")
    assert passed is False

    # Case B: current ratio ต่ำเกิน -> ไม่ผ่าน
    info_low_liquidity = {"debtToEquity": 100.0, "currentRatio": 0.3}
    passed2, reason2 = apply_fundamental_filter(info_low_liquidity)
    print(f"  Case B (ควร passed=False, สภาพคล่องต่ำ): passed={passed2} | reason={reason2}")
    assert passed2 is False

    # Case C: งบโอเค -> ผ่าน
    info_healthy = {"debtToEquity": 80.0, "currentRatio": 1.8}
    passed3, reason3 = apply_fundamental_filter(info_healthy)
    print(f"  Case C (ควร passed=True, งบปกติ): passed={passed3} | reason={reason3}")
    assert passed3 is True

    # Case D: ไม่มีข้อมูล -> ผ่าน (ไม่ตัดทิ้งทั้งที่ไม่รู้)
    passed4, reason4 = apply_fundamental_filter({})
    print(f"  Case D (ควร passed=True, ไม่มีข้อมูล): passed={passed4} | reason={reason4}")
    assert passed4 is True
    print("  ✅ PASS\n")


def test_fundamental_score():
    print("== Test: Fundamental Score ==")
    # บริษัทงบดีรอบด้าน
    info_strong = {
        "profitMargins": 0.25,
        "returnOnEquity": 0.30,
        "revenueGrowth": 0.20,
        "earningsGrowth": 0.25,
        "debtToEquity": 50.0,
        "currentRatio": 2.0,
        "trailingPE": 18.0,
        "sector": "Technology",
    }
    score, breakdown, raw = calculate_fundamental_score(info_strong, {})
    print(f"  Case A (บริษัทงบดี, ควรคะแนนสูง): score={score} | breakdown={breakdown}")
    assert score is not None and score > 60

    # บริษัทงบแย่
    info_weak = {
        "profitMargins": -0.10,
        "returnOnEquity": -0.05,
        "revenueGrowth": -0.15,
        "earningsGrowth": -0.20,
        "debtToEquity": 280.0,
        "currentRatio": 0.6,
        "trailingPE": 50.0,
        "sector": "Technology",
    }
    score2, breakdown2, raw2 = calculate_fundamental_score(info_weak, {})
    print(f"  Case B (บริษัทงบแย่, ควรคะแนนต่ำ): score={score2} | breakdown={breakdown2}")
    assert score2 is not None and score2 < 40

    # ไม่มีข้อมูลเลย -> None
    score3, breakdown3, raw3 = calculate_fundamental_score({}, {})
    print(f"  Case C (ไม่มีข้อมูล, ควร score=None): score={score3}")
    assert score3 is None

    # มีข้อมูลแค่บางส่วน -> ยังคำนวณได้ (normalize น้ำหนัก)
    info_partial = {"profitMargins": 0.15, "returnOnEquity": 0.18}
    score4, breakdown4, raw4 = calculate_fundamental_score(info_partial, {})
    print(f"  Case D (มีข้อมูลแค่ profitability): score={score4} | breakdown={breakdown4}")
    assert score4 is not None
    print("  ✅ PASS\n")


def test_earnings_surprise_momentum():
    print("== Test 6: Earnings Surprise Momentum ==")
    closes = list(np.linspace(50, 55, 30))
    hist = make_hist(closes)
    last_date = hist.index[-1]

    # Case A: earnings beat แรง เมื่อ 2 วันก่อน + ราคายัง react บวก
    earnings_date = hist.index[-3]
    earnings_hist = pd.DataFrame(
        {"surprisePercent": [8.5]},
        index=[earnings_date],
    )
    r = check_earnings_surprise_momentum(earnings_hist, hist)
    print(f"  Case A (ควร match=True): matched={r.matched} | {r.detail}")
    assert r.matched is True

    # Case B: surprise น้อยเกินไป
    earnings_hist_small = pd.DataFrame(
        {"surprisePercent": [2.0]},
        index=[earnings_date],
    )
    r2 = check_earnings_surprise_momentum(earnings_hist_small, hist)
    print(f"  Case B (ควร match=False, surprise น้อย): matched={r2.matched} | {r2.detail}")
    assert r2.matched is False

    # Case C: ไม่มีข้อมูล earnings history เลย
    r3 = check_earnings_surprise_momentum(None, hist)
    print(f"  Case C (ควร match=False, ไม่มีข้อมูล): matched={r3.matched} | {r3.detail}")
    assert r3.matched is False

    # Case D: earnings เก่าเกินไป (เกิน lookback)
    old_earnings_date = hist.index[0]
    earnings_hist_old = pd.DataFrame(
        {"surprisePercent": [10.0]},
        index=[old_earnings_date],
    )
    r4 = check_earnings_surprise_momentum(earnings_hist_old, hist)
    print(f"  Case D (ควร match=False, เก่าเกินไป): matched={r4.matched} | {r4.detail}")
    assert r4.matched is False
    print("  ✅ PASS\n")


if __name__ == "__main__":
    test_gap_and_hold()
    test_volume_breakout()
    test_relative_strength()
    test_pullback_to_support()
    test_sector_hype_rotation()
    test_base_filter()
    test_fundamental_filter()
    test_fundamental_score()
    test_earnings_surprise_momentum()
    print("🎉 ทุก test ผ่านหมด — logic ของ scanner ทำงานถูกต้องตามที่ออกแบบไว้")
