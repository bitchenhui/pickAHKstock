# -*- coding: utf-8 -*-
"""动态推荐清单：只给最合适的几只，自动进出。

设计原则（与仓库整体一致）：
- **硬门槛**：证据充分 + 零阻塞 + 状态为观察/持仓——不够格就不出现；
- **排序信号全透明**：每个因子打了多少分、为什么，全摆在页面上；
- **自动进出**：卡片降级/排除自动掉出清单；新卡立项且过门自动进来；
  不需要人手动维护「推荐名单」。

输出是 3-5 只 + 一句话理由（人话），作为汇总页最顶部区块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .card import Card, blocking, validate

MAX_SHORTLIST = 5


@dataclass
class ShortlistEntry:
    card: Card
    score: int
    factors: list[tuple[str, int, str]]    # (因子名, 分, 一句话)
    plain: str                              # 人话理由

    def factor_line(self) -> str:
        return " ｜ ".join(f"{name}{score}" for name, score, _ in self.factors)


def _score_card(card: Card) -> tuple[int, list[tuple[str, int, str]], str]:
    """给一张已过门的卡片打分。返回 (总分, 因子明细, 人话理由)。"""
    factors: list[tuple[str, int, str]] = []
    resonance = card.industry_context or {}
    execution = card.execution or {}
    strength = execution.get("strength") or {}
    liquidity = execution.get("liquidity") or {}
    commodities = (card.commodities or {}).get("items") or {}
    debate = card.debate or {}

    # ① 共振类型：公司自己的事最值钱，行业 beta 是放大器不是 alpha
    verdict = resonance.get("verdict", "")
    # 数据检验：公司特有 vs 行业共振无收益差异（factor_check 8 期）
    # → 不给共振类型加分，只做信息标注
    if verdict == "公司特有":
        factors.append(("共振", 0, "同行没这动静（注意：数据未显示比行业行情更好）"))
    elif verdict and verdict != "行业共振":
        factors.append(("共振", 0, "同行样本少，判断不了"))
    else:
        factors.append(("共振", 0, "行业行情，涨跌跟着行业走"))

    # ② 相对强度：数据检验显示已涨过的反而更差（追高陷阱）
    # → 跑输大盘的温和加分（没涨过头），跑赢太多的扣分
    rs = strength.get("rs")
    if rs is not None:
        if rs < -5:
            factors.append(("强度", 1, f"跑输大盘 {rs:+.0f}pct（未涨过头）"))
        elif rs > 15:
            factors.append(("强度", -1, f"跑赢大盘 {rs:+.0f}pct（已涨过多，追高陷阱）"))
        else:
            factors.append(("强度", 0, f"相对大盘 {rs:+.0f}pct"))

    # ②b RSI：超买扣分（数据支持）、超卖加分（温和）
    rsi_val = (card.execution or {}).get("rsi")
    if rsi_val is not None:
        if rsi_val > 70:
            factors.append(("RSI", -2, f"RSI {rsi_val:.0f} 超买"))
        elif rsi_val < 30:
            factors.append(("RSI", 1, f"RSI {rsi_val:.0f} 超卖（反弹潜力）"))
        else:
            factors.append(("RSI", 0, f"RSI {rsi_val:.0f}"))

    # ③ 催化剂距离：近的先看（该验证的事到了）
    today = date.today()
    nearest = min((c.date for c in card.catalysts if c.date), default="")
    if nearest:
        try:
            due = date.fromisoformat(nearest[:10])
            days = (due - today).days
            if days <= 30:
                factors.append(("催化", 2, f"催化剂 {days} 天后到期"))
            elif days <= 90:
                factors.append(("催化", 1, f"催化剂 {days} 天后到期"))
            else:
                factors.append(("催化", 0, f"催化剂 {days} 天后（还远）"))
        except ValueError:
            pass

    # ④ AI 对辩结论：模型说可进加分（但不是唯一依据）
    debate_verdict = debate.get("verdict", "")
    if "不进持仓" in debate_verdict or "不进" in debate_verdict:
        factors.append(("AI", -1, "AI 对辩认为不进持仓"))
    elif "可进持仓" in debate_verdict:
        factors.append(("AI", 2, "AI 对辩认为可进持仓研究"))
    elif "维持观察" in debate_verdict:
        factors.append(("AI", 0, "AI 对辩认为还需观察"))

    # ⑤ 盯价顺风：行业价格在涨（产品涨价 → 利润弹性）
    tailwind = any((item.get("chg_30d") or 0) > 0 for item in commodities.values())
    if commodities:
        factors.append(("盯价", 1 if tailwind else 0,
                        "行业价格30日上涨" if tailwind else "行业价格30日下跌"))

    # ⑥ 流动性：进出得去
    if liquidity.get("level") in ("无忧",):
        factors.append(("流动性", 1, "进出无冲击"))
    elif liquidity.get("level") in ("谨慎", "危险"):
        factors.append(("流动性", 0, f"流动性{liquidity['level']}"))

    score = sum(s for _, s, _ in factors)

    # 人话理由：用最强的 2-3 个因子拼一句
    top_reasons = [why for _, s, why in sorted(factors, key=lambda f: -f[1]) if s > 0][:3]
    negatives = [why for _, s, why in factors if s <= 0][:2]
    plain = ""
    if top_reasons:
        plain += "；".join(top_reasons)
    if negatives:
        plain += ("。但注意：" if plain else "注意：") + "；".join(negatives)
    if not plain:
        plain = "证据齐了，等催化剂到窗口"

    return score, factors, plain


def build_shortlist(cards: list[Card]) -> list[ShortlistEntry]:
    """构建动态推荐清单。硬门槛 + 透明排序，自动进出。"""
    entries: list[ShortlistEntry] = []
    for card in cards:
        if card.status not in ("观察", "持仓研究"):
            continue
        if card.derived_level() != "证据充分":
            continue
        if blocking(validate(card)):
            continue
        score, factors, plain = _score_card(card)
        entries.append(ShortlistEntry(card=card, score=score, factors=factors, plain=plain))
    entries.sort(key=lambda e: (-e.score, e.card.code))
    return entries[:MAX_SHORTLIST]


def shortlist_changed(previous: list[dict], current: list[ShortlistEntry]) -> bool:
    """清单有没有进出（推送用）：新进 or 掉出都算变化。"""
    prev_codes = {e.get("code") for e in previous}
    curr_codes = {e.card.code for e in current}
    return prev_codes != curr_codes
