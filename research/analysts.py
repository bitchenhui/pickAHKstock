"""分析师预期：独立于公司自述的第三方证据。

两个来源互补：
- 同花顺盈利预测（`stock_profit_forecast_ths`）：每股盈利的预测机构数、最小/均值/
  最大值、**行业平均数**。行业平均数是关键——它把「公司预期」放回行业坐标里，
  东财研报接口没有这一项。
- 东方财富研报（`reportapi`）：评级、目标价、当年/次年 EPS 预测。有覆盖才有，
  002132 这类没有机构覆盖的票会返回空——**「无人覆盖」本身是信息**。

用途：
- 推荐轨：预期上修是独立于业绩预告的事件源，也是判断「市场是否已price in」
  的对照（公司说好，但分析师上修/下修，是另一回事）
- 分析轨：卡片里把「公司自述」与「分析师预期」并排，两者不一致时最值得追问

注意：这是**当前快照**，不是变化序列。要看变化需要自己积累（快照落在本地缓存）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests

CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "analysts"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
REPORT_API = "https://reportapi.eastmoney.com/report/list"


@dataclass
class Expectation:
    """一家公司当前可见的分析师预期。"""

    code: str
    name: str = ""
    market: str = "A"
    eps_forecast: list = field(default_factory=list)   # [{年度, 机构数, 最小, 均值, 最大, 行业均值}]
    report_count: int = 0
    ratings: list = field(default_factory=list)        # [{日期, 评级, 目标价, 当年EPS预测, 标题}]
    latest_rating: str = ""
    target_price: float | None = None
    source: str = ""
    notes: list = field(default_factory=list)

    @property
    def coverage(self) -> int:
        """预测机构数（来自同花顺）；没有就退回研报条数。"""
        if self.eps_forecast:
            try:
                return int(self.eps_forecast[0].get("机构数") or 0)
            except (TypeError, ValueError, AttributeError):
                pass
        return self.report_count

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "market": self.market,
                "eps_forecast": self.eps_forecast, "report_count": self.report_count,
                "ratings": self.ratings, "latest_rating": self.latest_rating,
                "target_price": self.target_price, "coverage": self.coverage,
                "source": self.source, "notes": self.notes}


def fetch_ths_eps_forecast(code: str) -> list[dict]:
    """同花顺盈利预测：每股盈利的机构数与区间。"""
    import akshare as ak

    frame = ak.stock_profit_forecast_ths(symbol=code)
    if frame is None or len(frame) == 0:
        return []
    rows = []
    for row in frame.to_dict("records"):
        rows.append({
            "年度": row.get("年度"),
            "机构数": row.get("预测机构数"),
            "最小值": row.get("最小值"),
            "均值": row.get("均值"),
            "最大值": row.get("最大值"),
            "行业平均数": row.get("行业平均数"),
        })
    return rows


def fetch_reports(code: str, begin: str = "2026-01-01", end: str = "2026-12-31",
                  page_size: int = 20) -> list[dict]:
    """东方财富研报：评级、目标价、EPS 预测。"""
    params = {"cb": "", "industryCode": "*", "pageSize": str(page_size), "pageNo": "1",
              "beginTime": begin, "endTime": end, "qType": "0", "code": code, "_": str(int(time.time() * 1000))}
    try:
        response = requests.get(REPORT_API, headers=HEADERS, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []
    rows = []
    for item in (payload.get("data") or []):
        rows.append({
            "日期": str(item.get("publishDate") or "")[:10],
            "评级": item.get("emRatingName"),
            "目标价": item.get("indvAimPriceT"),
            "当年EPS预测": item.get("predictThisYearEps"),
            "次年EPS预测": item.get("predictNextYearEps"),
            "标题": item.get("title"),
        })
    return rows


HISTORY_DIR = CACHE_DIR / "history"


def snapshot(code: str, name: str = "", market: str = "A",
             day: str | None = None) -> dict:
    """给分析师预期打一个历史快照。

    预期变化是**独立于业绩预告**的事件源：公司说好是一件事，分析师上修/下修
    是另一件事。但接口只给当前值，要看变化只能自己积累——所以每次复核时打一个
    快照，快照攒够了就能算「预期修正」。
    """
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    expectation = fetch(code, name, market)
    day = day or date.today().isoformat()
    payload = {"day": day, "code": code, "name": expectation.name or name,
               "coverage": expectation.coverage,
               "eps_forecast": expectation.eps_forecast,
               "latest_rating": expectation.latest_rating,
               "target_price": expectation.target_price}
    path = HISTORY_DIR / f"{market}_{code}_{day}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def history(code: str, market: str = "A") -> list[dict]:
    """该标的的预期历史快照，按日期升序。"""
    if not HISTORY_DIR.exists():
        return []
    out = []
    for path in sorted(HISTORY_DIR.glob(f"{market}_{code}_*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return sorted(out, key=lambda item: item.get("day", ""))


def change(code: str, market: str = "A") -> dict:
    """预期变化：最早快照 → 最新快照。

    快照不足两份时返回空——**不要用一份快照说"预期上修"**。
    """
    snaps = history(code, market)
    if len(snaps) < 2:
        return {"available": False, "snapshots": len(snaps),
                "note": "快照不足两份，算不出变化"}
    first, last = snaps[0], snaps[-1]

    def mean_of(snapshot):
        rows = snapshot.get("eps_forecast") or []
        for row in rows:
            try:
                return float(row.get("均值"))
            except (TypeError, ValueError):
                continue
        return None

    old_mean, new_mean = mean_of(first), mean_of(last)
    delta = None
    if old_mean and new_mean and old_mean != 0:
        delta = (new_mean - old_mean) / abs(old_mean) * 100
    return {"available": True, "snapshots": len(snaps),
            "from_day": first.get("day"), "to_day": last.get("day"),
            "eps_mean_before": old_mean, "eps_mean_after": new_mean,
            "eps_mean_change_pct": None if delta is None else round(delta, 2),
            "coverage_before": first.get("coverage"), "coverage_after": last.get("coverage"),
            "rating_before": first.get("latest_rating"), "rating_after": last.get("latest_rating")}


def fetch(code: str, name: str = "", market: str = "A") -> Expectation:
    """取一家公司的分析师预期。取不到就返回空壳，并记下原因——「无人覆盖」是信息。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{market}_{code}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return Expectation(code=code, name=data.get("name", name), market=market,
                               eps_forecast=data.get("eps_forecast", []),
                               report_count=data.get("report_count", 0),
                               ratings=data.get("ratings", []),
                               latest_rating=data.get("latest_rating", ""),
                               target_price=data.get("target_price"),
                               source=data.get("source", ""), notes=data.get("notes", []))
        except ValueError:
            pass
    expectation = Expectation(code=code, name=name, market=market)
    try:
        expectation.eps_forecast = fetch_ths_eps_forecast(code)
        expectation.source = "同花顺盈利预测 + 东方财富研报"
        if expectation.eps_forecast:
            expectation.notes.append(
                "同花顺的「行业平均数」是全行业均值，与单只公司的可比性需自行判断")
    except Exception as exc:
        expectation.notes.append(f"同花顺盈利预测取不到：{type(exc).__name__}"
                                 "（该接口只覆盖 A 股，港股无此源）")
    try:
        expectation.ratings = fetch_reports(code)
        expectation.report_count = len(expectation.ratings)
        if expectation.ratings:
            expectation.latest_rating = str(expectation.ratings[0].get("评级") or "")
            target = expectation.ratings[0].get("目标价")
            try:
                expectation.target_price = float(target) if target else None
            except (TypeError, ValueError):
                expectation.target_price = None
    except Exception as exc:
        expectation.notes.append(f"东财研报取不到：{type(exc).__name__}")
    if not expectation.eps_forecast and not expectation.ratings:
        expectation.notes.append("无机构覆盖：预期这条证据缺失，不等于基本面差")
    cache_file.write_text(json.dumps(expectation.to_dict(), ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return expectation
