# -*- coding: utf-8 -*-
"""盯的价格：行业共振判了「行业给的」，就要盯行业价格本身。

行业共振层的结论（如石大胜华 4/4 同向 = 电池行业给的）指向的是价格变量
（碳酸锂、工业硅……），但工具一直拿不到价格序列——判断只能停在定性。
这个模块补上：akshare 的 futures_spot_price_daily 给出期货品种对应的
**现货价**日频序列（免登录），按卡片代码映射到该盯的品种。

诚实边界：
- 映射表是人工维护的（WATCHLIST），没映射的票明确跳过，不猜；
- 六氟磷酸锂等无公开日频现货的品种，用成本端/上游期货品种做**代理**，
  代理关系在名称里写明，不冒充该商品本身的价格；
- 水泥等无期货品种的，用建材链品种做代理并标注。
"""

from __future__ import annotations

import json
import warnings
from datetime import date, timedelta
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "commodity"

# 代码 → 盯什么价。名称里写明口径：直接的写商品名，代理的写「（代理）」。
WATCHLIST: dict[str, list[dict]] = {
    "603026": [{"symbol": "LC", "name": "碳酸锂（六氟磷酸锂成本端代理）"}],
    "09696": [{"symbol": "LC", "name": "碳酸锂"}],
    "605358": [{"symbol": "SI", "name": "工业硅（硅片上游）"}],
    "002132": [{"symbol": "SI", "name": "工业硅（有机硅 DMC 上游代理）"}],
    "03323": [{"symbol": "SA", "name": "纯碱（建材链代理）"},
              {"symbol": "FG", "name": "玻璃（建材链代理）"}],
}

SOURCE = "akshare futures_spot_price_daily（期货品种对应现货价）"


def _fetch_daily(start: str, end: str, symbols: list[str]) -> list[dict]:
    """拉一段现货价。akshare 对非交易日会打 warnings，静音即可。"""
    import akshare as ak

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame = ak.futures_spot_price_daily(start, end, vars_list=symbols)
    rows = []
    for record in frame.to_dict("records"):
        day = str(record.get("date") or "")
        price = record.get("spot_price")
        symbol = str(record.get("symbol") or "")
        if day and price is not None:
            rows.append({"day": f"{day[:4]}-{day[4:6]}-{day[6:8]}", "symbol": symbol,
                         "price": float(price)})
    rows.sort(key=lambda row: row["day"])
    return rows


def series(symbol: str, lookback_days: int = 400) -> list[dict]:
    """某品种的现货价序列（带落盘缓存，增量补齐）。

    缓存存全量并集：下次只取「缓存最后一天 → 今天」的增量，
    避免每次都逐日重拉（akshare 这个接口对区间内每个交易日各请求一次）。
    """
    cache_file = CACHE_DIR / f"spot_{symbol}.json"
    cached: list[dict] = []
    if cache_file.exists():
        try:
            cached = [row for row in json.loads(cache_file.read_text(encoding="utf-8"))
                      if row.get("symbol") == symbol]
        except (ValueError, OSError):
            cached = []
    start = date.today() - timedelta(days=lookback_days)
    if cached:
        last = date.fromisoformat(cached[-1]["day"])
        if last >= date.today() - timedelta(days=2):
            return cached
        start = last + timedelta(days=1)
    fresh = _fetch_daily(start.strftime("%Y%m%d"), date.today().strftime("%Y%m%d"), [symbol])
    if not fresh and not cached:
        raise RuntimeError(f"{symbol} 现货价未取到")
    merged = {row["day"]: row for row in cached}
    for row in fresh:
        merged[row["day"]] = row
    out = [merged[key] for key in sorted(merged)]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def changes(rows: list[dict]) -> dict:
    """最新价、近 30/250 交易日涨跌幅、一年区间位置（0%=最低，100%=最高）。

    窗口不足时用可得窗口并标注（short_window=True），不冒充满窗口读数。
    """
    if not rows:
        return {}
    prices = [row["price"] for row in rows]
    latest_day, latest = rows[-1]["day"], prices[-1]

    def _chg(window: int) -> tuple[float | None, bool]:
        if len(prices) <= 1:
            return None, False
        actual = min(window, len(prices) - 1)
        base = prices[-1 - actual]
        if base <= 0:
            return None, False
        return (latest / base) - 1, actual < window

    chg_30, short_30 = _chg(30)
    chg_250, short_250 = _chg(250)
    year = prices[-min(250, len(prices)):]
    low, high = min(year), max(year)
    position = (latest - low) / (high - low) if high > low else None
    return {"day": latest_day, "latest": latest,
            "chg_30d": chg_30, "chg_30d_short": short_30,
            "chg_250d": chg_250, "chg_250d_short": short_250,
            "low_250d": low, "high_250d": high,
            "position_250d": position}


def fill_commodities(card) -> dict:
    """把该盯的价格写进卡片 commodities 字段；无映射的票抛 KeyError 由调用方记 skip。"""
    watch = WATCHLIST.get(card.code)
    if not watch:
        raise KeyError(f"{card.code} 不在价格映射表（WATCHLIST），该盯什么价需要人定")
    from datetime import date as _date

    out: dict = {"as_of": _date.today().isoformat(), "source": SOURCE, "items": {}}
    for item in watch:
        rows = series(item["symbol"])
        stats = changes(rows)
        stats["name"] = item["name"]
        stats["symbol"] = item["symbol"]
        stats["series_tail"] = rows[-10:]     # 最近 10 个点，供渲染画迷你走势
        out["items"][item["symbol"]] = stats
    card.commodities = out
    return out
