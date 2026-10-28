"""港股发现层（L1）。

港股没有 A 股式的业绩预告制度，所以「经营变化」信号改由三处取得：
1. 已披露财务数据的同比与单季度变化（东方财富数据中心 RPT_HKF10_FN_MAININDICATOR）
2. 分析师一致预期的变化（经 etnet 口径，akshare stock_hk_profit_forecast_et）
3. 分红与公司概况（用于可投资性检查）

**每手股数从 RPT_HKF10_INFO_SECURITYINFO.TRADE_UNIT 程序化取得**，不必手填；
但仍需使用者按交易所/券商资料复核。

HKEXnews 与东方财富的港股公告接口实测均取不到数据（见 docs 备注），
因此港股卡片里的「原始出处」目前只能引用财务数据接口与公司公告 PDF，
事件类证据需要人工补。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import requests

from .quotes import Snapshot, fetch_snapshots

DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

MIN_PRICE_HKD = 1.0
MIN_TURNOVER_HKD = 5e6
MIN_CAP_HKD = 1e9          # 低于 10 亿港元的多为流动性陷阱
MAX_CAP_HKD = 5e10         # 高于 500 亿港元的不是小资金可研究的体量
MIN_REVENUE_YOY = 30.0     # 收入同比变化门槛（%）
MIN_PROFIT_YOY = 50.0      # 归母净利同比变化门槛（%），含扭亏


class HkError(RuntimeError):
    pass


@dataclass
class HkCandidate:
    code: str
    name: str
    market: str = "HK"
    headline: str = ""
    revenue_yoy: float | None = None
    profit_yoy: float | None = None
    profit_sq: float | None = None
    revenue_sq: float | None = None
    roe: float | None = None
    report_date: str = ""
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb_ttm: float | None = None
    price: float = 0.0
    turnover: float = 0.0
    lot_size: int | None = None
    board: str = ""
    as_of: str = ""
    source: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "market": self.market,
                "headline": self.headline, "revenue_yoy": self.revenue_yoy,
                "profit_yoy": self.profit_yoy, "profit_sq": self.profit_sq,
                "revenue_sq": self.revenue_sq, "roe": self.roe,
                "report_date": self.report_date, "market_cap": self.market_cap,
                "pe_ttm": self.pe_ttm, "pb_ttm": self.pb_ttm, "price": self.price,
                "turnover": self.turnover, "lot_size": self.lot_size,
                "board": self.board, "as_of": self.as_of, "source": self.source,
                "notes": self.notes}


def _fetch_report(report_name: str, report_date: str | None = None,
                  page_size: int = 500, max_pages: int = 12) -> list[dict]:
    """分页取一个数据中心报表的全市场数据。"""
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {"reportName": report_name, "columns": "ALL", "pageSize": str(page_size),
                  "pageIndex": str(page), "client": "WEB"}
        if report_date:
            params["filter"] = f"(REPORT_DATE='{report_date}')"
        try:
            response = requests.get(DC, headers=HEADERS, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise HkError(f"{report_name} 获取失败：{exc}") from exc
        data = (payload.get("result") or {}).get("data") or []
        if not data:
            break
        rows.extend(data)
        if len(data) < page_size:
            break
    return rows


def fetch_indicators(report_date: str) -> list[dict]:
    """全市场财务指标（含同比、单季度、ROE、PE、市值）。"""
    return _fetch_report("RPT_HKF10_FN_MAININDICATOR", report_date)


def fetch_securities() -> dict[str, dict]:
    """全市场证券资料：板块与每手股数（TRADE_UNIT）。"""
    out: dict[str, dict] = {}
    for row in _fetch_report("RPT_HKF10_INFO_SECURITYINFO"):
        code = str(row.get("SECURITY_CODE") or "").strip()
        if not code:
            continue
        unit = row.get("TRADE_UNIT")
        try:
            lot = int(str(unit).strip())
        except (TypeError, ValueError):
            lot = None
        out[code] = {"name": str(row.get("SECURITY_NAME_ABBR") or "").strip(),
                     "board": str(row.get("BOARD") or "").strip(),
                     "lot_size": lot, "listing_date": str(row.get("LISTING_DATE") or "")[:10]}
    return out


def _f(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def build_candidates(indicators: list[dict], securities: dict[str, dict],
                     quotes: dict[str, Snapshot]) -> list[HkCandidate]:
    candidates: list[HkCandidate] = []
    for row in indicators:
        code = str(row.get("SECURITY_CODE") or "").strip()
        info = securities.get(code)
        if not info:
            continue
        if info.get("board") not in ("主板", ""):
            continue
        snapshot = quotes.get(code)
        if not snapshot:
            continue
        notes: list[str] = []
        revenue_yoy = _f(row.get("OPERATE_INCOME_YOY"))
        profit_yoy = _f(row.get("HOLDER_PROFIT_YOY"))
        profit_sq = _f(row.get("HOLDER_PROFIT_SQ"))
        revenue_sq = _f(row.get("OPERATE_INCOME_SQ"))
        if revenue_yoy is None and profit_yoy is None:
            continue
        moved = (abs(revenue_yoy or 0) >= MIN_REVENUE_YOY
                 or abs(profit_yoy or 0) >= MIN_PROFIT_YOY
                 or (profit_sq is not None and abs(profit_sq) > 0))
        if not moved:
            continue
        if snapshot.price < MIN_PRICE_HKD:
            notes.append(f"股价 < {MIN_PRICE_HKD} 港元")
        if snapshot.turnover < MIN_TURNOVER_HKD:
            notes.append(f"成交额 < {MIN_TURNOVER_HKD / 1e6:.0f} 百万港元")
        cap = _f(row.get("TOTAL_MARKET_CAP"))
        if cap is not None and cap < MIN_CAP_HKD:
            notes.append("总市值 < 10 亿港元，流动性与治理风险高")
        if cap is not None and cap > MAX_CAP_HKD:
            notes.append("总市值 > 500 亿港元，超出小资金研究体量")
        if not info.get("lot_size"):
            notes.append("每手股数缺失，仓位将按 0 处理")
        candidates.append(HkCandidate(
            code=code, name=info.get("name") or snapshot.name,
            headline=f"收入同比 {revenue_yoy:.0f}%、归母同比 {profit_yoy:.0f}%"
            if revenue_yoy is not None and profit_yoy is not None
            else f"收入同比 {revenue_yoy}%、归母同比 {profit_yoy}",
            revenue_yoy=revenue_yoy, profit_yoy=profit_yoy,
            profit_sq=profit_sq, revenue_sq=revenue_sq,
            roe=_f(row.get("ROE_AVG")),
            report_date=str(row.get("REPORT_DATE") or "")[:10],
            market_cap=cap, pe_ttm=_f(row.get("PE_TTM")), pb_ttm=_f(row.get("PB_TTM")),
            price=snapshot.price, turnover=snapshot.turnover,
            lot_size=info.get("lot_size"), board=info.get("board", ""),
            as_of=snapshot.as_of,
            source=f"{DC}?reportName=RPT_HKF10_FN_MAININDICATOR&filter=(REPORT_DATE='{str(row.get('REPORT_DATE'))[:10]}')",
            notes=notes))
    candidates.sort(key=lambda c: -max(abs(c.revenue_yoy or 0), abs(c.profit_yoy or 0)))
    return candidates


def discover(report_date: str, limit: int = 20, quotes: bool = True) -> list[HkCandidate]:
    indicators = fetch_indicators(report_date)
    securities = fetch_securities()
    codes = [str(r.get("SECURITY_CODE") or "").strip() for r in indicators]
    codes = [c for c in codes if c and c in securities]
    snapshots = fetch_snapshots([("HK", c) for c in codes]) if quotes else {}
    return build_candidates(indicators, securities, snapshots)[:limit]


def latest_report_date() -> str:
    """取港股最新的中期报告期（通常为当年 6 月 30 日，次年 12 月 31 日为年报）。"""
    today = date.today()
    return f"{today.year}-06-30" if today.month >= 9 else f"{today.year - 1}-12-31"
