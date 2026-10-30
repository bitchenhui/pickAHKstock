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
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from .events import metric_rank, primary_rows

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
    excesses: list = field(default_factory=list)   # 相对沪深300的超额
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

# 取价失败的代码（接口限流时会静默返回空，必须能被看见）
PRICE_MISSES = []
# 进程内缓存：回测要在多期之间反复用同一批日线，
# 每次都去读几千个 JSON 文件会把时间全花在解析上。
_PRICE_MEM: dict = {}


def forecasts_by_report_period(report_period: date, page_size: int = 500,
                               max_pages: int = 16) -> list[dict]:
    """按**报告期**取一批业绩预告（含每条的公告日 NOTICE_DATE）。

    取数交给 forecast_pool：接口**忽略 pageIndex**，翻页拿到的永远是同一批行，
    早先的实现每期只取到第一页 500 行——**全量的约 7%**。现在是按数值区间递归
    切分，覆盖 100%（每期约 45 秒，落盘缓存）。

    每条自带公告日，于是可以在客户端按公告日切出任意历史时点的候选集——
    这才是 point-in-time 该有的做法。

    同一公司同一报告期有多条（归母、扣非、营收各一条），保留变动幅度最大的那条。
    """
    from .forecast_pool import fetch_pool

    rows = fetch_pool(report_period.isoformat())
    best: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        if not code or not code.startswith(MAIN_BOARD_PREFIX):
            continue
        # 缓存按（代码, 报告期, 指标）存一条：不同口径（归母/扣非/营收）都要能
        # 离线复算，不能在缓存阶段就把口径定死
        key = (code, str(row.get("REPORT_DATE") or "")[:10],
               str(row.get("PREDICT_FINANCE") or ""))
        if key in best:
            try:
                if abs(float(row.get("ADD_AMP_LOWER") or 0)) <= abs(
                        float(best[key].get("ADD_AMP_LOWER") or 0)):
                    continue
            except (TypeError, ValueError):
                pass
        best[key] = row
    return primary_rows(list(best.values()))


def forecasts_raw(report_period: date) -> list[dict]:
    """读取缓存里的**多指标**原始行（不挑主指标），用于离线比较不同口径。"""
    from .forecast_pool import fetch_pool

    return fetch_pool(report_period.isoformat())


def legacy_primary_rows(rows: list[dict]) -> list[dict]:
    """旧口径：同一公司同一报告期取「|同比幅度| 最大」的那条。

    保留它是为了能离线复现基线检验第一版的结果——那一版正是这个口径，
    而它也是低基数暴增被系统性选中的原因。
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        key = (code, str(row.get("REPORT_DATE") or "")[:10])
        grouped.setdefault(key, []).append(row)
    out: list[dict] = []
    for items in grouped.values():
        try:
            chosen = max(items, key=lambda r: abs(float(r.get("ADD_AMP_LOWER") or 0)))
        except (TypeError, ValueError):
            chosen = items[0]
        out.append(chosen)
    return out


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


def is_strong(row: dict, threshold: float = MIN_CHANGE,
              min_amount: float | None = None) -> bool:
    forecast_type = str(row.get("PREDICT_TYPE") or "")
    if forecast_type not in STRONG_TYPES:
        return False
    if min_amount:
        try:
            amount = float(row.get("PREDICT_AMT_LOWER"))
        except (TypeError, ValueError):
            amount = None
        if amount is not None and amount < min_amount:
            return False   # 绝对额过小：低基数噪音
    try:
        return abs(float(row.get("ADD_AMP_LOWER"))) >= threshold
    except (TypeError, ValueError):
        return False


def load_prices(code: str, start: date, end: date, count: int = 400) -> list[dict]:
    """前复权日线。返回 [{day, open, close, high, low}]，已按日期排序。

    注意腾讯的字段顺序是 [日期, 开, 收, 高, 低, 量]，不是常见的 OHLC。

    **缓存必须带窗口**：同一只股票在不同报告期需要的区间不同，只按代码存一份
    会让后一次取到前一次的窗口——数据看着有，日期却不对，持有期收益就算不出来。
    这里存合并后的并集，并在缓存覆盖不了请求区间时重新取。

    **取价失败必须看得见**：行情源限流时会返回空，空价格会让"无收益"混进
    中位数——那不是结论，是数据缺失。所以失败的代码记在 PRICE_MISSES 里，
    回测报告会把行情覆盖率一起打出来。
    """
    global _PRICE_MEM
    # 未来日期取不到东西（分年文件只有到当年），夹到今天，
    # 否则每年都会去下载尚不存在的年份文件，白等一轮超时。
    end = min(end, date.today())
    if end < start:
        return []
    memory = _PRICE_MEM.get(code)
    if memory and _covers(memory, start, end):
        return memory
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"prices_{code}.json"
    cached: list[dict] = []
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                cached = data
        except ValueError:
            cached = []
    if _covers(cached, start, end):
        _PRICE_MEM[code] = cached
        return cached

    bars = _fetch_tencent(symbol_for(code), start, end, count)
    if bars is None:
        bars = _fetch_ths(code, start, end, skip_years=_covered_years(cached, start, end))
    merged = _merge(cached, bars)
    if not _covers(merged, start, end):
        # 腾讯只回一部分（窗口太长或区间怪），再用同花顺补齐
        merged = _merge(merged, _fetch_ths(code, start, end,
                                           skip_years=_covered_years(merged, start, end)))
    merged = [bar for bar in merged if start.isoformat() <= bar["day"] <= end.isoformat()]
    if merged:
        cache_file.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        _PRICE_MEM[code] = merged
    else:
        PRICE_MISSES.append(code)
    time.sleep(0.15)   # 行情源都有限流，别把请求打太快
    return merged


def symbol_for(code: str) -> str:
    # 6 位是 A 股（sh/sz 前缀），5 位是港股（hk 前缀）
    if len(code) == 5:
        return f"hk{code}"
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def _covers(bars: list[dict], start: date, end: date) -> bool:
    """缓存是否覆盖请求区间（按首尾日期判断，中间停牌不算缺）。"""
    if not bars:
        return False
    days = [bar["day"] for bar in bars]
    return min(days) <= start.isoformat() and max(days) >= end.isoformat()


def _merge(old: list[dict], new: list[dict]) -> list[dict]:
    seen = {bar["day"]: bar for bar in old}
    for bar in new:
        seen[bar["day"]] = bar
    return [seen[day] for day in sorted(seen)]


THS_LINE = "https://d.10jqka.com.cn/v6/line/hs_{code}/01/{year}.js"


THS_INDEX = "https://d.10jqka.com.cn/v6/line/hs_399300/01/{year}.js"


def _fetch_ths_index(start: date, end: date) -> list[dict]:
    """沪深300 日线（同花顺分年文件，01 = 前复权）。

    腾讯的指数接口在请求量大时会被 WAF 挡掉，基准一缺，超额指标就没了。
    """
    import re

    out: list[dict] = []
    for year in range(start.year, end.year + 1):
        url = THS_INDEX.format(year=year)
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            text = response.text
        except requests.RequestException:
            continue
        match = re.search(r'"data"\s*:\s*"([^"]+)"', text)
        if not match:
            continue
        for line in match.group(1).split(";"):
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                out.append({"day": f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}",
                            "close": float(parts[4])})
            except ValueError:
                continue
        time.sleep(0.05)
    return [bar for bar in out if start.isoformat() <= bar["day"] <= end.isoformat()]


def _covered_years(bars: list[dict], start: date, end: date) -> set:
    """缓存里已经覆盖到**所需区间**的年份——这些年份文件不用再下一次。

    注意不能只看「整年都在缓存里」：窗口常常从三月份开始，
    那么 2026 这一年的文件虽然只下了一半，也已经够用，再下一次就是白花时间。
    """
    covered = set()
    if not bars:
        return covered
    days = [bar["day"] for bar in bars]
    first, last = min(days), max(days)
    # 留 7 天余量：缓存的起点是「起始日之后的第一个交易日」，
    # 卡得太死会每次都判定成没覆盖，于是把同一年的文件反复下一遍。
    slack = timedelta(days=7)
    for year in range(start.year, end.year + 1):
        need_start = max(start, date(year, 1, 1))
        need_end = min(end, date(year, 12, 31))
        if (date.fromisoformat(first) <= need_start + slack
                and date.fromisoformat(last) >= need_end - slack):
            covered.add(year)
    return covered


def _fetch_ths(code: str, start: date, end: date, skip_years: set | None = None) -> list[dict]:
    """同花顺分年日线（01 = 前复权）。腾讯被限流时的兜底源。

    一次取一年文件，跨年区间要取多个文件；已经缓存够用的年份跳过（skip_years）。
    """
    import re

    skip = skip_years or set()
    out: list[dict] = []
    for year in range(start.year, end.year + 1):
        if year in skip:
            continue
        url = THS_LINE.format(code=code, year=year)
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            text = response.text
        except requests.RequestException:
            continue
        match = re.search(r'"data"\s*:\s*"([^"]+)"', text)
        if not match:
            continue
        for line in match.group(1).split(";"):
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                out.append({"day": f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}",
                            "open": float(parts[1]), "close": float(parts[4]),
                            "high": float(parts[2]), "low": float(parts[3])})
            except ValueError:
                continue
        time.sleep(0.05)
    return out


# 腾讯的 WAF 会在请求量上来后返回 501（HTML 跳转页）。记住这个状态，
# 免得每只股票都去撞一次墙。
_TENCENT_BLOCKED = False


def prefetch_prices(codes, start: date, end: date, workers: int = 6) -> int:
    """并行预取日线。串行取几千只股票要几小时，并行后十几分钟。

    行情源都有限流，所以并发数要克制（默认 6），并且每个 worker 之间有间隔。
    返回成功取到行情的代码数。
    """
    from concurrent.futures import ThreadPoolExecutor

    codes = sorted({str(code).strip() for code in codes if str(code).strip()})
    if not codes:
        return 0
    ok = 0
    lock = __import__("threading").Lock()

    def one(code: str) -> bool:
        nonlocal ok
        try:
            bars = load_prices(code, start, end)
        except Exception:
            return False
        time.sleep(0.05)
        with lock:
            if bars:
                ok += 1
        return bool(bars)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, codes))
    return ok


def _fetch_tencent(symbol: str, start: date, end: date, count: int):
    """腾讯前复权日线。被 WAF 拦时返回 None（交给东财兜底），不返回空列表。"""
    global _TENCENT_BLOCKED
    if _TENCENT_BLOCKED:
        return None
    url = TENCENT_QFQ.format(symbol=symbol, start=start.isoformat(),
                             end=end.isoformat(), count=count)
    try:
        response = requests.get(url, headers=HEADERS, timeout=25)
    except requests.RequestException:
        return []
    if response.status_code == 501:
        _TENCENT_BLOCKED = True
        return None
    try:
        payload = response.json()
    except ValueError:
        return []
    block = (payload.get("data") or {}).get(symbol) or {}
    bars = block.get("qfqday") or block.get("day") or []
    out = []
    for bar in bars:
        if len(bar) < 5:
            continue
        try:
            out.append({"day": str(bar[0]), "open": float(bar[1]), "close": float(bar[2]),
                        "high": float(bar[3]), "low": float(bar[4])})
        except (TypeError, ValueError):
            continue
    return out


EM_KLINE = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
            "?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            "&klt=101&fqt=1&beg={beg}&end={end}")


def _em_secid(code: str) -> str:
    """东财 secid：1 = 沪市，0 = 深市／北交所。"""
    if code.startswith("6") or code.startswith("9"):
        return f"1.{code}"
    return f"0.{code}"


def _fetch_em(code: str, start: date, end: date):
    """东方财富前复权日线（push2his）。字段序：日期,开,收,高,低,量,额…。"""
    url = EM_KLINE.format(secid=_em_secid(code), beg=start.strftime("%Y%m%d"),
                           end=end.strftime("%Y%m%d"))
    try:
        response = requests.get(url, headers=HEADERS, timeout=25)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []
    klines = ((payload.get("data") or {}).get("klines") or [])
    out = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 5:
            continue
        out.append(parts[:5])
    time.sleep(0.12)   # 别把人家接口打出限流
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
             seed: int) -> tuple[list[tuple[dict, date, list]], int, list]:
    """抽样 + 取一次价格 + 取基准，供所有 (门槛, 持有期) 组合复用。"""
    rng = random.Random(seed)
    pool = forecasts[:]
    rng.shuffle(pool)
    sampled = pool[:sample * 3]
    notices = [d for d in (_notice_date(row) for row in sampled) if d]
    if not notices:
        return [], 0, []
    price_start = min(notices) - timedelta(days=40)
    price_end = max(notices) + timedelta(days=int(max(holds) * 2.2) + 30)
    prepared: list[tuple[dict, date, list]] = []
    for row in sampled:
        code = str(row.get("SECURITY_CODE") or "").strip()
        notice = _notice_date(row)
        if not notice:
            continue
        prepared.append((row, notice, load_prices(code, price_start, price_end)))
    index = index_bars(price_start, price_end)
    return prepared, len({str(r.get("SECURITY_CODE")) for r in forecasts}), index


def price_coverage(prepared) -> tuple:
    # 取价覆盖率：有多少只股票真的拿到了日线。
    # 这个数字必须报出来。行情接口被限流时会返回空，而空价格会让「无收益」
    # 混进中位数——那不是结论，是数据缺失。
    have = sum(1 for _, _, bars in prepared if bars)
    return have, len(prepared)


def index_bars(start: date, end: date) -> list[dict]:
    """沪深300 前复权日线，用作基准。

    所有组的中位数都是负的——说明绝对收益被市场环境主导。比较必须相对基准，
    否则比的是"谁在熊市里跌得少"，而不是"信号有没有信息量"。

    腾讯的指数 K 线接口对区间与复权参数比较挑剔（某些区间会返回非 JSON），
    所以依次尝试几个写法，取到就用；结果落盘缓存。
    """
    cache_file = CACHE_DIR / f"index_{start.isoformat()}_{end.isoformat()}.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached:
                return cached
        except ValueError:
            pass
    variants = [
        ("sh000300", "qfq"), ("sh000300", ""), ("sz399300", "qfq"), ("sz399001", "qfq"),
    ]
    out: list[dict] = []
    for symbol, adjust in variants:
        url = TENCENT_QFQ.format(symbol=symbol, start=start.isoformat(),
                                 end=end.isoformat(), count=400)
        if not adjust:
            url = (f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
                   f"?param={symbol},day,{start},{end},400")
        try:
            response = requests.get(url, headers=HEADERS, timeout=25)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            continue
        block = (payload.get("data") or {}).get(symbol) or {}
        bars = block.get("qfqday") or block.get("day") or []
        for bar in bars:
            if len(bar) < 3:
                continue
            try:
                out.append({"day": bar[0], "close": float(bar[2])})
            except (TypeError, ValueError):
                continue
        if out:
            break
    if not out:
        # 腾讯被限流时，基准会整个缺失——超额指标就无从谈起。
        # 同花顺的沪深300（深市代码 399300）可以兜底。
        out = _fetch_ths_index(start, end)
    out.sort(key=lambda b: b["day"])
    if out:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _window_return(bars: list[dict], start_after: str, hold_days: int) -> float | None:
    if not bars:
        return None
    start = None
    for index, bar in enumerate(bars):
        if bar["day"] > start_after:
            start = index
            break
    if start is None:
        return None
    end = min(start + hold_days, len(bars) - 1)
    if end <= start or bars[start]["close"] <= 0:
        return None
    return (bars[end]["close"] / bars[start]["close"]) - 1


def _bootstrap_median_diff(strong: list, control: list, rounds: int = 2000,
                            seed: int = 7) -> tuple[float | None, float | None]:
    """中位数差的 bootstrap 95% 区间。

    小样本下中位数差的噪声极大——不给区间的话，一个 −7.6pct 的差很容易被
    读成结论，其实可能只是抽样噪声。

    池子修好之后对照组动辄上千家，纯 Python 抽 2000 轮要几十秒，
    这里用 numpy 向量化（同样的重抽样次数，秒级完成）。
    """
    if len(strong) < 5 or len(control) < 5:
        return None, None
    try:
        import numpy as np
    except ImportError:
        return _bootstrap_median_diff_py(strong, control, rounds, seed)
    rng = np.random.default_rng(seed)
    a = np.asarray(strong, dtype=float)
    b = np.asarray(control, dtype=float)
    idx_a = rng.integers(0, a.size, size=(rounds, a.size))
    idx_b = rng.integers(0, b.size, size=(rounds, b.size))
    diffs = np.median(a[idx_a], axis=1) - np.median(b[idx_b], axis=1)
    diffs.sort()
    low = diffs[int(len(diffs) * 0.025)]
    high = diffs[int(len(diffs) * 0.975)]
    return round(float(low) * 100, 2), round(float(high) * 100, 2)


def _bootstrap_median_diff_py(strong: list, control: list, rounds: int,
                               seed: int) -> tuple[float | None, float | None]:
    """没有 numpy 时的兜底实现。"""
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(rounds):
        a = rng.choices(strong, k=len(strong))
        b = rng.choices(control, k=len(control))
        diffs.append(statistics.median(a) - statistics.median(b))
    diffs.sort()
    low = diffs[int(len(diffs) * 0.025)]
    high = diffs[int(len(diffs) * 0.975)]
    return round(low * 100, 2), round(high * 100, 2)


def pre_announcement_runup(prices: list[dict], notice: str, lookback: int = 20) -> float | None:
    """公告日之前的涨幅：判断「利好是否已被price in」。

    发现层目前只按业绩变动选股，没有"价格是否已经反映"这一层。若强预告候选
    普遍在公告前已大涨，公告后买等于接利好出尽的那一段——这正是要检验的。
    """
    if not prices:
        return None
    index = None
    for position, bar in enumerate(prices):
        if bar["day"] >= notice:
            index = position
            break
    if index is None or index == 0:
        return None
    start = max(0, index - lookback)
    base = prices[start]["close"]
    if base <= 0:
        return None
    return (prices[index - 1]["close"] / base) - 1


def _compare(prepared: list[tuple[dict, date, list]], threshold: float,
             hold_days: int, index: list | None = None,
             runup_filter: float | None = None,
             min_amount: float | None = None) -> tuple[dict, dict]:
    groups = {"强预告组": Group("强预告组"), "非强预告（对照）": Group("非强预告（对照）")}
    if runup_filter is not None:
        groups["强预告但公告前已涨透"] = Group("强预告但公告前已涨透")
    for row, notice, prices in prepared:
        value = forward_return(prices, notice.isoformat(), hold_days)
        if value is None:
            continue
        strong = is_strong(row, threshold, min_amount)
        if strong and runup_filter is not None:
            runup = pre_announcement_runup(prices, notice.isoformat())
            if runup is not None and runup > runup_filter:
                bucket = "强预告但公告前已涨透"
                groups[bucket].returns.append(value)
                continue
        bucket = "强预告组" if strong else "非强预告（对照）"
        groups[bucket].returns.append(value)
        if index:
            benchmark = _window_return(index, notice.isoformat(), hold_days)
            if benchmark is not None:
                groups[bucket].excesses.append(value - benchmark)
    stats = {name: group.stats() for name, group in groups.items()}
    event_median = stats.get("强预告组", {}).get("median")
    base_median = stats.get("非强预告（对照）", {}).get("median")
    excess: dict = {}
    if event_median is not None and base_median is not None:
        excess["强预告 − 对照（中位数）"] = round(event_median - base_median, 2)
        excess["强预告 − 对照（胜率）"] = round(
            (stats["强预告组"]["win_rate"] or 0) - (stats["非强预告（对照）"]["win_rate"] or 0), 1)
    strong_excess = groups["强预告组"].excesses
    control_excess = groups["非强预告（对照）"].excesses
    if strong_excess and control_excess:
        relative = (statistics.median(strong_excess) - statistics.median(control_excess)) * 100
        excess["相对沪深300超额（中位数）"] = round(relative, 2)
        low, high = _bootstrap_median_diff(strong_excess, control_excess)
        if low is not None:
            excess["超额 95% 区间"] = f"[{low}, {high}]"
            excess["显著性"] = ("区间不含 0，差异在此样本下不太像噪声" if low * high > 0
                                else "区间含 0，样本不足以判定")
    if runup_filter is not None and "强预告但公告前已涨透" in stats:
        excess["公告前涨幅门槛"] = f"{runup_filter * 100:.0f}%"
    return stats, excess


def run_backtest(report_period: str = "2026-06-30", hold_days: int = HOLD_DAYS,
                 threshold: float = MIN_CHANGE, sample: int = 150,
                 seed: int = 20261029,
                 runup_filter: float | None = None) -> BacktestResult:
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
    prepared, universe, index = _prepare(forecasts, (hold_days,), sample, seed)
    stats, excess = _compare(prepared, threshold, hold_days, index, runup_filter)
    result = BacktestResult(event_date="各公司自身公告日", hold_days=hold_days,
                            threshold=threshold, universe_size=universe)
    result.groups = stats
    result.excess = excess
    result.notes.append(f"报告期 {report_period}，池 {universe} 只，"
                        f"强预告 {stats.get('强预告组', {}).get('n')} 只，"
                        f"对照 {stats.get('非强预告（对照）', {}).get('n')} 只")
    if runup_filter is not None:
        result.notes.append(f"已按「公告前 {runup_filter * 100:.0f}% 涨幅」过滤，"
                            f"被过滤的 {stats.get('强预告但公告前已涨透', {}).get('n', 0)} 只单列一组")
    result.notes.append(
        "局限：对照组是同报告期非强预告——这批公司**本身就是坏消息集合**"
        "（小幅预减、首亏、续亏居多），拿它当基线测的是「挑强的比挑不强的强多少」，"
        "不是「跟着预告买是否跑赢市场」；真市场基线见 scripts/market_baseline.py。"
        "另外池子按主板代码粗筛（前视）、退市与停牌样本缺失、未计费用与滑点、"
        "A 股 T+1、业绩预告本身有选择性")
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
    prepared, _, index = _prepare(forecasts, tuple(holds), sample, 20261029)
    out: list[dict] = []
    for hold in holds:
        for threshold in thresholds:
            stats, excess = _compare(prepared, threshold, hold, index)
            out.append({"hold_days": hold, "threshold": threshold,
                        "groups": stats, "excess": excess})
    return out


def multi_period(periods=("2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30"),
                 hold_days: int = 60, threshold: float = MIN_CHANGE,
                 sample: int = 150) -> list[dict]:
    """多个报告期合并：单期样本太少、市场环境单一，结论会不稳。

    合并的收益是按公司取中位数（等权），不是组合净值——§51 要求的组合层
    检验（净值、回撤、现金占比）还需要一套完整的历史成分与交易规则，
    那是下一步的事。
    """
    out: list[dict] = []
    for period in periods:
        try:
            result = run_backtest(period, hold_days=hold_days, threshold=threshold,
                                  sample=sample)
        except BacktestError as exc:
            out.append({"period": period, "error": str(exc)})
            continue
        out.append({"period": period, "groups": result.groups, "excess": result.excess})
    return out
