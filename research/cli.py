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
    candidates, stats = discover(args.period, args.since_days, args.limit)
    _print_candidates(candidates)
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
    card = Card.load(args.market, args.code)
    if args.market == "HK":
        from .hk import fetch_stock_financials

        data = fetch_stock_financials(args.code)
        track = card.financials
        track.source = data["source"]
        track.periods = data["periods"]
        track.metrics = data["metrics"]
        track.quarter_metrics = {}
    else:
        from .ths import fetch_financials, key_series

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
    track.notes = [n for n in track.notes if not n.startswith("财务序列已于")]
    track.notes.append(f"财务序列已于 {date.today().isoformat()} 取自 {track.source}")
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
    print("\n小结：" + "｜".join(f"{k} {v}" for k, v in counts.items()))
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
                                min_amount=args.min_amount, config=config)
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
    for label, item in (("强预告组合", event), ("随机基线组合", base)):
        print(f"{label:>12s} {item['trades']:>4d} {item['total_return']:>7}% {item['max_drawdown']:>7}% "
              f"{item['cash_ratio']:>7}% {item['win_rate']:>5}% {item['fee_total']:>9,.0f} "
              f"{item['coverage']:>6}% {str(item['selection_median']):>9s} {str(item['pool_median']):>9s}")
    if index_return is not None:
        print(f"\n同期沪深300：{index_return * 100:+.2f}%")
    print(f"因整手或资金买不到的次数：强预告 {event['skipped_unaffordable']}｜"
          f"基线 {base['skipped_unaffordable']}")
    print("\n「覆盖率」= 买到的候选 / 候选总数（受最多持 5 只与资金约束限制）；"
          "「选中中位」= 买到的那些的持有期收益中位数，「全池中位」= 全体候选的中位数——"
          "两者的差才是「挑得准不准」。「现金占比」= 平均闲置现金占本金，小资金的真实约束会体现在这里。")
    print("局限：退市与停牌样本缺失（幸存者偏差）、池子未按市值区间过滤（前视）、"
          "预告本身有选择性、只有 4 个报告期。")
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

    from .backtest import load_prices
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
    result = score(snapshots, price_series)
    print(f"快照 {len(result['rows'])} 个（快照日 {min(s.day for s in snapshots)} 起）\n")
    print(f"{'状态':>8s} {'n':>4s} {'可评':>4s} {'60日中位':>9s} {'60日胜率':>9s}")
    for status, item in result["summary"].items():
        median = "—" if item["median_60"] is None else f"{item['median_60']}%"
        win = "—" if item["win_rate_60"] is None else f"{item['win_rate_60']}%"
        print(f"{status:>8s} {item['n']:>4d} {item['scored']:>4d} {median:>9s} {win:>9s}")
    pending = [r for r in result["rows"] if r["return_60"] is None]
    if pending:
        print(f"\n尚有 {len(pending)} 个快照未满 60 个交易日——检验集还没长成。"
              "这正是它需要时间的原因：卡片建了两天，就只能评两天。")
    return 0


def cmd_reference(args: argparse.Namespace) -> int:
    """用同花顺日线算 ATR 与 2ATR 参考失效线，写入卡片的市场数据段。

    这是参考线，不是止损保证：跳空、停牌、涨跌停都可能让参考价无法成交。
    """
    from .ths import reference_levels

    card = Card.load(args.market, args.code)
    levels = reference_levels(args.code)
    context = dict(card.market_context or {})
    context.update({"close": levels["close"], "atr": levels["atr"],
                    "atr_pct": levels["atr_pct"], "stop_reference": levels["stop_reference"],
                    "bars": levels["bars"], "bars_as_of": levels["last_day"],
                    "bars_source": levels["source"]})
    card.market_context = context
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
    print(f"卡片状态分布：{summary['card_states']}｜本轮计划新增名义金额 {summary['planned_additions']:,.0f}\n")
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

    for path in sorted(CARD_DIR.glob("*.json")):
        card = Card.from_dict(json.loads(path.read_text(encoding="utf-8")))
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
