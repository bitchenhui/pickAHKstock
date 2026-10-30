"""推荐清单：发现层输出的最终形态。

「候选列表」只是过滤结果，「推荐清单」要能回答三个问题：
- **为什么是它**：触发事件、方向、证据出处、预告绝对额与变化幅度
- **买不买得进**：一手金额占本金的比例（§51 检验里强预告组有 122 次因整手买不进）
- **市场怎么看**：分析师覆盖与预期，独立于公司自述的第三方证据

排序按 §51 八期检验固化的规则（docs/backtest-v3.md）：
买不进 → 一次性因素 → 绝对额过小 → 变化幅度小的优先（温和优先）→ 同幅度按绝对额降序。

这不是买入名单。卡片状态与账户检查才决定是否进入持仓研究；清单只负责
"先看哪个"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .analysts import Expectation, fetch as fetch_expectations
from .discover import Candidate


@dataclass
class Recommendation:
    candidate: Candidate
    direction: str = ""            # 正向 / 负向
    coverage: int = 0              # 分析师覆盖机构数
    eps_forecast: list = field(default_factory=list)
    latest_rating: str = ""
    notes: list = field(default_factory=list)
    next_actions: list = field(default_factory=list)

    @property
    def code(self) -> str:
        return self.candidate.code

    @property
    def name(self) -> str:
        return self.candidate.name

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "market": self.candidate.market,
                "direction": self.direction, "event": self.candidate.forecast_type,
                "change": self.candidate.change_lower,
                "forecast_amount": self.candidate.forecast_amount,
                "price": self.candidate.price, "turnover": self.candidate.turnover,
                "market_cap": self.candidate.market_cap,
                "lot_cost_pct": self.candidate.lot_cost_pct,
                "unaffordable": self.candidate.unaffordable,
                "one_off": self.candidate.one_off,
                "small_amount": self.candidate.small_amount,
                "industry": self.candidate.industry,
                "resonance": self.candidate.resonance,
                "resonance_peers": self.candidate.resonance_peers,
                "resonance_same_sign": self.candidate.resonance_same_sign,
                "coverage": self.coverage, "eps_forecast": self.eps_forecast,
                "latest_rating": self.latest_rating,
                "pdf_url": self.candidate.pdf_url,
                "notes": self.notes, "next_actions": self.next_actions,
                "candidate": self.candidate.to_dict()}


def _direction(candidate: Candidate) -> str:
    kind = candidate.forecast_type or ""
    if kind in ("预增", "略增", "扭亏", "减亏", "续盈", "预盈"):
        return "正向"
    if kind in ("预减", "略减", "首亏", "续亏"):
        return "负向"
    return ""


def _next_actions(candidate: Candidate, coverage: int) -> list[str]:
    """给每个候选一条「下一步该做什么」——推荐清单必须可执行。"""
    actions = ["读巨潮公告原文（业绩变动原因），核对是经营性还是一次性"]
    if candidate.one_off:
        actions.append("含重组/处置/补助等一次性因素：与重组前口径不可比，先确认可比性")
    if candidate.small_amount:
        actions.append(f"预告净利润下限仅 {(candidate.forecast_amount or 0) / 1e8:.2f} 亿，"
                       "同比幅度多半来自低基数，先核对绝对额")
    if candidate.unaffordable:
        actions.append(f"一手约 {candidate.price * 100:,.0f} 元占本金 "
                       f"{candidate.lot_cost_pct:.1%}，超过单只上限：当前账户规模建不了仓")
    if candidate.resonance == "行业共振":
        actions.append("同行业集体改善：这个「好」多半是行业给的，"
                       "先问自己若行业反转还剩多少 alpha")
    elif candidate.resonance == "公司特有":
        actions.append("同行业没有同向变化：若属实，这是公司自己的经营变化，重点追这条")
    if coverage == 0:
        actions.append("无机构覆盖：缺少第三方预期这条证据，需自行补")
    else:
        actions.append("对照分析师预期与公司自述是否一致，不一致处优先追问")
    actions.append("做单季拆解：累计数会掩盖动能在哪一段转向")
    return actions


def build(candidates: list[Candidate], with_analysts: bool = True) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    for candidate in candidates:
        recommendation = Recommendation(candidate=candidate, direction=_direction(candidate))
        if with_analysts:
            expectation: Expectation = fetch_expectations(candidate.code, candidate.name)
            recommendation.coverage = expectation.coverage
            recommendation.eps_forecast = expectation.eps_forecast
            recommendation.latest_rating = expectation.latest_rating
            recommendation.notes.extend(expectation.notes)
        recommendation.notes.extend(candidate.notes)
        recommendation.next_actions = _next_actions(candidate, recommendation.coverage)
        recommendations.append(recommendation)
    return recommendations


def render(recommendations: list[Recommendation]) -> str:
    lines = []
    for index, item in enumerate(recommendations, start=1):
        candidate = item.candidate
        amount = f"{(candidate.forecast_amount or 0) / 1e8:.2f} 亿"
        lines.append(f"{index}. {candidate.code} {candidate.name}"
                     f"［{item.direction or '方向待判'}］ {candidate.forecast_type}"
                     f"（{candidate.change_lower:+.0f}%）"
                     f"｜预告额 {amount}"
                     f"｜价 {candidate.price:.2f}"
                     f"｜覆盖 {item.coverage} 家"
                     f"｜{item.latest_rating or '—'}")
        if candidate.industry:
            lines.append(f"   行业共振：{candidate.industry}｜{candidate.resonance or '—'}"
                         f"（同行业 {candidate.resonance_peers} 家发预告，"
                         f"同向 {candidate.resonance_same_sign} 家）")
        if item.eps_forecast:
            row = item.eps_forecast[0]
            mean = row.get("均值")
            industry = row.get("行业平均数")
            try:
                comparison = f"（行业均值 {industry}）"
            except (TypeError, ValueError):
                comparison = ""
            lines.append(f"   预期：{row.get('年度')} 年 EPS 均值 {mean}{comparison}"
                         f"，机构 {row.get('机构数')} 家")
        if candidate.pdf_url:
            lines.append(f"   原文：{candidate.pdf_url}")
        for note in item.notes:
            lines.append(f"   ⚠ {note}")
        for action in item.next_actions:
            lines.append(f"   → {action}")
        lines.append("")
    return "\n".join(lines)
