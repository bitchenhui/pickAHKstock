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


def fetch_forecasts(report_period: str, page_size: int = 200) -> list[Forecast]:
    """东方财富数据中心业绩预告。report_period 形如 2026-09-30。"""
    url = (f"{EASTMONEY_DC}?reportName=RPT_PUBLIC_OP_NEWPREDICT&columns=ALL"
           f"&pageSize={page_size}&sortColumns=NOTICE_DATE&sortTypes=-1"
           f"&source=WEB&client=WEB")
    try:
        response = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=25)
        response.raise_for_status()
        rows = (response.json().get("result") or {}).get("data") or []
    except (requests.RequestException, ValueError) as exc:
        raise EventError(f"业绩预告获取失败：{exc}") from exc
    forecasts = []
    for row in rows:
        period = str(row.get("REPORT_DATE") or "")[:10]
        if period and period != report_period:
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
            source_url=f"https://data.eastmoney.com/bbsj/yjyg.html"))
    return forecasts


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
