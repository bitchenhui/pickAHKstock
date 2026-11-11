# -*- coding: utf-8 -*-
"""人话版结论：把卡片里已有的事实重组成零术语的结论。

为什么要这一层：用户（非金融专业）需要的是「结论能看懂」，但分析过程
必须保持专业。本模块只做**重组与翻译**，不引入任何新事实、不做新判断
——它读的是卡片里已经核实的证据与已写的判断，输出是同一件事的大白话。

AI 可选覆盖：有 LLM 配置时（research/llm.py），让模型在同样的事实下
写人话版（不允许引入卡片里没有的数字），失败则回落到规则版。
"""

from __future__ import annotations

from .card import Card

# 术语 → 大白话（只用于结论层，专业分析原样保留）
PLAIN_TERMS = {
    # 长词优先（按长度倒序替换），避免半途破坏词组
    "行业共振": "整个行业都在变（行业行情）",
    "单点不可辨": "同行数据太少，判断不了是不是行业行情",
    "公司特有": "同行没这动静（它自己的事）",
    "业绩预告": "公司提前报的业绩",
    "扣非净利润": "真实利润（去掉一次性收益）",
    "单季毛利率": "单个季度每卖 100 块赚多少",
    "单季净利润": "单个季度赚的钱",
    "单季": "单个季度",
    "毛利率": "每卖 100 块赚多少",
    "经营现金流": "公司实际收到的钱",
    "自由现金流": "收到的钱减去买设备等投入",
    "归母净利润": "股东实际赚到的钱",
    "归母净资产": "股东自己的钱",
    "应收": "卖出货但客户还没付的钱",
    "失效线": "跌到这里说明当初的判断已经不成立",
    "高危治理": "担保/质押这类可能暴雷的账",
    "相对强度": "跟大盘比谁更强",
    "盯价": "盯着的行业价格",
    "催化剂": "该验证的那件事",
    "区间位置": "在近一年价格区间里的高低",
    "共振": "同行也在变",
    "扣非": "真实利润",
    "同比": "跟去年同期比",
    "环比": "跟上个季度比",
    "衰减": "变弱",
    "流动性": "想买想卖能不能成交",
    "扭亏": "从亏钱变成赚钱",
    "减亏": "亏损变小",
    "预增": "公司提前报盈利增长",
}


def _plainize(text: str) -> str:
    """把专业词换成大白话（长词优先，避免半途替换破坏词组）。"""
    out = text
    for term in sorted(PLAIN_TERMS, key=len, reverse=True):
        out = out.replace(term, PLAIN_TERMS[term])
    return out


def _quarter_trend(card: Card) -> tuple[str, list[tuple[str, float]]]:
    """最近三个单季净利润：趋势判断 + (标签, 数值) 序列。"""
    from .note import quarterly

    quarters = quarterly(card.financials, "净利润") if card.financials.metrics else []
    values = [value for _, value in quarters]
    if len(values) < 2:
        return "", quarters
    if values[-1] < values[0]:
        trend = "变弱"
    elif values[-1] > values[0]:
        trend = "变强"
    else:
        trend = "持平"
    return trend, quarters


def _compose_plain(card: Card, facts: list[str], trend: str,
                   profit_quarters: list[tuple[str, float]]) -> str:
    """用已核实事实拼一句人话结论（不引用专业散文，避免术语替换的生硬感）。

    结构：靠什么赚钱 → 现在怎么样 → 最硬的一条反证 → 该不该动手。
    """
    resonance = card.industry_context or {}
    capital = card.capital or {}
    context = card.market_context or {}
    regime = context.get("market_regime") or {}
    execution = card.execution or {}

    parts: list[str] = []
    # ① 靠什么赚钱
    money_source = ""
    for item in ((card.commodities or {}).get("items") or {}).values():
        money_source = item.get("name", "").split("（")[0]
        break
    if money_source:
        parts.append(f"这家公司主要靠「{money_source}」的价格赚钱")
    elif card.financials.path == "已盈利":
        parts.append("这家公司是赚钱的")
    else:
        parts.append("这家公司还没稳定赚钱")
    # ② 现在怎么样
    if profit_quarters and trend:
        first, last = profit_quarters[0], profit_quarters[-1]
        direction = {"变弱": "最近在变弱", "变强": "最近在变强", "持平": "最近走平"}.get(trend, "")
        parts.append(f"单个季度赚的钱从 {first[1] / 1e8:.2f} 亿变成 {last[1] / 1e8:.2f} 亿"
                     f"（{direction}）")
    # ③ 是行业还是它自己
    verdict = resonance.get("verdict")
    if verdict == "行业共振":
        parts.append("但这是整个行业一起变，涨跌跟着行业走——行业掉头它也会一起跌")
    elif verdict == "公司特有":
        parts.append("同行没这动静，是它自己的事")
    # ④ 最硬的一条反证
    cash = capital.get("operating_cf_label")
    if cash and str(cash).strip().startswith("-"):
        parts.append(f"最扎眼的是：账面上赚钱，公司实际收到的钱却是 {cash}")
    high = [g for g in ((card.scans or {}).get("governance") or [])
            if g.get("severity") == "高"]
    if high:
        parts.append(f"另外担保这类可能暴雷的账有 {len(high)} 条")
    # ⑤ 该不该动手 + 市场环境
    if regime.get("state") == "下行":
        parts.append("现在大盘在跌，这类股票跌起来更凶，仓位要打折")
    if not card.view:
        parts.append("判断还没写完，现在只能观察")
    return "；".join(parts) + "。" if parts else "这张卡还没写判断——先别当结论用。"


def plain_conclusion(card: Card) -> dict:
    """从卡片生成人话版结论（不引入新事实）。

    返回：{one_line, facts[], watch[], decide[], plain_view}
    """
    context = card.market_context or {}
    resonance = card.industry_context or {}
    execution = card.execution or {}
    regime = context.get("market_regime") or {}
    capital = card.capital or {}
    trend, profit_quarters = _quarter_trend(card)

    facts: list[str] = []
    # ① 这公司在干嘛、赚不赚钱
    if card.financials.path:
        facts.append(f"公司属于「已盈利」还是「未盈利」路径：{card.financials.path}")
    if profit_quarters:
        first, last = profit_quarters[0], profit_quarters[-1]
        facts.append(f"最近三个季度赚的钱：{first[0]} {first[1] / 1e8:.2f} 亿 → "
                     f"{last[0]} {last[1] / 1e8:.2f} 亿（{trend}）")    # ② 钱有没有真的收到
    cash = capital.get("operating_cf_label")
    if cash:
        facts.append(f"公司实际收到的钱：{cash}（这是最硬的一条，纸面利润可以修饰，"
                     f"收到的钱很难）")
    # ③ 这是行业行情还是它自己的事
    if resonance.get("verdict"):
        verdict = resonance["verdict"]
        plain_verdict = {"行业共振": "这是整个行业的行情，涨跌跟着行业走，行业反转它也会一起跌",
                         "公司特有": "同行没这动静，是它自己的事",
                         "单点不可辨": "同行数据太少，判断不了是不是行业行情",
                         "介于两者之间": "一半一半，行业的成分更多"}.get(verdict, verdict)
        facts.append(f"{plain_verdict}")
    # ④ 盯的价格
    for item in ((card.commodities or {}).get("items") or {}).values():
        chg = item.get("chg_30d")
        if chg is not None:
            facts.append(f"它靠价格吃饭的产品：{item['name']}，最近 30 天 "
                         f"{'涨' if chg > 0 else '跌'} {abs(chg) * 100:.0f}%")
    # ⑤ 风险
    risks = ((card.scans or {}).get("risks") or [])
    high = [g for g in ((card.scans or {}).get("governance") or [])
            if g.get("severity") == "高"]
    if risks or high:
        parts = []
        if risks:
            parts.append(f"最近有 {len(risks)} 条风险公告要看")
        if high:
            parts.append(f"担保这类可能暴雷的账 {len(high)} 条")
        facts.append("、".join(parts) + "（每条都要点开原文看，标题扫描会误报）")

    # —— 结论句：用事实模板重组，不对专业散文做术语替换 ——
    one_line = _compose_plain(card, facts, trend, profit_quarters)

    # —— 该看什么 ——
    watch: list[str] = []
    for catalyst in card.catalysts[:2]:
        if catalyst.event:
            watch.append(f"{catalyst.event}（{catalyst.date or '时间待定'}）——到点要核对")
    if execution.get("strength", {}).get("rs") is not None:
        rs = execution["strength"]["rs"]
        watch.append(f"跟大盘比：{'强' if rs > 0 else '弱'} {rs:+.1f} 个百分点"
                     f"（转弱时要重新看判断还成不成立）")
    if regime.get("state"):
        watch.append(f"大盘现在{regime.get('state')}（这种行情里这类股票跌起来更凶，"
                     f"仓位要打折）")

    # —— 什么情况该改变主意 ——
    decide: list[str] = []
    for line in card.invalidation[:3]:
        decide.append(_plainize(line))
    stop = context.get("stop_reference")
    if stop:
        decide.append(f"股价跌到 {stop} 附近：当初的判断可能已经不成立，去看卡片核对")
    if not decide and card.counter.exit_trigger:
        decide.append(_plainize(card.counter.exit_trigger))

    return {"one_line": one_line, "facts": facts, "watch": watch, "decide": decide}


def plain_text(card: Card) -> str:
    """人话版全文（推送用，短）。"""
    result = plain_conclusion(card)
    lines = [result["one_line"]]
    if result["watch"]:
        lines.append("该看：" + "；".join(result["watch"][:2]))
    if result["decide"]:
        lines.append("什么时候该改主意：" + "；".join(result["decide"][:2]))
    return "\n".join(lines)
