# -*- coding: utf-8 -*-
"""高赔率漏斗的历史基础率：这套结构条件到底选出过什么。

为什么必须有这个脚本：v5 的教训是「任何新筛选先过历史基础率，且对照组的选择
先于结论」。高赔率漏斗（市值带 30-150 亿 + 近一年涨幅 <80% + 扭亏/减亏/预增）
上线之前，先回答：13 个报告期里它筛出的候选，公告后一年内最大涨幅的分布
是什么样？≥5x 的占比多少？与两组对照比呢？

对照组（先说清怎么来的，v5 教训）：
- 对照 A：同报告期、拐点类型有效但结构条件未过（failed 里是市值/回看原因，
  按原因分解）——测「结构条件加了多少信息」。
- 对照 B：同期全市场随机（非预告公司，伪公告日从当期公告日分布里抽）——
  测「漏斗整体 vs 市场」。

诚实边界：
- 幸存者偏差：退市股没有当前快照，会从候选里静默消失；随机基线的成分表
  也是当前的清单。覆盖率随报告输出。
- 2026 两期的前向窗口被今天截断（不满 250 交易日），最大涨幅是下界，
  表里带实际天数。
- 市值是「当前股本 × 公告日收盘」的近似口径。

用法：
    .venv/bin/python scripts/odds_base_rate.py                 # 全部 13 期
    .venv/bin/python scripts/odds_base_rate.py --period 2025-12-31
    .venv/bin/python scripts/odds_base_rate.py --skip-market   # 跳过对照 B（省时）
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.backtest import (_bootstrap_median_diff,  # noqa: E402
                               forecasts_by_report_period, load_prices,
                               prefetch_prices, primary_rows)
from research.odds import (DEFAULT_CAP_MAX, DEFAULT_CAP_MIN, DEFAULT_RUNUP_MAX,  # noqa: E402
                           FORWARD_WINDOW, INFLECTION_TYPES, evaluate,
                           forward_window, inflection_type, load_snapshots,
                           prefilter_cap)

PERIODS = ("2022-12-31", "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
           "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
LOOKBACK_DAYS = 400      # 自然日，覆盖 250 交易日回看
FORWARD_DAYS = 380       # 自然日 ≈ 250 交易日
CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "odds"


def market_universe() -> list[str]:
    from research.industry import industry_map

    return sorted(industry_map("second").keys())


def run_period(period: str, market_sample: int = 0, seed: int = 7,
               workers: int = 6, use_cache: bool = True) -> dict:
    """评估一个报告期并缓存。返回 {hits: [...], coverage: {...}}。

    hits 含通过组与对照 A（拐点有效但结构条件未过），每条带前向窗口指标。
    """
    cache_file = CACHE_DIR / f"base_{period}.json"
    if use_cache and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except ValueError:
            pass

    report_date = date(*map(int, period.split("-")))
    rows = primary_rows(forecasts_by_report_period(report_date))
    typed = [row for row in rows if inflection_type(row) in INFLECTION_TYPES]
    codes = sorted({str(row.get("SECURITY_CODE") or "").strip() for row in typed
                    if str(row.get("SECURITY_CODE") or "").strip()})
    notices = sorted({str(row.get("NOTICE_DATE") or "")[:10] for row in typed
                      if str(row.get("NOTICE_DATE") or "")[:10]})
    if not typed or not notices:
        return {"period": period, "hits": [], "coverage": {"typed": len(typed)}}

    snapshots = load_snapshots([("A", code) for code in codes])
    prefiltered = prefilter_cap(snapshots, codes, DEFAULT_CAP_MIN, DEFAULT_CAP_MAX)
    start = date.fromisoformat(notices[0]) - timedelta(days=LOOKBACK_DAYS)
    end = min(date.today(), date.fromisoformat(notices[-1]) + timedelta(days=FORWARD_DAYS))
    if prefiltered:
        prefetch_prices(prefiltered, start, end, workers=workers)

    prices_map = {code: load_prices(code, start, end) for code in prefiltered}
    hits = evaluate(typed, snapshots, prices_map)
    out_hits = []
    for hit in hits:
        record = {**{k: getattr(hit, k) for k in
                     ("code", "name", "inflection", "notice", "report_period",
                      "cap_at", "runup", "short_lookback", "predict_lower",
                      "annualized", "needed_10x", "gap_10x", "failed")},
                  "forward": None}
        prices = prices_map.get(hit.code) or []
        window = forward_window(prices, hit.notice, FORWARD_WINDOW)
        if window:
            record["forward"] = {"max_close": window.max_gain_close,
                                 "max_high": window.max_gain_high,
                                 "end": window.end_return, "days": window.days}
        out_hits.append(record)

    coverage = {"pool": len(rows), "typed": len(typed), "snapshots": len(snapshots),
                "prefiltered": len(prefiltered),
                "with_prices": sum(1 for p in prices_map.values() if p),
                "forward_window_days": int((end - date.fromisoformat(notices[-1])).days)}

    market = []
    if market_sample:
        rng = random.Random(seed)
        forecasters = {str(row.get("SECURITY_CODE") or "").strip() for row in rows}
        pool = [c for c in market_universe() if c not in forecasters]
        picked = rng.sample(pool, min(market_sample, len(pool)))
        pseudo = {c: notices[rng.randrange(len(notices))] for c in picked}
        if picked:
            prefetch_prices(picked, start, end, workers=workers)
        for code, notice in pseudo.items():
            window = forward_window(load_prices(code, start, end), notice, FORWARD_WINDOW)
            if window:
                market.append({"code": code, "notice": notice,
                               "max_close": window.max_gain_close,
                               "max_high": window.max_gain_high,
                               "end": window.end_return, "days": window.days})

    result = {"period": period, "hits": out_hits, "market": market,
              "coverage": coverage}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


# ---------------------------------------------------------------- 统计


def bucket(values: list[float]) -> dict:
    """最大涨幅分布：中位、≥5x/≥3x/≥2x/翻倍/为正的占比。"""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {"n": len(values),
            "median_max": round(statistics.median(ordered) * 100, 1),
            "ge5x": round(sum(1 for v in ordered if v >= 4.0) / len(ordered) * 100, 1),
            "ge3x": round(sum(1 for v in ordered if v >= 2.0) / len(ordered) * 100, 1),
            "ge2x": round(sum(1 for v in ordered if v >= 1.0) / len(ordered) * 100, 1),
            "positive": round(sum(1 for v in ordered if v > 0) / len(ordered) * 100, 1)}


def collect(result: dict) -> dict:
    """按组收集 max_close 序列：pass / control_a（按首个失败原因再分）/ market。

    对照 A 限于预筛带内有行情的行（当前市值 18.75-240 亿）：市值差三倍的
    组互为对照没有意义。预筛外/无行情的行不进任何组，只在覆盖率里计数。
    """
    passes, control = [], []
    by_reason: dict[str, list] = {}
    for hit in result.get("hits", []):
        forward = hit.get("forward")
        if not forward:
            continue
        value = forward["max_close"]
        if hit.get("failed"):
            if any("缺失" in f for f in hit["failed"]):
                continue          # 预筛外或无行情，进不了对照
            control.append(value)
            reason = next((f for f in hit["failed"] if "市值" in f or "已涨" in f),
                          hit["failed"][0])
            key = "市值带" if "市值" in reason else "已涨透"
            by_reason.setdefault(key, []).append(value)
        else:
            passes.append(value)
    market = [row["max_close"] for row in result.get("market", [])]
    return {"pass": passes, "control_a": control, "by_reason": by_reason,
            "market": market}


def sensitivity(results: list[dict]) -> list[dict]:
    """市值带 × 回看阈值 的敏感性：只动这两个参数，其余不动。

    主口径若在网格上翻转（≥5x 占比相对对照的优劣变了方向），说明结论是
    参数的产物。**不做数据挑选**：所有格子都报出来。网格在数据完整的行上
    重算（不能只在默认通过集里收窄——「不限」档必须能把默认刷掉的行放回来）。
    """
    grid = []
    for cap_min, cap_max in ((30e8, 80e8), (30e8, 150e8), (50e8, 200e8), (0.0, 1e12)):
        for runup_max in (0.5, 0.8, 10.0):
            values = []
            for result in results:
                for hit in result.get("hits", []):
                    forward = hit.get("forward")
                    if not forward:
                        continue
                    if hit["cap_at"] is None or hit["runup"] is None:
                        continue
                    if not (cap_min <= hit["cap_at"] <= cap_max):
                        continue
                    if hit["runup"] > runup_max:
                        continue
                    values.append(forward["max_close"])
            stats = bucket(values)
            band = f"{cap_min / 1e8:.0f}-{int(cap_max / 1e8) if cap_max < 1e11 else '∞'}亿"
            stats.update({"band": band,
                          "runup_max": f"{runup_max * 100:.0f}%" if runup_max < 10 else "不限"})
            grid.append(stats)
    return grid


def _fmt(stats: dict, key: str) -> str:
    return f"{stats[key]}%" if stats.get("n") else "—"


def report(results: list[dict], with_market: bool) -> str:
    lines = ["# 高赔率漏斗 v1：历史基础率（13 期）", ""]
    lines.append("> 由 `scripts/odds_base_rate.py` 生成。主口径：公告日市值 30-150 亿 + "
                 "近一年涨幅 <80% + 扭亏/减亏/预增。")
    lines.append("> **这是结构条件筛选的基础率，不是信号预测**。最大涨幅是 close 口径"
                 "（可兑现），窗口 250 交易日（近两期被今天截断，为下界）。")
    lines.append("> 市值为「当前股本 × 公告日收盘」近似；退市股无当前快照，"
                 "构成幸存者偏差，覆盖率见表尾。")
    lines.append("")
    lines.append("## 怎么读这张表（先于任何结论）")
    lines.append("")
    lines.append("1. **看 ≥5x 占比三组是否拉开**：通过组、对照 A（同池结构条件未过）、"
                 "全市场随机。三组都在 1% 附近 ⇒ 这套结构条件**不构成 5-10 倍的选股"
                 "优势**——市值带与回看阈值筛掉的是波动，不是 5x 的命中。")
    lines.append("2. **中位差的 bootstrap 区间**：区间不含 0 且为负 ⇒ 通过组的一年内"
                 "最大涨幅反而**低于**对照（对照的市值带桶含公告日 30 亿以下的小票，"
                 "天然高波动，这解释了方向但改变不了结论）。")
    lines.append("3. **敏感性 12 格**：≥5x 全部在同一水平 ⇒ 结论不是参数的产物，"
                 "是「业绩预告池里 1 年 ≥5x 本来就是约 1% 的事件」这个事实本身。")
    lines.append("4. **绝对数字的用途是期望管理**：每期 20 只小仓多注，期望约 0.2 只"
                 "≥5x、2 只 ≥3x、2 只 ≥2x——这是「小仓多注 + 早杀错」数学的现实版本，"
                 "也是这个漏斗真正的价值：它把候选缩小并把「10x 需要什么为真」摆到"
                 "人面前，**不承诺筛出会走完的那只**。")
    lines.append("")
    lines.append("| 报告期 | 通过 n | ≥5x% | ≥3x% | ≥2x% | 中位最大 | 对照A n | 对照A ≥5x% | 全市场 n | 全市场 ≥5x% |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    pooled = {"pass": [], "control_a": [], "market": [], "by_reason": {}}
    for result in results:
        groups = collect(result)
        for key in ("pass", "control_a", "market"):
            pooled[key].extend(groups[key])
        for key, values in groups["by_reason"].items():
            pooled["by_reason"].setdefault(key, []).extend(values)
        passed = bucket(groups["pass"])
        control = bucket(groups["control_a"])
        market = bucket(groups["market"]) if with_market else {"n": 0}
        lines.append(
            f"| {result['period']} | {passed.get('n', 0)} | {_fmt(passed, 'ge5x')} "
            f"| {_fmt(passed, 'ge3x')} | {_fmt(passed, 'ge2x')} "
            f"| {_fmt(passed, 'median_max')} | {control.get('n', 0)} "
            f"| {_fmt(control, 'ge5x')} | {market.get('n', 0)} | {_fmt(market, 'ge5x')} |")
    total_pass = bucket(pooled["pass"])
    total_control = bucket(pooled["control_a"])
    total_market = bucket(pooled["market"]) if with_market else {"n": 0}
    lines.append(f"| **合计** | {total_pass.get('n', 0)} | {_fmt(total_pass, 'ge5x')} "
                 f"| {_fmt(total_pass, 'ge3x')} | {_fmt(total_pass, 'ge2x')} "
                 f"| {_fmt(total_pass, 'median_max')} | {total_control.get('n', 0)} "
                 f"| {_fmt(total_control, 'ge5x')} | {total_market.get('n', 0)} "
                 f"| {_fmt(total_market, 'ge5x')} |")
    lines.append("")
    if pooled["pass"] and pooled["control_a"]:
        low, high = _bootstrap_median_diff(pooled["pass"], pooled["control_a"])
        if low is not None:
            lines.append(f"通过组 − 对照A 的最大涨幅中位差：95% bootstrap 区间 "
                         f"[{low}, {high}]（含 0 则不能判定）。")
    if pooled["pass"] and pooled["market"]:
        low, high = _bootstrap_median_diff(pooled["pass"], pooled["market"])
        if low is not None:
            lines.append(f"通过组 − 全市场随机：95% 区间 [{low}, {high}]。")
    lines.append("")
    lines.append("## 对照 A 分解（哪个条件刷掉的）")
    lines.append("")
    lines.append("| 刷掉原因 | n | ≥5x% | ≥3x% | 中位最大 |")
    lines.append("|---|---|---|---|---|")
    for key, values in sorted(pooled["by_reason"].items()):
        stats = bucket(values)
        lines.append(f"| {key} | {stats.get('n', 0)} | {_fmt(stats, 'ge5x')} "
                     f"| {_fmt(stats, 'ge3x')} | {_fmt(stats, 'median_max')} |")
    lines.append("")
    lines.append("## 敏感性（市值带 × 回看阈值，全格子照报）")
    lines.append("")
    lines.append("| 市值带 | 回看阈值 | n | ≥5x% | ≥3x% | ≥2x% | 中位最大 |")
    lines.append("|---|---|---|---|---|---|---|")
    for cell in sensitivity(results):
        lines.append(f"| {cell['band']} | {cell['runup_max']} | {cell.get('n', 0)} "
                     f"| {_fmt(cell, 'ge5x')} | {_fmt(cell, 'ge3x')} "
                     f"| {_fmt(cell, 'ge2x')} | {_fmt(cell, 'median_max')} |")
    lines.append("")
    lines.append("## 覆盖率")
    lines.append("")
    lines.append("| 报告期 | 池子 | 拐点有效 | 有快照 | 预筛后 | 有行情 |")
    lines.append("|---|---|---|---|---|---|")
    for result in results:
        cov = result.get("coverage", {})
        lines.append(f"| {result['period']} | {cov.get('pool', '—')} "
                     f"| {cov.get('typed', '—')} | {cov.get('snapshots', '—')} "
                     f"| {cov.get('prefiltered', '—')} | {cov.get('with_prices', '—')} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="高赔率漏斗历史基础率")
    parser.add_argument("--period", action="append")
    parser.add_argument("--market-sample", type=int, default=200,
                        help="对照 B 每期随机样本数（0 关闭）")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重跑")
    args = parser.parse_args()
    periods = tuple(args.period) if args.period else PERIODS

    results = []
    for period in periods:
        started = time.time()
        result = run_period(period, market_sample=args.market_sample,
                            workers=args.workers, use_cache=not args.refresh)
        results.append(result)
        cov = result.get("coverage", {})
        passed = sum(1 for h in result.get("hits", []) if not h.get("failed"))
        print(f"{period}: 拐点有效 {cov.get('typed', 0)}，通过 {passed}，"
              f"行情 {cov.get('with_prices', 0)}/{cov.get('prefiltered', 0)}"
              f"（{time.time() - started:.0f}s）", flush=True)

    text = report(results, with_market=bool(args.market_sample))
    target = ROOT / "docs" / "odds-v1.md"
    target.write_text(text + "\n", encoding="utf-8")
    print(f"\n已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
