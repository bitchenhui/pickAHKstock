"""命令行入口：发现候选 → 生成卡片草稿 → 校验 → 渲染。

用法：
  python -m research.cli discover --period 2026-09-30 --limit 8
  python -m research.cli new --period 2026-09-30 --codes 603991,002132
  python -m research.cli check A 002132
  python -m research.cli show A 002132
  python -m research.cli list
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .card import CARD_DIR, Card, blocking, validate
from .discover import Candidate, discover
from .ths import KEY_SUBJECTS
from .workflow import render_markdown, scaffold


def _print_candidates(candidates: list[Candidate]) -> None:
    if not candidates:
        print("没有符合筛选条件的候选。缺失不等于没有机会——先核对数据源与筛选口径。")
        return
    print(f"候选 {len(candidates)} 只（按变化幅度排序，仅为研究顺序）\n")
    for candidate in candidates:
        cap = f"{candidate.market_cap / 1e8:.0f}亿" if candidate.market_cap else "—"
        print(f"{candidate.code} {candidate.name:8s} {candidate.headline:14s} "
              f"市值{cap:>7s} 成交{candidate.turnover / 1e8:>6.2f}亿 "
              f"价{candidate.price:>7.2f} 公告 {candidate.notice_date}")
        print(f"    指标 {candidate.metric}")
        print(f"    出处 {candidate.pdf_url or candidate.source_url}")
        if candidate.reason:
            print(f"    原因 {candidate.reason[:100]}")
        for note in candidate.notes:
            print(f"    ⚠ {note}")
        print()


def cmd_discover(args: argparse.Namespace) -> int:
    _print_candidates(discover(args.period, args.since_days, args.limit))
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    wanted = {c.strip() for c in args.codes.split(",") if c.strip()}
    # 指定代码时要多取一些候选，否则按幅度排序的目标可能落在取样窗口之外。
    pool = max(args.limit, len(wanted) * 5, 20)
    candidates = discover(args.period, args.since_days, pool)
    picked = [c for c in candidates if c.code in wanted] if wanted else candidates[:args.limit]
    if not picked:
        print("没有匹配的候选。")
        return 1
    for candidate in picked:
        card = scaffold(candidate)
        path = card.save()
        print(f"{candidate.code} {candidate.name} → {path}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    card = Card.load(args.market, args.code)
    findings = validate(card)
    level = card.derived_level()
    print(f"{card.name}（{card.market} {card.code}）状态={card.status} 结论等级={level}")
    if not findings:
        print("校验通过，没有阻塞项。")
        return 0
    for finding in findings:
        print(" ", finding)
    if blocking(findings):
        print(f"\n{len(blocking(findings))} 项阻塞：当前状态不可推进，也不能进入持仓研究。")
        return 2
    print(f"\n{len(findings)} 项警告，无阻塞。")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    card = Card.load(args.market, args.code)
    print(render_markdown(card))
    return 0


def cmd_financials(args: argparse.Namespace) -> int:
    """取同花顺 F10 财务主表写入卡片，并打印供人工判断的摘要。

    只取数，不下判断：path / recurring_judgment / cyclical_judgment 仍要人写。
    """
    from .ths import fetch_financials, key_series

    card = Card.load(args.market, args.code)
    data = fetch_financials(args.code)
    track = card.financials
    track.source = data["source"]
    track.periods = list(data["periods"])[:args.periods]
    track.quarter_periods = list(data.get("simple_periods", []))[:args.quarter_periods]
    # 只保留展示区间内的报告期：卡片要能被人读完，不能把 F10 全历史塞进来
    track.metrics = {name: {p: series[p] for p in track.periods if p in series}
                     for name, series in key_series(data, subjects=tuple(KEY_SUBJECTS)).items()}
    track.quarter_metrics = {
        name: {p: series[p] for p in track.quarter_periods if p in series}
        for name, series in key_series(data, block="simple", subjects=tuple(KEY_SUBJECTS)).items()}
    track.notes = [n for n in track.notes if not n.startswith("数据已于")]
    track.notes.append(f"数据已于 {date.today().isoformat()} 取自同花顺 F10 财务主表")
    card.updated = date.today().isoformat()
    card.save()
    print(f"{card.name}（{card.market} {card.code}）财务轨迹已更新：{track.source}")
    print(f"报告期：{' | '.join(track.periods)}\n")
    for name, series in track.metrics.items():
        cells = [f"{period}={series.get(period, '-')}" for period in track.periods]
        print(f"{name:18s} " + " | ".join(cells))
    if track.quarter_metrics:
        print("\n单季度口径：")
        for name in ("营业总收入", "净利润", "扣非净利润", "销售毛利率", "每股经营现金流"):
            series = track.quarter_metrics.get(name)
            if not series:
                continue
            cells = [f"{period}={series.get(period, '-')}" for period in track.quarter_periods]
            print(f"{name:18s} " + " | ".join(cells))
    print("\n待人工判断（工具不代填）：")
    print(f"  路径 path                : {track.path or '（未填）'}  → 已盈利 / 未盈利")
    print("  recurring_judgment      : 收入/毛利/现金流的变化是否由可重复的业务带来")
    print("  cyclical_judgment       : 景气反弹还是长期竞争力提升")
    print("  working_capital_note    : 应收、库存、资本开支是否与增长匹配")
    print("  path_evidence           : （未盈利路径）现金续航、里程碑、融资情景")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    if not CARD_DIR.exists():
        print("还没有卡片。")
        return 0
    for path in sorted(CARD_DIR.glob("*.json")):
        card = Card.from_dict(json.loads(path.read_text(encoding="utf-8")))
        findings = validate(card)
        mark = "✗" if blocking(findings) else "✓"
        print(f"{mark} {card.market} {card.code} {card.name:8s} "
              f"状态={card.status:6s} 等级={card.derived_level():6s} 阻塞={len(blocking(findings))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser("discover", help="按经营变化发现候选")
    p_discover.add_argument("--period", default="2026-09-30")
    p_discover.add_argument("--since-days", type=int, default=90)
    p_discover.add_argument("--limit", type=int, default=10)
    p_discover.set_defaults(func=cmd_discover)

    p_new = sub.add_parser("new", help="从候选生成卡片草稿")
    p_new.add_argument("--period", default="2026-09-30")
    p_new.add_argument("--since-days", type=int, default=90)
    p_new.add_argument("--codes", default="", help="逗号分隔的代码；留空则取前 N 只")
    p_new.add_argument("--limit", type=int, default=3)
    p_new.set_defaults(func=cmd_new)

    p_check = sub.add_parser("check", help="校验卡片")
    p_check.add_argument("market")
    p_check.add_argument("code")
    p_check.set_defaults(func=cmd_check)

    p_show = sub.add_parser("show", help="渲染卡片")
    p_show.add_argument("market")
    p_show.add_argument("code")
    p_show.set_defaults(func=cmd_show)

    p_list = sub.add_parser("list", help="列出所有卡片")
    p_list.set_defaults(func=cmd_list)

    p_fin = sub.add_parser("financials", help="取同花顺财务主表写入卡片（只取数，不下判断）")
    p_fin.add_argument("market")
    p_fin.add_argument("code")
    p_fin.add_argument("--periods", type=int, default=8, help="保留最近几个报告期")
    p_fin.add_argument("--quarter-periods", type=int, default=8, help="保留最近几个单季度")
    p_fin.set_defaults(func=cmd_financials)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
