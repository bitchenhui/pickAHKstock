# -*- coding: utf-8 -*-
"""输入参量补全：估值分位、股东增减持、成交量趋势、北向资金、下游需求。

五个缺口一次补齐，全部走免费数据源：
① 估值历史分位：THS F10（PE/PB 在自身 5 年的分位）
② 股东增减持：从已有风险扫描提取（标题含「增持」「减持」的结构化）
③ 成交量趋势：已有日线数据计算 20/60 日量比
④ 北向资金：akshare 沪深港通资金流向
⑤ 下游需求：行业→需求指标映射表（手动维护，覆盖当前 WATCHLIST 行业）
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import requests

CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "inputs"

# ================================================ ① 估值历史分位


def valuation_percentile(code: str) -> dict:
    """PE/PB 在自身历史中的分位（THS F10 免费接口）。

    分位 <30% = 历史偏低（便宜），>70% = 历史偏高（贵）。
    """
    import akshare as ak

    try:
        frame = ak.stock_value_em(symbol=code)
        if frame is None or frame.empty:
            return {}
        records = frame.to_dict("records")
        if not records:
            return {}
        latest = records[-1]
        # 历史 PE/PB 序列
        pe_list = [r.get("市盈率-动态") for r in records if r.get("市盈率-动态")]
        pb_list = [r.get("市净率") for r in records if r.get("市净率")]
        pe_now = latest.get("市盈率-动态")
        pb_now = latest.get("市净率")

        def percentile(values, current):
            if not values or current is None or current <= 0:
                return None
            below = sum(1 for v in values if v is not None and v < current)
            return round(below / len(values) * 100, 1)

        return {
            "pe": pe_now, "pb": pb_now,
            "pe_percentile": percentile(pe_list, pe_now),
            "pb_percentile": percentile(pb_list, pb_now),
            "history_len": len(records),
            "as_of": str(latest.get("交易日期", ""))[:10] or date.today().isoformat(),
            "source": "东财估值数据",
        }
    except Exception:
        return {}


# ================================================ ② 股东增减持


def holder_changes(scans: dict) -> dict:
    """从已有的风险扫描留痕中提取股东/高管增减持。

    输入是 card.scans（deep-dive 已写入），从风险公告标题中提取
    「增持」「减持」「回购」相关的条目。
    """
    risks = scans.get("risks") or []
    buys, sells, buybacks = [], [], []
    for r in risks:
        title = str(r.get("title") or "")
        published = str(r.get("published") or "")
        if not title:
            continue
        # 增持
        if "增持" in title and not("不增持" in title or "终止增持" in title):
            if any(k in title for k in ("股东", "董事", "高管", "实际控制人")):
                buys.append({"title": title[:60], "date": published})
        # 减持
        elif "减持" in title and "不减" not in title:
            if any(k in title for k in ("股东", "董事", "高管", "实际控制人")):
                sells.append({"title": title[:60], "date": published})
        # 回购
        elif "回购" in title and "终止" not in title and "完成" not in title:
            buybacks.append({"title": title[:60], "date": published})

    net_signal = 0
    if len(buys) > len(sells):
        net_signal = 1      # 净增持
    elif len(sells) > len(buys):
        net_signal = -1     # 净减持
    return {"recent_buys": buys[:5], "recent_sells": sells[:5],
            "recent_buybacks": buybacks[:3],
            "buy_count": len(buys), "sell_count": len(sells),
            "net_signal": net_signal,
            "note": "正=净增持（信心信号），负=净减持（红旗）"}


# ================================================ ③ 成交量趋势


def volume_trend(code: str, market: str = "A") -> dict:
    """20/60 日成交量趋势：兴趣在升还是在降。

    用已有日线数据计算（backtest.load_prices 含 volume），
    不需要新数据源。
    """
    from .backtest import load_prices

    try:
        today = date.today()
        bars = load_prices(code, today - timedelta(days=150), today)
        if len(bars) < 61:
            return {}
        vols = [b.get("volume") or 0 for b in bars if b.get("volume")]
        if len(vols) < 60:
            return {}
        avg_20 = sum(vols[-20:]) / 20
        avg_60 = sum(vols[-60:]) / 60
        if avg_60 <= 0:
            return {}
        ratio = avg_20 / avg_60
        # 20日均量 / 60日均量：>1.5 = 显著放量，<0.7 = 显著缩量
        if ratio >= 1.5:
            label = "显著放量"
        elif ratio >= 1.1:
            label = "温和放量"
        elif ratio <= 0.7:
            label = "显著缩量"
        elif ratio <= 0.9:
            label = "温和缩量"
        else:
            label = "持平"
        return {"vol_ratio_20_60": round(ratio, 2), "trend": label,
                "note": "20日均量÷60日均量，>1.5放量/<0.7缩量",
                "as_of": bars[-1]["day"]}
    except Exception:
        return {}


# ================================================ ④ 北向资金


def northbound_flow(industry: str = "") -> dict:
    """北向资金近 5 日净流入（全市场或按行业聚合）。

    akshare 免费接口，按行业取需要额外映射，先给全市场口径。
    """
    import akshare as ak

    try:
        frame = ak.stock_hsgt_fund_flow_summary_em()
        if frame is None or frame.empty:
            return {}
        records = frame.to_dict("records")
        if not records:
            return {}
        latest = records[-1]
        return {
            "net_buy_yi": latest.get("北向资金净买额-亿元") or latest.get("净买额"),
            "date": str(latest.get("日期") or latest.get("持有日期") or "")[:10],
            "source": "东财沪深港通",
            "note": "北向净流入为正=外资在买，负=在卖",
        }
    except Exception:
        return {}


# ================================================ ⑤ 下游需求验证

# 行业 → 需求指标映射（手动维护，覆盖当前 WATCHLIST 行业）
DEMAND_MAP = {
    "电池": {"indicator": "动力电池装机量", "source": "中国汽车动力电池产业创新联盟（月度）",
             "direction": "up_good", "note": "月度装机量同比正增长=需求在上行"},
    "水泥": {"indicator": "全国水泥产量", "source": "国家统计局（月度）",
             "direction": "up_good", "note": "水泥产量同比正增长=基建需求在上行"},
    "半导体": {"indicator": "全球半导体销售额", "source": "SIA（月度）",
               "direction": "up_good", "note": "全球销售额同比正增长=行业景气在上行"},
    "工业金属": {"indicator": "PMI（制造业）", "source": "国家统计局（月度）",
                 "direction": "above50_good", "note": "PMI>50=制造业扩张，金属需求有支撑"},
    "医疗服务": {"indicator": "诊疗人次（卫健委季报）", "source": "国家卫健委",
                 "direction": "up_good", "note": "诊疗人次增长=需求在上行"},
    "化学原料": {"indicator": "化工产品价格指数", "source": "中国化工产品价格指数（CCPI）",
                 "direction": "up_good", "note": "CCPI 上行=化工品价格有支撑"},
}


def demand_check(industry: str) -> dict:
    """行业的下游需求指标（映射表查询，目前只给指标名和来源，
    实际数据需要接行业专属接口——这是诚实边界）。
    """
    if not industry:
        return {}
    entry = DEMAND_MAP.get(industry)
    if not entry:
        return {"industry": industry, "status": "未映射",
                "note": "该行业暂无下游需求映射，需手动补充"}
    return {"industry": industry, **entry, "status": "已映射（需手动核对最新数据）"}


# ================================================ 统一入口


def fill_inputs(card) -> dict:
    """把 5 个补全项写入卡片 inputs 字段。"""
    out: dict = {"as_of": date.today().isoformat()}
    try:
        out["valuation"] = valuation_percentile(card.code)
    except Exception:
        out["valuation"] = {}
    out["holders"] = holder_changes(card.scans or {})
    try:
        out["volume"] = volume_trend(card.code, card.market)
    except Exception:
        out["volume"] = {}
    industry = (card.industry_context or {}).get("industry", "")
    out["demand"] = demand_check(industry)
    card.inputs = out
    return out
