# -*- coding: utf-8 -*-
"""全市场基线：强预告组 vs 同期随机 A 股。

为什么要这个实验：事件研究现在的对照组是「同报告期发了预告、但变动幅度不达标」
的公司。这批公司**本身就是坏消息集合**（预告小幅预减、首亏、续亏的居多），
拿它当基线，测的是「挑强的比挑不强的强多少」，不是「跟着预告买是否跑赢市场」。

发现层面对的却是市场：它从全市场里挑出预告的公司。所以基线必须是同期随机 A 股。

做法：
- 池子：申万成分股表里的全市场 A 股代码（5221 只）减去当期的预告公司
- 每期随机抽 N 只（固定种子），给每只抽一个**当期真实的公告日**作为事件日
  （从当期公告日分布里等概率抽），这样持有期窗口与事件组在时间上可比
- 用同一套前复权日线、同一套持有期算前瞻收益

局限（必须承认）：
- 成分股表是**当前**的，含退市/新股的幸存者偏差；新上市公司没有足够的
  历史区间会被剔除
- 没做点对点的行业中性化（随机基线天然混合了各行业）
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.backtest import (forecasts_by_report_period,  # noqa: E402
                               forward_return, index_bars, is_strong,
                               load_prices, prefetch_prices, primary_rows)

PERIODS = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
HOLDS = (20, 60)


def universe() -> list[str]:
    """全市场 A 股代码（申万成分股表，覆盖 5221 只）。"""
    from research.industry import industry_map

    return sorted(industry_map("second").keys())


def run(period: str, sample: int = 600, holds=HOLDS, seed: int = 20261030) -> dict:
    report_date = date(*map(int, period.split("-")))
    rows = primary_rows(forecasts_by_report_period(report_date))
    forecasters = {str(row.get("SECURITY_CODE") or "").strip() for row in rows}
    notices = sorted({str(row.get("NOTICE_DATE") or "")[:10] for row in rows
                      if str(row.get("NOTICE_DATE") or "")[:10]})
    if not notices:
        return {"period": period, "error": "没有公告日"}

    market = [code for code in universe() if code not in forecasters]
    rng = random.Random(seed)
    picked = rng.sample(market, min(sample, len(market)))

    # 事件日从当期的真实公告日分布里抽，窗口与事件组可比
    pseudo = {code: notices[rng.randrange(len(notices))] for code in picked}

    start = date.fromisoformat(notices[0]) - timedelta(days=40)
    end = date.fromisoformat(notices[-1]) + timedelta(days=int(max(holds) * 2.2) + 30)
    got = prefetch_prices(picked, start, end)

    out = {"period": period, "pool": len(forecasters), "market_pool": len(market),
           "sampled": len(picked), "with_prices": got, "holds": {}}
    for hold in holds:
        returns = []
        for code, notice in pseudo.items():
            value = forward_return(load_prices(code, start, end), notice, hold)
            if value is not None:
                returns.append(value)
        if not returns:
            continue
        out["holds"][hold] = {
            "n": len(returns),
            "median": round(statistics.median(returns) * 100, 2),
            "win_rate": round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1),
        }

    # 事件组同一口径的可比读数
    for hold in holds:
        event = []
        for row in rows:
            notice = str(row.get("NOTICE_DATE") or "")[:10]
            if not notice or not is_strong(row, 50.0, None):
                continue
            value = forward_return(load_prices(str(row.get("SECURITY_CODE") or "").strip(),
                                               start, end), notice, hold)
            if value is not None:
                event.append(value)
        if event:
            out.setdefault("event", {})[hold] = {
                "n": len(event),
                "median": round(statistics.median(event) * 100, 2),
                "win_rate": round(sum(1 for r in event if r > 0) / len(event) * 100, 1),
            }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="全市场基线对照")
    parser.add_argument("--period", action="append")
    parser.add_argument("--sample", type=int, default=600)
    args = parser.parse_args()
    periods = tuple(args.period) if args.period else PERIODS
    for period in periods:
        result = run(period, sample=args.sample)
        print(result, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
