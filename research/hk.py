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

import time
from dataclasses import dataclass, field
from datetime import date, timedelta

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
CATEGORY_RESULTS = ("季度業績", "中期業績", "年度業績", "半年度報告", "中期報告")
# 基金与噪音：不是研究对象
NOISE = ("基金", "ETF", "3456", "債券", "债", "票据", "可换股", "貨幣市場基金")


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
