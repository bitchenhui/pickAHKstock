"""卡片工作流：从发现结果生成草稿、渲染给人看。草稿永远停在「观察」。"""

from __future__ import annotations

from datetime import date

from .card import Card, CounterCase, Evidence, LogEntry, Scenario
from .discover import Candidate


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
              "", "## 失效条件"]
    lines += [f"- {t}" for t in card.invalidation] or ["- （未填写）"]
    lines += ["", "## 账户检查"]
    lines += [f"- {c.name}：{c.result} {c.note} {c.source}".rstrip()
              for c in card.account_checks] or ["- （未填写）"]
    lines += ["", "## 数据缺口"]
    lines += [f"- {g}" for g in card.gaps] or ["- （无）"]
    context = card.market_context or {}
    if context:
        lines += ["", "## 市场数据（仅供执行参考）",
                  f"- 最新价 {context.get('price')}｜成交额 {context.get('turnover')}｜"
                  f"市值 {context.get('market_cap')}｜时间 {context.get('as_of')}"]
    return "\n".join(lines)
