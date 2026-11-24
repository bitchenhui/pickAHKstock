# -*- coding: utf-8 -*-
"""补全的输入：估值分位、股东增减持、成交量趋势、下游需求对照。

每一项都写明数据从哪来、取不到时会怎样：
① 估值分位：东财的逐日估值历史（PE(TTM)、市净率），算当前值在**近 3 年**
   交易日里的分位。PE 取不到（亏损）时只有 PB。只覆盖 A 股。
② 股东增减持：risk.scan_announcements 在同一次公告抓取里分出来的
   增持/减持/回购标题（风险关键词表里没有这两个词，所以必须单独分类）。
③ 成交量趋势：同花顺分年日线里的成交量（backtest 的日线缓存不存成交量，
   不能用那个），算近 20 日均量 ÷ 近 60 日均量。
④ 下游需求：只是一张「行业 → 该看哪个需求指标」的对照表，**没有接数据**，
   页面上只显示指标名和出处。

北向资金不在这里：港交所从 2024-08 起不再逐日披露北向持股与净买入
（akshare 的个股北向持股数据停在 2024-08-16，资金流向接口返回 0），
没有真实数据可接，所以撤掉了，不保留空壳。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

VAL_CACHE = Path.home() / ".cache" / "pickahkstock" / "valuation"
VALUATION_LOOKBACK = 750      # 约三年的交易日


# ================================================ ① 估值历史分位


def valuation_history(code: str) -> list[dict]:
    """逐日 PE(TTM)/PB 历史，按日期升序。每只股票每天最多取一次，其余读缓存。"""
    path = VAL_CACHE / f"{code}.json"
    today = date.today().isoformat()
    cached: list[dict] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached = payload.get("rows", []) if isinstance(payload, dict) else payload
            fetched = payload.get("fetched", "") if isinstance(payload, dict) else ""
        except (ValueError, OSError):
            cached, fetched = [], ""
        if cached and fetched == today:
            return cached
    try:
        import warnings

        warnings.simplefilter("ignore")
        import akshare as ak

        frame = ak.stock_value_em(symbol=code)
        rows = []
        for day, pe, pb in zip(frame["数据日期"], frame["PE(TTM)"], frame["市净率"]):
            try:
                rows.append({"d": str(day)[:10], "pe": float(pe), "pb": float(pb)})
            except (TypeError, ValueError):
                continue
    except Exception:
        return cached          # 今天没取到就用缓存里的历史，分位照算，日期照实写
    if not rows:
        return cached
    VAL_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched": today, "rows": rows}), encoding="utf-8")
    return rows


def _percentile(rows: list[dict], key: str) -> float | None:
    values = [row[key] for row in rows[-VALUATION_LOOKBACK:] if row.get(key) is not None]
    if len(values) < 60:
        return None
    current = values[-1]
    if current <= 0:
        return None            # PE 为负（亏损）时没有分位的意义
    return round(100.0 * sum(1 for value in values if value < current) / len(values), 1)


def valuation_percentile(code: str, market: str = "A") -> dict:
    """当前 PE(TTM)/PB 各自在近 3 年里的分位（0 = 三年来最低，100 = 最高）。

    分位低只说明「比自己过去便宜」，不等于值得买；它是一个排序信号，
    值多少分由 research/evolution.py 里的权重决定。
    """
    if market != "A":
        return {"status": "无数据", "note": "估值历史接口只覆盖 A 股"}
    rows = sorted(valuation_history(code), key=lambda row: row["d"])
    if not rows:
        return {"status": "无数据", "note": "估值历史未取到"}
    pe_pct = _percentile(rows, "pe")
    pb_pct = _percentile(rows, "pb")
    return {"pe": rows[-1].get("pe"), "pb": rows[-1].get("pb"),
            "pe_percentile": pe_pct, "pb_percentile": pb_pct,
            "window_days": min(len(rows), VALUATION_LOOKBACK),
            "as_of": rows[-1]["d"], "status": "有数据" if (pe_pct or pb_pct) else "样本不足",
            "source": "东方财富逐日估值（PE(TTM)、市净率）"}


# ================================================ ② 股东增减持


def holder_changes(scans: dict) -> dict:
    """把公告扫描分出的增持/减持/回购条目汇总成一个方向信号。

    输入是 card.scans（deep-dive 写入）。近 240 天里减持类公告多于增持类
    记 -1，反之 +1，相等或都没有记 0。条数是公告条数，不是股数或金额
    ——一条「减持计划」和它的每一条进展公告都各算一条。
    """
    items = scans.get("holders") or []
    buys = [i for i in items if i.get("side") == "增持"]
    sells = [i for i in items if i.get("side") == "减持"]
    buybacks = [i for i in items if i.get("side") == "回购"]
    return {"buy_count": len(buys), "sell_count": len(sells),
            "buyback_count": len(buybacks),
            "net_signal": (len(buys) > len(sells)) - (len(sells) > len(buys)),
            "recent_buys": buys[:5], "recent_sells": sells[:5],
            "recent_buybacks": buybacks[:3],
            "note": "按公告标题分类，条数不是股数；每条都要回原文核对"}


# ================================================ ③ 成交量趋势


def volume_trend(code: str, market: str = "A") -> dict:
    """近 20 日平均成交量 ÷ 近 60 日平均成交量：关注在升温还是在消退。

    用同花顺分年日线的成交量（backtest 的日线缓存不存成交量）。
    港股没有这个日线源，返回空。
    """
    if market != "A":
        return {}
    from .ths import fetch_daily_history

    try:
        today = date.today()
        bars = [bar for bar in fetch_daily_history(code, [today.year - 1, today.year])
                if bar.get("volume")]
    except Exception:
        return {}
    if len(bars) < 60:
        return {}
    volumes = [float(bar["volume"]) for bar in bars]
    ratio = (sum(volumes[-20:]) / 20) / (sum(volumes[-60:]) / 60)
    if ratio >= 1.5:
        trend = "显著放量"
    elif ratio >= 1.1:
        trend = "温和放量"
    elif ratio <= 0.7:
        trend = "显著缩量"
    elif ratio <= 0.9:
        trend = "温和缩量"
    else:
        trend = "持平"
    return {"vol_ratio_20_60": round(ratio, 2), "trend": trend, "as_of": bars[-1]["day"],
            "note": "近 20 日均量 ÷ 近 60 日均量；≥1.5 显著放量，≤0.7 显著缩量"}


# ================================================ ④ 下游需求对照（没有接数据）

DEMAND_MAP = {
    "电池": {"indicator": "动力电池装机量", "source": "中国汽车动力电池产业创新联盟（月度）"},
    "水泥": {"indicator": "全国水泥产量", "source": "国家统计局（月度）"},
    "半导体": {"indicator": "全球半导体销售额", "source": "SIA（月度）"},
    "工业金属": {"indicator": "制造业 PMI", "source": "国家统计局（月度）"},
    "医疗服务": {"indicator": "诊疗人次", "source": "国家卫健委"},
    "化学原料": {"indicator": "中国化工产品价格指数（CCPI）", "source": "中国化工信息中心"},
}


def demand_check(industry: str) -> dict:
    """这个行业该看哪个下游需求指标。只给指标名和出处，没有接数据。"""
    if not industry or industry not in DEMAND_MAP:
        return {"industry": industry or "", "status": "未映射"}
    return {"industry": industry, **DEMAND_MAP[industry], "status": "仅有指标名，未接数据"}


# ================================================ 统一入口


def fill_inputs(card) -> dict:
    """把补全的输入写进卡片 inputs 字段。任何一项取不到都不影响其他项。"""
    out: dict = {"as_of": date.today().isoformat()}
    try:
        out["valuation"] = valuation_percentile(card.code, card.market)
    except Exception as exc:
        out["valuation"] = {"status": "取数失败", "note": f"{type(exc).__name__}: {exc}"[:120]}
    out["holders"] = holder_changes(card.scans or {})
    try:
        out["volume"] = volume_trend(card.code, card.market)
    except Exception as exc:
        out["volume"] = {"status": "取数失败", "note": f"{type(exc).__name__}: {exc}"[:120]}
    out["demand"] = demand_check((card.industry_context or {}).get("industry", ""))
    card.inputs = out
    return out
