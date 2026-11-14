# -*- coding: utf-8 -*-
"""RSI 因子检验：用标准 14 期 RSI 量化「已涨过的更差」效应。

思路：把 factor_check 的二分（涨>5% / 没>5%）换成连续的 RSI，
看超买(>70)/中性(30-70)/超卖(<30)三组的前瞻收益是否有显著差异。
"""
from __future__ import annotations
import json, statistics, sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.backtest import (forecasts_by_report_period, forward_return,
                                index_bars, load_prices, prefetch_prices,
                                primary_rows, _window_return)

PERIODS = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
HOLD = 60
RSI_PERIOD = 14


def rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """标准 Wilder RSI。"""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for prev, curr in zip(closes[-(period + 1):-1], closes[-period:]):
        diff = curr - prev
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def rsi_at(code: str, notice: str, start: date, end: date) -> float | None:
    bars = load_prices(code, start, end)
    if not bars:
        return None
    visible = [b for b in bars if b["day"] <= notice]
    if len(visible) < RSI_PERIOD + 1:
        return None
    return rsi([b["close"] for b in visible])


def run(period: str) -> dict:
    report_date = date(*map(int, period.split("-")))
    rows = primary_rows(forecasts_by_report_period(report_date))
    if not rows:
        return {"period": period, "error": "no rows"}
    notices = sorted({str(r.get("NOTICE_DATE") or "")[:10] for r in rows
                      if str(r.get("NOTICE_DATE") or "")[:10]})
    if not notices:
        return {"period": period, "error": "no notices"}
    start = date.fromisoformat(notices[0]) - timedelta(days=60)
    end = date.fromisoformat(notices[-1]) + timedelta(days=int(HOLD * 2.2) + 30)

    codes = sorted({str(r.get("SECURITY_CODE") or "").strip() for r in rows
                    if str(r.get("SECURITY_CODE") or "").strip()})
    prefetch_prices(codes[:300], start, end, workers=6)
    index = index_bars(start, end)

    buckets = {"超卖(<30)": [], "中性(30-70)": [], "超买(>70)": []}
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        notice = str(row.get("NOTICE_DATE") or "")[:10]
        if not code or not notice:
            continue
        value = forward_return(load_prices(code, start, end), notice, HOLD)
        if value is None:
            continue
        rsi_val = rsi_at(code, notice, start, end)
        if rsi_val is None:
            continue
        benchmark = _window_return(index, notice, HOLD) if index else 0
        excess = value - (benchmark or 0)
        if rsi_val < 30:
            buckets["超卖(<30)"].append((value, excess))
        elif rsi_val > 70:
            buckets["超买(>70)"].append((value, excess))
        else:
            buckets["中性(30-70)"].append((value, excess))

    out = {"period": period}
    for name, items in buckets.items():
        if not items:
            out[name] = {"n": 0}
            continue
        returns = [r for r, _ in items]
        excesses = [e for _, e in items]
        out[name] = {
            "n": len(items),
            "ret_median": round(statistics.median(returns) * 100, 2),
            "exc_median": round(statistics.median(excesses) * 100, 2) if excesses else None,
        }
    return out


def main() -> int:
    pooled = {"超卖(<30)": [], "中性(30-70)": [], "超买(>70)": []}
    pooled_exc = {"超卖(<30)": [], "中性(30-70)": [], "超买(>70)": []}
    for period in PERIODS:
        result = run(period)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        for name in pooled:
            stats = result.get(name, {})
            if stats.get("ret_median") is not None:
                pooled[name].append(stats["ret_median"])
            if stats.get("exc_median") is not None:
                pooled_exc[name].append(stats["exc_median"])

    print("\n=== RSI 分组汇总（8 期中位数 %） ===")
    for name in ("超卖(<30)", "中性(30-70)", "超买(>70)"):
        if pooled[name]:
            print(f"{name}: n={len(pooled[name])} 期, "
                  f"收益中位={statistics.median(pooled[name]):.2f}%, "
                  f"超额中位={statistics.median(pooled_exc[name]) if pooled_exc[name] else '—'}%")
    # 超卖 vs 超买 差
    if pooled["超卖(<30)"] and pooled["超买(>70)"]:
        diff = statistics.median(pooled["超卖(<30)"]) - statistics.median(pooled["超买(>70)"])
        print(f"\n超卖 − 超买 = {diff:+.2f}pct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
