"""Disclosure events: cninfo announcements (authoritative, dated, PDF link) and
Eastmoney data-center forecasts (structured magnitude). 两者都只做读取与留痕，不做解读。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
}
CNINFO_QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF = "http://static.cninfo.com.cn/"
EASTMONEY_DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 巨潮公告的 category 参数在检索接口上不生效（实测返回全市场），因此按标题关键词检索。
# 常用关键词与必须排除的干扰项（"业绩说明会" 也含"预告"二字）。
TITLE_NOISE = ("说明会", "取消", "更正后", "补充更正", "取消召开")
# 业绩预告类公告的标题写法不统一：预告/预盈/预亏/预增/预减/扭亏，漏一种就漏一批候选。
FORECAST_TITLES = ("业绩预告", "业绩预盈", "业绩预亏", "业绩预增", "业绩预减",
                   "年度业绩", "中期业绩", "季度业绩", "扭亏为盈")


class EventError(RuntimeError):
    pass


@dataclass(frozen=True)
class Announcement:
    code: str
    name: str
    title: str
    published: date
    url: str
    category: str


@dataclass(frozen=True)
class Forecast:
    code: str
    name: str
    notice_date: date
    report_period: str
    metric: str
    forecast_type: str
    change_lower: float | None   # 同比变动下限，百分比
    change_upper: float | None
    reason: str
    source_url: str
    amount_lower: float | None = None   # 预告金额下限（元）
    amount_upper: float | None = None   # 预告金额上限（元）


def load_org_ids() -> dict[str, str]:
    """巨潮股票列表（代码 → orgId）。个股公告查询需要 orgId。"""
    import json
    from pathlib import Path

    cache_dir = Path.home() / ".cache" / "pickahkstock"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "cninfo_stock_list.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached:
                return cached
        except ValueError:
            pass
    try:
        response = requests.get("http://www.cninfo.com.cn/new/data/szse_stock.json",
                                headers={"User-Agent": HEADERS["User-Agent"]}, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise EventError(f"巨潮股票列表获取失败：{exc}") from exc
    items = payload.get("stockList") or payload.get("data") or []
    mapping = {str(item.get("code")): str(item.get("orgId")) for item in items
               if item.get("code") and item.get("orgId")}
    cache_file.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return mapping


def fetch_stock_announcements(code: str, start: date, end: date,
                              org_id: str | None = None) -> list[Announcement]:
    """按**个股**取公告。

    市场级检索（searchkey=业绩）在宽窗口下按日期倒序返回，页数不够就永远
    取不到窗口早期的公告——业绩预告的公告日集中在报告期后 1–4 个月，正好
    落在被截断的那一段。个股查询带上 orgId 就没有这个问题。
    """
    if org_id is None:
        org_id = load_org_ids().get(code)
    if not org_id:
        return []
    results: list[Announcement] = []
    for page in range(1, 6):
        params = {"pageNum": page, "pageSize": 30, "column": "szse", "tabName": "fulltext",
                  "stock": f"{code},{org_id}", "searchkey": "", "secid": "", "plate": "",
                  "category": "", "trade": "", "seDate": f"{start}~{end}",
                  "sortName": "", "sortType": "", "isHLtitle": "false"}
        try:
            response = requests.post(CNINFO_QUERY, headers=HEADERS, params=params,
                                     timeout=30)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise EventError(f"个股公告获取失败 {code}：{exc}") from exc
        items = payload.get("announcements") or []
        if not items:
            break
        for item in items:
            stamp = item.get("announcementTime")
            if stamp is None:
                continue
            adjunct = item.get("adjunctUrl") or ""
            results.append(Announcement(
                code=str(item.get("secCode") or code).strip(),
                name=str(item.get("secName") or "").strip(),
                title=_clean(str(item.get("announcementTitle") or "")),
                published=datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).date(),
                url=(CNINFO_PDF + adjunct) if adjunct else "",
                category="个股公告"))
        if len(items) < 30:
            break
    return results


def fetch_announcements(searchkey: str, start: date, end: date, max_pages: int = 8,
                         title_must: tuple[str, ...] = (),
                         title_exclude: tuple[str, ...] = TITLE_NOISE) -> list[Announcement]:
    """巨潮公告检索。结果带公告日期与 PDF 原文链接。

    title_must / title_exclude 在本地按标题过滤：接口的 category 参数不生效，
    标题是唯一可靠的分类依据。"""
    results: list[Announcement] = []
    for page in range(1, max_pages + 1):
        try:
            response = requests.post(
                CNINFO_QUERY, headers=HEADERS, timeout=25,
                data={"pageNum": page, "pageSize": 30, "column": "szse",
                      "tabName": "fulltext", "stock": "", "searchkey": searchkey,
                      "secid": "", "plate": "", "category": "", "trade": "",
                      "seDate": f"{start}~{end}",
                      "sortName": "", "sortType": "", "isHLtitle": "false"})
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise EventError(f"巨潮公告获取失败（{searchkey}）：{exc}") from exc
        items = payload.get("announcements") or []
        if not items:
            break
        for item in items:
            stamp = item.get("announcementTime")
            if stamp is None:
                continue
            title = _clean(str(item.get("announcementTitle") or ""))
            if title_must and not any(word in title for word in title_must):
                continue
            if any(word in title for word in title_exclude):
                continue
            published = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).date()
            adjunct = item.get("adjunctUrl") or ""
            results.append(Announcement(
                code=str(item.get("secCode") or "").strip(),
                name=str(item.get("secName") or "").strip(),
                title=title,
                published=published,
                url=(CNINFO_PDF + adjunct) if adjunct else "",
                category=searchkey))
        if len(items) < 30:
            break
    return results


def _clean(text: str) -> str:
    return text.replace("<em>", "").replace("</em>", "").replace("&nbsp;", " ").strip()


def fetch_forecast_rows(report_period: str, page_size: int = 500,
                        max_pages: int = 16) -> list[dict]:
    """按**报告期**取业绩预告原始行（服务端过滤 + 切分取全 + 落盘缓存）。

    为什么必须服务端过滤：接口默认按公告日倒序，只取第一页就只覆盖最近
    的一期预告，往期报告期会全部取空。

    为什么不是翻页而是切分：接口忽略 pageIndex（翻页拿到的永远是同一批），
    早先的实现每期只取到第一页 500 行——**全量的约 7%**。发现层的候选池与
    回测的样本都建立在那个 7% 上面。现在交给 forecast_pool 用数值区间
    递归切分取全（覆盖 100%，每期约 45 秒）。

    回测模块（research/backtest.py）用的是同一套取数，两边口径才对得上。
    """
    from .forecast_pool import fetch_pool

    return fetch_pool(report_period)


def fetch_forecasts(report_period: str, page_size: int = 500) -> list[Forecast]:
    """东方财富数据中心业绩预告。report_period 形如 2026-09-30。"""
    return rows_to_forecasts(fetch_forecast_rows(report_period, page_size), report_period)


def primary_rows(rows: list[dict]) -> list[dict]:
    """从多指标行里挑出**主指标**行：归母 > 扣非 > 营收，口径固定。

    发现层、回测层、共振层必须用同一套口径，否则对照无效。
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        key = (code, str(row.get("REPORT_DATE") or "")[:10])
        grouped.setdefault(key, []).append(row)
    out: list[dict] = []
    for items in grouped.values():
        try:
            chosen = min(items, key=lambda r: (metric_rank(str(r.get("PREDICT_FINANCE") or "")),
                                              -abs(float(r.get("ADD_AMP_LOWER") or 0))))
        except (TypeError, ValueError):
            chosen = items[0]
        out.append(chosen)
    return out


def rows_to_forecasts(rows: list[dict], report_period: str = "") -> list[Forecast]:
    """原始行 → Forecast 列表。发现层与共振层共用同一批行，口径才不会分叉。"""
    forecasts = []
    for row in rows:
        period = str(row.get("REPORT_DATE") or "")[:10]
        if report_period and period and period != report_period:
            continue
        notice = str(row.get("NOTICE_DATE") or "")[:10]
        forecasts.append(Forecast(
            code=str(row.get("SECURITY_CODE") or "").strip(),
            name=str(row.get("SECURITY_NAME_ABBR") or "").strip(),
            notice_date=date.fromisoformat(notice) if notice else date.min,
            report_period=period or report_period,
            metric=str(row.get("PREDICT_FINANCE") or ""),
            forecast_type=str(row.get("PREDICT_TYPE") or ""),
            change_lower=_f(row.get("ADD_AMP_LOWER")),
            change_upper=_f(row.get("ADD_AMP_UPPER")),
            reason=str(row.get("CHANGE_REASON_EXPLAIN") or "").strip(),
            source_url=f"https://data.eastmoney.com/bbsj/yjyg.html",
            amount_lower=_f(row.get("PREDICT_AMT_LOWER")),
            amount_upper=_f(row.get("PREDICT_AMT_UPPER"))))
    return forecasts


# 预告指标的优先级：归母净利润口径优先，避免在归母/扣非/营收之间挑到「幅度最大」
# 的那一条——那会把低基数暴增的公司顶到候选前面（基线检验里就是这么出问题的）。
METRIC_PRIORITY = ("归属于上市公司股东的净利润", "归属母公司股东的净利润",
                   "扣除非经常性损益后的净利润", "净利润", "营业收入")


def metric_rank(metric: str) -> int:
    for index, name in enumerate(METRIC_PRIORITY):
        if metric == name:
            return index
    return len(METRIC_PRIORITY)


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
