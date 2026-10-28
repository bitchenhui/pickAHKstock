"""L1 候选发现：按「经营变化」而非量价形态提出研究对象。

每条候选都必须带：公开日期、原始出处（PDF 链接）、变动量级、流动性与市值。
筛选只用来排除不可投资的对象，不产生任何买卖结论。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, timedelta

from .events import Announcement, Forecast, fetch_announcements, fetch_forecasts
from .quotes import Snapshot, fetch_snapshots

MAIN_BOARD = ("00", "60")
GOOD_FORECAST_TYPES = ("预增", "略增", "扭亏", "减亏", "续盈", "预盈")
MIN_CHANGE_PCT = 30.0
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
            "notice_date": self.notice_date.isoformat() if self.notice_date else None,
            "reason": self.reason, "source_url": self.source_url,
            "pdf_url": self.pdf_url, "pdf_title": self.pdf_title,
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
    return sorted(pool, key=lambda a: (-("业绩预告" in a.title), a.published))[0]


def build_candidates(forecasts: list[Forecast], snapshots: dict[str, Snapshot],
                     announcements: list[Announcement]) -> list[Candidate]:
    grouped: dict[str, list[Forecast]] = {}
    for forecast in forecasts:
        if forecast.forecast_type not in GOOD_FORECAST_TYPES:
            continue
        if forecast.change_lower is None or forecast.change_lower < MIN_CHANGE_PCT:
            continue
        grouped.setdefault(forecast.code, []).append(forecast)

    candidates: list[Candidate] = []
    for code, items in grouped.items():
        snapshot = snapshots.get(code)
        if not snapshot:
            continue
        keep, reason = is_researchable(snapshot)
        if not keep:
            continue
        primary = max(items, key=lambda f: abs(f.change_lower or 0))
        announcement = _match_announcement(code, primary.notice_date, announcements)
        notes: list[str] = []
        if not announcement:
            notes.append("未取得巨潮原文链接，需人工补出处")
        if any(marker in primary.reason for marker in NON_RECURRING_MARKERS):
            notes.append("变动原因含重组/处置/补助等一次性因素：需区分经常性经营改善与一次性损益")
        if primary.metric not in ("归属于上市公司股东的净利润", "净利润"):
            notes.append(f"主指标为「{primary.metric}」，非归母净利润口径，需在卡片中改用归母口径核对")
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
            as_of=snapshot.as_of, notes=notes))
    # 排序只用于安排研究顺序：变化幅度优先，同幅按成交额降序（流动性）。
    return sorted(candidates, key=lambda c: (-(c.change_lower or 0), -c.turnover))


def discover(report_period: str, since_days: int = 90, limit: int = 15) -> list[Candidate]:
    today = date.today()
    start = today - timedelta(days=since_days)
    forecasts = fetch_forecasts(report_period)
    if not forecasts:
        raise DiscoveryError(f"{report_period} 没有取到业绩预告数据")
    codes = sorted({f.code for f in forecasts})
    snapshots = fetch_snapshots([("A", code) for code in codes])
    announcements = fetch_announcements("业绩预告", start, today,
                                        title_must=("业绩预告",))
    candidates = build_candidates(forecasts, snapshots, announcements)
    return candidates[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description="按经营变化发现研究候选")
    parser.add_argument("--period", default="2026-09-30", help="报告期，如 2026-09-30")
    parser.add_argument("--since-days", type=int, default=90)
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()

    candidates = discover(args.period, args.since_days, args.limit)
    if not candidates:
        print("没有符合筛选条件的候选。缺失不等于没有机会——先核对数据源与筛选口径。")
        return
    print(f"候选 {len(candidates)} 只（报告期 {args.period}，按变化幅度排序，仅为研究顺序）\n")
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
