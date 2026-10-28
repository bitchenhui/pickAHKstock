"""同花顺财务与行情数据接入（免登录、免账号）。

只用公开接口：
- F10 财务主表 basic.10jqka.com.cn/api/stock/finance/{code}_main.json
  含报告期/年度/单季度三套序列与同比：净利润、扣非净利润、营业总收入、
  销售毛利率、净资产收益率、每股经营现金流、存货与应收周转等。
- 分年日线 d.10jqka.com.cn/v6/line/hs_{code}/01/{year}.js
- 分析师盈利预测、管理层变动（经 akshare 包装）

用途仅限「财务证据」与「市场确认」，不作为原始披露出处——卡片里的业务判断
仍要回到巨潮公告原文。
"""

from __future__ import annotations

import json
import re
from datetime import date

import requests

F10_MAIN = "https://basic.10jqka.com.cn/api/stock/finance/{code}_main.json"
THS_LINE = "https://d.10jqka.com.cn/v6/line/hs_{code}/01/{year}.js"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

# 卡片里真正会用到的主��。其余科目留在原始数据里备查。
KEY_SUBJECTS = (
    "净利润", "净利润同比增长率", "扣非净利润", "扣非净利润同比增长率",
    "营业总收入", "营业总收入同比增长率", "基本每股收益", "每股经营现金流",
    "销售净利率", "销售毛利率", "净资产收益率", "净资产收益率-摊薄",
    "存货周转率", "存货周转天数", "应收账款周转天数", "经营现金流",
)


class ThsError(RuntimeError):
    pass


def _get_json(url: str) -> dict:
    try:
        response = requests.get(url, headers=HEADERS, timeout=25)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise ThsError(f"同花顺数据获取失败 {url}：{exc}") from exc
    except ValueError as exc:
        raise ThsError(f"同花顺返回内容无法解析 {url}：{exc}") from exc


def _series(payload: dict, block: str) -> tuple[list[str], dict[str, dict[str, str]]]:
    """把 F10 的 [表头, 科目行...] 结构拆成 {科目: {报告期: 值}}。"""
    rows = payload.get(block)
    subjects = [x[0] for x in payload["title"][1:]] if isinstance(payload.get("title"), list) else []
    if not rows:
        return [], {}
    periods = list(rows[0])
    series: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows[1:]):
        name = subjects[index] if index < len(subjects) else f"科目{index}"
        series[name] = {period: row[i] for i, period in enumerate(periods) if i < len(row)}
    return periods, series


def fetch_financials(code: str) -> dict:
    """取 F10 财务主表：报告期/年度/单季度三套序列及其同比。

    返回 {"periods": [...], "report": {科目: {期: 值}}, "report_yoy": {...},
          "year": {...}, "year_yoy": {...}, "quarter": {...}, "source": url}
    """
    url = F10_MAIN.format(code=code)
    payload = _get_json(url)
    inner = payload.get("flashData")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except ValueError as exc:
            raise ThsError(f"F10 数据无法解析：{code}") from exc
    if not isinstance(inner, dict) or "report" not in inner:
        raise ThsError(f"F10 返回结构异常：{code}")
    periods, report = _series(inner, "report")
    _, report_yoy = _series(inner, "report_yoy")
    _, year = _series(inner, "year")
    _, year_yoy = _series(inner, "year_yoy")
    quarter_periods, quarter = _series(inner, "simple")
    return {"periods": periods, "report": report, "report_yoy": report_yoy,
            "year": year, "year_yoy": year_yoy, "simple": quarter, "quarter": quarter,
            "simple_periods": quarter_periods, "source": url}


def key_series(financials: dict, block: str = "report",
               subjects: tuple[str, ...] = KEY_SUBJECTS) -> dict[str, dict[str, str]]:
    series = financials.get(block, {})
    return {name: series[name] for name in subjects if name in series}


def fetch_daily_history(code: str, years: list[int]) -> list[dict]:
    """同花顺分年日线。原始未复权，跨年拼接后按日期排序。

    返回 [{"day": "YYYY-MM-DD", "open": float, "high": float, "low": float,
           "close": float, "volume": float}, ...]
    """
    rows: list[dict] = []
    for year in years:
        url = THS_LINE.format(code=code, year=year)
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            text = response.text
        except requests.RequestException as exc:
            raise ThsError(f"日线获取失败 {code}/{year}：{exc}") from exc
        match = re.search(r'"data"\s*:\s*"([^"]+)"', text)
        if not match:
            continue
        for line in match.group(1).split(";"):
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                rows.append({"day": f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}",
                             "open": float(parts[1]), "high": float(parts[2]),
                             "low": float(parts[3]), "close": float(parts[4]),
                             "volume": float(parts[5])})
            except ValueError:
                continue
    rows.sort(key=lambda r: r["day"])
    return rows


def atr(bars: list[dict], period: int = 14) -> float | None:
    """Wilder 口径 ATR（真实波幅均值），用于给出波动参考。

    这是**参考线**，不是止损保证：跳空、停牌、涨跌停都可能让参考价无法成交。
    """
    if len(bars) < period + 1:
        return None
    ranges = []
    for previous, current in zip(bars[-(period + 1):], bars[-period:]):
        high, low, close = current["high"], current["low"], current["close"]
        ranges.append(max(high - low, abs(high - previous["close"]), abs(low - previous["close"])))
    return sum(ranges) / len(ranges)


def reference_levels(code: str, years: list[int] | None = None) -> dict:
    """取日线算出 ATR 与 2ATR 参考失效线，供账户层做单笔风险估算。"""
    if years is None:
        today = date.today()
        years = [today.year - 1, today.year]
    bars = fetch_daily_history(code, years)
    if not bars:
        raise ThsError(f"没有取到 {code} 的日线")
    close = bars[-1]["close"]
    value = atr(bars)
    if not value:
        raise ThsError(f"{code} 日线不足以计算 ATR")
    return {"close": close, "atr": round(value, 4),
            "atr_pct": round(value / close * 100, 2),
            "stop_reference": round(close - 2 * value, 3),
            "bars": len(bars), "last_day": bars[-1]["day"],
            "source": THS_LINE.format(code=code, year=years[-1])}


def fetch_profit_forecast(code: str) -> list[dict]:
    """分析师一致预期（年度、机构数、最小/均值/最大值、行业均值）。"""
    import akshare as ak

    frame = ak.stock_profit_forecast_ths(symbol=code)
    return frame.to_dict("records")


def fetch_management_changes(code: str) -> list[dict]:
    """高管与股东持股变动，用于治理风险核查。"""
    import akshare as ak

    frame = ak.stock_management_change_ths(symbol=code)
    return frame.to_dict("records")
