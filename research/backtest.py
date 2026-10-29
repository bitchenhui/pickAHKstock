"""§51 基线检验：发现层挑出来的候选，是否比简单基线更有信息量。

这是策略稿明写的第一个验收条件：「先验证发现经营变化是否比简单基线有增量信息」。

口径（能做什么、不能做什么）：

**能做**
- 用东财业绩预告的 NOTICE_DATE（公告日）重建当时可得的候选集——这是真正的
  point-in-time，不用事后才知道的信息去选股
- 用腾讯前复权日线算公告日之后的持有期收益
- 对照组：同一个池子里的**没有发强预告**的公司（最直接的对照）、同池随机样本、
  同池市值最小/最大的一端

**不能做（必须承认的局限）**
- 合格池用**当前**的主板/非ST/市值区间筛选来近似，是前视偏差；退市与停牌样本
  取不到，有幸存者偏差
- 未计佣金、印花税、点差与滑点；A 股 T+1 无法当日卖出
- 业绩预告本身有选择性（只有预计大幅变动的才预告），这是内生性问题，
  对照组只能部分缓解

因此结论只能是「提示」而非「证明」：如果事件组的中位数收益明显高于对照组，
且在多个阈值与持有期下不翻转，说明发现层有增量信息，值得继续投入；
如果翻转或没有差异，这条线就该停。
"""

from __future__ import annotations

import json
import random
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
TENCENT_QFQ = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
               "?param={symbol},day,{start},{end},{count},qfq")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

STRONG_TYPES = ("预增", "略增", "扭亏", "减亏", "续盈", "预盈")
MAIN_BOARD_PREFIX = ("00", "60")
MIN_CHANGE = 50.0          # 归母同比变动门槛（%）
HOLD_DAYS = 60


class BacktestError(RuntimeError):
    pass


@dataclass
class Group:
    name: str
    returns: list = field(default_factory=list)
    missing: int = 0

    def stats(self) -> dict:
        if not self.returns:
            return {"n": 0, "median": None, "mean": None, "win_rate": None, "p10": None}
        ordered = sorted(self.returns)
        return {"n": len(ordered),
                "median": round(statistics.median(ordered) * 100, 2),
                "mean": round(statistics.fmean(ordered) * 100, 2),
                "win_rate": round(sum(1 for r in ordered if r > 0) / len(ordered) * 100, 1),
                "p10": round(ordered[max(0, int(len(ordered) * 0.1) - 1)] * 100, 2)}


@dataclass
class BacktestResult:
    event_date: str
    hold_days: int
    threshold: float
    groups: dict = field(default_factory=dict)
    excess: dict = field(default_factory=dict)
    universe_size: int = 0
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"event_date": self.event_date, "hold_days": self.hold_days,
                "threshold": self.threshold, "universe_size": self.universe_size,
                "groups": self.groups, "excess": self.excess, "notes": self.notes}


CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "backtest"


def forecasts_by_report_period(report_period: date, page_size: int = 500,
                               max_pages: int = 16) -> list[dict]:
    """按**报告期**取一批业绩预告（含每条的公告日 NOTICE_DATE）。

    为什么按报告期取、而不是按公告日取：
    - 按 NOTICE_DATE 过滤时，接口会**忽略 pageIndex**（第 1、2 页返回同样的行），
      靠翻页取全量会拿到重复数据
    - 按 REPORT_DATE 取分页正常，而且每条自带公告日，于是可以在客户端按公告日
      切出任意历史时点的候选集——这才是 point-in-time 该有的做法

    同一公司同一报告期有多条（归母、扣非、营收各一条），保留变动幅度最大的那条。
    结果按报告期落盘缓存：这个接口每次 90 秒，敏感性分析要反复用同一批数据。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"forecasts_{report_period.isoformat()}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except ValueError:
            pass
    rows: list[dict] = []
    seen_pages: set[str] = set()
    for page in range(1, max_pages + 1):
        params = {"reportName": "RPT_PUBLIC_OP_NEWPREDICT", "columns": "ALL",
                  "pageSize": str(page_size), "pageIndex": str(page), "client": "WEB",
                  "sortColumns": "NOTICE_DATE", "sortTypes": "1",
                  "filter": f"(REPORT_DATE='{report_period.isoformat()}')"}
        try:
            response = requests.get(DC, headers=HEADERS, params=params, timeout=200)
            response.raise_for_status()
            data = (response.json().get("result") or {}).get("data") or []
        except (requests.RequestException, ValueError) as exc:
            raise BacktestError(f"业绩预告获取失败：{exc}") from exc
        if not data:
            break
        # 护栏：接口在某些过滤组合下会忽略 pageIndex，重复页要停
        fingerprint = f"{data[0].get('SECURITY_CODE')}|{data[0].get('NOTICE_DATE')}|{len(data)}"
        if fingerprint in seen_pages:
            break
        seen_pages.add(fingerprint)
        rows.extend(data)
        if len(data) < page_size:
            break
    best: dict[tuple[str, str], tuple[float, dict]] = {}
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        if not code or not code.startswith(MAIN_BOARD_PREFIX):
            continue
        key = (code, str(row.get("REPORT_DATE") or "")[:10])
        try:
            change = abs(float(row.get("ADD_AMP_LOWER")))
        except (TypeError, ValueError):
            change = 0.0
        if key not in best or change > best[key][0]:
            best[key] = (change, row)
    result = [row for _, row in best.values()]
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def point_in_time(forecasts: list[dict], event_date: date,
                  threshold: float = MIN_CHANGE) -> tuple[list[dict], list[dict]]:
    """切出事件日当时已知的强预告组，以及当时**尚未**预告的对照组。

    尚未预告 = 公告日晚于事件日。同一报告期、同一市场，只是当时没有信息。
    """
    known, unknown = [], []
    for row in forecasts:
        notice = str(row.get("NOTICE_DATE") or "")[:10]
        try:
            notice_date = datetime.strptime(notice, "%Y-%m-%d").date()
        except ValueError:
            continue
        if notice_date <= event_date:
            known.append(row)
        else:
            unknown.append(row)
    strong = [row for row in known if is_strong(row, threshold)]
    return strong, unknown


def is_strong(row: dict, threshold: float = MIN_CHANGE) -> bool:
    forecast_type = str(row.get("PREDICT_TYPE") or "")
    if forecast_type not in STRONG_TYPES:
        return False
    try:
        return abs(float(row.get("ADD_AMP_LOWER"))) >= threshold
    except (TypeError, ValueError):
        return False


def load_prices(code: str, start: date, end: date, count: int = 400) -> list[dict]:
    """腾讯前复权日线。返回 [{day, open, close, high, low}]，已按日期排序。

    注意腾讯的字段顺序是 [日期, 开, 收, 高, 低, 量]，不是常见的 OHLC。
    按代码落盘缓存：一只股票一次请求，敏感性分析要反复用。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"prices_{code}.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached:
                return cached
        except ValueError:
            pass
    symbol = f"sh{code}" if code.startswith("6") else f"sz{code}"
    url = TENCENT_QFQ.format(symbol=symbol, start=start.isoformat(),
                             end=end.isoformat(), count=count)
    try:
        response = requests.get(url, headers=HEADERS, timeout=25)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []
    block = (payload.get("data") or {}).get(symbol) or {}
    bars = block.get("qfqday") or block.get("day") or []
    out: list[dict] = []
    for bar in bars:
        if len(bar) < 6:
            continue
        try:
            out.append({"day": bar[0], "open": float(bar[1]), "close": float(bar[2]),
                        "high": float(bar[3]), "low": float(bar[4])})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda b: b["day"])
    if out:
        cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def forward_return(prices: list[dict], buy_after: str, hold_days: int) -> float | None:
    """在 buy_after（公告日）之后第一个交易日买入，持有 hold_days 个交易日的收益。

    A 股 T+1：买入当日不能卖，所以从次一交易日开始持有。
    """
    if not prices:
        return None
    start = None
    for index, bar in enumerate(prices):
        if bar["day"] > buy_after:
            start = index
            break
    if start is None:
        return None
    entry = prices[start]["close"]
    if entry <= 0:
        return None
    end = min(start + hold_days, len(prices) - 1)
    if end <= start:
        return None
    exit_price = prices[end]["close"]
    return (exit_price / entry) - 1


def _prepare(forecasts: list[dict], holds: tuple[int, ...], sample: int,
             seed: int) -> tuple[list[tuple[dict, date, list]], int]:
    """抽样 + 取一次价格，供所有 (门槛, 持有期) 组合复用。"""
    rng = random.Random(seed)
    pool = forecasts[:]
    rng.shuffle(pool)
    sampled = pool[:sample * 3]
    notices = [d for d in (_notice_date(row) for row in sampled) if d]
    if not notices:
        return [], 0
    price_start = min(notices) - timedelta(days=40)
    price_end = max(notices) + timedelta(days=int(max(holds) * 2.2) + 30)
    prepared: list[tuple[dict, date, list]] = []
    for row in sampled:
        code = str(row.get("SECURITY_CODE") or "").strip()
        notice = _notice_date(row)
        if not notice:
            continue
        prepared.append((row, notice, load_prices(code, price_start, price_end)))
    return prepared, len({str(r.get("SECURITY_CODE")) for r in forecasts})


def _compare(prepared: list[tuple[dict, date, list]], threshold: float,
             hold_days: int) -> tuple[dict, dict]:
    groups = {"强预告组": Group("强预告组"), "非强预告（对照）": Group("非强预告（对照）")}
    for row, notice, prices in prepared:
        value = forward_return(prices, notice.isoformat(), hold_days)
        if value is None:
            continue
        bucket = "强预告组" if is_strong(row, threshold) else "非强预告（对照）"
        groups[bucket].returns.append(value)
    stats = {name: group.stats() for name, group in groups.items()}
    event_median = stats.get("强预告组", {}).get("median")
    base_median = stats.get("非强预告（对照）", {}).get("median")
    excess: dict = {}
    if event_median is not None and base_median is not None:
        excess["强预告 − 对照（中位数）"] = round(event_median - base_median, 2)
        excess["强预告 − 对照（胜率）"] = round(
            (stats["强预告组"]["win_rate"] or 0) - (stats["非强预告（对照）"]["win_rate"] or 0), 1)
    return stats, excess


def run_backtest(report_period: str = "2026-06-30", hold_days: int = HOLD_DAYS,
                 threshold: float = MIN_CHANGE, sample: int = 150,
                 seed: int = 20261029) -> BacktestResult:
    """事件研究：用每家公司**自己的公告日**做事件日，比较持有期收益。

    为什么不用固定事件日：强预告组集中在 4 月公告，若事件日定在 4 月底，它们的
    公告效应已部分计入；对照组（5–7 月才公告）在窗口内还没被定价。这样比，
    对照组在窗口内还没被定价，比较是不对等的。
    改成各家按自己的公告日算，公告效应才在两侧对称。

    对照组是「同报告期但没有强预告」的公司（含预减/略增幅度小的），
    不是「完全没有预告」——缺少全市场代码池，那是个已知缺口。
    """
    forecasts = forecasts_by_report_period(datetime.strptime(report_period, "%Y-%m-%d").date())
    if not forecasts:
        raise BacktestError(f"报告期 {report_period} 没有取到业绩预告")
    prepared, universe = _prepare(forecasts, (hold_days,), sample, seed)
    stats, excess = _compare(prepared, threshold, hold_days)
    result = BacktestResult(event_date="各公司自身公告日", hold_days=hold_days,
                            threshold=threshold, universe_size=universe)
    result.groups = stats
    result.excess = excess
    result.notes.append(f"报告期 {report_period}，池 {universe} 只，"
                        f"强预告 {stats.get('强预告组', {}).get('n')} 只，"
                        f"对照 {stats.get('非强预告（对照）', {}).get('n')} 只")
    result.notes.append(
        "局限：对照组是同报告期非强预告而非「完全无预告」（缺全市场代码池）；"
        "池子按主板代码粗筛（前视）、退市与停牌样本缺失、未计费用与滑点、A 股 T+1、"
        "业绩预告本身有选择性")
    return result


def _notice_date(row: dict) -> date | None:
    notice = str(row.get("NOTICE_DATE") or "")[:10]
    try:
        return datetime.strptime(notice, "%Y-%m-%d").date()
    except ValueError:
        return None


def sensitivity(report_period: str = "2026-06-30", thresholds=(30.0, 50.0, 100.0),
                holds=(10, 20, 60), sample: int = 150) -> list[dict]:
    """参数敏感性：门槛与持有期同时变，看结论是否翻转。

    策略稿明写要报「对参数变化的敏感性」——一次结论不算数，翻转就不算数。
    价格只取一次，所有组合复用。
    """
    forecasts = forecasts_by_report_period(datetime.strptime(report_period, "%Y-%m-%d").date())
    if not forecasts:
        return []
    prepared, _ = _prepare(forecasts, tuple(holds), sample, 20261029)
    out: list[dict] = []
    for hold in holds:
        for threshold in thresholds:
            stats, excess = _compare(prepared, threshold, hold)
            out.append({"hold_days": hold, "threshold": threshold,
                        "groups": stats, "excess": excess})
    return out
