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
from pathlib import Path
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
    print(f"候选 {len(candidates)} 只（买不进／一次性因素／低基数在前，同档内按公告日新的"
          "先看；这是研究顺序，不是收益排序——完整池的检验里随机反而最好，"
          "见 docs/backtest-v5.md）\n")
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
    candidates, stats = discover(args.period, args.since_days, args.limit)
    _print_candidates(candidates)
    print(f"预告池 {stats.get('pool', '—')} 家｜其中幅度达标 {stats.get('strong', '—')} 家"
          f"｜通过可研究性筛选 {stats.get('passed', '—')} 只")
    print(f"低基数噪音标记（已降权不剔除）：{stats.get('small_amount', 0)} 条")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    wanted = {c.strip() for c in args.codes.split(",") if c.strip()}
    if args.market == "HK":
        from .hk import discover as hk_discover
        from .workflow import scaffold_hk

        candidates = hk_discover(since_days=args.since_days, limit=max(args.limit, len(wanted) * 3))
        picked = [c for c in candidates if c.code in wanted] if wanted else candidates[:args.limit]
        if not picked:
            print("没有匹配的港股候选。先跑 hk-discover 看有哪些。")
            return 1
        for candidate in picked:
            card = scaffold_hk(candidate)
            path = card.save()
            print(f"{candidate.code} {candidate.name}（{candidate.direction}）→ {path}")
        return 0
    # 指定代码时要多取一些候选，否则按幅度排序的目标可能落在取样窗口之外。
    pool = max(args.limit, len(wanted) * 5, 20)
    candidates, _stats = discover(args.period, args.since_days, pool)
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
    """取财务序列写入卡片：A股走同花顺 F10，港股走东方财富数据中心。

    只取数，不下判断：path / recurring_judgment / cyclical_judgment 仍要人写。
    """
    from .deepdive import fill_financials

    card = Card.load(args.market, args.code)
    source = fill_financials(card, args.periods, args.quarter_periods)
    card.updated = date.today().isoformat()
    card.save()
    track = card.financials
    print(f"{card.name}（{card.market} {card.code}）财务轨迹已更新：{source}")
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


def cmd_risks(args: argparse.Namespace) -> int:
    """扫描某只标的的风险公告（法律、监管、治理、资产质量）。"""
    from .risk import scan_risks

    card = Card.load(args.market, args.code)
    events = scan_risks(card.code, card.name, since_days=args.days, market=card.market)
    if not events:
        print(f"{card.name}（{card.market} {card.code}）：近 {args.days} 天未发现风险类公告标题"
              "（不等于没有风险：关键词扫描会漏，也读不出公告正文里的问题）")
        return 0
    print(f"{card.name}（{card.market} {card.code}）风险类公告 {len(events)} 条：\n")
    for event in events:
        print(f"  {event}")
    return 0


def cmd_hk_discover(args: argparse.Namespace) -> int:
    """港股候选发现：盈警/盈喜（--source alerts）或业绩公告（--source results）。"""
    from .hk import discover, discover_by_financials, discover_by_results

    if args.source == "results":
        candidates = discover_by_results(since_days=args.days, limit=args.limit)
        source_note = "业绩公告（强制披露，覆盖全市场）"
    elif args.source == "financials":
        candidates = discover_by_financials(args.date or "2026-06-30", limit=args.limit)
        source_note = f"财务同比变化（报告期 {args.date or '2026-06-30'}，首次抓取约 8 分钟，落盘缓存）"
    else:
        candidates = discover(since_days=args.days, limit=args.limit)
        source_note = "盈利警告/正面盈利预告/业绩预告（港股不强制预告，但很多公司会主动发）"
    if not candidates:
        print("没有符合条件的港股候选。缺失不等于没有机会——先核对时间窗口与公告类别。")
        return 0
    print(f"港股候选 {len(candidates)} 只（近 {args.days} 天 · {source_note}，按公告时间倒序）\n")
    for c in candidates:
        cap = f"{c.market_cap / 1e8:.0f}亿" if c.market_cap else "—"
        arrow = {"正向": "▲", "负向": "▼", "混合": "◆", "待判读": "·"}.get(c.direction, "·")
        print(f"{arrow} {c.code} {c.name:10s} {c.direction:4s} {c.event[:24]:26s} "
              f"市值{cap:>7s} 价{c.price:>8.2f} 成交{c.turnover / 1e8:>6.2f}亿 每手{c.lot_size or '—'}")
        print(f"    {c.published}｜换手 {c.turnover_rate}%｜{c.direction_note}")
        print(f"    {c.detail_url}")
        for note in c.notes:
            print(f"    ⚠ {note}")
    print("\n每手股数已从行情取到，下单前仍需按交易所资料复核；"
          "业绩公告本身不是变化信号，数字要回原文或财务接口比对。")
    return 0


def cmd_governance(args: argparse.Namespace) -> int:
    """治理与筹码结构扫描：股东户数、股权质押、诉讼、担保。"""
    from .governance import scan

    card = Card.load(args.market, args.code)
    items = scan(card.code, card.name)
    if not items:
        print(f"{card.name}（{card.market} {card.code}）：未取到治理数据。"
              "缺口不等于没有风险——巨潮这几个接口只覆盖已归集的披露，"
              "仲裁、质押存量等要靠公告扫描补。")
        return 0
    print(f"{card.name}（{card.market} {card.code}）治理与筹码：\n")
    for item in items:
        print(f"  [{item.severity}] {item.category}：{item.detail}")
        print(f"      来源 {item.source}")
    print("\n口径提醒：巨潮接口为二手归集数据，结论前需回原始公告核对；"
          "诉讼统计不含仲裁，质押只覆盖已归集的新增记录。")
    return 0


def cmd_segments(args: argparse.Namespace) -> int:
    """分产品/行业收入与毛利：把「增长从哪来」拆成四象限。"""
    from .segments import compare, fetch_segments

    rows = fetch_segments(args.code, args.market)
    if not rows:
        print(f"{args.code}：没有取到主营构成数据。")
        return 0
    periods = sorted({r.report_date for r in rows}, reverse=True)
    print(f"{args.code} 主营构成：{len(rows)} 行，最近报告期 {periods[:3]}\n")
    for kind in ("按产品", "按行业"):
        changes = compare(rows, kind)
        if not changes:
            continue
        print(f"--- {kind}（最新报告期 vs 去年同期）")
        for change in sorted(changes, key=lambda c: -(c.income_yoy or 0))[:args.limit]:
            income = "—" if change.income_yoy is None else f"{change.income_yoy:+.1f}%"
            margin = "—" if change.margin_change is None else f"{change.margin_change:+.2f}pct"
            print(f"  {change.item[:16]:18s} 收入同比 {income:>10s}  毛利率变动 {margin:>10s} | {change.verdict}")
        print()
    print("这是代理判断，不是量价拆分：真正的单价×销量只在定期报告正文里，本工具拿不到，")
    print("结论前需回原文核对；主营构成只到半年报/年报频率。")
    return 0


def cmd_business(args: argparse.Namespace) -> int:
    """港股经营回顾逐期对比：港股没有分部数字，但经营回顾正文是中文可读的。"""
    from .hk_business import diff_reviews, fetch_reviews

    reviews = fetch_reviews(args.code, args.market)
    if not reviews:
        print(f"{args.code}：没有取到经营回顾。")
        return 0
    print(f"{args.code} 经营回顾 {len(reviews)} 期（最近 {reviews[0].period}）")
    diff = diff_reviews(reviews)
    if not diff:
        print("  没有去年同期可对比。")
        return 0
    print(f"  对比 {diff.prior_period} → {diff.period}，篇幅 {diff.length_change:+d} 字")
    changed = {k: v for k, v in diff.theme_delta.items() if v}
    print(f"  主题词频变化：{changed or '无明显变化'}")
    if diff.numbers:
        print("  本期数字：")
        for item in diff.numbers[:8]:
            print(f"    · {item}")
    if diff.new_phrases:
        print("  新增表述：")
        for phrase in diff.new_phrases[:5]:
            print(f"    + {phrase[:150]}")
    if diff.dropped_phrases:
        print("  消失表述：")
        for phrase in diff.dropped_phrases[:4]:
            print(f"    - {phrase[:150]}")
    print("\n这是管理层自述，不是第三方数据；数字要回原文核对。")
    return 0


def cmd_exclude(args: argparse.Namespace) -> int:
    """把一只候选标记为「不看了」，并记下理由（可撤销）。"""
    from .excluded import exclude, unexclude

    if args.remove:
        if unexclude(args.market, args.code):
            print(f"{args.market} {args.code} 已放回待立项")
        else:
            print(f"{args.market} {args.code} 不在排除清单里")
        return 0
    item = exclude(args.market, args.code, args.reason or "")
    print(f"{args.market} {args.code} 已排除（{item.day}）"
          + (f"｜理由：{item.reason}" if item.reason else ""))
    print("待立项清单下次不再出现它；改主意用 `unexclude` 放回。")
    return 0


def cmd_board(args: argparse.Namespace) -> int:
    """研究看板：谁该被研究，按优先级排好。

    到期必看 > 临近复核 > 观察池 > 待立项。前三类由卡片上的日期与状态定，
    最后一类由发现层提议、由人决定是否立项。
    """
    from .board import build_board, new_candidates

    cards = _load_all_cards()
    items = build_board(cards, horizon_days=args.horizon)
    fresh: list = []
    if args.new:
        from .discover import discover as a_discover
        from .hk import discover as hk_discover

        try:
            a_candidates, _stats = a_discover(args.period, args.days, args.limit)
        except Exception as exc:
            print(f"A 股发现层未取到候选：{exc}")
            a_candidates = []
        try:
            hk_candidates = hk_discover(since_days=args.days, limit=args.limit)
        except Exception as exc:
            print(f"港股发现层未取到候选：{exc}")
            hk_candidates = []
        fresh = new_candidates(a_candidates, hk_candidates)
        # 已经有卡片的票不该再出现在「待立项」里——它是待复核项，不是新对象
        existing = {(card.market, card.code) for card in cards}
        fresh = [item for item in fresh
                 if (item.market, item.code) not in existing]
    if not items and not fresh:
        print("看板是空的。没有卡片，也没有新候选。")
        return 0
    current = None
    for item in items + fresh:
        if item.bucket != current:
            current = item.bucket
            print(f"\n=== {current}")
        mark = "!" if item.overdue else " "
        print(f" {mark} {item.market} {item.code} {item.name:10s} "
              f"[{item.card_status or item.status}] {item.reason}")
        if item.due:
            print(f"      到期 {item.due}")
    counts: dict[str, int] = {}
    for item in items + fresh:
        counts[item.bucket] = counts.get(item.bucket, 0) + 1
    summary = "｜".join(f"{k} {v}" for k, v in counts.items())
    try:
        from .excluded import list_exclusions

        dropped = list_exclusions()
        if dropped:
            summary += f"｜已排除 {len(dropped)}"
    except Exception:
        pass
    print("\n小结：" + summary)
    print("「到期复核」里的条目不是建议，是已经欠下的功课；「待立项」由你决定要不要立项。")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """§51 基线检验：强预告组相对对照组的持有期收益，以及参数敏感性。"""
    from .backtest import run_backtest, sensitivity

    result = run_backtest(args.period, hold_days=args.hold, threshold=args.threshold,
                          sample=args.sample, runup_filter=args.runup_filter)
    print(f"=== 事件研究｜报告期 {result.event_date}｜持有 {result.hold_days} 个交易日"
          f"｜门槛 {result.threshold:.0f}%｜池 {result.universe_size} 只")
    print("（事件日 = 各公司自己的业绩预告公告日，避免固定事件日带来的定价不对称）\n")
    for name, stats in result.groups.items():
        if not stats.get("n"):
            print(f"  {name:16s} 样本不足")
            continue
        print(f"  {name:16s} n={stats['n']:>3d} 中位 {stats['median']:>7}% 均值 {stats['mean']:>7}% "
              f"胜率 {stats['win_rate']:>5}% 最差十分位 {stats['p10']:>7}%")
    if result.excess:
        print("\n  超额：" + "｜".join(f"{k} {v}" for k, v in result.excess.items()))
    for note in result.notes:
        print(f"  注：{note}")
    if args.sensitivity:
        print("\n=== 参数敏感性（结论若在参数变动下翻转，就不算数）")
        print(f"  {'持有':>5s} {'门槛':>6s} | {'强预告 n/中位/胜率':>22s} | {'对照 n/中位/胜率':>22s} | 中位超额")
        for row in sensitivity(args.period, thresholds=(30.0, 50.0, 100.0),
                               holds=(10, 20, 60), sample=args.sample):
            strong = row["groups"].get("强预告组", {})
            control = row["groups"].get("非强预告（对照）", {})
            excess = row["excess"].get("强预告 − 对照（中位数）")
            print(f"  {row['hold_days']:>5d} {row['threshold']:>5.0f}% | "
                  f"{strong.get('n')}/{strong.get('median')}/{strong.get('win_rate')} | "
                  f"{control.get('n')}/{control.get('median')}/{control.get('win_rate')} | {excess}")
    return 0


def cmd_portfolio(args: argparse.Namespace) -> int:
    """§51 组合层检验：按交易约束跑组合，看净值、回撤、现金占比、错过率与费用。"""
    from .portfolio import PortfolioConfig, run_portfolio_test

    config = PortfolioConfig(initial_capital=args.capital, max_positions=args.max_positions,
                             position_pct=args.position_pct, cash_floor_pct=args.cash_floor,
                             hold_days=args.hold, max_drawdown=args.max_drawdown,
                             apply_drawdown_rule=not args.no_drawdown_rule,
                             force_exit_on_drawdown=args.force_exit)
    periods = ("2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
    if args.per_period:
        print("分期拆解（每期独立起算 10 万）——合并数字会掩盖收益集中在单一市场环境\n")
        print(f"{'报告期':>12s} | {'总收益':>7s} {'最差回撤':>8s} {'交易':>4s} {'覆盖率':>7s} | "
              f"{'随机基线':>8s} {'基线回撤':>8s}")
        for period in periods:
            result = run_portfolio_test(periods=(period,), threshold=args.threshold,
                                        min_amount=args.min_amount, config=config)
            if "error" in result:
                continue
            event, base = result["event"], result["baseline"]
            print(f"{period:>12s} | {event['total_return']:>6}% {event['max_drawdown']:>7}% "
                  f"{event['trades']:>4d} {event['coverage']:>6}% | {base['total_return']:>7}% "
                  f"{base['max_drawdown']:>7}%")
        print()
    result = run_portfolio_test(periods=periods, threshold=args.threshold,
                                min_amount=args.min_amount, config=config,
                                market_baseline=args.market_baseline)
    if "error" in result:
        print(result["error"])
        return 1
    event, base = result["event"], result["baseline"]
    index_return = result.get("index_return")
    print(f"组合层检验｜事件 {result['events']} 个｜本金 {config.initial_capital:,.0f}"
          f"｜最多 {config.max_positions} 只｜单只 {config.position_pct:.0%}"
          f"｜现金下限 {config.cash_floor_pct:.0%}｜持有 {config.hold_days} 日")
    print("含佣金（万2.5，最低 5 元）、印花税（千1，卖出）、双边各 0.2% 滑点、100 股整手\n")
    header = (f"{'组合':>12s} {'交易':>4s} {'总收益':>8s} {'最差回撤':>8s} {'现金占比':>8s} {'胜率':>6s} "
              f"{'费用':>9s} {'覆盖率':>7s} {'选中中位':>9s} {'全池中位':>9s}")
    print(header)
    rows = [("强预告组合", event), ("池内随机基线", base)]
    market = result.get("market_baseline")
    if market:
        rows.append(("全市场随机基线", market))
    for label, item in rows:
        print(f"{label:>12s} {item['trades']:>4d} {item['total_return']:>7}% {item['max_drawdown']:>7}% "
              f"{item['cash_ratio']:>7}% {item['win_rate']:>5}% {item['fee_total']:>9,.0f} "
              f"{item['coverage']:>6}% {str(item['selection_median']):>9s} {str(item['pool_median']):>9s}")
    if index_return is not None:
        print(f"\n同期沪深300：{index_return * 100:+.2f}%")
    print(f"因整手或资金买不到的次数：强预告 {event['skipped_unaffordable']}｜"
          f"基线 {base['skipped_unaffordable']}")
    print("\n两条基线含义不同：**池内随机**是「发了预告但变动不达标」的公司，那批本身"
          "就是坏消息集合；**全市场随机**才是「有没有 alpha」该问的基线。")
    print("\n「覆盖率」= 买到的候选 / 候选总数（受最多持 5 只与资金约束限制）；"
          "「选中中位」= 买到的那些的持有期收益中位数，「全池中位」= 全体候选的中位数——"
          "两者的差才是「挑得准不准」。「现金占比」= 平均闲置现金占本金，小资金的真实约束会体现在这里。")
    print("局限：退市与停牌样本缺失（幸存者偏差）、池子未按市值区间过滤（前视）、"
          "预告本身有选择性、只有 4 个报告期。")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    """把资本轨迹与同业对照写进卡片。"""
    from .deepdive import fill_context

    card = Card.load(args.market, args.code)
    capital, peers = fill_context(card)
    card.updated = date.today().isoformat()
    card.save()
    capital_data = card.capital
    print(f"{card.name}（{card.market} {card.code}）资本轨迹与同业对照已更新")
    print(f"  报告期 {capital.periods[0] if capital.periods else '—'}"
          f"｜资本开支 {capital_data['capex_label']}"
          f"｜经营现金流 {capital_data['operating_cf_label']}"
          f"｜自由现金流 {capital_data['fcf_label']}")
    print(f"  在建工程 {capital_data['construction_label']}｜固定资产 {capital_data['fixed_assets_label']}")
    print(f"  同业排名 {peers.rank or '—'}｜PE-TTM {peers.rows.get('市盈率-TTM') or '—'}"
          f"｜26E {peers.rows.get('市盈率-26E') or '—'}｜PB {peers.rows.get('市净率-MRQ') or '—'}")
    for note in capital.notes + peers.notes:
        print(f"  ⚠ {note}")
    return 0


def cmd_expectations(args: argparse.Namespace) -> int:
    """把分析师预期写进卡片：与公司自述并排看。"""
    from .deepdive import fill_expectations

    card = Card.load(args.market, args.code)
    expectation = fill_expectations(card)
    card.updated = date.today().isoformat()
    card.save()
    print(f"{card.name}（{card.market} {card.code}）分析师预期已更新")
    print(f"  覆盖机构 {expectation.coverage} 家｜最新评级 {expectation.latest_rating or '—'}"
          f"｜目标价 {expectation.target_price or '—'}")
    for row in expectation.eps_forecast[:2]:
        print(f"  {row.get('年度')} 年 EPS 均值 {row.get('均值')}（区间 {row.get('最小值')}–{row.get('最大值')}），"
              f"行业平均 {row.get('行业平均数')}")
    for row in expectation.ratings[:3]:
        print(f"  研报 {row.get('日期')}：{row.get('标题')}")
    for note in expectation.notes:
        print(f"  ⚠ {note}")
    return 0


def cmd_industry(args: argparse.Namespace) -> int:
    """同业共振：判断这是行业性变化还是公司自己的变化。

    --map 只看某报告期全池子的行业共振地图；给了 --code 则算这只票的共振并写进卡片。
    """
    from datetime import date as _date

    from .backtest import forecasts_by_report_period, primary_rows
    from .deepdive import fill_industry
    from .industry import resonance_report

    if args.map:
        period = _date.fromisoformat(args.period)
        rows = primary_rows(forecasts_by_report_period(period))
        if not rows:
            print(f"{period} 报告期没有取到预告数据")
            return 1
        report = resonance_report(period, rows, threshold=args.threshold)
        print(f"报告期 {period}｜预告池 {report['pool']} 家"
              f"｜业绩改善 {report['up_pool']} 家（占 {report['market_up_rate']}%）"
              f"｜全市场预告渗透率 {report['market_penetration']}%\n")
        print("行业改善共振（相对倍数 = 行业改善率 / 全市场改善率）")
        print(f"{'行业':<14}{'发预告':>6}{'行业家数':>8}{'改善':>6}{'恶化':>6}"
              f"{'改善率%':>9}{'相对倍数':>9}{'渗透率%':>9}")
        for item in report["industries"][:15]:
            print(f"{item['industry']:<14}{item['with_forecast']:>6}{item['members']:>8}"
                  f"{item['up']:>6}{item['down']:>6}{item['up_rate']:>9}"
                  f"{item['lift']:>9}{item['penetration']:>9}")
        print(f"\n口径：业绩预告只对大幅变动的公司强制披露，所以池子里的每一家都已是一次自选择；"
              f"这里比较的是**方向是否一致**，不是幅度大小。"
              f"来源：{report['source']}")
        return 0

    card = Card.load(args.market, args.code)
    resonance = fill_industry(card, period=args.period,
                              threshold=args.threshold, level=args.level)
    card.updated = date.today().isoformat()
    card.save()
    print(f"{card.name}（{card.market} {card.code}）同业共振已更新")
    if isinstance(resonance, dict):        # 港股口径：字段少，只能内部横比
        print(f"  行业（东财口径）：{resonance['industry'] or '—'}")
        print(f"  同行业发预告 {resonance['peers_with_forecast']} 家，其中同向 "
              f"{resonance['peers_same_sign']} 家（正向 {resonance['peers_up']}／负向 {resonance['peers_down']}）")
        print(f"  判定：**{resonance['verdict']}**　{resonance['note']}")
        print(f"  {resonance['caveat']}")
        return 0
    print(f"  报告期 {resonance.report_period}｜申万{ {'first': '一级', 'second': '二级'}[args.level] }行业：{resonance.industry or '—'}"
          f"（行业 {resonance.industry_members} 家）")
    print(f"  同行业发预告 {resonance.peers_with_forecast} 家，其中同向 "
          f"{resonance.peers_same_sign} 家（改善 {resonance.peers_up}／恶化 {resonance.peers_down}）")
    print(f"  同向率 {resonance.same_sign_rate * 100:.1f}%｜全市场同向率 "
          f"{resonance.market_same_sign_rate * 100:.1f}%｜相对倍数 {resonance.lift:.2f}")
    print(f"  行业预告渗透率 {resonance.penetration * 100:.1f}%｜全市场 {resonance.market_penetration * 100:.1f}%"
          f"｜行业同向幅度中位 {resonance.median_change_industry}%／全市场 {resonance.median_change_market}%")
    print(f"  判定：**{resonance.verdict}**　{resonance.note}")
    if resonance.same_sign_peers:
        print("  同向的同业：" + "、".join(
            f"{p['code']}({p['change']:+.0f}%)" for p in resonance.same_sign_peers[:8]))
    return 0


def cmd_recommend(args: argparse.Namespace) -> int:
    """推荐清单：先看哪个、为什么、下一步做什么。"""
    from .analysts import fetch as fetch_expectations
    from .discover import discover
    from .recommend import build, render

    capital = args.capital
    position_pct = args.position_pct
    if not capital:
        # 账户规模是发现层的一等输入：不给数字就按 account.json 里的算，
        # 否则「买不买得进」这一栏永远是空的，等于没做
        account_file = Path(__file__).resolve().parent.parent / "account.json"
        if account_file.exists():
            try:
                account = json.loads(account_file.read_text(encoding="utf-8"))
                capital = float(account.get("capital_cny") or 0)
                position_pct = float(account.get("max_position_pct") or position_pct)
            except (ValueError, OSError):
                pass
    candidates, stats = discover(args.period, args.since_days, args.limit,
                                capital=capital, position_pct=position_pct)
    if not candidates:
        print("没有符合条件的候选。缺失不等于没有机会——先核对数据源与筛选口径。")
        return 0
    recommendations = build(candidates, with_analysts=not args.no_analysts)
    scope = f"账户本金 {capital:,.0f} 元、单只上限 {position_pct:.0%}" if capital else "未设账户"
    print(f"推荐清单 {len(recommendations)} 只（报告期 {args.period}｜{scope}）\n")
    print(render(recommendations))
    print("这是「该看哪几张卡片」的清单，不是买入名单，也不是「先买哪个」的排序——"
          "完整池上的检验里，候选内部的四种排序**随机反而最好**"
          "（见 docs/backtest-v5.md），排序只是可读性安排。")
    print("卡片状态、证据等级与账户检查才决定能否进入持仓研究；"
          "无机构覆盖与买不进的候选已单独标出。")
    return 0


def cmd_odds(args: argparse.Namespace) -> int:
    """高赔率候选清单：结构条件筛选 + 「10 倍需要什么为真」的拆解。

    结构条件 ≠ 上涨概率：这是把「1 年内 5-10 倍在结构上需要什么前提」摆到
    人面前，历史基础率见 docs/odds-v1.md。
    """
    from datetime import date as _date
    from datetime import timedelta as _timedelta

    from .backtest import (forecasts_by_report_period, load_prices,
                           prefetch_prices, primary_rows)
    from .excluded import is_excluded
    from .odds import (DEFAULT_TARGET_PE, evaluate, inflection_type,
                       load_snapshots, prefilter_cap, screen)

    report_date = _date(*map(int, args.period.split("-")))
    rows = primary_rows(forecasts_by_report_period(report_date))
    typed = [row for row in rows if inflection_type(row) != "其他"]
    if not typed:
        print(f"{args.period} 报告期没有扭亏/减亏/预增类预告行。")
        return 0
    codes = sorted({str(row.get("SECURITY_CODE") or "").strip() for row in typed
                    if str(row.get("SECURITY_CODE") or "").strip()})
    notices = sorted({str(row.get("NOTICE_DATE") or "")[:10] for row in typed
                      if str(row.get("NOTICE_DATE") or "")[:10]})
    snapshots = load_snapshots([("A", code) for code in codes])
    prefiltered = prefilter_cap(snapshots, codes, args.cap_min * 1e8, args.cap_max * 1e8)
    start = _date.fromisoformat(notices[0]) - _timedelta(days=400)
    end = _date.fromisoformat(notices[-1]) + _timedelta(days=20)
    if prefiltered:
        prefetch_prices(prefiltered, start, end)
    prices_map = {code: load_prices(code, start, end) for code in prefiltered}
    hits = screen(typed, snapshots, prices_map,
                  cap_min=args.cap_min * 1e8, cap_max=args.cap_max * 1e8,
                  runup_max=args.runup_max, target_pe=args.target_pe)
    visible = [hit for hit in hits if not is_excluded("A", hit.code)]
    skipped = len(hits) - len(visible)

    print(f"高赔率候选 {len(visible)} 只（报告期 {args.period}｜公告日市值 "
          f"{args.cap_min:.0f}-{args.cap_max:.0f} 亿｜近一年涨幅 <{args.runup_max * 100:.0f}%"
          f"｜扭亏/减亏/预增）")
    print(f"池子 {len(rows)} 行 → 拐点有效 {len(typed)} → 快照 {len(snapshots)} → "
          f"预筛带内 {len(prefiltered)} → 通过 {len(hits)}"
          + (f"（另跳过已排除 {skipped}）" if skipped else ""))
    print("\n**这是结构条件筛选，不是上涨概率预测**：1 年内 5-10 倍是极小概率事件，"
          "历史基础率与两组对照见 docs/odds-v1.md。")
    print("「10x 需要什么为真」= 走完倍数所需的年化正常化利润（市值×10/目标PE），"
          "与当前预告轨迹的差距——差距是可核对的事实，不是预测。\n")

    order = {"扭亏": 0, "减亏": 1, "预增": 2}
    for hit in sorted(visible, key=lambda h: (order.get(h.inflection, 9),
                                              -(h.cap_at or 0)))[:args.limit]:
        cap = f"{hit.cap_at / 1e8:.0f}亿" if hit.cap_at else "—"
        runup = f"{(hit.runup or 0) * 100:+.0f}%" + ("（短窗口）" if hit.short_lookback else "")
        print(f"{hit.inflection} {hit.code} {hit.name:10s} 公告 {hit.notice}"
              f"｜市值 {cap}｜近一年 {runup}")
        if hit.predict_lower is not None:
            track = f"预告下限 {hit.predict_lower / 1e8:.2f} 亿"
            if hit.annualized:
                track += f"（年化 {hit.annualized / 1e8:.2f} 亿）"
            print(f"    {track}")
        if hit.needed_10x:
            needed = f"10x 需要年化利润 {hit.needed_10x / 1e8:.0f} 亿（{hit.target_pe:.0f}x PE）"
            if hit.gap_10x:
                needed += f"，当前轨迹差 {hit.gap_10x:.1f} 倍"
            else:
                needed += "；尚无正利润基准，须人工补盈利路径与融资情景"
            print(f"    {needed}")
    if len(visible) > args.limit:
        print(f"\n（还有 {len(visible) - args.limit} 只未展示；排序只是可读性安排，"
              "不是「先买哪个」——候选内部排序已被检验否定，见 docs/backtest-v5.md）")
    print("\n每只都要走卡片流程（单季拆解、行业共振、治理与风险、反证），"
          "仓位按账户层小仓多注（20×5%）执行；这一层不产生「先买哪个」。")
    return 0


def cmd_deep_dive(args: argparse.Namespace) -> int:
    """一键灌卡：一张卡片补齐全部数据面，人只做判断题。

    行情/财务/参考线/预期/资本轨迹/行业共振写进卡片；风险公告、治理
    （或港股经营回顾）作为原始扫描打印出来——那是需要人过目的材料，
    不是结论。判断字段（path/情景/反证等）工具不代填，check 会拦。
    """
    from .deepdive import deep_dive, scan_outputs

    result = deep_dive(args.market, args.code, period=args.period,
                       with_scan=not args.no_scan, days=args.days)
    card = result.card
    marks = {"ok": "✓", "fail": "✗", "skip": "·", "scan": "▸"}
    print(f"{card.name}（{card.market} {card.code}）deep-dive 完成，卡片已保存：\n")
    for step in result.steps:
        note = f" — {step.note}" if step.note and step.status in ("fail", "skip") else ""
        print(f"  {marks.get(step.status, ' ')} {step.face}{note}")
    if not args.no_scan:
        print("\n── 以下需要人工过目（原始扫描，不是结论）──\n")
        for title, lines in scan_outputs(card, days=args.days):
            print(title)
            for line in lines:
                print(line)
            print()
    track = card.financials
    print("待人工判断（工具不代填，check 会拦）：")
    print(f"  路径 path           : {track.path or '（未填）'}  → 已盈利 / 未盈利")
    print("  recurring_judgment  : 收入/毛利/现金流的变化是否由可重复的业务带来")
    print("  cyclical_judgment   : 景气反弹还是竞争力提升")
    print("  情景（基准/乐观/失败）与反证、失效条件：写不出来就说明这一关没过")
    print(f"\n下一步：python -m research.cli show {card.market} {card.code}"
          f" ｜ check {card.market} {card.code}")
    return 1 if any(s.status == "fail" for s in result.steps) else 0


def cmd_note(args: argparse.Namespace) -> int:
    """单张卡片 → 小红书笔记样式 HTML（只渲染卡片里已有的数据）。"""
    from .note import note_fragment, save_document

    card = Card.load(args.market, args.code)
    fragment = note_fragment(card)
    from pathlib import Path as _Path

    default = _Path(__file__).resolve().parent.parent / "cards" / "notes" / \
        f"{card.market}_{card.code}.html"
    path = _Path(args.out) if args.out else default
    save_document(fragment, path, f"{card.name}（{card.market} {card.code}）研究笔记")
    print(f"{card.name}（{card.market} {card.code}）笔记 → {path}")
    print("只渲染卡片里已有的数据：缺的面（如未 deep-dive 的扫描留痕）不会编造。")
    return 0


def cmd_notebook(args: argparse.Namespace) -> int:
    """全部卡片 → 一页小红书信息流式汇总 HTML。"""
    from pathlib import Path as _Path

    from .note import notebook_fragment, save_document

    cards = _load_all_cards()
    if not cards:
        print("还没有卡片。")
        return 0
    fragment = notebook_fragment(cards)
    default = _Path(__file__).resolve().parent.parent / "cards" / "notes" / "index.html"
    path = _Path(args.out) if args.out else default
    save_document(fragment, path, "小资金研究台 · 观察池信息流", dark=True)
    order = {"持仓研究": 0, "观察": 1, "降级": 2, "淘汰": 3}
    ranked = sorted(cards, key=lambda c: (order.get(c.status, 9), c.code))
    print(f"研究台汇总 {len(ranked)} 张卡片 → {path}")
    for card in ranked:
        print(f"  {card.status:4s} {card.market} {card.code} {card.name}")
    return 0


def cmd_sentinel(args: argparse.Namespace) -> int:
    """哨兵：对照行动条件检查全部卡片，命中触发器则推通知。

    检查的是低频数据（快照/日线/盯价），但可以每小时跑一次。
    未配置任何通知渠道时只打印不发——先把触发器摆到眼前。
    """
    from .sentinel import check_cards, notify_alerts

    cards = _load_all_cards()
    if not cards:
        print("还没有卡片。")
        return 0
    alerts, checked = check_cards(cards)
    print(f"哨兵检查 {checked} 张卡片：命中 {len(alerts)} 条触发器\n")
    for alert in alerts:
        print(f"  {alert.line()}")
    if not alerts:
        print("  （无触发：参考线、相对强度、催化剂、盯价都安静）")
        return 0
    results = notify_alerts(alerts)
    if results:
        print("\n通知：")
        for line in results:
            print(f"  {line}")
    else:
        print("\n未配置通知渠道（环境变量/Secrets），只打印未推送。")
    return 0


def cmd_backtest_report(args: argparse.Namespace) -> int:
    """定期回测报告：跑八期事件研究 + 组合层 + 分期拆解。"""
    import runpy

    script = Path(__file__).resolve().parent.parent / "scripts" / "backtest_report.py"
    argv = ["backtest_report.py"] + (["--append"] if args.append else [])
    runpy.run_path(str(script), run_name="__main__")
    return 0


def cmd_card_snapshot(args: argparse.Namespace) -> int:
    """给所有卡片打快照——卡片检验集的起点。

    「卡片状态 vs 后续表现」是前瞻检验，卡片建了多久就有多少数据可评。
    """
    from datetime import date

    from .backtest import load_prices
    from .tracking import record

    cards = _load_all_cards()
    if not cards:
        print("还没有卡片。")
        return 0
    today = date.today()

    def price_lookup(code: str, market: str):
        series = load_prices(code, today - __import__("datetime").timedelta(days=40),
                             today + __import__("datetime").timedelta(days=10))
        return series[-1]["close"] if series else None

    snapshots = record(cards, today, price_lookup)
    print(f"已记录 {len(snapshots)} 张卡片快照（{today.isoformat()}）→ cards/_snapshots/")
    from .analysts import snapshot as expectation_snapshot
    for card in cards:
        try:
            expectation_snapshot(card.code, card.name, card.market)
        except Exception:
            continue
    print("已同时记录分析师预期快照（预期变化要靠积累，快照不足两份时不会硬算）")
    for snapshot in snapshots:
        price = "—" if snapshot.price is None else f"{snapshot.price:.2f}"
        print(f"  {snapshot.market} {snapshot.code} {snapshot.name:10s} [{snapshot.status}] "
              f"价 {price} 证据 {snapshot.evidence_count} 条 哈希 {snapshot.card_hash}")
    print("\n检验集从今天开始积累。到 60 个交易日后运行 card-score 即可回看"
          "「标记为观察/降级的那些，后来表现如何」。")
    return 0


def cmd_card_score(args: argparse.Namespace) -> int:
    """回看卡片检验集：快照时的状态 vs 后续 20/60 日表现。"""
    from datetime import date, timedelta

    from .backtest import index_bars, load_prices
    from .tracking import load_snapshots, score

    snapshots = load_snapshots()
    if not snapshots:
        print("还没有快照。先运行 card-snapshot 开始积累。")
        return 0
    today = date.today()
    price_series: dict[str, list] = {}
    for snapshot in snapshots:
        if snapshot.code not in price_series:
            start = date.fromisoformat(snapshot.day) - timedelta(days=40)
            price_series[snapshot.code] = load_prices(snapshot.code, start,
                                                      today + timedelta(days=10))
    # 基准：同期沪深300。没有基准，「后续跌 3%」这件事没法判断好坏。
    first_day = min(date.fromisoformat(s.day) for s in snapshots)
    index_series = index_bars(first_day - timedelta(days=40), today + timedelta(days=10))
    result = score(snapshots, price_series, index_series=index_series)
    print(f"快照 {len(result['rows'])} 个（快照日 {min(s.day for s in snapshots)} 起）"
          f"｜基准：沪深300\n")
    print(f"{'状态':>8s} {'n':>4s} {'可评':>4s} {'60日中位':>9s} {'60日胜率':>9s} "
          f"{'超额中位':>9s} {'跑赢':>6s}")
    for status, item in result["summary"].items():
        median = "—" if item.get("median_60") is None else f"{item['median_60']}%"
        win = "—" if item.get("win_rate_60") is None else f"{item['win_rate_60']}%"
        excess = "—" if item.get("excess_60") is None else f"{item['excess_60']}%"
        beat = item.get("beat_market_60")
        scored = item.get("scored_60", 0)
        print(f"{status:>8s} {item['n']:>4d} {scored:>4d} {median:>9s} {win:>9s} "
              f"{excess:>9s} {'—' if beat is None else f'{beat}/{scored}':>6s}")
    if index_series:
        print("\n「超额中位」= 相对同期沪深300；「跑赢」= 跑赢基准的卡片数/可评卡片数。"
              "\n样本极小，这里给的是可数的读数，不是统计结论——检验集要靠卡片自己长大。")
    print("\n逐张卡片（样本小的时候，逐条比分组有意义）：")
    print(f"{'卡片':>16s} {'快照日':>12s} {'状态':>6s} {'20日':>8s} {'超额':>8s} "
          f"{'60日':>8s} {'超额':>8s}")
    for row in sorted(result["rows"], key=lambda r: (r["day"], r["code"])):
        def fmt(key):
            value = row.get(key)
            return "—" if value is None else f"{value:+.2f}%"
        print(f"{row['name'][:15]:>16s} {row['day']:>12s} {row['status']:>6s} "
              f"{fmt('return_20'):>8s} {fmt('excess_20'):>8s} "
              f"{fmt('return_60'):>8s} {fmt('excess_60'):>8s}")
    pending = [r for r in result["rows"] if r["return_60"] is None]
    if pending:
        print(f"\n尚有 {len(pending)} 个快照未满 60 个交易日——检验集还没长成。"
              "这正是它需要时间的原因：卡片建了两天，就只能评两天。")
    return 0


def cmd_reference(args: argparse.Namespace) -> int:
    """用同花顺日线算 ATR 与 2ATR 参考失效线，写入卡片的市场数据段。

    这是参考线，不是止损保证：跳空、停牌、涨跌停都可能让参考价无法成交。
    """
    from .deepdive import fill_reference

    card = Card.load(args.market, args.code)
    levels = fill_reference(card)
    card.updated = date.today().isoformat()
    card.save()
    print(f"{card.name}（{card.market} {card.code}）参考线已更新")
    print(f"  收盘 {levels['close']}｜ATR {levels['atr']}（{levels['atr_pct']}%）"
          f"｜2ATR 参考失效线 {levels['stop_reference']}")
    print(f"  样本 {levels['bars']} 根日线，截至 {levels['last_day']}（{levels['source']}）")
    return 0


def _load_all_cards() -> list[Card]:
    if not CARD_DIR.exists():
        return []
    cards = []
    for path in sorted(CARD_DIR.glob("*.json")):
        # 下划线开头的是辅助文件（_excluded.json 等），不是卡片
        if path.name.startswith("_"):
            continue
        cards.append(Card.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return cards


def cmd_account_show(args: argparse.Namespace) -> int:
    from .account import Account, AccountError

    try:
        account = Account.load()
    except AccountError as exc:
        print_error(str(exc))
        return 1
    print(f"权益合计（人民币口径）{account.equity():,.0f}｜持仓市值 {account.position_value():,.0f}")
    print(f"A 股可用现金 {account.cash('A'):,.0f} CNY｜港股可用现金 {account.cash('HK'):,.0f} HKD（两地资金不自动换汇）")
    print(f"回撤 {account.drawdown() * 100:.1f}%（上限 {account.max_drawdown_pct * 100:.0f}%）"
          f" → 风险状态：{account.drawdown_state()}")
    if not account.positions:
        print("持仓：无")
    for position in account.positions:
        print(f"  {position.market} {position.code} {position.name} "
              f"{position.shares} 股 @ {position.cost} {position.currency}"
              f"{'｜' + position.sector if position.sector else ''}")
    if not account.confirmed_by_user:
        print("\n⚠ 参数尚未确认（confirmed_by_user=false）：单笔风险、仓位上限等仍是占位值。")
    return 0


def cmd_account_plan(args: argparse.Namespace) -> int:
    from .account import Account, AccountError, portfolio_plan

    try:
        account = Account.load()
    except AccountError as exc:
        print_error(str(exc))
        return 1
    cards = _load_all_cards()
    if not cards:
        print("还没有卡片。")
        return 0
    from .account import account_summary

    summary = account_summary(cards, account)
    print(f"账户状态：回撤 {summary['drawdown'] * 100:.1f}% → {summary['drawdown_state']}；"
          f"A 股可用现金 {summary['cash_cny']:,.0f} CNY｜港股可用现金 {summary['cash_hkd']:,.0f} HKD")
    print(f"卡片状态分布：{summary['card_states']}｜本轮计划新增名义金额 {summary['planned_additions']:,.0f}")
    try:
        from .regime import market_regime

        regime = market_regime()
        print(f"市场状态：**{regime.state}**（{regime.note}）")
        if regime.down:
            print("  ⚠ 下行市里高 beta 标的的下跌日弹性是随机的 1.1–1.3 倍，"
                  "仓位建议按 0.5× 折算（工具不自动改账户参数）")
    except Exception:
        pass
    print()
    plans = portfolio_plan(cards, account)
    for plan in plans:
        print(f"{plan.code} {plan.name:8s} → {plan.action}")
        if plan.lots:
            print(f"    参考 {plan.lots} 手 / {plan.shares} 股 / 金额 {plan.notional:,.0f}")
        for reason in plan.reasons:
            print(f"    · {reason}")
    print("\n这是账户级动作，不是股票名单，也不构成下单指令。"
          "卡片仍在「观察」时不出手，是纪律，不是没找到机会。")
    return 0


def print_error(message: str) -> None:
    print(f"错误：{message}")


def cmd_list(args: argparse.Namespace) -> int:
    if not CARD_DIR.exists():
        print("还没有卡片。")
        return 0
    from .card import quality_grade

    for card in _load_all_cards():
        findings = validate(card)
        grade, thin = quality_grade(card)
        mark = "✗" if blocking(findings) else "✓"
        print(f"{mark} {card.market} {card.code} {card.name:8s} "
              f"状态={card.status:6s} 等级={card.derived_level():6s} 质量={grade:6s} "
              f"阻塞={len(blocking(findings))} 警告={len(findings) - len(blocking(findings))}")
        for reason in thin[:2]:
            print(f"      · {reason}")
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
    p_new.add_argument("--market", default="A", choices=("A", "HK"))
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

    p_fin = sub.add_parser("financials", help="取财务序列写入卡片（A股同花顺 F10 / 港股东方财富；只取数，不下判断）")
    p_fin.add_argument("market")
    p_fin.add_argument("code")
    p_fin.add_argument("--periods", type=int, default=8, help="保留最近几个报告期")
    p_fin.add_argument("--quarter-periods", type=int, default=8, help="保留最近几个单季度")
    p_fin.set_defaults(func=cmd_financials)

    p_ctx = sub.add_parser("context", help="把资本轨迹与同业对照写进卡片")
    p_ctx.add_argument("market")
    p_ctx.add_argument("code")
    p_ctx.set_defaults(func=cmd_context)

    p_exp = sub.add_parser("expectations", help="把分析师预期写进卡片（第三方证据）")
    p_exp.add_argument("market")
    p_exp.add_argument("code")
    p_exp.set_defaults(func=cmd_expectations)

    p_rec = sub.add_parser("recommend", help="推荐清单：先看哪个、为什么、下一步做什么")
    p_rec.add_argument("--period", default="2026-09-30", help="业绩预告的报告期")
    p_rec.add_argument("--since-days", type=int, default=90)
    p_rec.add_argument("--limit", type=int, default=10)
    p_rec.add_argument("--capital", type=float, default=0.0,
                       help="账户本金（用于算可买性）；不给则读 account.json")
    p_rec.add_argument("--position-pct", type=float, default=0.05, help="单只仓位上限")
    p_rec.add_argument("--no-analysts", action="store_true", help="不取分析师预期（更快）")
    p_rec.set_defaults(func=cmd_recommend)

    p_dd = sub.add_parser("deep-dive", help="一键灌卡：行情/财务/参考线/预期/资本/共振 + 风险扫描")
    p_dd.add_argument("market")
    p_dd.add_argument("code")
    p_dd.add_argument("--period", default="", help="行业共振报告期（默认从卡片财务序列推断）")
    p_dd.add_argument("--days", type=int, default=240, help="风险公告回看天数")
    p_dd.add_argument("--no-scan", action="store_true", help="跳过风险/治理扫描（只灌卡）")
    p_dd.set_defaults(func=cmd_deep_dive)

    p_note = sub.add_parser("note", help="单张卡片 → 小红书笔记样式 HTML")
    p_note.add_argument("market")
    p_note.add_argument("code")
    p_note.add_argument("--out", default="", help="输出路径（默认 cards/notes/市场_代码.html）")
    p_note.set_defaults(func=cmd_note)

    p_nb = sub.add_parser("notebook", help="全部卡片 → 一页小红书信息流汇总 HTML")
    p_nb.add_argument("--out", default="", help="输出路径（默认 cards/notes/index.html）")
    p_nb.set_defaults(func=cmd_notebook)

    p_sent = sub.add_parser("sentinel", help="哨兵：触发器命中检查 + 多渠道通知")
    p_sent.set_defaults(func=cmd_sentinel)

    p_odds = sub.add_parser("odds", help="高赔率候选：结构条件筛选 + 10x 需要什么为真")
    p_odds.add_argument("--period", default="2026-09-30", help="业绩预告的报告期")
    p_odds.add_argument("--cap-min", type=float, default=30.0, help="公告日市值下限（亿）")
    p_odds.add_argument("--cap-max", type=float, default=150.0, help="公告日市值上限（亿）")
    p_odds.add_argument("--runup-max", type=float, default=0.8, help="公告前近一年涨幅上限")
    p_odds.add_argument("--target-pe", type=float, default=25.0, help="10x 情景的目标 PE")
    p_odds.add_argument("--limit", type=int, default=20, help="最多展示几只")
    p_odds.set_defaults(func=cmd_odds)

    p_ind = sub.add_parser("industry", help="同业共振：行业 beta 还是公司特有")
    p_ind.add_argument("--map", action="store_true", help="看整个报告期的行业共振地图")
    p_ind.add_argument("market", nargs="?", default="A")
    p_ind.add_argument("code", nargs="?")
    p_ind.add_argument("--period", default="2026-06-30", help="报告期")
    p_ind.add_argument("--threshold", type=float, default=30.0, help="强预告门槛（%%）")
    p_ind.add_argument("--level", choices=["first", "second"], default="second",
                       help="行业层级：申万一级／二级")
    p_ind.set_defaults(func=cmd_industry)

    p_rep = sub.add_parser("backtest-report", help="生成定期回测报告（--append 追加进日志）")
    p_rep.add_argument("--append", action="store_true")
    p_rep.set_defaults(func=cmd_backtest_report)

    p_snap = sub.add_parser("card-snapshot", help="给所有卡片打快照（卡片检验集的起点）")
    p_snap.set_defaults(func=cmd_card_snapshot)

    p_score = sub.add_parser("card-score", help="回看卡片检验集：状态 vs 后续 20/60 日表现")
    p_score.set_defaults(func=cmd_card_score)

    p_ref = sub.add_parser("reference", help="用日线算 ATR 与 2ATR 参考失效线")
    p_ref.add_argument("market")
    p_ref.add_argument("code")
    p_ref.set_defaults(func=cmd_reference)

    p_seg = sub.add_parser("segments", help="分产品收入与毛利的四象限拆分")
    p_seg.add_argument("market")
    p_seg.add_argument("code")
    p_seg.add_argument("--limit", type=int, default=6)
    p_seg.set_defaults(func=cmd_segments)

    p_bus = sub.add_parser("business", help="港股经营回顾逐期对比")
    p_bus.add_argument("market")
    p_bus.add_argument("code")
    p_bus.set_defaults(func=cmd_business)

    p_board = sub.add_parser("board", help="研究看板：谁该被研究（按优先级排）")
    p_board.add_argument("--horizon", type=int, default=30, help="临近多少天算临近复核")
    p_board.add_argument("--new", action="store_true", help="附上发现层的新候选（待立项）")
    p_board.add_argument("--days", type=int, default=60, help="新候选的回看窗口")
    p_board.add_argument("--period", default="2026-09-30", help="A 股新候选的报告期")
    p_board.add_argument("--limit", type=int, default=10, help="每个市场取几个新候选")
    p_board.set_defaults(func=cmd_board)

    p_exc = sub.add_parser("exclude", help="把候选标记为「不看了」（可撤销）")
    p_exc.add_argument("market")
    p_exc.add_argument("code")
    p_exc.add_argument("--reason", default="", help="为什么排除（留痕用）")
    p_exc.add_argument("--remove", action="store_true", help="撤销排除")
    p_exc.set_defaults(func=cmd_exclude)

    p_bt = sub.add_parser("backtest", help="§51 基线检验：强预告组 vs 对照组")
    p_bt.add_argument("--period", default="2026-06-30", help="业绩预告的报告期")
    p_bt.add_argument("--hold", type=int, default=60, help="持有交易日数")
    p_bt.add_argument("--threshold", type=float, default=50.0, help="强预告的同比变动门槛（%%）")
    p_bt.add_argument("--sample", type=int, default=150, help="抽样上限（×3）")
    p_bt.add_argument("--runup-filter", type=float, default=None,
                      help="按公告前涨幅过滤（如 0.15 表示剔除公告前涨超 15%% 的）")
    p_bt.add_argument("--sensitivity", action="store_true", help="附参数敏感性")
    p_bt.set_defaults(func=cmd_backtest)

    p_pf = sub.add_parser("portfolio", help="§51 组合层检验：净值/回撤/现金占比/错过率")
    p_pf.add_argument("--threshold", type=float, default=30.0, help="强预告同比门槛（%%）")
    p_pf.add_argument("--min-amount", type=float, default=5e7, help="预告净利润下限门槛（元，0 表示不过滤）")
    p_pf.add_argument("--capital", type=float, default=100000.0)
    p_pf.add_argument("--max-positions", type=int, default=5)
    p_pf.add_argument("--position-pct", type=float, default=0.2)
    p_pf.add_argument("--cash-floor", type=float, default=0.2)
    p_pf.add_argument("--hold", type=int, default=60)
    p_pf.add_argument("--max-drawdown", type=float, default=0.20, help="账户回撤边界")
    p_pf.add_argument("--no-drawdown-rule", action="store_true", help="不启用回撤触发的停止新增")
    p_pf.add_argument("--force-exit", action="store_true", help="触及回撤边界时强制平仓")
    p_pf.add_argument("--per-period", action="store_true", help="附分期拆解（每期独立起算）")
    p_pf.add_argument("--market-baseline", action="store_true",
                      help="加一条全市场随机基线（当期没发预告的 A 股随机抽）")
    p_pf.set_defaults(func=cmd_portfolio)

    p_acc = sub.add_parser("account", help="账户层：查看账户状态或给出账户级动作")
    acc_sub = p_acc.add_subparsers(dest="account_command", required=True)
    a_show = acc_sub.add_parser("show", help="账户权益、现金、持仓与回撤状态")
    a_show.set_defaults(func=cmd_account_show)
    a_plan = acc_sub.add_parser("plan", help="对所有卡片给出账户级动作")
    a_plan.set_defaults(func=cmd_account_plan)
    p_acc.set_defaults(func=lambda a: p_acc.print_help())

    p_risk = sub.add_parser("risks", help="扫描某只标的的风险类公告")
    p_risk.add_argument("market")
    p_risk.add_argument("code")
    p_risk.add_argument("--days", type=int, default=240)
    p_risk.set_defaults(func=cmd_risks)

    p_gov = sub.add_parser("governance", help="治理与筹码扫描（股东户数/质押/诉讼/担保）")
    p_gov.add_argument("market")
    p_gov.add_argument("code")
    p_gov.set_defaults(func=cmd_governance)

    p_hk = sub.add_parser("hk-discover", help="港股候选发现")
    p_hk.add_argument("--days", type=int, default=60, help="回看天数")
    p_hk.add_argument("--limit", type=int, default=15)
    p_hk.add_argument("--source", choices=("alerts", "results", "financials"), default="alerts",
                      help="alerts=盈警/盈喜公告；results=业绩公告；financials=财务同比变化")
    p_hk.add_argument("--date", default="", help="财务同比模式的报告期 YYYY-MM-DD")
    p_hk.set_defaults(func=cmd_hk_discover)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
