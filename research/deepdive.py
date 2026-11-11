# -*- coding: utf-8 -*-
"""deep-dive：把分析一只股票所需的全部数据面一次灌进卡片。

定位：发现层把标的摆到面前之后，补全一张卡片要手动跑七八条命令
（financials / reference / expectations / context / industry / risks / governance）。
这个模块把「取数」合并成一步，人只做判断题——path、recurring_judgment、
情景与反证仍然必须由人写（card.py 的质量门会拦）。

每一面独立成败：一个接口挂了不影响其余面，失败原因进步骤报告；
卡片最后统一 save 一次（部分成功也落盘）。

诚实边界不变：工具只取数不判断；扫描段（风险公告/治理/经营回顾）
是「需要人工过目」的原始输出，不是结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .analysts import fetch as fetch_expectations
from .backtest import forecasts_by_report_period
from .card import Card
from .context import fetch_capital, fetch_peers
from .events import primary_rows
from .hk import HK_METRIC_LABELS, fetch_stock_financials
from .hk import discover as hk_discover
from .industry import co_occurrence, hk_resonance
from .quotes import fetch_snapshots
from .ths import KEY_SUBJECTS, fetch_financials, key_series, reference_levels
from .workflow import _regime_note


def amount_label(value) -> str:
    """资金规模的中文标签（亿元，带符号）。cli 的 context 命令也用它。"""
    if value is None:
        return "—"
    return f"{value / 1e8:+.2f} 亿"


# ---------------------------------------------------------------- 各数据面


def fill_financials(card: Card, periods: int = 8, quarter_periods: int = 8) -> str:
    """财务主表序列写进卡片（A 股 THS，港股东财）。只取数，不下判断。

    返回数据源说明。扭亏/减亏类候选尤其要看单季度口径——累计数会骗人
    （恒星科技：前三季度累计扭亏，Q3 单季扣非 -4991 万）。
    """
    track = card.financials
    if card.market == "HK":
        data = fetch_stock_financials(card.code)
        track.source = data["source"]
        track.periods = data["periods"]
        # 卡片里一律用中文科目名，渲染与判断才有一致的口径
        track.metrics = {HK_METRIC_LABELS.get(name, name): series
                         for name, series in data["metrics"].items()}
        track.quarter_metrics = {}
    else:
        data = fetch_financials(card.code)
        track.source = data["source"]
        track.periods = list(data["periods"])[:periods]
        track.quarter_periods = list(data.get("simple_periods", []))[:quarter_periods]
        # 只保留展示区间内的报告期：卡片要能被人读完，不能把 F10 全历史塞进来
        track.metrics = {name: {p: series[p] for p in track.periods if p in series}
                         for name, series in key_series(data, subjects=tuple(KEY_SUBJECTS)).items()}
        track.quarter_metrics = {
            name: {p: series[p] for p in track.quarter_periods if p in series}
            for name, series in key_series(data, block="simple", subjects=tuple(KEY_SUBJECTS)).items()}
    track.notes = [n for n in track.notes if not n.startswith("财务序列已于")]
    track.notes.append(f"财务序列已于 {date.today().isoformat()} 取自 {track.source}")
    return track.source


def fill_reference(card: Card) -> dict:
    """ATR 与 2ATR 参考失效线（仅 A 股，THS 日线不覆盖港股）。"""
    levels = reference_levels(card.code)
    context = dict(card.market_context or {})
    context.update({"close": levels["close"], "atr": levels["atr"],
                    "atr_pct": levels["atr_pct"], "stop_reference": levels["stop_reference"],
                    "bars": levels["bars"], "bars_as_of": levels["last_day"],
                    "bars_source": levels["source"]})
    card.market_context = context
    return levels


def fill_quote(card: Card) -> dict:
    """行情快照刷新（价/成交额/市值；港股含每手股数）+ 市场状态快照。

    市场状态是时点信息，A/HK 各看自己的指数（沪深300/恒生指数），
    每次 deep-dive 顺手刷新——它决定仓位要不要按 0.5× 折算。
    """
    snapshot = fetch_snapshots([(card.market, card.code)]).get(card.code)
    if snapshot is None:
        raise RuntimeError("行情快照未取到")
    context = dict(card.market_context or {})
    context.update({"price": snapshot.price, "turnover": snapshot.turnover,
                    "market_cap": snapshot.market_cap, "pe": snapshot.pe,
                    "pb": snapshot.pb, "as_of": snapshot.as_of})
    if card.market == "HK" and snapshot.lot_size:
        context["lot_size"] = snapshot.lot_size
    context["market_regime"] = _regime_note(card.market)
    card.market_context = context
    return {"price": snapshot.price, "turnover": snapshot.turnover,
            "market_cap": snapshot.market_cap, "lot_size": snapshot.lot_size}


def fill_expectations(card: Card):
    """分析师预期写进卡片（THS 盈利预测 + 东财研报；接口只覆盖 A 股）。"""
    expectation = fetch_expectations(card.code, card.name, card.market)
    card.expectations = expectation.to_dict()
    return expectation


def fill_context(card: Card):
    """资本轨迹 + 同业对照写进卡片。港股的 capital 接口不支持，失败会带 note。"""
    capital = fetch_capital(card.code)
    peers = fetch_peers(card.code, card.market)
    capital_data = capital.to_dict()
    capital_data["capex_label"] = amount_label(capital.capex_at())
    capital_data["operating_cf_label"] = amount_label(capital.operating_cf_at())
    capital_data["fcf_label"] = amount_label(capital.fcf_at())
    capital_data["construction_label"] = (capital.construction[0] if capital.construction else "—")
    capital_data["fixed_assets_label"] = (capital.fixed_assets[0] if capital.fixed_assets else "—")
    card.capital = capital_data
    card.peers = peers.to_dict()
    return capital, peers


def _infer_period(card: Card) -> str:
    """行业共振的报告期：默认取卡片财务序列里最新的报告期。"""
    periods = getattr(card.financials, "periods", None) or []
    for period in periods:
        text = str(period)[:10]
        try:
            date.fromisoformat(text)
            return text
        except ValueError:
            continue
    return ""


def fill_industry(card: Card, period: str = "", threshold: float = 30.0,
                  level: str = "second"):
    """行业共振写进卡片。A 股走申万口径；港股走东财行业（内部横比）。

    港股的 hk_resonance 此前只在建卡时手工算过一次，这里把它接进工具链：
    peers 来自港股 120 天披露窗口（池子小，判不了就照实写「单点不可辨」）。
    """
    if card.market == "HK":
        candidates = hk_discover(since_days=120)
        peers = [{"code": c.code, "name": c.name, "direction": c.direction}
                 for c in candidates]
        context = hk_resonance(card.code, peers, days=120)
        card.industry_context = context
        return context
    period = period or _infer_period(card)
    if not period:
        raise RuntimeError("无法推断报告期：卡片没有财务序列，请先跑 financials 或给 --period")
    rows = primary_rows(forecasts_by_report_period(date.fromisoformat(period)))
    if not rows:
        raise RuntimeError(f"{period} 报告期没有取到预告数据")
    resonance = co_occurrence(card.code, rows, report_period=period,
                              threshold=threshold, level=level, name=card.name)
    card.industry_context = resonance.to_dict()
    return resonance


# ---------------------------------------------------------------- 编排


@dataclass
class Step:
    face: str
    status: str              # ok / fail / skip
    note: str = ""


@dataclass
class DiveResult:
    card: Card
    steps: list[Step] = field(default_factory=list)

    def ok(self, face: str) -> bool:
        return any(s.face == face and s.status == "ok" for s in self.steps)


def deep_dive(market: str, code: str, period: str = "", with_scan: bool = True,
              days: int = 240) -> DiveResult:
    """一张卡片跑完所有数据面。返回步骤报告；卡片已保存（含部分成功）。"""
    card = Card.load(market, code)
    result = DiveResult(card=card)

    def run(face: str, func, *args, **kwargs):
        try:
            value = func(*args, **kwargs)
        except Exception as exc:      # 每面独立成败，一个接口不拖垮其余
            result.steps.append(Step(face, "fail", f"{type(exc).__name__}: {exc}"))
            return None
        result.steps.append(Step(face, "ok"))
        return value

    run("行情快照", fill_quote, card)
    run("财务序列", fill_financials, card)
    if market == "A":
        run("参考线与市场状态", fill_reference, card)
    else:
        result.steps.append(Step("参考线与市场状态", "skip", "港股无 THS 日线，ATR 参考线不适用"))
    run("分析师预期", fill_expectations, card)
    run("资本轨迹与同业", fill_context, card)
    run("行业共振", fill_industry, card, period=period)
    from .commodity import WATCHLIST, fill_commodities

    if card.code in WATCHLIST:
        run("盯的价格", fill_commodities, card)
    else:
        result.steps.append(Step(
            "盯的价格", "skip", f"{card.code} 不在价格映射表（WATCHLIST），该盯什么价需要人定"))
    from .execution import fill_execution

    run("执行层（流动性/相对强度）", fill_execution, card)

    if with_scan:
        # 扫描结果结构化留痕进卡片：终端会滚走，卡片才是资产
        scans, scan_steps = collect_scans(card, days=days)
        card.scans = scans
        result.steps.extend(scan_steps)

    card.updated = date.today().isoformat()
    card.save()
    return result


def collect_scans(card: Card, days: int = 240) -> tuple[dict, list[Step]]:
    """风险公告 + 治理（港股为经营回顾对比）扫描，返回可序列化 dict 与步骤报告。

    每段独立成败：一段接口失败只记 note，不拖垮其余段。
    """
    from .governance import scan as governance_scan
    from .hk_business import diff_reviews, fetch_reviews
    from .risk import scan_risks

    scans: dict = {"as_of": date.today().isoformat(), "days": days}
    steps: list[Step] = []

    try:
        events = scan_risks(card.code, card.name, since_days=days, market=card.market)
        scans["risks"] = [event.to_dict() for event in events]
        steps.append(Step("风险公告扫描", "scan" if events else "ok",
                          f"{len(events)} 条" if events else f"近 {days} 天未发现风险类标题"))
    except Exception as exc:
        steps.append(Step("风险公告扫描", "fail", f"{type(exc).__name__}: {exc}"))

    if card.market == "A":
        try:
            items = governance_scan(card.code, card.name)
            scans["governance"] = [item.to_dict() for item in items]
            steps.append(Step("治理与筹码扫描", "scan" if items else "ok",
                              f"{len(items)} 条" if items else "未取到治理数据"))
        except Exception as exc:
            steps.append(Step("治理与筹码扫描", "fail", f"{type(exc).__name__}: {exc}"))
    else:
        steps.append(Step("治理与筹码扫描", "skip", "港股无巨潮治理数据接口"))
        try:
            reviews = fetch_reviews(card.code, card.market)
            diff = diff_reviews(reviews) if reviews else None
            scans["business"] = {
                "latest": reviews[0].period if reviews else "",
                "diff": diff.to_dict() if diff else None}
            steps.append(Step("经营回顾对比", "scan" if diff else "ok",
                              f"最近 {reviews[0].period}" if reviews else "没有取到经营回顾"))
        except Exception as exc:
            steps.append(Step("经营回顾对比", "fail", f"{type(exc).__name__}: {exc}"))
    return scans, steps


def scan_outputs(card: Card, days: int = 240) -> list[tuple[str, list[str]]]:
    """卡片里已留痕的扫描结果 → CLI 打印行。deep-dive 落卡，打印与卡片同一份。"""
    scans = card.scans or {}
    out: list[tuple[str, list[str]]] = []
    risks = scans.get("risks") or []
    lines = [f"  [{r.get('category')}] {r.get('published')} {r.get('title')}"
             f"  {r.get('url')}" for r in risks] or [
        f"  近 {scans.get('days', days)} 天未发现风险类公告标题（不等于没有风险：关键词扫描会漏）"]
    out.append((f"风险公告（{len(risks)} 条）", lines))
    governance = scans.get("governance")
    if governance is not None:
        lines = [f"  [{g.get('severity')}] {g.get('category')}：{g.get('detail')}"
                 for g in governance] or ["  未取到治理数据（缺口不等于没有风险）"]
        out.append((f"治理与筹码（{len(governance)} 条）", lines))
    business = scans.get("business")
    if business is not None:
        lines = []
        if business.get("diff"):
            diff = business["diff"]
            changed = {k: v for k, v in (diff.get("theme_delta") or {}).items() if v}
            lines = [f"  最近 {business.get('latest', '')}，对比 "
                     f"{diff.get('prior_period')} → {diff.get('period')}",
                     f"  主题词频变化：{changed or '无明显变化'}"]
            lines += [f"  · {item}" for item in (diff.get("numbers") or [])[:6]]
        elif business.get("latest"):
            lines = [f"  最近 {business['latest']}，没有去年同期可对比"]
        else:
            lines = ["  没有取到经营回顾"]
        out.append(("经营回顾对比（港股）", lines))
    return out
