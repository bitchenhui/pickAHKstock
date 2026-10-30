"""L1 候选发现：按「经营变化」而非量价形态提出研究对象。

每条候选都必须带：公开日期、原始出处（PDF 链接）、变动量级、流动性与市值。
筛选只用来排除不可投资的对象，不产生任何买卖结论。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .events import (Announcement, Forecast, FORECAST_TITLES, fetch_announcements,
                     fetch_forecast_rows, metric_rank, rows_to_forecasts)
from .quotes import Snapshot, fetch_snapshots

MAIN_BOARD = ("00", "60")
GOOD_FORECAST_TYPES = ("预增", "略增", "扭亏", "减亏", "续盈", "预盈")
MIN_CHANGE_PCT = 30.0
# 预告净利润下限的绝对额门槛（元）。低于此值的多为低基数暴增：同比幅度看着惊人，
# 绝对额却小到不改变基本面——基线检验里这类是最差的一批。
MIN_FORECAST_AMOUNT = 5e7
MIN_TURNOVER = 2e7
MIN_PRICE = 2.0
CAP_LOW, CAP_HIGH = 2e9, 3e10


class DiscoveryError(RuntimeError):
    pass


@dataclass
class Candidate:
    code: str
    name: str
    market: str = "A"
    forecast_type: str = ""
    change_lower: float | None = None
    change_upper: float | None = None
    forecast_amount: float | None = None   # 预告净利润下限（元）
    one_off: bool = False                  # 原因含重组/处置/补助等一次性因素
    small_amount: bool = False             # 预告净利润绝对额低于门槛（低基数噪音）
    lot_cost_pct: float | None = None      # 一手金额占账户本金的比例（小资金买不进的关键）
    unaffordable: bool = False             # 一手金额超过单只仓位上限，账户建不了仓
    metric: str = ""
    metrics: list[str] = field(default_factory=list)
    notice_date: date | None = None
    reason: str = ""
    source_url: str = ""
    pdf_url: str = ""
    pdf_title: str = ""
    price: float = 0.0
    turnover: float = 0.0
    market_cap: float | None = None
    pe: float | None = None
    pb: float | None = None
    as_of: str = ""
    industry: str = ""                 # 申万二级行业
    resonance: str = ""                # 行业共振 / 公司特有 / 单点不可辨
    resonance_note: str = ""           # 一句话说明
    resonance_peers: int = 0           # 同行业发预告家数
    resonance_same_sign: int = 0       # 其中与本公司同向的
    notes: list[str] = field(default_factory=list)

    @property
    def headline(self) -> str:
        if self.change_lower is None:
            return self.forecast_type or "经营变化"
        return f"{self.forecast_type} ≥{self.change_lower:.0f}%"

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "market": self.market,
            "headline": self.headline, "forecast_type": self.forecast_type,
            "metric": self.metric, "metrics": self.metrics,
            "change_lower": self.change_lower, "change_upper": self.change_upper,
            "forecast_amount": self.forecast_amount, "one_off": self.one_off,
            "small_amount": self.small_amount,
            "lot_cost_pct": self.lot_cost_pct, "unaffordable": self.unaffordable,
            "notice_date": self.notice_date.isoformat() if self.notice_date else None,
            "reason": self.reason, "source_url": self.source_url,
            "pdf_url": self.pdf_url, "pdf_title": self.pdf_title,
            "industry": self.industry, "resonance": self.resonance,
            "resonance_note": self.resonance_note,
            "resonance_peers": self.resonance_peers,
            "resonance_same_sign": self.resonance_same_sign,
            "price": self.price, "turnover": self.turnover, "market_cap": self.market_cap,
            "pe": self.pe, "pb": self.pb, "as_of": self.as_of, "notes": self.notes,
        }


# 重组、资产处置、政府补助等会让利润同比失真：幅度大不等于经营拐点。
NON_RECURRING_MARKERS = ("重组", "资产处置", "出售", "政府补助", "非经常性",
                         "合并范围", "股权", "公允价值", "重整")


def is_researchable(snapshot: Snapshot) -> tuple[bool, str]:
    """排除不可投资对象。返回 (是否保留, 排除原因)。"""
    name = snapshot.name.upper()
    if snapshot.market == "A":
        if not snapshot.code.isdigit() or len(snapshot.code) != 6:
            return False, "代码非 6 位"
        if not snapshot.code.startswith(MAIN_BOARD):
            return False, "非主板（创业板/科创板/北交所）"
        if name.startswith(("ST", "*ST")) or "退" in snapshot.name:
            return False, "ST 或退市标记"
        if snapshot.price < MIN_PRICE:
            return False, f"股价 < {MIN_PRICE}"
        cap = snapshot.market_cap
        if cap is None:
            return False, "缺总市值"
        if not CAP_LOW <= cap <= CAP_HIGH:
            return False, "总市值不在 20–300 亿元"
    if snapshot.turnover < MIN_TURNOVER:
        return False, f"成交额 < {MIN_TURNOVER / 1e8:.2f} 亿"
    return True, ""


def _match_announcement(code: str, notice_date: date | None,
                        announcements: list[Announcement]) -> Announcement | None:
    """按代码 + 公告日期就近匹配原文；业绩预告日期与东财口径可能差 1–2 天。"""
    pool = [a for a in announcements if a.code == code]
    if not pool:
        return None
    if notice_date:
        near = [a for a in pool if abs((a.published - notice_date).days) <= 5]
        if near:
            pool = near
    # 业绩预告类标题有多种写法，优先取真正含「业绩预」的
    return sorted(pool, key=lambda a: (-("业绩预" in a.title), a.published))[0]


def build_candidates(forecasts: list[Forecast], snapshots: dict[str, Snapshot],
                     announcements: list[Announcement],
                     min_amount: float = MIN_FORECAST_AMOUNT,
                     capital: float = 0.0,
                     position_pct: float = 0.05) -> tuple[list[Candidate], dict]:
    """把业绩预告变成候选。

    两处关键改动（都来自基线检验 docs/backtest-v1.md）：
    1. **主指标按口径固定**（归母 > 扣非 > 营收），不再取「幅度最大」的那条——
       否则一家「营收 +30%、归母 +500%」的公司会以 +500% 被顶到最前面，
       低基数暴增就是这样被系统性地选出来的
    2. **加绝对额门槛**：预告净利润下限过小的，多为低基数噪音，剔除但计数可见

    返回 (候选, 统计)，统计里含被剔除的低基数数量，不静默丢弃。
    """
    grouped: dict[str, list[Forecast]] = {}
    small_amount = 0
    for forecast in forecasts:
        if forecast.forecast_type not in GOOD_FORECAST_TYPES:
            continue
        if forecast.change_lower is None or forecast.change_lower < MIN_CHANGE_PCT:
            continue
        if (min_amount and forecast.amount_lower is not None
                and forecast.amount_lower < min_amount):
            # 不剔除，只标记并降权：基线检验里绝对额门槛的收益证据不稳健
            # （9 格改善、5 持平、6 变差），不足以当硬规则；但绝对额小的
            # 更可能是低基数噪音，排后面是合理的
            small_amount += 1
        grouped.setdefault(forecast.code, []).append(forecast)

    candidates: list[Candidate] = []
    for code, items in grouped.items():
        snapshot = snapshots.get(code)
        if not snapshot:
            continue
        keep, reason = is_researchable(snapshot)
        if not keep:
            continue
        primary = min(items, key=lambda f: (metric_rank(f.metric), -(f.change_lower or 0)))
        announcement = _match_announcement(code, primary.notice_date, announcements)
        notes: list[str] = []
        if not announcement:
            notes.append("未取得巨潮原文链接，需人工补出处")
        one_off = any(marker in primary.reason for marker in NON_RECURRING_MARKERS)
        if one_off:
            notes.append("变动原因含重组/处置/补助等一次性因素：需区分经常性经营改善与一次性损益")
        if primary.metric not in ("归属于上市公司股东的净利润", "净利润"):
            notes.append(f"主指标为「{primary.metric}」，非归母净利润口径，需在卡片中改用归母口径核对")
        if (primary.amount_lower is not None and min_amount
                and primary.amount_lower < min_amount):
            notes.append(f"预告净利润下限仅 {primary.amount_lower / 1e8:.2f} 亿元，"
                         f"同比幅度多半来自低基数，绝对额需先核对")
        if primary.change_lower is not None and primary.change_lower >= 300:
            notes.append(f"同比幅度 {primary.change_lower:.0f}% 属极端值，多为低基数所致，须核对绝对额与经营性")
        # 可买性：一手金额占本金的比例。超过单只仓位上限就等于账户建不了仓——
        # §51 组合层检验里强预告组有 122 次因整手买不进（基线仅 23 次）
        lot_cost = snapshot.price * 100          # A 股 100 股一手
        lot_cost_pct = (lot_cost / capital) if capital else None
        unaffordable = bool(lot_cost_pct is not None and lot_cost_pct > position_pct)
        if unaffordable:
            notes.append(f"一手约 {lot_cost:,.0f} 元，占本金 {lot_cost_pct:.1%}，"
                         f"超过单只上限 {position_pct:.0%}：按当前账户规模买不进")
        candidates.append(Candidate(
            code=code, name=primary.name or snapshot.name,
            forecast_type=primary.forecast_type,
            change_lower=primary.change_lower, change_upper=primary.change_upper,
            metric=primary.metric, metrics=sorted({f.metric for f in items}),
            notice_date=primary.notice_date, reason=primary.reason,
            source_url=primary.source_url,
            pdf_url=announcement.url if announcement else "",
            pdf_title=announcement.title if announcement else "",
            price=snapshot.price, turnover=snapshot.turnover,
            market_cap=snapshot.market_cap, pe=snapshot.pe, pb=snapshot.pb,
            as_of=snapshot.as_of, notes=notes,
            forecast_amount=primary.amount_lower, one_off=one_off,
            small_amount=bool(primary.amount_lower is not None
                              and primary.amount_lower < min_amount),
            lot_cost_pct=lot_cost_pct, unaffordable=unaffordable))
    # 排序用**预告绝对额**而不是同比幅度：幅度会把低基数暴增顶到最前面。
    # 一次性因素（重组/处置/补助）的候选降权但保留——它们仍有研究价值。
    # 排序依据（每条都有检验支持，见 docs/backtest-v3.md）：
    # 1. 买不进的（按当前账户规模）排最后——组合层检验里强预告组 122 次因
    #    整手买不进，小资金吃不到右尾
    # 2. 一次性因素（重组/处置/补助）排后——§30 要求区分经营性与一次性
    # 3. 绝对额过小的排后——低基数噪音
    # 4. **变化幅度小的优先**：事件研究（门槛 30% 优于 50%/100%）与组合层排序
    #    对照（幅度升序 +28.24% 最好、幅度降序 +15.96% 最差）两个独立读数
    #    指向同一件事——极端幅度是噪音，不是信号
    # 5. 同幅度按预告绝对额降序（金额大的信息量更大）
    return sorted(candidates, key=lambda c: (c.unaffordable, c.one_off, c.small_amount,
                                           c.change_lower if c.change_lower is not None else 0.0,
                                           -(c.forecast_amount or 0))), {
        "small_amount": small_amount}


def attach_documents(candidates: list[Candidate], report_period: str) -> None:
    """给候选补上巨潮原文 PDF 链接（按个股查询）。

    市场级检索在宽窗口下按日期倒序返回，页数不够就取不到窗口早期的公告——
    业绩预告的公告日集中在报告期后 1–4 个月，正好落在被截断的那一段。
    所以这里按个股查（带 orgId），只查清单里的这几只。
    """
    from .events import fetch_stock_announcements

    period_end = datetime.strptime(report_period, "%Y-%m-%d").date()
    start = period_end - timedelta(days=45)
    end = min(date.today(), period_end + timedelta(days=150))
    for candidate in candidates:
        try:
            items = fetch_stock_announcements(candidate.code, start, end)
        except Exception:
            continue
        best = None
        for item in items:
            if not any(word in item.title for word in
                       ("业绩预告", "业绩预增", "业绩预盈", "业绩预减", "业绩预亏", "预告")):
                continue
            if best is None or abs((item.published - candidate.notice_date).days) < \
                    abs((best.published - candidate.notice_date).days):
                best = item
        if best is not None:
            candidate.pdf_url = best.url
            candidate.pdf_title = best.title
            candidate.notes = [n for n in candidate.notes
                               if "未取得巨潮原文链接" not in n]


def discover(report_period: str, since_days: int = 90, limit: int = 15,
             min_amount: float = MIN_FORECAST_AMOUNT, capital: float = 0.0,
             position_pct: float = 0.05) -> tuple[list[Candidate], dict]:
    today = date.today()
    rows = fetch_forecast_rows(report_period)
    forecasts = rows_to_forecasts(rows, report_period)
    if not forecasts:
        raise DiscoveryError(f"{report_period} 没有取到业绩预告数据")
    codes = sorted({f.code for f in forecasts})
    snapshots = fetch_snapshots([("A", code) for code in codes])
    # 原文窗口由**报告期**推导：业绩预告的公告日集中在报告期后 1–4 个月
    # （年报预告在次年 1 月、中报预告在 7 月）。若按「今天往前 since_days 天」
    # 取窗口，往期报告期的公告会整段落空——原文链接就再也匹配不上。
    period_end = datetime.strptime(report_period, "%Y-%m-%d").date()
    start = period_end - timedelta(days=45)
    end = min(today, period_end + timedelta(days=150))
    # 巨潮按证券代码倒序返回，页数不够会漏掉后段代码的候选；这里取够页数再匹配原文
    announcements = fetch_announcements("业绩", start, end,
                                        max_pages=30, title_must=FORECAST_TITLES)
    candidates, stats = build_candidates(forecasts, snapshots, announcements,
                                         min_amount, capital, position_pct)
    stats["unaffordable"] = sum(1 for c in candidates if c.unaffordable)
    annotate_resonance(candidates, rows, report_period)
    candidates = candidates[:limit]
    attach_documents(candidates, report_period)
    return candidates, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="按经营变化发现研究候选")
    parser.add_argument("--period", default="2026-09-30", help="报告期，如 2026-09-30")
    parser.add_argument("--since-days", type=int, default=90)
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()

    capital = 0.0
    position_pct = 0.05
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
        print("没有符合筛选条件的候选。缺失不等于没有机会——先核对数据源与筛选口径。")
        return
    print(f"候选 {len(candidates)} 只（报告期 {args.period}，按预告绝对额排序，仅为研究顺序）")
    print(f"已剔除低基数噪音（预告净利润下限 < {MIN_FORECAST_AMOUNT / 1e8:.2f} 亿）："
          f"{stats.get('dropped_small_amount', 0)} 条")
    print()
    for c in candidates:
        cap = f"{c.market_cap / 1e8:.0f}亿" if c.market_cap else "—"
        turn = f"{c.turnover / 1e8:.2f}亿"
        print(f"{c.code} {c.name:8s} {c.headline:14s} 市值{cap:>7s} 成交{turn:>7s} "
              f"价{c.price:>7.2f} 公告 {c.notice_date}")
        print(f"    指标 {c.metric} · 出处 {c.pdf_url or c.source_url}")
        if c.reason:
            print(f"    原因 {c.reason[:90]}")
        for note in c.notes:
            print(f"    ⚠ {note}")
        print()


if __name__ == "__main__":
    main()


def annotate_resonance(candidates: list[Candidate], rows: list[dict],
                       report_period: str) -> None:
    """给候选标注行业共振：这是行业 beta，还是公司自己的事。

    这一层直接回答 §30 的核心问题。共振高说明「变好」是行业给的，
    选谁的区别变小，而且行业反转时一起跌；共振低才说明这是个股逻辑。
    """
    from .events import primary_rows
    from .industry import resonance_from_pairs

    if not candidates:
        return
    pairs = []
    for row in primary_rows(rows):
        code = str(row.get("SECURITY_CODE") or "").strip()
        raw = row.get("ADD_AMP_LOWER")
        if not code or raw is None:
            continue
        try:
            pairs.append((code, float(raw)))
        except (TypeError, ValueError):
            continue
    if not pairs:
        return
    for candidate in candidates:
        try:
            res = resonance_from_pairs(candidate.code, pairs,
                                       report_period=report_period,
                                       name=candidate.name,
                                       company_change=candidate.change_lower)
        except Exception:
            continue
        candidate.industry = res.industry
        candidate.resonance = res.verdict
        candidate.resonance_note = res.note
        candidate.resonance_peers = res.peers_with_forecast
        candidate.resonance_same_sign = res.peers_same_sign
