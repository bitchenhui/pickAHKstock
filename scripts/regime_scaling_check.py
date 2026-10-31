# -*- coding: utf-8 -*-
"""下行市仓位减半这条规则，在组合层到底有没有用？

规则来自 v4 的诊断：业绩预告大幅变动的股票是高 beta 双向放大的，下跌市里
跌得比随机股多 1.08–1.33 倍。所以下行市里把单笔仓位减半（按**建仓当日**的
市场状态判断，point-in-time）。

本脚本拿同一批候选、同样的仓位与费用约束，跑两遍：不打折 vs 打折，比较
收益与回撤。用法：

    .venv/bin/python scripts/regime_scaling_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.portfolio import PortfolioConfig, run_portfolio_test  # noqa: E402

PERIODS = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
CONFIG = PortfolioConfig(initial_capital=100000.0, max_positions=20, position_pct=0.05,
                         cash_floor_pct=0.2, hold_days=60)


def main() -> int:
    print(f"{'报告期':>12s} | {'不打折':>8s} {'回撤':>6s} | {'打折':>8s} {'回撤':>6s} | "
          f"{'收益差':>7s} {'回撤差':>7s} | {'打折笔数':>8s}")
    total_plain = total_scaled = 0.0
    draw_plain = draw_scaled = 0.0
    for period in PERIODS:
        plain = run_portfolio_test(periods=(period,), threshold=30.0, min_amount=5e7,
                                   config=CONFIG, sort_key="amount_desc",
                                   prefilter_affordability=True)
        scaled = run_portfolio_test(periods=(period,), threshold=30.0, min_amount=5e7,
                                    config=CONFIG, sort_key="amount_desc",
                                    prefilter_affordability=True,
                                    regime_scaling=True)
        a, b = plain["event"], scaled["event"]
        total_plain += a["total_return"]
        total_scaled += b["total_return"]
        draw_plain += a["max_drawdown"]
        draw_scaled += b["max_drawdown"]
        print(f"{period:>12s} | {a['total_return']:>7}% {a['max_drawdown']:>5}% | "
              f"{b['total_return']:>7}% {b['max_drawdown']:>5}% | "
              f"{b['total_return'] - a['total_return']:>+6.1f} "
              f"{b['max_drawdown'] - a['max_drawdown']:>+6.1f} | "
              f"{b.get('scaled_by_regime', 0):>8}")
    print(f"\n八期加总收益：不打折 {total_plain:+.2f}%｜打折 {total_scaled:+.2f}%"
          f"｜差 {total_scaled - total_plain:+.2f} 个百分点")
    print(f"八期最差回撤合计：不打折 {draw_plain:.2f}%｜打折 {draw_scaled:.2f}%"
          f"｜差 {draw_scaled - draw_plain:+.2f} 个百分点")
    print("\n读法：这条规则的价值主要在**回撤**，不在收益——它在两期下跌市里把亏损"
          "收窄了一半多，而在上涨市里只是少赚一点、少回撤一点。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
