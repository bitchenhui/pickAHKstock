# -*- coding: utf-8 -*-
"""推荐层多因子的历史检验：共振类型/相对强度/单季动能 是否预测了后续收益。

背景：v5 已证明发现层信号无 alpha（-5.38pct vs 全市场）。但推荐层的
因子（公司特有 vs 行业共振、相对强度正负、单季利润趋势）是否有
增量信息——这是独立的问题，v5 没测过。

方法：取 13 个报告期的预告池，对每只股票算三因子（数据全部来自
deep-dive 已灌入的卡片字段或可从日线推算），然后比较不同因子组的
前瞻收益（公告后 60 日，相对沪深300 超额）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.backtest import (forecasts_by_report_period, forward_return,
                               index_bars, load_prices, prefetch_prices,
                               primary_rows)
from research.industry import industry_map

PERIODS = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
HOLD = 60


def run(period: str, seed: int = 7) -> dict:
    report_date = date(*map(int, period.split("-")))
    rows = primary_rows(forecasts_by_report_period(report_date))
    if not rows:
        return {"period": period, "error": "no rows"}

    mapping = industry_map("second")
    notices = sorted({str(r.get("NOTICE_DATE") or "")[:10] for r in rows
                      if str(r.get("NOTICE_DATE") or "")[:10]})
    if not notices:
        return {"period": period, "error": "no notices"}

    start = date.fromisoformat(notices[0]) - timedelta(days=40)
    end = date.fromisoformat(notices[-1]) + timedelta(days=int(HOLD * 2.2) + 30)

    # --- 因子 1：单季利润趋势（从累计预告金额推不了，用幅度方向替代） ---
    # 预告幅度 ADD_AMP_LOWER 的正负作为经营方向的粗代理

    # --- 因子 2：行业共振（用同行业同报告期发预告的公司数与方向） ---
    from research.odds import inflection_type

    by_industry: dict[str, list[tuple[str, float]]] = {}
    codes = []
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        if not code:
            continue
        try:
            change = float(row.get("ADD_AMP_LOWER") or 0)
        except (TypeError, ValueError):
            change = 0
        industry = mapping.get(code, "")
        if industry:
            by_industry.setdefault(industry, []).append((code, change))
        codes.append(code)

    if codes:
        prefetch_prices(list(set(codes))[:200], start, end, workers=6)
    index = index_bars(start, end)

    # 分类
    groups = {
        "公司特有": [],     # 同行业 ≤2 家发预告
        "行业共振": [],     # 同行业 ≥3 家且 ≥80% 同向
        "其他": [],
    }
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        if not code:
            continue
        industry = mapping.get(code, "")
        peers = by_industry.get(industry, [])
        same_sign = sum(1 for _, c in peers if (c > 0) == (float(row.get("ADD_AMP_LOWER") or 0) > 0))
        if len(peers) <= 2:
            groups["公司特有"].append((row, code))
        elif len(peers) >= 3 and same_sign / len(peers) >= 0.8:
            groups["行业共振"].append((row, code))
        else:
            groups["其他"].append((row, code))

    # --- 因子 3：公告前价格动能（近 20 日涨幅，模拟相对强度） ---
    def pre_runup(code: str, notice: str) -> float | None:
        bars = load_prices(code, start, end)
        if not bars:
            return None
        idx = None
        for i, bar in enumerate(bars):
            if bar["day"] >= notice:
                idx = i
                break
        if idx is None or idx < 21:
            return None
        base = bars[idx - 20]["close"]
        if base <= 0:
            return None
        return bars[idx - 1]["close"] / base - 1

    # 计算各组收益
    out = {"period": period, "groups": {}}
    for group_name, items in groups.items():
        returns, excesses = [], []
        strong_rs, weak_rs = [], []       # 公告前跑赢/跑输（相对强度代理）
        for row, code in items:
            notice = str(row.get("NOTICE_DATE") or "")[:10]
            if not notice:
                continue
            value = forward_return(load_prices(code, start, end), notice, HOLD)
            if value is None:
                continue
            returns.append(value)
            if index:
                from research.backtest import _window_return
                benchmark = _window_return(index, notice, HOLD)
                if benchmark is not None:
                    excesses.append(value - benchmark)
            runup = pre_runup(code, notice)
            if runup is not None:
                (strong_rs if runup > 0.05 else weak_rs).append(value)
        stats = {
            "n": len(returns),
            "median": round(statistics.median(returns) * 100, 2) if returns else None,
            "excess_median": round(statistics.median(excesses) * 100, 2) if excesses else None,
        }
        if strong_rs:
            stats["strong_rs_n"] = len(strong_rs)
            stats["strong_rs_median"] = round(statistics.median(strong_rs) * 100, 2)
        if weak_rs:
            stats["weak_rs_n"] = len(weak_rs)
            stats["weak_rs_median"] = round(statistics.median(weak_rs) * 100, 2)
        out["groups"][group_name] = stats
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", action="append")
    args = parser.parse_args()
    periods = tuple(args.period) if args.period else PERIODS

    pooled = {"公司特有": [], "行业共振": [], "其他": []}
    pooled_rs = {"strong": [], "weak": []}
    for period in periods:
        result = run(period)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        for name, stats in result.get("groups", {}).items():
            if stats.get("median") is not None:
                pooled[name].append(stats["median"])
            if stats.get("strong_rs_median") is not None:
                pooled_rs["strong"].append(stats["strong_rs_median"])
            if stats.get("weak_rs_median") is not None:
                pooled_rs["weak"].append(stats["weak_rs_median"])

    print("\n=== 汇总（中位数 %） ===")
    for name in ("公司特有", "行业共振", "其他"):
        if pooled[name]:
            print(f"{name}: n={len(pooled[name])} 期, "
                  f"中位={statistics.median(pooled[name]):.2f}%, "
                  f"均值={statistics.fmean(pooled[name]):.2f}%")
    if pooled_rs["strong"] and pooled_rs["weak"]:
        print(f"\n公告前动能强（>5%）：中位={statistics.median(pooled_rs['strong']):.2f}%")
        print(f"公告前动能弱（≤5%）：中位={statistics.median(pooled_rs['weak']):.2f}%")
        diff = statistics.median(pooled_rs["strong"]) - statistics.median(pooled_rs["weak"])
        print(f"差值：{diff:+.2f}pct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
