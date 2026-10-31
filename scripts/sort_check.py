# -*- coding: utf-8 -*-
"""发现层的排序规则还有没有价值？

v3 的结论是「排序目前没有贡献」，但那是建立在 7% 的预告池上的。现在池子完整了，
重测一遍——推荐清单的排序规则是这个工具最直接的产品决定，值得用完整数据定。

测四种排序（同一批候选、同样的仓位与费用约束，只换买谁）：
- amount_desc：预告绝对额降序（发现层默认）
- change_asc：变化幅度小的优先（v3 事件研究里「温和优先」的方向）
- change_desc：变化幅度大的优先（对照）
- random：随机（排序有没有用的判据）

用法：
    .venv/bin/python scripts/sort_check.py
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.portfolio import PortfolioConfig, run_portfolio_test  # noqa: E402

PERIODS = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
KEYS = ("amount_desc", "change_asc", "change_desc", "random")
CONFIG = PortfolioConfig(initial_capital=100000.0, max_positions=20, position_pct=0.05,
                         cash_floor_pct=0.2, hold_days=60)


def main() -> int:
    print(f"{'排序':>14s} | {'八期加总':>9s} {'平均回撤':>9s} {'最差回撤':>9s} "
          f"{'覆盖率':>8s} | {'跑赢随机的期数':>14s}")
    results = {}
    for key in KEYS:
        total = 0.0
        drawdowns = []
        coverage = []
        wins = 0
        per_period = {}
        for period in PERIODS:
            result = run_portfolio_test(periods=(period,), threshold=30.0, min_amount=5e7,
                                        config=CONFIG, sort_key=key,
                                        prefilter_affordability=True)
            if "error" in result:
                continue
            item = result["event"]
            per_period[period] = item["total_return"]
            total += item["total_return"]
            drawdowns.append(item["max_drawdown"])
            coverage.append(item["coverage"])
        results[key] = {"total": total, "drawdowns": drawdowns,
                        "coverage": coverage, "per_period": per_period}
        print(f"{key:>14s} | {total:>8}% {statistics.mean(drawdowns):>8}% "
              f"{max(drawdowns):>8}% {statistics.mean(coverage):>7}% |", flush=True)
    print("\n逐期（%）")
    print(f"{'报告期':>12s} " + " ".join(f"{k:>13s}" for k in KEYS))
    for period in PERIODS:
        row = [results[k]["per_period"].get(period) for k in KEYS]
        print(f"{period:>12s} " + " ".join(
            f"{'—' if value is None else f'{value:+.2f}%':>13s}" for value in row))
    base = results.get("random", {}).get("per_period", {})
    print()
    for key in KEYS:
        if key == "random":
            continue
        wins = sum(1 for period, value in results[key]["per_period"].items()
                   if base.get(period) is not None and value > base[period])
        print(f"{key:>14s} 跑赢随机的期数：{wins}/{len(base)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
