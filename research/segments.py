"""分产品收入与毛利：把「增长从哪来」拆开看。

策略稿 §30 要求区分「景气反弹」与「竞争力提升」，§30 还要看毛利变化是否来自
可重复的业务。利润表的两个数（收入、毛利率）合在一起能做一个粗但可靠的
四象限判断：

  收入↑ 毛利率↑  → 提价或结构改善（价格贡献为主，需再核对是否可持续）
  收入↑ 毛利率↓  → 以量换价（放量但牺牲单位盈利）
  收入↓ 毛利率↑  → 主动收缩低毛利业务（结构改善，但规模在缩）
  收入↓ 毛利率↓  → 双杀

注意：这是**代理判断**，不是量价拆分。真正的单价×销量拆分只在定期报告正文或
招股书里，本模块拿不到，所以结论必须回到原文核对。
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

MAINOP_TYPE = {"1": "按行业", "2": "按产品", "3": "按地区"}


class SegmentError(RuntimeError):
    pass


@dataclass
class SegmentRow:
    report_date: str
    report_name: str
    kind: str            # 按行业 / 按产品 / 按地区
    item: str
    income: float | None
    share: float | None
    gross_margin: float | None

    def to_dict(self) -> dict:
        return {"report_date": self.report_date, "report_name": self.report_name,
                "kind": self.kind, "item": self.item, "income": self.income,
                "share": self.share, "gross_margin": self.gross_margin}


@dataclass
class SegmentChange:
    item: str
    kind: str
    income_yoy: float | None
    margin_change: float | None   # 百分点
    verdict: str                  # 四象限判断

    def to_dict(self) -> dict:
        return {"item": self.item, "kind": self.kind, "income_yoy": self.income_yoy,
                "margin_change": self.margin_change, "verdict": self.verdict}


def _num(value) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def secucode(code: str, market: str = "A") -> str:
    if market == "HK":
        return f"{code}.HK"
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


def fetch_segments(code: str, market: str = "A", report_date: str | None = None,
                   limit: int = 40) -> list[SegmentRow]:
    """分产品/行业/地区的收入、占比与毛利率。

    datacenter 带过滤的查询每次要 60–150 秒，所以结果按代码落盘缓存。
    """
    import json
    from pathlib import Path

    cache = Path.home() / ".cache" / "pickahkstock" / "segments"
    cache.mkdir(parents=True, exist_ok=True)
    cache_file = cache / f"{code}_{report_date or 'all'}.json"
    if cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
        except ValueError:
            raw = None
    else:
        raw = None
    if raw is None:
        params = {"reportName": "RPT_F10_FN_MAINOP", "columns": "ALL", "pageSize": "500",
                  "pageIndex": "1", "client": "WEB",
                  "sortColumns": "REPORT_DATE", "sortTypes": "-1",
                  "filter": f"(SECUCODE=\"{secucode(code, market)}\")"}
        try:
            response = requests.get(DC, headers=HEADERS, params=params, timeout=200)
            response.raise_for_status()
            raw = (response.json().get("result") or {}).get("data") or []
        except (requests.RequestException, ValueError) as exc:
            raise SegmentError(f"主营构成获取失败 {code}：{exc}") from exc
        cache_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    rows: list[SegmentRow] = []
    for item in raw[:limit]:
        rows.append(SegmentRow(
            report_date=str(item.get("REPORT_DATE") or "")[:10],
            report_name=str(item.get("REPORT_NAME") or ""),
            kind=MAINOP_TYPE.get(str(item.get("MAINOP_TYPE")), "其他"),
            item=str(item.get("ITEM_NAME") or ""),
            income=_num(item.get("MAIN_BUSINESS_INCOME")),
            share=_num(item.get("MBI_RATIO")),
            gross_margin=_num(item.get("GROSS_RPOFIT_RATIO"))))
    return rows


def _period_key(report_date: str) -> str:
    """把报告期归到「年×季度」，用于同比匹配：2025-06-30 → 2025Q2。"""
    if len(report_date) < 10:
        return report_date
    year, month = report_date[:4], report_date[5:7]
    quarter = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}.get(month, month)
    return f"{year}{quarter}"


def compare(rows: list[SegmentRow], kind: str = "按产品") -> list[SegmentChange]:
    """最新报告期 vs 去年同期，逐项算收入同比与毛利率变动。"""
    periods = sorted({_period_key(r.report_date) for r in rows if r.kind == kind},
                     reverse=True)
    if len(periods) < 2:
        return []
    latest = periods[0]
    # 基线是最新期间的**去年同期**，不是上一个期间——同比才可比
    prior_year = f"{int(latest[:4]) - 1}{latest[4:]}"

    def table(period: str) -> dict[str, SegmentRow]:
        return {r.item: r for r in rows if r.kind == kind and _period_key(r.report_date) == period}

    current, previous = table(latest), table(prior_year)
    changes: list[SegmentChange] = []
    for item, row in current.items():
        if row.income is None:
            continue
        old = previous.get(item)
        income_yoy = None
        margin_change = None
        if old and old.income:
            income_yoy = (row.income - old.income) / abs(old.income) * 100
        if old and old.gross_margin is not None and row.gross_margin is not None:
            margin_change = row.gross_margin - old.gross_margin
        changes.append(SegmentChange(item=item, kind=kind, income_yoy=income_yoy,
                                     margin_change=margin_change,
                                     verdict=_verdict(income_yoy, margin_change)))
    return changes


def _verdict(income_yoy: float | None, margin_change: float | None) -> str:
    if income_yoy is None and margin_change is None:
        return "无可比数据"
    up_income = (income_yoy or 0) > 5
    down_income = (income_yoy or 0) < -5
    up_margin = (margin_change or 0) > 0.5
    down_margin = (margin_change or 0) < -0.5
    if up_income and up_margin:
        return "收入↑毛利↑：提价或结构改善，价格贡献为主"
    if up_income and down_margin:
        return "收入↑毛利↓：以量换价，放量但牺牲单位盈利"
    if down_income and up_margin:
        return "收入↓毛利↑：主动收缩低毛利业务，结构改善但规模在缩"
    if down_income and down_margin:
        return "收入↓毛利↓：量价双杀"
    return "变化不显著"
