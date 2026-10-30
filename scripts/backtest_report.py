# -*- coding: utf-8 -*-
"""定期回测报告：把八期检验的结论固化成可重复跑的脚本。

用法：
    .venv/bin/python scripts/backtest_report.py            # 打印到终端
    .venv/bin/python scripts/backtest_report.py --append   # 追加进 docs/backtest-log.md

为什么要有这个：结论不该只活在一份文档里。每月重跑一次并把结果追加进
日志，结论会自己更新——而不是每次都要有人想起来重跑几百页缓存。
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.backtest import (_compare, _prepare, forecasts_raw,  # noqa: E402
                               primary_rows)
from research.portfolio import PortfolioConfig, run_portfolio_test  # noqa: E402

PERIODS = ("2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
LOG = ROOT / "docs" / "backtest-log.md"
THRESHOLDS = (30.0, 50.0, 100.0)
HOLDS = (20, 60)
CONFIG = PortfolioConfig(initial_capital=100000.0, max_positions=20, position_pct=0.05,
                        cash_floor_pct=0.2, hold_days=60)


def event_study() -> tuple[list[str], dict]:
    lines = ["### 事件研究（相对沪深300超额中位数 %）", "",
             "| 报告期 | 门槛 | 持有 | 超额 | n | 95% 区间 |", "|---|---|---|---|---|---|"]
    values: dict[float, list] = {t: [] for t in THRESHOLDS}
    for period in PERIODS:
        raw = forecasts_raw(date(*map(int, period.split("-"))))
        if not raw:
            lines.append(f"| {period} | （数据缺失） | | | | |")
            continue
        primary = primary_rows(raw)
        for hold in HOLDS:
            for threshold in THRESHOLDS:
                prepared, _, index = _prepare(primary, (hold,), 150, 20261029)
                stats, excess = _compare(prepared, threshold, hold, index)
                value = excess.get("相对沪深300超额（中位数）")
                if value is not None:
                    values[threshold].append(value)
                lines.append(f"| {period} | {threshold:.0f}% | {hold} | "
                             f"{'—' if value is None else f'{value:+.2f}'} | "
                             f"{stats.get('强预告组', {}).get('n', '—')} | "
                             f"{excess.get('超额 95% 区间', '—')} |")
    lines.append("")
    for threshold, items in values.items():
        if items:
            lines.append(f"- 门槛 {threshold:.0f}%：{len(items)} 格中位 "
                         f"{statistics.median(items):+.2f}，"
                         f"{sum(1 for v in items if v > 0)} 格为正")
    return lines, values


def portfolio() -> list[str]:
    lines = ["### 组合层（八期合并，20 只 × 5%，含可买性过滤）", ""]
    result = run_portfolio_test(periods=PERIODS, threshold=30.0, min_amount=5e7,
                                sort_key="amount_desc", prefilter_affordability=True,
                                config=CONFIG)
    if "error" in result:
        return lines + [f"（{result['error']}）"]
    event, base = result["event"], result["baseline"]
    lines += [f"事件 {result['events']} 个｜同期沪深300 {result['index_return'] * 100:+.2f}%", "",
              "| 组合 | 总收益 | 最差回撤 | 现金占比 | 交易 | 覆盖率 | 选中中位 | 全池中位 |",
              "|---|---|---|---|---|---|---|---|"]
    for label, item in (("强预告组合", event), ("随机基线组合", base)):
        lines.append(f"| {label} | {item['total_return']:+.2f}% | {item['max_drawdown']:.2f}% | "
                     f"{item['cash_ratio']:.1f}% | {item['trades']} | {item['coverage']:.1f}% | "
                     f"{item['selection_median']}% | {item['pool_median']}% |")
    return lines


def per_period() -> list[str]:
    lines = ["### 分期拆解（每期独立起算 10 万）", "",
             "| 报告期 | 强预告 | 最差回撤 | 交易 | 随机基线 | 基线回撤 | 差 |",
             "|---|---|---|---|---|---|---|"]
    total_e = total_b = 0.0
    wins = 0
    for period in PERIODS:
        result = run_portfolio_test(periods=(period,), threshold=30.0, min_amount=5e7,
                                    sort_key="amount_desc", prefilter_affordability=True,
                                    config=CONFIG)
        if "error" in result:
            continue
        event, base = result["event"], result["baseline"]
        total_e += event["total_return"]
        total_b += base["total_return"]
        wins += 1 if event["total_return"] >= base["total_return"] else 0
        lines.append(f"| {period} | {event['total_return']:+.2f}% | "
                     f"{event['max_drawdown']:.2f}% | {event['trades']} | "
                     f"{base['total_return']:+.2f}% | {base['max_drawdown']:.2f}% | "
                     f"{event['total_return'] - base['total_return']:+.1f} |")
    lines += ["", f"八期加总：强预告 {total_e:+.2f}%｜随机基线 {total_b:+.2f}%｜"
                  f"差 {total_e - total_b:+.2f}｜不劣于基线的期数 {wins}/{len(PERIODS)}"]
    return lines


def build_report() -> str:
    today = date.today().isoformat()
    lines = [f"## 回测报告 {today}", "",
             "口径：新发现层口径（主指标按归母优先）、绝对额标记、20 只 × 5%、"
             "含可买性过滤、10 万本金、含佣金印花税与滑点。", ""]
    study, _values = event_study()
    lines += study + [""]
    lines += portfolio() + [""]
    lines += per_period() + [""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成定期回测报告")
    parser.add_argument("--append", action="store_true", help="追加进 docs/backtest-log.md")
    args = parser.parse_args()
    report = build_report()
    print(report)
    if args.append:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if not LOG.exists():
            LOG.write_text("# 回测日志\n\n每次定期跑的结果追加在这里，结论会自己更新。\n\n",
                          encoding="utf-8")
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(report + "\n")
        print(f"已追加到 {LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
