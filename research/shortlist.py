# -*- coding: utf-8 -*-
"""动态清单：从过了质量门的卡片里挑出最靠前的几只，自动进出。

- **硬门槛**：证据充分 + 零阻塞 + 状态为观察/持仓，不够格就不出现；
- **得分 = 权重 × 原始信号**：信号只描述「现在是什么情况」（比如 RSI 超买），
  权重说明「这种情况历史检验下来值多少分」。权重来自 research/evolution.py；
- **每个因子为什么给分、给多少**都摆在清单上，权重为 0 的只作信息显示；
- **自动进出**：卡片降级/排除就掉出，新卡过门就进来。

大盘处于下行时这份清单只作观察名单（见 note.py 的标题处理）：仓库自己的
十三期检验说明这类票在下跌市里是放大器，此时不出建仓结论。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .card import Card, blocking, validate
from .evolution import current_weights
from .llm import ai_stance, debate_stamp

MAX_SHORTLIST = 20        # 现在没有通过检验的排序，所以把过门槛的卡都列出来（而不是
                          # 按代码随便截 5 张，把其余的藏起来）；卡片总数由自动立项的上限约束


@dataclass
class Factor:
    name: str
    signal: int        # 原始信号，-2 到 2，不含权重
    why: str           # 一句话说明
    weight: float      # 当前权重（0 = 不计分，只作信息）

    @property
    def points(self) -> float:
        return round(self.weight * self.signal, 2)


@dataclass
class ShortlistEntry:
    card: Card
    factors: list[Factor]
    score: float
    plain: str         # 人话理由

    def factor_line(self) -> str:
        """计分的因子：名称+得分；不计分但有信号的因子单列，免得被当成没用上。"""
        parts = [f"{f.name}{f.points:+g}" for f in self.factors if f.points]
        if not parts:
            return "（各因子均未计分）"
        unweighted = [f.name for f in self.factors if f.signal and not f.weight]
        if unweighted:
            parts.append("未计分：" + "、".join(unweighted))
        return " ｜ ".join(parts)


def market_regimes(cards: list[Card]) -> dict[str, dict]:
    """每个市场当前的市场状态（取该市场卡片里日期最新的那一份）。"""
    out: dict[str, dict] = {}
    for card in cards:
        regime = (card.market_context or {}).get("market_regime") or {}
        if not regime.get("state"):
            continue
        current = out.get(card.market)
        if current is None or str(regime.get("day", "")) > str(current.get("day", "")):
            out[card.market] = regime
    return out


def is_down(card: Card, regimes: dict[str, dict]) -> bool:
    """这只票所在的市场是否处于下行。状态取不到时按「不是下行」处理。"""
    return (regimes.get(card.market) or {}).get("state") == "下行"


def _card_signals(card: Card) -> list[tuple[str, int, str]]:
    """一只卡在各因子上的原始信号与说明。"""
    rows: list[tuple[str, int, str]] = []
    execution = card.execution or {}
    inputs = card.inputs or {}
    strength = execution.get("strength") or {}
    liquidity = execution.get("liquidity") or {}
    commodities = (card.commodities or {}).get("items") or {}

    verdict = (card.industry_context or {}).get("verdict", "")
    if verdict == "公司特有":
        rows.append(("共振", 0, "同行没这动静"))
    elif verdict and verdict != "行业共振":
        rows.append(("共振", 0, "同行样本少，判断不了是不是行业行情"))
    elif verdict:
        rows.append(("共振", 0, "行业行情，涨跌跟着行业走"))

    rs = strength.get("rs")
    if rs is not None:
        if rs < -5:
            rows.append(("强度", 1, f"近 60 日跑输大盘 {rs:+.0f} 个点"))
        elif rs > 15:
            rows.append(("强度", -1, f"近 60 日跑赢大盘 {rs:+.0f} 个点"))
        else:
            rows.append(("强度", 0, f"近 60 日相对大盘 {rs:+.0f} 个点"))

    rsi = execution.get("rsi")
    if rsi is not None:
        if rsi > 70:
            rows.append(("RSI", -2, f"RSI {rsi:.0f}，超买"))
        elif rsi < 30:
            rows.append(("RSI", 1, f"RSI {rsi:.0f}，超卖"))
        else:
            rows.append(("RSI", 0, f"RSI {rsi:.0f}"))

    valuation = inputs.get("valuation") or {}
    metric, pct = "PB", valuation.get("pb_percentile")
    if pct is None and valuation.get("pe_percentile") is not None:
        metric, pct = "PE", valuation["pe_percentile"]
    if pct is not None:
        # 标签写明是哪个指标：PE 取不到时用的是 PB（市净率），不能笼统写 PE
        label = "PB（市净率）" if metric == "PB" else "PE（市盈率）"
        if pct <= 30:
            rows.append(("估值", 2, f"{label} 在近 3 年 {pct:.0f}% 分位，偏低"))
        elif pct >= 70:
            rows.append(("估值", -1, f"{label} 在近 3 年 {pct:.0f}% 分位，偏高"))
        else:
            rows.append(("估值", 0, f"{label} 在近 3 年 {pct:.0f}% 分位"))

    holders = inputs.get("holders") or {}
    buys, sells = holders.get("buy_count", 0), holders.get("sell_count", 0)
    if buys or sells:
        if buys > sells:
            rows.append(("股东", 2, f"近 240 天增持类公告 {buys} 条、减持类 {sells} 条"))
        elif sells > buys:
            rows.append(("股东", -2, f"近 240 天减持类公告 {sells} 条、增持类 {buys} 条"))
        else:
            rows.append(("股东", 0, f"近 240 天增持、减持类公告各 {buys} 条"))

    volume = inputs.get("volume") or {}
    ratio = volume.get("vol_ratio_20_60")
    if volume.get("trend") in ("显著放量", "温和放量"):
        rows.append(("量能", 1, f"成交量{volume['trend']}（近 20 日均量是 60 日的 {ratio} 倍）"))
    elif volume.get("trend") in ("显著缩量", "温和缩量"):
        rows.append(("量能", -1, f"成交量{volume['trend']}（近 20 日均量是 60 日的 {ratio} 倍）"))

    # 催化剂：只看还没验证的；日期已经过了的记 0 分——过期没核对不是好消息
    pending = [c.date for c in card.catalysts if c.date and c.status == "待验证"]
    if pending:
        try:
            days = (date.fromisoformat(min(pending)[:10]) - date.today()).days
            if days < 0:
                rows.append(("催化", 0, f"催化剂已过期 {-days} 天，还没核对"))
            elif days <= 30:
                rows.append(("催化", 2, f"{days} 天后有该验证的事"))
            elif days <= 90:
                rows.append(("催化", 1, f"{days} 天后有该验证的事"))
            else:
                rows.append(("催化", 0, f"下一次该验证的事在 {days} 天后"))
        except ValueError:
            pass

    stance = ai_stance((card.debate or {}).get("verdict", ""))
    if stance:
        stamp = debate_stamp(card.debate)
        rows.append(("AI", 2 * stance,
                     f"AI 对辩（{stamp}）倾向{'正面' if stance > 0 else '负面'}"
                     if stamp else
                     f"AI 对辩倾向{'正面' if stance > 0 else '负面'}"))

    if commodities:
        tailwind = any((item.get("chg_30d") or 0) > 0 for item in commodities.values())
        rows.append(("盯价", 1 if tailwind else 0,
                     "盯的行业价格近 30 天在涨" if tailwind else "盯的行业价格近 30 天在跌"))

    level = liquidity.get("level")
    if level in ("谨慎", "危险"):
        rows.append(("流动性", -2, f"流动性{level}"))
    elif level == "无忧":
        rows.append(("流动性", 1, "进出无冲击"))

    return rows


def _plain_reason(factors: list[Factor]) -> str:
    positives = [f.why for f in sorted(factors, key=lambda f: -f.points) if f.points > 0][:3]
    negatives = [f.why for f in factors if f.points < 0][:2]
    text = "；".join(positives)
    if negatives:
        text += ("。但注意：" if text else "注意：") + "；".join(negatives)
    if not text:
        hints = [f.why for f in factors if f.signal][:3]
        text = "没有通过检验的排序依据" + (f"；供参考：{'；'.join(hints)}" if hints else "")
    return text


def score_card(card: Card, weights: dict[str, float] | None = None) -> ShortlistEntry:
    """给一张卡打分：得分 = Σ 权重 × 原始信号。"""
    if weights is None:
        weights, _ = current_weights()
    factors = [Factor(name, signal, why, float(weights.get(name, 0)))
               for name, signal, why in _card_signals(card)]
    score = round(sum(f.points for f in factors), 2)
    return ShortlistEntry(card=card, factors=factors, score=score,
                          plain=_plain_reason(factors))


def passes_gate(card: Card) -> bool:
    """硬门槛：状态在观察/持仓、结论等级证据充分、没有阻塞项、进得去出得来。

    流动性「谨慎/危险」的卡不进清单——这是能不能成交的问题（strategy-v1 的
    可投资性检查），不是收益预测，所以做成门槛而不是打分。
    """
    level = ((card.execution or {}).get("liquidity") or {}).get("level")
    return (card.status in ("观察", "持仓研究")
            and card.derived_level() == "证据充分"
            and not blocking(validate(card))
            and level not in ("谨慎", "危险"))


def stance_group(card: Card) -> int:
    """AI 对辩的表态：1 正面、0 没表态、-1 负面。只用来分组摆放，不计分。"""
    return ai_stance((card.debate or {}).get("verdict", ""))


def _ordered(entries: list[ShortlistEntry]) -> list[ShortlistEntry]:
    """得分高的在前；得分相同的按 AI 对辩的表态分组（正面 → 没表态 → 负面），
    组内按代码顺序。

    分组不是排序因子：不计分、没有权重，只是把模型明确看好的摆在一起——十几张卡
    里只有三四张是正面的，按代码排会把它们埋在中间。它同样没经过历史检验，
    页面上写明。
    """
    entries.sort(key=lambda e: (-e.score, -stance_group(e.card), e.card.code))
    return entries


def build_shortlist(cards: list[Card], weights: dict[str, float] | None = None,
                    limit: int = MAX_SHORTLIST) -> list[ShortlistEntry]:
    """动态清单：过硬门槛的卡片按得分取前几只。得分相同的见 _ordered。"""
    if weights is None:
        weights, _ = current_weights()
    entries = [score_card(card, weights) for card in cards if passes_gate(card)]
    return _ordered(entries)[:limit]


def candidates(cards: list[Card], weights: dict[str, float] | None = None) -> list[ShortlistEntry]:
    """全部过硬门槛的卡（不限数量），给清单变化检测与健康检查用。"""
    if weights is None:
        weights, _ = current_weights()
    entries = [score_card(card, weights) for card in cards if passes_gate(card)]
    return _ordered(entries)
