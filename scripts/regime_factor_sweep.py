# -*- coding: utf-8 -*-
"""下行市仓位折算系数：0.3 / 0.5 / 0.7 / 不打折，哪一档最好？

规则来自 v4 的诊断：业绩预告大幅变动的股票在高 beta 双向放大，下跌市里跌得
比随机股多 1.08–1.33 倍。0.5× 是一拍脑袋的中值，这里把几档都跑一遍，
看收益与回撤怎么动。

判断标准不是「收益最高」，而是**回撤降得最多、收益掉得最少**——
这条规则的价值本来就在控制暴露，不在选股。

用法：
    .venv/bin/python scripts/regime_factor_sweep.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.portfolio import PortfolioConfig, run_portfolio_test  # noqa: E402

PERIODS = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
FACTORS = (1.0, 0.7, 0.5, 0.3)


def run(factor: float) -> dict:
    config = PortfolioConfig(initial_capital=100000.0, max_positions=20,
                             position_pct=0.05, cash_floor_pct=0.2, hold_days=60,
                             regime_factor=factor)
    total = 0.0
    drawdown = 0.0
    worst = 0.0
    scaled = 0
    for period in PERIODS:
        result = run_portfolio_test(periods=(period,), threshold=30.0, min_amount=5e7,
                                    config=config, sort_key="amount_desc",
                                    prefilter_affordability=True,
                                    regime_scaling=factor < 1.0)
        item = result["event"]
        total += item["total_return"]
        drawdown += item["max_drawdown"]
        worst = max(worst, item["max_drawdown"])
        scaled += item.get("scaled_by_regime", 0)
    return {"total": total, "drawdown": drawdown, "worst": worst, "scaled": scaled}


def main() -> int:
    print(f"{'折算系数':>10s} | {'八期加总收益':>12s} | {'回撤合计':>9s} | "
          f"{'最差单期回撤':>12s} | {'打折笔数':>8s}")
    rows = {}
    for factor in FACTORS:
        result = run(factor)
        rows[factor] = result
        label = "不打折" if factor >= 1.0 else f"{factor:.1f}×"
        print(f"{label:>10s} | {result['total']:>11.2f}% | {result['drawdown']:>8.2f}% | "
              f"{result['worst']:>11.2f}% | {result['scaled']:>8}")
    base = rows[1.0]
    print("\n相对不打折：")
    for factor in FACTORS:
        if factor >= 1.0:
            continue
        result = rows[factor]
        print(f"  {factor:.1f}×：收益 {result['total'] - base['total']:+.2f} 个百分点，"
              f"回撤 {result['drawdown'] - base['drawdown']:+.2f} 个百分点")
    print("\n怎么选：看「每少一个百分点的回撤，换来几个百分点的收益」。"
          "收益掉得比回撤少的那一档才是划算的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
