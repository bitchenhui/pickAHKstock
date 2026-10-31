# -*- coding: utf-8 -*-
"""市场环境诊断：强预告股在下跌市里是不是更脆？

v4 的读数里有一件事需要解释：最近两期（2026-03-31、2026-06-30）强预告组合
跑输全市场随机组合最多（−11.6、−6.6 个百分点），而更早的几期是赢的。
两种可能：

- **脆性假说**：业绩预告大幅变动的股票弹性更大，下跌日跌得更多。
  若成立，这类票在下跌市里就该少拿、或者按下跌日弹性打折。
- **池子结构假说**：2026 年的预告池结构变了（比如市值分布、行业分布），
  与信号本身无关。

本脚本查第一种：把每只股票的日收益按「指数当天的涨跌」分成上涨日与下跌日，
比较强预告组与市场随机组在下跌日的平均日收益（下跌日捕获率）。

用法：
    .venv/bin/python scripts/regime_check.py
"""

from __future__ import annotations

import random
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.backtest import (forecasts_by_report_period,  # noqa: E402
                               index_bars, is_strong, load_prices,
                               prefetch_prices, primary_rows)

PERIODS = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
HOLDS = (20, 60)
MARKET_SAMPLE = 400


def _daily_returns(bars: list[dict]) -> dict[str, float]:
    out = {}
    for previous, current in zip(bars, bars[1:]):
        if previous["close"]:
            out[current["day"]] = current["close"] / previous["close"] - 1
    return out


def _capture(returns: dict[str, float], index_returns: dict[str, float]) -> dict:
    """上涨日 / 下跌日的平均日收益。"""
    up, down = [], []
    for day, value in returns.items():
        move = index_returns.get(day)
        if move is None:
            continue
        (up if move > 0 else down if move < 0 else []).append(value)
    def mean(items):
        return round(statistics.mean(items) * 100, 3) if items else None
    return {"up_mean": mean(up), "down_mean": mean(down),
            "up_n": len(up), "down_n": len(down),
            "down_capture": (round(mean(down) / (mean(up) or -1), 3)
                             if up and down and mean(up) else None)}


def run(period: str) -> dict:
    report_date = date(*map(int, period.split("-")))
    rows = primary_rows(forecasts_by_report_period(report_date))
    strong_rows = [row for row in rows if is_strong(row, 30.0, 5e7)]
    notices = [str(row.get("NOTICE_DATE") or "")[:10] for row in rows
               if str(row.get("NOTICE_DATE") or "")[:10]]
    if not notices or not strong_rows:
        return {"period": period, "error": "数据不足"}

    from research.industry import industry_map

    forecasters = {str(row.get("SECURITY_CODE") or "").strip() for row in rows}
    candidates = sorted(set(industry_map("second").keys()) - forecasters)
    rng = random.Random(20261031)
    picked = rng.sample(candidates, min(MARKET_SAMPLE, len(candidates)))

    start = date.fromisoformat(notices[0]) - timedelta(days=40)
    end = date.fromisoformat(notices[-1]) + timedelta(days=int(max(HOLDS) * 2.2) + 30)
    codes = sorted({str(row.get("SECURITY_CODE") or "").strip() for row in strong_rows}
                   | set(picked))
    prefetch_prices(codes, start, end)

    index = index_bars(start, end)
    index_returns = _daily_returns(index)

    def group_stats(code_list):
        ups, downs = [], []
        for code in code_list:
            bars = load_prices(code, start, end)
            capture = _capture(_daily_returns(bars), index_returns)
            if capture["up_mean"] is not None:
                ups.append(capture["up_mean"])
            if capture["down_mean"] is not None:
                downs.append(capture["down_mean"])
        return (round(statistics.mean(ups), 3) if ups else None,
                round(statistics.mean(downs), 3) if downs else None,
                len(ups))

    strong_codes = [str(row.get("SECURITY_CODE") or "").strip() for row in strong_rows]
    s_up, s_down, s_n = group_stats(strong_codes)
    m_up, m_down, m_n = group_stats(picked)
    index_return = (index[-1]["close"] / index[0]["close"] - 1) if index else None
    return {
        "period": period,
        "index_return": round(index_return * 100, 2) if index_return is not None else None,
        "strong": {"n": s_n, "up_mean": s_up, "down_mean": s_down},
        "market": {"n": m_n, "up_mean": m_up, "down_mean": m_down},
        "down_gap": (round(s_down - m_down, 3)
                     if s_down is not None and m_down is not None else None),
    }


def main() -> int:
    print(f"{'报告期':>12s} | {'指数':>7s} | {'强预告 上涨日':>12s} {'下跌日':>8s} | "
          f"{'市场 上涨日':>10s} {'下跌日':>8s} | {'下跌日差':>8s}")
    down_gaps = []
    for period in PERIODS:
        result = run(period)
        if "error" in result:
            print(f"{period:>12s} | {result['error']}")
            continue
        s, m = result["strong"], result["market"]
        print(f"{period:>12s} | {result['index_return']:>6}% | "
              f"{s['up_mean']:>11}% {s['down_mean']:>7}% | "
              f"{m['up_mean']:>9}% {m['down_mean']:>7}% | {result['down_gap']:>7}%")
        down_gaps.append(result["down_gap"])
    if down_gaps:
        print(f"\n八期下跌日平均收益差的中位：{statistics.median(down_gaps):+.3f} 个百分点"
              "（负 = 强预告股在下跌日跌得更多）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
