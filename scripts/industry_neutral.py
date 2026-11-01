# -*- coding: utf-8 -*-
"""行业中性化：收益里有多少是「押对行业」，有多少是「选对股票」？

强预告组不是均匀分布在市场上的——业绩预告密集出现在景气向上的行业（半导体、
有色、航运），所以它的组合收益里天然带着**行业押注**。这跟「共振」是同一件事
的两面：共振高说明这个「好」是行业给的。

这里把每个标的的前瞻收益减去**它自己行业同期的中位收益**，得到行业内超额：
- 行业中性超额 ≈ 0 → 赚的是行业，不是选股
- 行业中性超额 > 0 → 在行业内部确实挑出了更好的

用法：
    .venv/bin/python scripts/industry_neutral.py
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.backtest import (forecasts_by_report_period, forward_return,  # noqa: E402
                               is_strong, load_prices, prefetch_prices, primary_rows)

PERIODS = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
HOLD = 60
THRESHOLD = 30.0
MIN_AMOUNT = 5e7
# 行业样本少于这个数，行业「中位收益」就是它自己，超额恒等于 0，没有意义
MIN_INDUSTRY_MEMBERS = 3


def _median(values):
    return statistics.median(values) if values else None


def run(period: str) -> dict:
    from research.industry import industry_map

    report_date = date(*map(int, period.split("-")))
    rows = primary_rows(forecasts_by_report_period(report_date))
    if not rows:
        return {"period": period, "error": "没有预告数据"}
    notices = [str(row.get("NOTICE_DATE") or "")[:10] for row in rows
               if str(row.get("NOTICE_DATE") or "")[:10]]
    if not notices:
        return {"period": period, "error": "没有公告日"}
    start = date.fromisoformat(min(notices)) - timedelta(days=40)
    end = date.fromisoformat(max(notices)) + timedelta(days=int(HOLD * 2.2) + 30)

    codes = sorted({str(row.get("SECURITY_CODE") or "").strip() for row in rows})
    prefetch_prices(codes, start, end)

    mapping = industry_map("second")
    returns: dict[str, float] = {}
    industry_of: dict[str, str] = {}
    strong: dict[str, bool] = {}
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        notice = str(row.get("NOTICE_DATE") or "")[:10]
        value = forward_return(load_prices(code, start, end), notice, HOLD)
        if value is None:
            continue
        returns[code] = value
        industry_of[code] = mapping.get(code, "")
        strong[code] = is_strong(row, THRESHOLD, MIN_AMOUNT)

    # 各行业的中位收益（用当期池子里有行情的公司）
    by_industry: dict[str, list[float]] = defaultdict(list)
    for code, value in returns.items():
        industry = industry_of.get(code)
        if industry:
            by_industry[industry].append(value)

    def split(codes_filter):
        raw, neutral = [], []
        for code, value in returns.items():
            if not codes_filter(code):
                continue
            raw.append(value)
            industry = industry_of.get(code, "")
            members = by_industry.get(industry, [])
            if len(members) < MIN_INDUSTRY_MEMBERS:
                continue        # 行业样本太小，行业中位没有参考价值
            median = _median(members)
            if median is not None:
                neutral.append(value - median)
        return raw, neutral

    event_raw, event_neutral = split(lambda c: strong.get(c))
    other_raw, other_neutral = split(lambda c: not strong.get(c))
    if not event_raw or not other_raw:
        return {"period": period, "error": "样本不足"}
    return {
        "period": period,
        "event_n": len(event_raw),
        "other_n": len(other_raw),
        "event_neutral_n": len(event_neutral),
        "other_neutral_n": len(other_neutral),
        "event_raw": round(_median(event_raw) * 100, 2),
        "other_raw": round(_median(other_raw) * 100, 2),
        "event_neutral": round(_median(event_neutral) * 100, 2) if event_neutral else None,
        "other_neutral": round(_median(other_neutral) * 100, 2) if other_neutral else None,
        "industries": len({industry_of[c] for c in returns if industry_of.get(c)}),
    }


def main() -> int:
    print(f"{'报告期':>12s} | {'强预告 n':>8s} {'原始中位':>9s} {'行业内超额':>10s} | "
          f"{'非强预告 n':>10s} {'原始中位':>9s} {'行业内超额':>10s} | {'差(中性)':>9s}")
    rows = []
    for period in PERIODS:
        result = run(period)
        if "error" in result:
            print(f"{period:>12s} | {result['error']}")
            continue
        rows.append(result)
        diff = (None if result["event_neutral"] is None or result["other_neutral"] is None
                else result["event_neutral"] - result["other_neutral"])
        event_cell = ("—" if result["event_neutral"] is None
                      else f"{result['event_neutral']:+.2f}%")
        other_cell = ("—" if result["other_neutral"] is None
                      else f"{result['other_neutral']:+.2f}%")
        diff_cell = "—" if diff is None else f"{diff:+.2f}%"
        print(f"{period:>12s} | {result['event_n']:>8d} {result['event_raw']:>8}% "
              f"{event_cell:>9} (n={result.get('event_neutral_n', 0)}) | "
              f"{result['other_n']:>10d} {result['other_raw']:>8}% "
              f"{other_cell:>9} (n={result.get('other_neutral_n', 0)}) | {diff_cell:>9}")
    if rows:
        event_neutral = [r["event_neutral"] for r in rows if r["event_neutral"] is not None]
        other_neutral = [r["other_neutral"] for r in rows if r["other_neutral"] is not None]
        if event_neutral and other_neutral:
            print(f"\n八期加总：强预告 行业内超额中位 {statistics.median(event_neutral):+.2f}%"
                  f"｜非强预告 {statistics.median(other_neutral):+.2f}%"
                  f"｜差 {statistics.median(event_neutral) - statistics.median(other_neutral):+.2f}%")
        print("读法：行业内超额接近 0，说明强预告组赚的是**行业**的钱而不是选股的钱——"
              "那正是「同业共振」在提示的事。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
