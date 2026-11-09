# -*- coding: utf-8 -*-
"""预取回测需要的日线（并行、限流友好）。

用法：
    .venv/bin/python scripts/prefetch_prices.py               # 八个报告期全取
    .venv/bin/python scripts/prefetch_prices.py --period 2026-06-30
    .venv/bin/python scripts/prefetch_prices.py --lookback-days 400 --forward-days 380 \
        --period 2022-12-31 ...        # 高赔率基础率：回看一年 + 前向一年

为什么单独一个脚本：回测要在多个报告期上取几千只股票的日线，串行要几小时。
这里按报告期并行预取（6 并发，带间隔），结果按代码落盘缓存，重复跑只补缺口。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.backtest import (forecasts_by_report_period,  # noqa: E402
                               prefetch_prices, primary_rows)

PERIODS = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
HOLD_DAYS = 60


def main() -> int:
    parser = argparse.ArgumentParser(description="预取回测日线")
    parser.add_argument("--period", action="append", help="只取指定报告期（可多次）")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--lookback-days", type=int, default=40,
                        help="公告日之前的自然日窗口（默认 40，高赔率基础率用 400）")
    parser.add_argument("--forward-days", type=int, default=None,
                        help="公告日之后的自然日窗口（默认按持有期 60 交易日折算；"
                             "高赔率基础率传 380 ≈ 250 交易日）")
    args = parser.parse_args()
    periods = tuple(args.period) if args.period else PERIODS
    forward = args.forward_days if args.forward_days is not None else int(HOLD_DAYS * 2.2) + 30

    for period in periods:
        report_date = date(*map(int, period.split("-")))
        rows = primary_rows(forecasts_by_report_period(report_date))
        notices = [str(row.get("NOTICE_DATE") or "")[:10] for row in rows
                   if str(row.get("NOTICE_DATE") or "")[:10]]
        if not notices:
            print(f"{period}: 没有预告行", flush=True)
            continue
        start = date.fromisoformat(min(notices)) - timedelta(days=args.lookback_days)
        end = date.fromisoformat(max(notices)) + timedelta(days=forward)
        codes = [str(row.get("SECURITY_CODE") or "").strip() for row in rows]
        started = time.time()
        got = prefetch_prices(codes, start, end, workers=args.workers)
        print(f"{period}: {len(set(codes))} 只，取到 {got} 只"
              f"（窗口 {start} → {end}），用时 {time.time() - started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
