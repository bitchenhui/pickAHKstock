"""治理与筹码结构：把「关键词扫描」升级成结构化硬数据。

关键词扫公告能发现「有诉讼」，但说不出诉讼几次、涉及多少钱、是谁在质押、
筹码是在集中还是分散。巨潮这几个接口给的是结构化数字，比标题可靠：

- 股东户数（季度报告期）：本期/上期人数与增减 → 筹码集中度
- 股权质押：出质人、质押数量、占总股本比例 → 平仓压力
- 诉讼 / 担保：按市场与统计区间，次数与金额 → 法律与或有负债
- 机构评级与目标价：只作市场预期对照，不是我们的输出

口径提醒：这些是二手结构化数据（巨潮接口已归集），结论仍要回原始公告核对。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import requests


class GovernanceError(RuntimeError):
    pass


@dataclass
class GovernanceItem:
    category: str      # 股东户数 / 股权质押 / 诉讼 / 担保 / 机构评级
    detail: str
    date: str = ""
    severity: str = "中"   # 高 / 中 / 低
    source: str = ""

    def to_dict(self) -> dict:
        return {"category": self.category, "detail": self.detail,
                "date": self.date, "severity": self.severity, "source": self.source}


def _ak():
    import akshare as ak

    return ak


def latest_holder_period(today: date | None = None) -> str:
    """股东户数按季度报告期披露：0331 / 0630 / 0930 / 1231。取最近一个已披露的。"""
    today = today or date.today()
    candidates = []
    for year in (today.year, today.year - 1):
        candidates += [f"{year}0930", f"{year}0630", f"{year}0331", f"{year}1231"]
    return candidates[0]


def fetch_holder_numbers(period: str | None = None) -> dict[str, dict]:
    """全市场股东户数：{代码: {人数, 上期人数, 增幅, 变动日期}}。"""
    period = period or latest_holder_period()
    frame = _ak().stock_hold_num_cninfo(date=period)
    out: dict[str, dict] = {}
    for row in frame.to_dict("records"):
        raw = str(row.get("证券代码") or "").strip()
        if not raw:
            continue
        # 接口有时返回整数形式（2044），统一补零成 6 位
        code = raw.zfill(6) if raw.isdigit() else raw
        out[code] = {
            "name": str(row.get("证券简称") or "").strip(),
            "holders": row.get("本期股东人数"),
            "previous": row.get("上期股东人数"),
            "change_pct": row.get("股东人数增幅"),
            "per_capita": row.get("本期人均持股数量"),
            "date": str(row.get("变动日期") or "")[:10],
            "period": period,
        }
    return out


def fetch_pledges(start: str = "20250101") -> list[dict]:
    """股权质押记录。

    注意：该接口只返回**单个报告期**的新增质押（date=某季末），不是区间累计，
    而且巨潮归集并不完整——历史存量的质押常常只在公告标题里可见。
    所以这里按最近几个季末各取一次，与公告标题扫描互补。
    """
    frame = _ak().stock_cg_equity_mortgage_cninfo(date=start)
    return frame.to_dict("records")


def recent_periods(today: date | None = None, count: int = 4) -> list[str]:
    """最近几个季度报告期（按巨潮归集的季末口径）。"""
    today = today or date.today()
    periods: list[str] = []
    year, quarter = today.year, (today.month - 1) // 3 + 1
    while len(periods) < count:
        periods.append(f"{year}{['0331', '0630', '0930', '1231'][quarter - 1]}")
        quarter -= 1
        if quarter == 0:
            quarter, year = 4, year - 1
    return periods


def fetch_lawsuits(market: str = "全部", start: str | None = None,
                   end: str | None = None) -> list[dict]:
    """诉讼统计：按市场与统计区间，给出次数与金额。"""
    end = end or date.today().strftime("%Y%m%d")
    start = start or (date.today() - timedelta(days=365)).strftime("%Y%m%d")
    frame = _ak().stock_cg_lawsuit_cninfo(symbol=market, start_date=start, end_date=end)
    return frame.to_dict("records")


def fetch_guarantees(market: str = "全部", start: str | None = None,
                     end: str | None = None) -> list[dict]:
    """对外担保统计。"""
    end = end or date.today().strftime("%Y%m%d")
    start = start or (date.today() - timedelta(days=365)).strftime("%Y%m%d")
    frame = _ak().stock_cg_guarantee_cninfo(symbol=market, start_date=start, end_date=end)
    return frame.to_dict("records")


def _severity_for_holders(change) -> str:
    """股东户数大增 = 筹码分散（负面）；大减 = 集中（需同时看股价）。"""
    try:
        value = float(change)
    except (TypeError, ValueError):
        return "低"
    if value >= 30:
        return "高"
    if value >= 10:
        return "中"
    return "低"


def scan(code: str, name: str = "", market: str = "",
         holder_period: str | None = None,
         pledge_start: str = "20250101") -> list[GovernanceItem]:
    """对一只标的做治理与筹码扫描。

    任何一项取不到都不阻断其余项——缺口由调用方记入卡片的数据缺口。
    """
    items: list[GovernanceItem] = []
    period = holder_period or latest_holder_period()

    try:
        # 股东户数按季度披露，最近一期常常还没归集完（本机实测 20260930 只有
        # 3279 只，而 20260630 有 5248 只），所以从新到旧回退直到命中该代码。
        holder_period_used = None
        for period in (holder_period,) if holder_period else [
                "20260930", "20260630", "20260331"]:
            holders = fetch_holder_numbers(period)
            row = holders.get(code)
            if row:
                change = row.get("change_pct")
                items.append(GovernanceItem(
                    category="股东户数",
                    detail=f"{period} 股东 {row.get('holders')} 户，上期 {row.get('previous')} 户，"
                           f"增减 {change}%；人均持股 {row.get('per_capita')} 股",
                    date=row.get("date", ""),
                    severity=_severity_for_holders(change),
                    source=f"巨潮资讯 股东户数（{period}）"))
                holder_period_used = period
                break
        if holder_period_used is None:
            items.append(GovernanceItem(
                category="股东户数", detail="最近三个季度报告期均未取到该代码的股东户数",
                severity="低", source="巨潮资讯 股东户数（缺）"))
    except Exception:
        pass

    try:
        for period in recent_periods():
            for row in fetch_pledges(period):
                if str(row.get("股票代码") or "").strip() != code:
                    continue
                ratio = row.get("占总股本比例")
                items.append(GovernanceItem(
                    category="股权质押",
                    detail=f"{row.get('公告日期')} {row.get('出质人')} 质押 {row.get('质押数量')} 股，"
                           f"占总股本 {ratio}%",
                    date=str(row.get("公告日期") or "")[:10],
                    severity="高" if (ratio or 0) >= 10 else "中",
                    source="巨潮资讯 股权质押"))
    except Exception:
        pass

    for fetcher, category in ((fetch_lawsuits, "诉讼"), (fetch_guarantees, "担保")):
        try:
            for row in fetcher(market or "全部"):
                if str(row.get("证券代码") or "").strip() != code:
                    continue
                if category == "诉讼":
                    count = row.get("诉讼次数")
                    amount = row.get("诉讼金额")
                    ratio = None
                    unit_note = ""
                else:
                    count = row.get("担保笔数")
                    amount = row.get("担保金额")
                    ratio = row.get("担保金融占净资产比例")
                    unit_note = f"，占归母净资产 {ratio}%"
                items.append(GovernanceItem(
                    category=category,
                    detail=f"统计区间 {row.get('公告统计区间')}：{count} 次，金额 {amount}（万元）{unit_note}",
                    date=str(row.get("公告统计区间") or "")[-10:],
                    severity="高" if (count or 0) else "低",
                    source=f"巨潮资讯 {category}统计"))
        except Exception:
            pass

    return items
