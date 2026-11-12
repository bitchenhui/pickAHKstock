# -*- coding: utf-8 -*-
"""多空对辩：让模型对同一组事实分别站在两边攻击，再合成结论。

设计原则（与仓库整体一致）：
- 模型**只读卡片里已有的事实**，不允许引入外部数字（提示词明确禁止，
  且输出里出现的数字必须能在卡片中找到）；
- 多空两方独立生成，互不可见，避免一方被另一方带偏；
- 合成方给出结论 + 反证 + 失效条件，结论必须落到卡片已写的失效条件上；
- 不产生买卖指令、不给概率、不给目标价（与仓库边界一致）。

配置（环境变量或 GitHub Secrets）：
  LLM_API_KEY     MiniMax 开放平台 key（必填）
  LLM_BASE_URL    默认 https://api.minimaxi.com/anthropic（Anthropic 兼容）
                  或 https://api.minimaxi.com/v1（OpenAI 兼容）
  LLM_MODEL       默认 MiniMax-M3.1
  LLM_PROTOCOL    anthropic（默认） | openai
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import requests

from .card import Card
from .plain import plain_conclusion

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M3.1-Flash"
TIMEOUT = 120


class LLMError(RuntimeError):
    pass


@dataclass
class Debate:
    bull: str = ""          # 多方论证
    bear: str = ""          # 空方论证
    verdict: str = ""       # 合成结论
    checks: list[str] = None  # 合成方列出的待核实项
    source: str = ""        # 模型与协议，便于留痕

    def to_dict(self) -> dict:
        return {"bull": self.bull, "bear": self.bear, "verdict": self.verdict,
                "checks": self.checks or [], "source": self.source}


def configured() -> bool:
    return bool(os.environ.get("LLM_API_KEY"))


def _card_facts(card: Card) -> str:
    """给模型的事实清单：只含卡片里已有的内容，附数值。"""
    from .note import quarterly

    lines = [f"标的：{card.name}（{card.market} {card.code}）",
             f"当前判断：{card.view or '（未写）'}",
             f"研究主线：{card.horizon or '（未写）'}"]
    track = card.financials
    if track.path:
        lines.append(f"盈利路径：{track.path}")
    if track.recurring_judgment:
        lines.append(f"收入/毛利变化是否可持续：{track.recurring_judgment}")
    if track.cyclical_judgment:
        lines.append(f"周期性判断：{track.cyclical_judgment}")
    quarters = quarterly(track, "净利润") if track.metrics else []
    if quarters:
        lines.append("单季净利润：" + "、".join(f"{q} {v / 1e8:.2f} 亿" for q, v in quarters))
    capital = card.capital or {}
    if capital:
        lines.append(f"资本轨迹：经营现金流 {capital.get('operating_cf_label', '—')}、"
                     f"自由现金流 {capital.get('fcf_label', '—')}、"
                     f"资本开支 {capital.get('capex_label', '—')}")
    resonance = card.industry_context or {}
    if resonance:
        lines.append(f"行业共振判定：{resonance.get('verdict', '—')}"
                     f"（{resonance.get('industry', '—')}，同行业 "
                     f"{resonance.get('peers_with_forecast', 0)} 家发预告，"
                     f"同向 {resonance.get('peers_same_sign', 0)} 家）")
    execution = card.execution or {}
    strength = execution.get("strength") or {}
    if strength.get("rs") is not None:
        lines.append(f"相对强度（60 日 vs 指数）：{strength['rs']:+.1f} pct")
    liquidity = execution.get("liquidity") or {}
    if liquidity.get("level"):
        lines.append(f"流动性：{liquidity['level']}（{liquidity.get('note', '')}）")
    for item in ((card.commodities or {}).get("items") or {}).values():
        lines.append(f"盯的价格：{item.get('name', '')} 最新 {item.get('latest', '—')}"
                     f"（30 日 {item.get('chg_30d', 0) * 100:+.1f}%）")
    context = card.market_context or {}
    if context.get("stop_reference"):
        lines.append(f"2ATR 参考失效线：{context['stop_reference']}"
                     f"（现价 {context.get('price', '—')}）")
    regime = context.get("market_regime") or {}
    if regime.get("state"):
        lines.append(f"市场状态：{regime.get('state')}（{regime.get('index', '')} "
                     f"{regime.get('close', '')}）")
    expectations = card.expectations or {}
    if expectations:
        lines.append(f"机构覆盖：{expectations.get('coverage', '—')} 家，"
                     f"评级 {expectations.get('latest_rating') or '—'}")
    for scenario in card.scenarios:
        if scenario.narrative:
            lines.append(f"情景·{scenario.name}：{scenario.narrative}"
                         f"（驱动：{'、'.join(scenario.drivers)}）")
    if card.counter.strongest_failure:
        lines.append(f"最强反证（已写）：{card.counter.strongest_failure}")
    if card.counter.exit_trigger:
        lines.append(f"退出触发（已写）：{card.counter.exit_trigger}")
    for line in card.invalidation:
        lines.append(f"失效条件：{line}")
    risks = (card.scans or {}).get("risks") or []
    if risks:
        lines.append("风险公告（标题层，需回原文核对）："
                     + "；".join(f"{r.get('published', '')} {r.get('title', '')}"
                                for r in risks[:6]))
    governance = [g for g in ((card.scans or {}).get("governance") or [])
                  if g.get("severity") == "高"]
    for item in governance:
        lines.append(f"高危治理：{item.get('category', '')}：{item.get('detail', '')}")
    return "\n".join(lines)


BULL_PROMPT = """你是一位只看多方的资深买方分析师。基于下面的事实，写出这只股票最有力的
看多论证。要求：
1. 只使用事实清单里出现的信息与数字，不得引入任何外部数据、传闻或假设数字；
2. 直接给论据，不寒暄、不重复事实清单；
3. 结尾给出一句「多头最怕的是：<一句话>」——这是空方最该攻击的地方；
4. 全文不超过 260 字，中文。

事实清单：
{facts}"""

BEAR_PROMPT = """你是一位只看空方的资深卖方分析师。基于下面的事实，写出这只股票最有力的
看空论证。要求：
1. 只使用事实清单里出现的信息与数字，不得引入任何外部数据、传闻或假设数字；
2. 攻击面要具体：现金流与利润背离、需求端证据缺失、价格趋势、治理与合规、
   行业beta、估值与流动性，事实清单里有什么就打什么；
3. 直接给论据，不寒暄；
4. 全文不超过 260 字，中文。

事实清单：
{facts}"""

SYNTH_PROMPT = """你是最终决策者，面前有两份对同一组事实的相反论证。请输出：
1. 一句结论（不超过 60 字）：这只票现在是什么状态、能不能进持仓研究、还差什么；
2. 三到五条「最值得你亲自核对的事实」（每条都要指向事实清单里的具体内容）；
3. 一句「什么情况出现就该放弃这只」（要具体到数字或事件，若事实清单里已有失效条件
   就直接引用它）；
4. 一句提醒：这个结论在什么情况下不成立。

硬约束：不得引入事实清单之外的任何数字或事实；不写买入/卖出指令，不给概率、不给
目标价；结论必须落在事实清单给出的证据上。

事实清单：
{facts}

多方论证：
{bull}

空方论证：
{bear}

按以下 JSON 输出（不要 markdown 代码块）：
{{"conclusion": "...", "checks": ["...", "...", "..."], "abandon_if": "...", "caveat": "..."}}"""


def _call_anthropic(base_url: str, api_key: str, model: str, system: str,
                     user: str) -> str:
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "Content-Type": "application/json"},
        json={"model": model, "max_tokens": 4096, "temperature": 0.3,
              "system": system, "messages": [{"role": "user", "content": user}]},
        timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    parts = []
    for block in payload.get("content", []):
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


def _call_openai(base_url: str, api_key: str, model: str, system: str,
                  user: str) -> str:
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"model": model, "max_tokens": 4096, "temperature": 0.3,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
        timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content", "").strip()


def _call(system: str, user: str) -> str:
    api_key = os.environ["LLM_API_KEY"]
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    protocol = os.environ.get("LLM_PROTOCOL", "anthropic").lower()
    if protocol == "openai":
        return _call_openai(base_url, api_key, model, system, user)
    return _call_anthropic(base_url, api_key, model, system, user)


def debate(card: Card) -> Debate:
    """跑一轮多空对辩。任一环节失败即抛错（调用方决定回落）。"""
    if not configured():
        raise LLMError("未配置 LLM_API_KEY")
    facts = _card_facts(card)
    bull = _call("你是有经验的买方分析师，只用给定事实。", BULL_PROMPT.format(facts=facts))
    bear = _call("你是有经验的卖方分析师，只用给定事实。", BEAR_PROMPT.format(facts=facts))
    raw = _call("你是最终决策者，只用给定事实。",
                 SYNTH_PROMPT.format(facts=facts, bull=bull, bear=bear))
    verdict: dict = {}
    try:
        start, end = raw.find("{"), raw.rfind("}")
        verdict = json.loads(raw[start:end + 1]) if start >= 0 and end > start else {}
    except ValueError:
        verdict = {"conclusion": raw[:300]}
    return Debate(
        bull=bull, bear=bear,
        verdict=verdict.get("conclusion", ""),
        checks=list(verdict.get("checks", [])) + (
            [verdict["abandon_if"]] if verdict.get("abandon_if") else []) + (
            [verdict["caveat"]] if verdict.get("caveat") else []),
        source=f"{os.environ.get('LLM_MODEL', DEFAULT_MODEL)}/{os.environ.get('LLM_PROTOCOL', 'anthropic')}")


def analyze(card: Card) -> dict:
    """完整分析：人话版结论 + （若配置了 LLM）多空对辩。

    返回 {plain: {...}, debate: {...} | None}
    """
    result = {"plain": plain_conclusion(card), "debate": None}
    if configured():
        try:
            result["debate"] = debate(card).to_dict()
        except Exception as exc:
            result["debate_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return result
