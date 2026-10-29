"""§51 组合层检验：把候选变成有交易约束的组合，看净值、回撤、现金占比与错过率。

前面的事件研究只回答「信号有没有区分度」，这一层回答「按它操作会怎样」：
- 有交易成本吗（佣金、印花税、滑点）
- 整手约束下还买得起吗（小资金这是硬约束）
- 现金下限与单只上限怎么影响结果
- 最差情境回撤多大
- 错过率——没买到的那些涨得更好的，是常态还是系统性遗漏

组合按**公告日**逐日构建，持有到期卖出，因此净值曲线是真实的时间序列，
不是把几段重叠的窗口拼起来。

局限（与事件研究同源）：退市与停牌样本缺失、幸存者偏差、池子未按市值区间
过滤、预告本身有选择性。
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime

from .backtest import (Group, forward_return, is_strong, load_prices,
                       pre_announcement_runup)


@dataclass
class PortfolioConfig:
    initial_capital: float = 100000.0
    max_positions: int = 5
    position_pct: float = 0.2      # 单只占权益上限
    cash_floor_pct: float = 0.2    # 现金下限
    hold_days: int = 60
    commission_rate: float = 0.00025   # 佣金 万2.5
    min_commission: float = 5.0
    stamp_duty: float = 0.001          # 印花税 千1（卖出侧）
    slippage: float = 0.002            # 单边滑点
    lot_size: int = 100


@dataclass
class Trade:
    code: str
    buy_day: str
    signal_day: str = ""
    sell_day: str = ""
    buy_price: float = 0.0
    sell_price: float = 0.0
    shares: int = 0
    fees: float = 0.0
    pnl: float = 0.0

    def to_dict(self) -> dict:
        return {"code": self.code, "buy_day": self.buy_day,
                "signal_day": self.signal_day, "sell_day": self.sell_day,
                "buy_price": self.buy_price, "sell_price": self.sell_price,
                "shares": self.shares, "fees": round(self.fees, 2), "pnl": round(self.pnl, 2)}


@dataclass
class PortfolioResult:
    name: str
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)   # [(day, equity)]
    cash_ratio: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    fee_total: float = 0.0
    turnover: float = 0.0
    missed_top_share: float = 0.0     # 错过率：没买到的窗口里最好的那只是谁
    skipped_unaffordable: int = 0     # 因整手/资金买不到的次数
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "trades": len(self.trades),
                "total_return": round(self.total_return * 100, 2),
                "max_drawdown": round(self.max_drawdown * 100, 2),
                "cash_ratio": round(self.cash_ratio * 100, 1),
                "win_rate": round(self.win_rate * 100, 1),
                "fee_total": round(self.fee_total, 2),
                "turnover": round(self.turnover, 2),
                "missed_top_share": round(self.missed_top_share * 100, 1),
                "skipped_unaffordable": self.skipped_unaffordable}


def _price_map(prices: list[dict]) -> dict[str, float]:
    return {bar["day"]: bar["close"] for bar in prices}


def _fee(notional: float, config: PortfolioConfig, side: str) -> float:
    commission = max(notional * config.commission_rate, config.min_commission)
    duty = notional * config.stamp_duty if side == "sell" else 0.0
    return commission + duty


def simulate(events: list[dict], price_series: dict[str, list[dict]],
             trading_days: list[str], config: PortfolioConfig,
             name: str = "组合") -> PortfolioResult:
    """按公告日逐日建仓、到期卖出的事件驱动组合模拟。"""
    result = PortfolioResult(name=name)
    if not events or not trading_days:
        result.notes.append("没有事件或没有交易日")
        return result
    closes = {code: _price_map(series) for code, series in price_series.items()}
    day_index = {day: index for index, day in enumerate(trading_days)}
    events_by_day: dict[str, list[dict]] = {}
    for event in events:
        notice = str(event.get("NOTICE_DATE") or "")[:10]
        if notice:
            events_by_day.setdefault(notice, []).append(event)

    cash = config.initial_capital
    holdings: list[Trade] = []      # 未平仓
    closed: list[Trade] = []
    equity_curve: list[tuple[str, float]] = []
    peak = config.initial_capital
    max_drawdown = 0.0
    cash_sum = 0.0
    misses = []
    best_window = []

    for day in trading_days:
        index = day_index[day]
        # 1) 先卖出到期持仓
        still_open = []
        for trade in holdings:
            if index - day_index.get(trade.buy_day, index) >= config.hold_days:
                sell = closes.get(trade.code, {}).get(day)
                if sell:
                    sell_price = sell * (1 - config.slippage)
                    notional = sell_price * trade.shares
                    fee = _fee(notional, config, "sell")
                    cash += notional - fee
                    trade.sell_day = day
                    trade.sell_price = round(sell_price, 4)
                    trade.fees += fee
                    trade.pnl = (sell_price - trade.buy_price) * trade.shares - trade.fees
                    result.fee_total += fee
                    result.turnover += notional
                    closed.append(trade)
                else:
                    still_open.append(trade)
            else:
                still_open.append(trade)
        holdings = still_open

        # 2) 处理当日公告：按候选顺序买入
        todays = events_by_day.get(day, [])
        for event in todays:
            code = str(event.get("SECURITY_CODE") or "").strip()
            series = price_series.get(code) or []
            bar = next((b for b in series if b["day"] > day), None)
            if not bar:
                continue
            if len(holdings) >= config.max_positions:
                continue
            if any(h.code == code for h in holdings):
                continue          # 同一只在持仓中不重复建仓（多期预告会重复出现）
            equity = cash + sum(h.shares * closes.get(h.code, {}).get(day, h.buy_price)
                                for h in holdings)
            if equity <= 0:
                continue
            # 两个约束取小：单只占权益上限；买完仍要留够现金下限
            budget = min(equity * config.position_pct,
                         cash - equity * config.cash_floor_pct)
            price = bar["close"] * (1 + config.slippage)
            shares = int(budget // (price * config.lot_size)) * config.lot_size
            if shares <= 0:
                result.skipped_unaffordable += 1
                continue
            notional = price * shares
            fee = _fee(notional, config, "buy")
            if notional + fee > cash:
                result.skipped_unaffordable += 1
                continue
            cash -= notional + fee
            result.fee_total += fee
            result.turnover += notional
            # 成交发生在公告次日（T+1），持仓期要从**成交日**起算，
            # 不是从公告日起算——否则持有期会短一天
            holdings.append(Trade(code=code, buy_day=bar["day"], signal_day=day,
                                  buy_price=round(price, 4), shares=shares, fees=fee))

        # 3) 记净值
        equity = cash + sum(h.shares * closes.get(h.code, {}).get(day, h.buy_price)
                            for h in holdings)
        equity_curve.append((day, equity))
        cash_sum += cash
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, 1 - equity / peak)

    # 未平仓按最后一天估值
    result.trades = closed + holdings
    result.equity_curve = equity_curve
    result.cash_ratio = (cash_sum / len(equity_curve) / config.initial_capital
                         if equity_curve else 0.0)
    final_equity = equity_curve[-1][1] if equity_curve else config.initial_capital
    result.total_return = final_equity / config.initial_capital - 1
    result.max_drawdown = max_drawdown
    result.win_rate = (sum(1 for t in closed if t.pnl > 0) / len(closed) * 100) if closed else 0.0

    # 错过率：每个事件窗口里表现最好的标的，是否被组合买过
    bought = {t.code for t in result.trades}
    for event in events:
        code = str(event.get("SECURITY_CODE") or "").strip()
        notice = str(event.get("NOTICE_DATE") or "")[:10]
        series = price_series.get(code) or []
        value = forward_return(series, notice, config.hold_days)
        if value is None:
            continue
        best_window.append((value, code, code in bought))
    if best_window:
        best_window.sort(reverse=True)
        top_third = best_window[:max(1, len(best_window) // 3)]
        result.missed_top_share = sum(1 for _, _, bought_flag in top_third
                                      if not bought_flag) / len(top_third)
    return result


def run_portfolio_test(periods=("2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30"),
                       threshold: float = 30.0, min_amount: float | None = 5e7,
                       config: PortfolioConfig | None = None,
                       seed: int = 20261029) -> dict:
    """组合层检验：事件组 vs 随机基线，报告净值、最差回撤、现金占比、错过率与费用。"""
    from .backtest import (forecasts_by_report_period, index_bars, primary_rows,
                           forecasts_raw, legacy_primary_rows)

    config = config or PortfolioConfig()
    events: list[dict] = []
    pool: list[dict] = []
    for period in periods:
        try:
            report_date = datetime.strptime(period, "%Y-%m-%d").date()
        except ValueError:
            continue
        rows = primary_rows(forecasts_by_report_period(report_date))
        for row in rows:
            if is_strong(row, threshold, min_amount):
                events.append(row)
        pool.extend(rows)
    if not events:
        return {"error": "没有符合条件的强预告事件"}

    notices = [str(e.get("NOTICE_DATE") or "")[:10] for e in events]
    notices = [n for n in notices if n]
    start = date.fromisoformat(min(notices)) - __import__("datetime").timedelta(days=40)
    end = date.fromisoformat(max(notices)) + __import__("datetime").timedelta(days=config.hold_days + 30)
    index = index_bars(start, end)
    trading_days = [bar["day"] for bar in index]

    codes = sorted({str(e.get("SECURITY_CODE") or "").strip() for e in events})
    series = {code: load_prices(code, start, end) for code in codes}

    event_result = simulate(events, series, trading_days, config, name="强预告组合")

    # 基线：同一池子里随机抽等量的事件（同样按公告日建仓）
    rng = random.Random(seed)
    baseline_events = [row for row in pool if not is_strong(row, threshold, min_amount)]
    rng.shuffle(baseline_events)
    baseline_events = baseline_events[:len(events)]
    baseline_codes = sorted({str(e.get("SECURITY_CODE") or "").strip() for e in baseline_events})
    baseline_series = {code: load_prices(code, start, end) for code in baseline_codes}
    baseline_result = simulate(baseline_events, baseline_series, trading_days, config,
                               name="随机基线组合")
    return {
        "config": config,
        "events": len(events),
        "index_return": (index[-1]["close"] / index[0]["close"] - 1) if index else None,
        "event": event_result.to_dict(),
        "baseline": baseline_result.to_dict(),
        "event_detail": event_result,
        "baseline_detail": baseline_result,
    }
