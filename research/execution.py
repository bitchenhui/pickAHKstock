# -*- coding: utf-8 -*-
"""执行层：进出得去吗（流动性门槛）+ 现在相对大盘强不强（相对强度）。

边界（先说清）：这里**不产生买卖点**。流动性是执行保护（strategy-v1
可投资性检查里一直没做的一条），相对强度是市场确认——与「高 beta
双向放大」的诊断（v4）同构，回答「这只票现在比大盘强还是弱」。
它们都是反应式检查，不是预测：预测买卖点的指标在这个仓库里
必须先过十三期基础率检验（regime 的 MA60 过了，别的还没轮到）。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .ths import fetch_daily_history

# 流动性分级：日均成交额 ÷ 单笔金额的倍数。20 倍以下冲击显著。
LIQUID_DANGER = 20
LIQUID_CAUTION = 100

ACCOUNT_FILE = Path(__file__).resolve().parent.parent / "account.json"


def avg_turnover_a(code: str, window: int = 20) -> dict | None:
    """A 股 20 日均成交额（元）：THS 日线的量（手）×100×当日收盘，逐日近似。

    近似口径：用量×收盘价代替真实成交额（缺日内 VWAP），对数量级判断
    （够不够 20 倍冗余）完全够用，不做精确成交额声明。
    """
    today = date.today()
    bars = fetch_daily_history(code, [today.year - 1, today.year])
    if len(bars) < window:
        return None
    daily = [(bar["volume"] * 100 * bar["close"]) for bar in bars[-window:]]
    if not any(daily):
        return None
    return {"avg_turnover": sum(daily) / len(daily), "window": window,
            "as_of": bars[-1]["day"],
            "note": "量×收盘近似口径，够数量级判断"}


def liquidity_gate(avg_turnover: float | None, stake: float | None) -> dict:
    """流动性门槛：日均额 ÷ 单笔金额。stake 未给时只报日均额与规则。"""
    out: dict = {"avg_turnover": avg_turnover, "stake": stake}
    if avg_turnover is None:
        out["level"] = "未知"
        out["note"] = "未取到日均成交额"
        return out
    if stake is None or stake <= 0:
        out["level"] = "未评估"
        out["note"] = f"20 日均成交额 {avg_turnover / 1e8:.2f} 亿；规则：单笔不超过其 5%"
        return out
    ratio = avg_turnover / stake
    out["ratio"] = round(ratio, 1)

    def _mult(value: float) -> str:
        return f"{value / 1e4:.0f} 万倍" if value >= 1e4 else f"{value:.0f} 倍"

    if ratio < LIQUID_DANGER:
        out["level"] = "危险"
        out["note"] = f"日均额仅为单笔的 {_mult(ratio)}：进出冲击显著，别用市价单"
    elif ratio < LIQUID_CAUTION:
        out["level"] = "谨慎"
        out["note"] = f"日均额为单笔的 {_mult(ratio)}：分批挂单，避开开盘半小时"
    else:
        out["level"] = "无忧"
        out["note"] = f"日均额为单笔的 {_mult(ratio)}：小资金进出无冲击"
    return out


def relative_strength(code: str, market: str, window: int = 60) -> dict | None:
    """个股相对指数的强弱：window 交易日收益之差（前复权口径）。

    与 regime 层同一套价格源（backtest 缓存）。解读规则：
    市场下行时 rs>0 = 抗跌或独立行情；市场上行时 rs<0 = 掉队。
    这是「市场确认」维度，不改写基本面判断。
    """
    from .backtest import hk_index_bars, index_bars, load_prices

    today = date.today()
    start = today - timedelta(days=int(window * 2.2) + 30)
    prices = load_prices(code, start, today)
    if len(prices) < window:
        return None
    index = (hk_index_bars if market == "HK" else index_bars)(start, today)
    if len(index) < window:
        return None

    def _ret(series: list[dict]) -> float:
        head = series[-1]["close"]
        tail = series[-window]["close"]
        return head / tail - 1 if tail else 0.0

    stock_ret, index_ret = _ret(prices), _ret(index)
    rs = stock_ret - index_ret
    return {"window": window, "stock_return": round(stock_ret * 100, 1),
            "index_return": round(index_ret * 100, 1), "rs": round(rs * 100, 1),
            "as_of": prices[-1]["day"],
            "note": ("跑赢大盘" if rs > 0 else "跑输大盘") + f"（{window} 日，前复权）"}


def _stake_from_account() -> float | None:
    """单笔参考金额：account.json 的 capital × 单只上限。缺文件就不猜。"""
    import json

    try:
        data = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
        capital = float(data.get("capital_cny") or 0)
        pct = float(data.get("max_position_pct") or 0)
        return capital * pct if capital and pct else None
    except (OSError, ValueError):
        return None


def fill_execution(card) -> dict:
    """把执行层检查写进卡片 execution 字段（deep-dive 调用）。"""
    stake = _stake_from_account()
    out: dict = {"as_of": date.today().isoformat(), "stake": stake}
    if card.market == "A":
        liquidity = liquidity_gate((avg_turnover_a(card.code) or {}).get("avg_turnover")
                                   if avg_turnover_a(card.code) else None, stake)
    else:
        # 港股无 THS 日线：用快照当日成交额做单日口径，标注清楚
        from .quotes import fetch_snapshots

        snapshot = fetch_snapshots([(card.market, card.code)]).get(card.code)
        turnover = snapshot.turnover if snapshot else None
        liquidity = liquidity_gate(turnover, stake)
        if turnover:
            liquidity["note"] = "单日成交额口径（港股无日线额缓存）：" + liquidity["note"]
    strength = relative_strength(card.code, card.market)
    out["liquidity"] = liquidity
    out["strength"] = strength or {}
    card.execution = out
    return out


def rsi_14(closes: list[float]) -> float | None:
    """标准 Wilder 14 期 RSI。"""
    if len(closes) < 15:
        return None
    gains, losses = [], []
    for prev, curr in zip(closes[-15:-1], closes[-14:]):
        diff = curr - prev
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def rsi_current(code: str, market: str) -> float | None:
    """当前 RSI（14 期，前复权日线）。"""
    from .backtest import hk_index_bars, index_bars, load_prices
    from datetime import timedelta

    today = date.today()
    start = today - timedelta(days=80)
    bars = load_prices(code, start, today)
    if len(bars) < 15:
        return None
    return rsi_14([b["close"] for b in bars])
