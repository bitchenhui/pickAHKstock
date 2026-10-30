"""卡片工作流：从发现结果生成草稿、渲染给人看。草稿永远停在「观察」。"""

from __future__ import annotations

from datetime import date

from .card import Card, CounterCase, Evidence, LogEntry, Scenario
from .discover import Candidate


def scaffold_hk(candidate, source: str = "港股发现层") -> Card:
    """港股卡片草稿：公告事件 + 行情（含每手股数），财务序列另用 financials 命令补。

    港股公告 PDF 的中文抽不出来，所以业务判断只能依赖结构化财务接口；
    公告本身在这里的作用是「事件与公开时间」的出处。
    """
    from .card import Card, Evidence

    today = date.today().isoformat()
    card = Card(
        market="HK", code=candidate.code, name=candidate.name, status="观察",
        view="", horizon="",
        evidence=[Evidence(
            id="E1",
            claim=f"{candidate.published} 公告：{candidate.event}",
            kind="披露", publisher="香港交易所披露易（经东方财富转发）",
            title=candidate.event, published=str(candidate.published),
            url=candidate.detail_url, observed=today,
            alternative=candidate.direction_note,
            next_check="2026 年报与下一次业绩公告",
            status="已核实" if candidate.direction in ("正向", "负向") else "待核实",
            note="港股公告 PDF 中文为 CID 字体无法抽取，方向以标题判定并需财务数据复核")],
        market_context={"price": candidate.price, "turnover": candidate.turnover,
                        "market_cap": candidate.market_cap, "lot_size": candidate.lot_size,
                        "turnover_rate": candidate.turnover_rate, "as_of": candidate.as_of,
                        "role": "市场确认与执行参考，不是业务证据"},
        gaps=["港股无巨潮治理数据接口：股东户数、质押、担保、诉讼等治理维度未取得",
              "港股公告 PDF 中文无法抽取，原文需人工阅读"],
        log=[LogEntry(at=today, event=f"由{source}生成草稿", from_status="", to_status="观察")],
        created=today, updated=today)
    return card


def scaffold(candidate: Candidate, source: str = "发现层") -> Card:
    """按 §22 五要素把一条披露事件变成卡片草稿：变化、公开时间、原始出处、可替代解释、下一验证节点。"""
    today = date.today().isoformat()
    evidence = Evidence(
        id="E1",
        claim=f"{candidate.name}在{candidate.notice_date}披露的{candidate.forecast_type}"
              f"（{candidate.metric} ≥ {candidate.change_lower:.0f}%）",
        kind="披露",
        publisher="巨潮资讯 / 公司公告" if candidate.pdf_url else "东方财富数据中心（转载）",
        title=candidate.pdf_title or f"{candidate.forecast_type}：{candidate.metric}",
        published=candidate.notice_date.isoformat() if candidate.notice_date else "",
        url=candidate.pdf_url or candidate.source_url,
        observed=today,
        alternative=candidate.reason[:200],
        next_check="",
        status="已核实" if candidate.pdf_url else "待核实",
        note="原始出处未取得链接前不得据此下结论")
    card = Card(
        market=candidate.market,
        code=candidate.code,
        name=candidate.name,
        status="观察",
        view="",
        horizon="",
        evidence=[evidence],
        scenarios=[Scenario(name="基准"), Scenario(name="乐观"), Scenario(name="失败")],
        counter=CounterCase(),
        invalidation=[],
        market_context={
            "price": candidate.price, "turnover": candidate.turnover,
            "market_cap": candidate.market_cap, "pe": candidate.pe, "pb": candidate.pb,
            "as_of": candidate.as_of, "role": "市场确认与执行参考，不是业务证据"},
        account_checks=[],
        gaps=list(candidate.notes),
        log=[LogEntry(at=today, event=f"由{source}生成草稿", from_status="", to_status="观察")],
        created=today, updated=today)
    return card


def render_markdown(card: Card) -> str:
    level = card.derived_level()
    lines = [f"# {card.name}（{card.market} {card.code}）",
             f"- 状态：**{card.status}**　结论等级：**{level}**（由证据推导）",
             f"- 当前判断：{card.view or '（未填写）'}",
             f"- 研究主线：{card.horizon or '（未填写）'}",
             "",
             "## 证据"]
    for evidence in card.evidence:
        origin = evidence.url or "（无链接）"
        lines.append(f"- **{evidence.id}** [{evidence.status}] {evidence.claim}")
        lines.append(f"  - 出处：{evidence.publisher} {evidence.title}｜{evidence.published or '无日期'}｜{origin}")
        if evidence.alternative:
            lines.append(f"  - 可替代解释：{evidence.alternative}")
        if evidence.next_check:
            lines.append(f"  - 下一验证节点：{evidence.next_check}")
    lines += ["", "## 情景"]
    for scenario in card.scenarios:
        lines.append(f"- **{scenario.name}**：{scenario.narrative or '（未填写）'}")
        for driver in scenario.drivers:
            lines.append(f"  - 驱动：{driver}")
    lines += ["", "## 反证", f"- 最强失败路径：{card.counter.strongest_failure or '（未填写）'}",
              f"- 退出触发：{card.counter.exit_trigger or '（未填写）'}",
              f"- 我可能错在哪：{card.counter.where_wrong or '（未填写）'}"]
    lines += ["", "## 失效条件"]
    lines += [f"- {t}" for t in card.invalidation] or ["- （未填写）"]
    if card.catalysts:
        lines += ["", "## 催化剂与复核事件"]
        for catalyst in card.catalysts:
            lines.append(f"- {catalyst.event}｜{catalyst.date or '无日期'}｜"
                         f"{catalyst.direction}｜{catalyst.magnitude}｜{catalyst.status}")
    if card.risks:
        lines += ["", "## 风险"]
        for risk in card.risks:
            lines.append(f"- [{risk.severity}] {risk.category}：{risk.mechanism}"
                         f"　监测触发：{risk.trigger or '（未填）'}")
    lines += ["", "## 账户检查"]
    lines += [f"- {c.name}：{c.result} {c.note} {c.source}".rstrip()
              for c in card.account_checks] or ["- （未填写）"]
    if card.expectations:
        lines += ["", "## 分析师预期（独立于公司自述的第三方证据）"]
        rows = card.expectations.get("eps_forecast") or []
        if rows:
            for row in rows[:2]:
                lines.append(f"- {row.get('年度')} 年 EPS 预期：均值 {row.get('均值')}"
                             f"（区间 {row.get('最小值')}–{row.get('最大值')}），"
                             f"行业平均 {row.get('行业平均数')}，覆盖机构 {row.get('机构数')} 家")
        lines.append("- ⚠ 口径提示：同花顺的「行业平均数」是全行业均值，与单只公司的可比性需自行判断"
                     "（半导体等宽行业尤其容易失真）；同花顺盈利预测接口只覆盖 A 股")
        coverage = card.expectations.get("coverage")
        lines.append(f"- 覆盖机构 {coverage} 家｜最新评级 {card.expectations.get('latest_rating') or '—'}"
                     f"｜目标价 {card.expectations.get('target_price') or '—'}")
        for note in card.expectations.get("notes") or []:
            lines.append(f"- ⚠ {note}")
        for row in (card.expectations.get("ratings") or [])[:3]:
            lines.append(f"- 研报 {row.get('日期')}：{row.get('标题')}")
    if card.capital:
        lines += ["", "## 资本轨迹（利润之外的第三只眼）"]
        periods = card.capital.get("periods") or []
        if periods:
            lines.append(f"- 报告期：{periods[0]}")
        lines.append(f"- 资本开支：{card.capital.get('capex_label') or '—'}"
                     f"｜经营活动现金流：{card.capital.get('operating_cf_label') or '—'}"
                     f"｜自由现金流：{card.capital.get('fcf_label') or '—'}")
        lines.append(f"- 在建工程：{card.capital.get('construction_label') or '—'}"
                     f"｜固定资产：{card.capital.get('fixed_assets_label') or '—'}")
    if card.peers:
        lines += ["", "## 同业对照"]
        lines.append(f"- 同业排名：{card.peers.get('rank') or '—'}"
                     f"｜PEG {card.peers.get('PEG') or '—'}")
        lines.append(f"- 市盈率 TTM {card.peers.get('市盈率-TTM') or '—'}"
                     f"｜25E {card.peers.get('市盈率-25E') or '—'}"
                     f"｜26E {card.peers.get('市盈率-26E') or '—'}"
                     f"｜27E {card.peers.get('市盈率-27E') or '—'}")
        lines.append(f"- 市净率 MRQ {card.peers.get('市净率-MRQ') or '—'}"
                     f"｜市销率 TTM {card.peers.get('市销率-TTM') or '—'}"
                     f"｜市现率 TTM {card.peers.get('市现率1-TTM') or '—'}")
        lines.append("- 远期 PE 基于分析师一致预期：把「分析师预期」换算成「市场为它付几倍」")
        for note in card.peers.get("notes") or []:
            lines.append(f"- ⚠ {note}")
    if card.judgments:
        lines += ["", "## 结构化判断（可回溯核对）"]
        for judgment in card.judgments:
            value = "—" if judgment.value is None else f"{judgment.value:g} {judgment.unit}".strip()
            lines.append(f"- **{judgment.id}** {judgment.text}")
            lines.append(f"  - 指标 {judgment.metric}｜数值 {value}｜期间 {judgment.period}"
                         f"｜依据 {judgment.basis}｜核对 {judgment.checked}｜置信 {judgment.confidence}")
    track = card.financials
    lines += ["", "## 财务轨迹"]
    lines.append(f"- 路径：**{track.path}**　来源：{track.source or '（未取得）'}")
    if track.periods:
        lines.append(f"- 报告期：{' | '.join(track.periods)}")
    for name in ("营业总收入", "净利润", "扣非净利润", "销售毛利率", "净资产收益率"):
        series = track.metrics.get(name)
        if series:
            cells = [f"{period}:{series.get(period, '-')}" for period in track.periods]
            lines.append(f"- {name}：" + " | ".join(cells))
    lines.append(f"- 经常性判断：{track.recurring_judgment or '（未填写）'}")
    lines.append(f"- 周期性判断：{track.cyclical_judgment or '（未填写）'}")
    if track.working_capital_note:
        lines.append(f"- 营运资本：{track.working_capital_note}")
    for item in track.path_evidence:
        lines.append(f"- 路径证据：{item}")
    lines += ["", "## 数据缺口"]
    lines += [f"- {g}" for g in card.gaps] or ["- （无）"]
    context = card.market_context or {}
    if context:
        lines += ["", "## 市场数据（仅供执行参考）",
                  f"- 最新价 {context.get('price')}｜成交额 {context.get('turnover')}｜"
                  f"市值 {context.get('market_cap')}｜时间 {context.get('as_of')}"]
    return "\n".join(lines)
