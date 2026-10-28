"""港股发现层（L1）：以「盈利警告 / 正面盈利预告」为事件源。

港股的经营变化预告是**强制披露**的：盈警与盈喜都挂在同一个机器字段
`column_name == "盈利警告"` 下，标题才是自由文本（正面盈利预告 / 盈利预警 /
盈利警告 / 内幕消息盈利警告）。所以判定要靠类别字段，不要靠标题猜。

数据来源（均为公开、免账号）：
- 东方财富公告接口 np-anotice-stock/np-anotice-pc，ann_type=H,H_Fund
  原文详情页 data.eastmoney.com/notices/detail/{code}/{art_code}.html
  原文 PDF   pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf
- 腾讯行情 qt.gtimg.cn（港股 78 段字段，含每手股数与总市值）

已知坑：
- 该接口并发请求会触发 WAF（HTTP 567 + HTML），必须串行 + 间隔
- begin_time/end_time 才是日期过滤；start_time 无效
- HKEXnews 的 titleSearchServlet 实测取不到数据，不作为依赖
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import requests

from .quotes import Snapshot, fetch_snapshots

ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
DETAIL_URL = "https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"
PDF_URL = "https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

MIN_PRICE_HKD = 1.0
MIN_TURNOVER_HKD = 5e6
MIN_CAP_HKD = 1e9
MAX_CAP_HKD = 5e10

# 机器可判的公告类别（column_name），标题不可靠
CATEGORY_ALERT = "盈利警告"      # 盈警与盈喜共用
CATEGORY_RESULTS = ("季度業績", "中期業績", "年度業績", "半年度報告", "中期報告",
                    "年度業績公佈", "全年業績公佈")
# 基金与噪音：不是研究对象
NOISE = ("基金", "ETF", "3456", "債券", "债", "票据", "可换股", "貨幣市場基金")
# GEM（创业板）代码以 08 开头：主板研究里排除
GEM_PREFIX = "08"


class HkError(RuntimeError):
    pass


@dataclass
class HkEvent:
    code: str
    name: str
    title: str
    published: date
    category: str
    art_code: str
    detail_url: str = ""
    pdf_url: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "title": self.title,
                "published": self.published.isoformat(), "category": self.category,
                "art_code": self.art_code, "detail_url": self.detail_url,
                "pdf_url": self.pdf_url}


@dataclass
class HkCandidate:
    code: str
    name: str
    market: str = "HK"
    event: str = ""
    category: str = ""
    published: date | None = None
    detail_url: str = ""
    pdf_url: str = ""
    price: float = 0.0
    turnover: float = 0.0
    market_cap: float | None = None
    turnover_rate: float | None = None
    lot_size: int | None = None
    as_of: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def headline(self) -> str:
        return self.event[:40]

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "market": self.market,
                "headline": self.headline, "category": self.category,
                "published": self.published.isoformat() if self.published else None,
                "detail_url": self.detail_url, "pdf_url": self.pdf_url,
                "price": self.price, "turnover": self.turnover,
                "market_cap": self.market_cap, "turnover_rate": self.turnover_rate,
                "lot_size": self.lot_size, "as_of": self.as_of, "notes": self.notes}


def fetch_events(start: date, end: date, max_pages: int = 60,
                 categories: tuple[str, ...] = (CATEGORY_ALERT,)) -> list[HkEvent]:
    """抓取区间内的港股公告，只保留指定机器类别（默认盈利警告）。

    接口按公告日期倒序返回，所以可以按日期提前收手。
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    events: list[HkEvent] = []
    for page in range(1, max_pages + 1):
        params = {"page_size": "50", "page_index": str(page),
                  "ann_type": "H,H_Fund", "client_source": "web",
                  "begin_time": start.strftime("%Y-%m-%d"),
                  "end_time": end.strftime("%Y-%m-%d")}
        try:
            response = session.get(ANN_URL, params=params, timeout=25)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise HkError(f"港股公告获取失败：{exc}") from exc
        data = payload.get("data") or {}
        items = data.get("list") or []
        if not items:
            break
        for item in items:
            event = _to_event(item)
            if event is None:
                continue
            if event.published < start:
                continue
            events.append(event)
        time.sleep(0.35)
    if categories:
        events = [e for e in events if e.category in categories]
    return events


def _to_event(item: dict) -> HkEvent | None:
    codes = item.get("codes") or []
    if not codes:
        return None
    code = str(codes[0].get("stock_code") or "").strip()
    if not code:
        return None
    title = str(item.get("title") or "").strip()
    if any(noise in title for noise in NOISE):
        return None
    column_names = [str(c.get("column_name") or "") for c in (item.get("columns") or [])]
    category = next((c for c in column_names if c), "")
    if not category:
        return None
    stamp = str(item.get("notice_date") or "")[:10]
    try:
        published = date.fromisoformat(stamp)
    except ValueError:
        published = date.min
    art_code = str(item.get("art_code") or "").strip()
    return HkEvent(code=code, name=str(codes[0].get("short_name") or "").strip(),
                   title=title, published=published, category=category,
                   art_code=art_code,
                   detail_url=DETAIL_URL.format(code=code, art_code=art_code),
                   pdf_url=PDF_URL.format(art_code=art_code) if art_code else "")


def build_candidates(events: list[HkEvent], quotes: dict[str, Snapshot]) -> list[HkCandidate]:
    candidates: list[HkCandidate] = []
    for event in events:
        if event.code.startswith(GEM_PREFIX):
            continue
        snapshot = quotes.get(event.code)
        if not snapshot:
            continue
        notes: list[str] = []
        if snapshot.price < MIN_PRICE_HKD:
            notes.append(f"股价 < {MIN_PRICE_HKD} 港元")
        if snapshot.turnover < MIN_TURNOVER_HKD:
            notes.append(f"成交额 < {MIN_TURNOVER_HKD / 1e6:.0f} 百万港元")
        if snapshot.market_cap is None:
            notes.append("总市值未能从行情核对（总股本×价格与市值字段不符），暂不判断体量")
        else:
            if snapshot.market_cap < MIN_CAP_HKD:
                notes.append("总市值 < 10 亿港元，流动性与治理风险高")
            if snapshot.market_cap > MAX_CAP_HKD:
                notes.append("总市值 > 500 亿港元，超出小资金研究体量")
        if not snapshot.lot_size:
            notes.append("每手股数缺失，仓位将按 0 处理")
        candidates.append(HkCandidate(
            code=event.code, name=event.name or snapshot.name,
            event=event.title, category=event.category, published=event.published,
            detail_url=event.detail_url, pdf_url=event.pdf_url,
            price=snapshot.price, turnover=snapshot.turnover,
            market_cap=snapshot.market_cap, turnover_rate=snapshot.turnover_rate,
            lot_size=snapshot.lot_size, as_of=snapshot.as_of, notes=notes))
    return candidates


def discover(since_days: int = 60, limit: int = 20,
             categories: tuple[str, ...] = (CATEGORY_ALERT,)) -> list[HkCandidate]:
    end = date.today()
    start = end - timedelta(days=since_days)
    events = fetch_events(start, end, categories=categories)
    codes = sorted({e.code for e in events})
    quotes = fetch_snapshots([("HK", code) for code in codes])
    return build_candidates(events, quotes)[:limit]


def discover_by_results(since_days: int = 60, limit: int = 20) -> list[HkCandidate]:
    """以**业绩公告**为事件源。

    盈警/盈喜只是结果公布前的预告，且港股的预告多来自微盘；业绩公告
    （季度/中期/年度）是强制披露，覆盖全市场，能覆盖到流动性好的标的。
    代价是它本身不是「变化」信号，变化要靠业绩数字比对——所以候选的
    排序按公告类型与流动性，具体数字需回公告原文或财务接口核对。
    """
    end = date.today()
    start = end - timedelta(days=since_days)
    events = fetch_events(start, end, categories=CATEGORY_RESULTS)
    # 同一家公司同一报告期只留一条，避免重复
    seen: set[tuple[str, str]] = set()
    unique: list[HkEvent] = []
    for event in events:
        key = (event.code, event.title[:24])
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    codes = sorted({e.code for e in unique})
    quotes = fetch_snapshots([("HK", code) for code in codes])
    return build_candidates(unique, quotes)[:limit]


# ---------- 财务变化信号（结构化，比 PDF 抽文可靠） ----------

DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "hk"
DC_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

MIN_REVENUE_YOY = 30.0
MIN_PROFIT_YOY = 50.0


def fetch_financial_snapshot(report_date: str, page_size: int = 500,
                             max_pages: int = 8) -> list[dict]:
    """港股市场级财务快照（东方财富数据中心）。

    带日期过滤的查询每次要 60–150 秒（服务端全表扫描），所以结果按报告期
    落盘缓存：第一次跑几分钟，之后是秒开。
    """
    cache_file = CACHE_DIR / f"RPT_HKF10_FN_MAININDICATOR_{report_date}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except ValueError:
            pass
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {"reportName": "RPT_HKF10_FN_MAININDICATOR", "columns": "ALL",
                  "pageSize": str(page_size), "pageIndex": str(page), "client": "WEB",
                  "sortColumns": "REPORT_DATE", "sortTypes": "-1",
                  "filter": f"(REPORT_DATE='{report_date}')"}
        try:
            response = requests.get(DC, headers=DC_HEADERS, params=params, timeout=180)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise HkError(f"港股财务快照获取失败：{exc}") from exc
        data = (payload.get("result") or {}).get("data") or []
        if not data:
            break
        rows.extend(data)
        if len(data) < page_size:
            break
    if rows:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def _num(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(rows: list[dict], report_date: str) -> list[dict]:
    """同一只股票同一报告期可能有多行（重述版本），收敛成一行。"""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        if code:
            grouped.setdefault(code, []).append(row)
    best: list[dict] = []
    for code, items in grouped.items():
        exact = [r for r in items if str(r.get("REPORT_DATE", ""))[:10] == report_date]
        pool = exact or items
        pool.sort(key=lambda r: str(r.get("REPORT_DATE") or ""), reverse=True)
        best.append(pool[0])
    return best


def discover_by_financials(report_date: str, limit: int = 20) -> list[HkCandidate]:
    """按已披露财务数据的同比变化找港股候选。

    这是港股更贴近 A 股「业绩预告」的等效信号：与盈警公告不同，它覆盖
    全市场（含流动性好的标的），且数字来自结构化接口而不是 PDF 抽文
    ——港股公告 PDF 的中文是 CID 字体，pypdf 抽不出可读文本。
    """
    rows = _dedupe(fetch_financial_snapshot(report_date), report_date)
    picked: list[dict] = []
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        if not code or code.startswith(GEM_PREFIX):
            continue
        revenue_yoy = _num(row.get("OPERATE_INCOME_YOY"))
        profit_yoy = _num(row.get("HOLDER_PROFIT_YOY"))
        if revenue_yoy is None and profit_yoy is None:
            continue
        if not (abs(revenue_yoy or 0) >= MIN_REVENUE_YOY
                or abs(profit_yoy or 0) >= MIN_PROFIT_YOY):
            continue
        picked.append(row)
    picked.sort(key=lambda r: -max(abs(_num(r.get("OPERATE_INCOME_YOY")) or 0),
                                    abs(_num(r.get("HOLDER_PROFIT_YOY")) or 0)))
    picked = picked[:limit * 2]
    quotes = fetch_snapshots([("HK", str(r.get("SECURITY_CODE")).strip()) for r in picked])
    candidates: list[HkCandidate] = []
    for row in picked:
        code = str(row.get("SECURITY_CODE")).strip()
        snapshot = quotes.get(code)
        if not snapshot:
            continue
        revenue_yoy = _num(row.get("OPERATE_INCOME_YOY"))
        profit_yoy = _num(row.get("HOLDER_PROFIT_YOY"))
        revenue_abs = _num(row.get("OPERATE_INCOME"))
        profit_abs = _num(row.get("HOLDER_PROFIT"))
        notes: list[str] = []
        # 可投资性硬约束：这些决定能不能买，排序时优先把它们排掉
        blockers: list[str] = []
        # 同比幅度大但绝对额小 = 低基数或扭亏，不是经营改善
        small_base = profit_abs is not None and abs(profit_abs) < 5e8
        if abs(profit_yoy or 0) >= 1000 and small_base:
            notes.append(f"归母同比 {profit_yoy:.0f}% 但归母绝对额仅 "
                         f"{abs(profit_abs) / 1e8:.2f} 亿港元：低基数或扭亏，不是经营改善")
        elif abs(profit_yoy or 0) >= 1000:
            notes.append(f"归母同比 {profit_yoy:.0f}% 由上年低基数造成（绝对额 "
                         f"{abs(profit_abs or 0) / 1e8:.2f} 亿港元），看绝对额不看倍数")
        prior = _num(row.get("HOLDER_PROFIT_SQ"))
        if profit_abs is not None and profit_abs > 0 and prior is not None and prior < 0:
            notes.append("上年同期为亏损，本次为扭亏：性质与持续增长不同")
        if snapshot.price < MIN_PRICE_HKD:
            blockers.append(f"股价 < {MIN_PRICE_HKD} 港元")
        if snapshot.turnover < MIN_TURNOVER_HKD:
            blockers.append(f"成交额 < {MIN_TURNOVER_HKD / 1e6:.0f} 百万港元")
        cap = snapshot.market_cap
        if cap is not None and cap > MAX_CAP_HKD:
            blockers.append("总市值 > 500 亿港元，超出小资金研究体量")
        if cap is not None and cap < MIN_CAP_HKD:
            blockers.append("总市值 < 10 亿港元，流动性与治理风险高")
        if not snapshot.lot_size:
            blockers.append("每手股数缺失，仓位将按 0 处理")
        notes = blockers + notes
        candidates.append(HkCandidate(
            code=code, name=snippet_name(row) or snapshot.name,
            event=f"收入同比 {revenue_yoy:.0f}%、归母同比 {profit_yoy:.0f}%"
            if revenue_yoy is not None and profit_yoy is not None
            else f"收入同比 {revenue_yoy}%、归母同比 {profit_yoy}",
            category="财务变化", published=report_date,
            detail_url=f"{DC}?reportName=RPT_HKF10_FN_MAININDICATOR&filter=(REPORT_DATE='{report_date}')",
            pdf_url="",
            price=snapshot.price, turnover=snapshot.turnover, market_cap=cap,
            turnover_rate=snapshot.turnover_rate, lot_size=snapshot.lot_size,
            as_of=snapshot.as_of, notes=notes))
    # 排序：先按可投资性硬约束（有条目的排后面），再按变化幅度。
    # 大幅变化常常来自小基数，所以不能只看倍数排序。
    magnitude = {}
    for row in picked:
        code = str(row.get("SECURITY_CODE")).strip()
        magnitude[code] = max(abs(_num(row.get("OPERATE_INCOME_YOY")) or 0),
                              abs(_num(row.get("HOLDER_PROFIT_YOY")) or 0))
    candidates.sort(key=lambda c: (len(blockers_of(c)) > 0, -magnitude.get(c.code, 0.0)))
    return candidates[:limit]


def blockers_of(candidate: HkCandidate) -> list[str]:
    """从候选的标记里挑出可投资性硬约束（其余是解读性提示）。"""
    keys = ("股价 <", "成交额 <", "总市值 >", "总市值 <", "每手股数缺失")
    return [n for n in candidate.notes if any(k in n for k in keys)]


def snippet_name(row: dict) -> str:
    return str(row.get("SECURITY_NAME_ABBR") or "").strip()
