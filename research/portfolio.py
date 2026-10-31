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
                       prefetch_prices, pre_announcement_runup)


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
    max_drawdown: float = 0.20         # 账户回撤边界（策略稿 §44）
    apply_drawdown_rule: bool = True   # 是否按回撤状态限制新增仓位
    force_exit_on_drawdown: bool = False  # 触及边界时是否强制平掉现有持仓


def drawdown_state(drawdown: float, limit: float) -> str:
    """与账户层同一套阈值：警戒 50% / 收缩 75% / 暂停新增 100%。

    模拟器必须用工具真正执行的那套规则，否则测出来的不是策略自己能做到的事。
    """
    if limit <= 0:
        return "正常"
    if drawdown >= limit:
        return "暂停新增"
    if drawdown >= limit * 0.75:
        return "收缩"
    if drawdown >= limit * 0.5:
        return "警戒"
    return "正常"


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
    win_rate: float = 0.0            # 0-1 的小数
    fee_total: float = 0.0
    turnover: float = 0.0
    missed_top_share: float = 0.0     # 错过率：表现最好的那批窗口里没买到的比例
    coverage: float = 0.0             # 候选覆盖率：买到的 / 事件总数
    selection_median: float | None = None   # 买到的候选的持有期收益中位数
    pool_median: float | None = None        # 全体事件窗口的收益中位数
    halted_day: str = ""              # 因回撤停止新增仓位的日期
    halted_state: str = ""            # 触发时的回撤状态
    blocked_by_drawdown: int = 0      # 因回撤规则被拒的新增次数
    skipped_unaffordable: int = 0     # 因整手/资金买不到的次数
    blocked_by_limit: int = 0         # 因跌停收盘卖不掉、顺延的次数
    scaled_by_regime: int = 0         # 因市场在下行而打了折的建仓次数
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "trades": len(self.trades),
                "total_return": round(self.total_return * 100, 2),
                "max_drawdown": round(self.max_drawdown * 100, 2),
                "cash_ratio": round(self.cash_ratio * 100, 1),
                "win_rate": round(self.win_rate * 100, 1),
                "coverage": round(self.coverage * 100, 1),
                "selection_median": (None if self.selection_median is None
                                     else round(self.selection_median * 100, 2)),
                "pool_median": (None if self.pool_median is None
                                else round(self.pool_median * 100, 2)),
                "fee_total": round(self.fee_total, 2),
                "turnover": round(self.turnover, 2),
                "missed_top_share": round(self.missed_top_share * 100, 1),
                "skipped_unaffordable": self.skipped_unaffordable,
                "blocked_by_limit": self.blocked_by_limit,
                "scaled_by_regime": self.scaled_by_regime,
                "halted_day": self.halted_day, "halted_state": self.halted_state,
                "blocked_by_drawdown": self.blocked_by_drawdown}


def _price_map(prices: list[dict]) -> dict[str, float]:
    return {bar["day"]: bar["close"] for bar in prices}


def _fee(notional: float, config: PortfolioConfig, side: str) -> float:
    commission = max(notional * config.commission_rate, config.min_commission)
    duty = notional * config.stamp_duty if side == "sell" else 0.0
    return commission + duty


def limit_pct(code: str) -> float:
    """该股的涨跌停幅度（近似）。

    主板 10%、创业板/科创板 20%、北交所 30%。ST 股是 5%，但我们没有
    ST 标记，会把 ST 股按 10% 判定——这只会**少**判几次跌停，
    不会多判，方向是保守的。
    """
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("4", "8", "9")):
        return 30.0
    return 10.0


def limit_down_days(series: list[dict], code: str, tolerance: float = 0.3) -> set:
    """收在跌停的交易日：这些天卖不掉（没有买盘）。

    判据：当日跌幅达到停板幅度，且收盘价就是当日最低价（收盘封在跌停）。
    跌停开盘但盘中打开、当日没封住的，按能卖处理——那说明有成交。
    """
    limit = limit_pct(code)
    out = set()
    for previous, current in zip(series, series[1:]):
        base = previous.get("close")
        if not base:
            continue
        change = (current["close"] / base - 1) * 100
        if change <= -(limit - tolerance) and current.get("low") == current.get("close"):
            out.add(current["day"])
    return out


def simulate(events: list[dict], price_series: dict[str, list[dict]],
             trading_days: list[str], config: PortfolioConfig,
             name: str = "组合", sort_key: str = "amount_desc",
             index_bars: list[dict] | None = None,
             regime_scaling: bool = False) -> PortfolioResult:
    """按公告日逐日建仓、到期卖出的事件驱动组合模拟。

    regime_scaling=True 时启用「下行市仓位减半」：按**建仓当日**的市场状态
    决定该笔仓位打折多少（只用当日之前的数据，point-in-time）。
    依据是 v4 的诊断：这类高 beta 标的在下跌市里跌得比随机股多 1.08–1.33 倍。
    """
    result = PortfolioResult(name=name)
    if not events or not trading_days:
        result.notes.append("没有事件或没有交易日")
        return result
    closes = {code: _price_map(series) for code, series in price_series.items()}
    # 跌停收盘的交易日：卖不掉，只能顺延到下一个能卖的日子
    locked = {code: limit_down_days(series, code)
              for code, series in price_series.items()}
    day_index = {day: index for index, day in enumerate(trading_days)}
    rng = random.Random(20261029)
    events_by_day: dict[str, list[dict]] = {}
    for event in events:
        notice = str(event.get("NOTICE_DATE") or "")[:10]
        if notice:
            events_by_day.setdefault(notice, []).append(event)
    # 同一天内按**发现层的排序**买（默认：预告绝对额降序），否则买到的只是
    # API 返回顺序决定的任意子集，组合结果无法归因于选股
    for day, items in events_by_day.items():
        if sort_key == "random":
            rng.shuffle(items)
        elif sort_key == "change_asc":       # 温和优先：幅度小的先买
            items.sort(key=lambda r: (float(r.get("ADD_AMP_LOWER") or 0)
                                      if str(r.get("ADD_AMP_LOWER") or "") else 0.0))
        elif sort_key == "change_desc":      # 幅度大的先买（对照）
            items.sort(key=lambda r: -(float(r.get("ADD_AMP_LOWER") or 0)
                                       if str(r.get("ADD_AMP_LOWER") or "") else 0.0))
        else:                                # amount_desc：发现层默认
            items.sort(key=lambda r: -(float(r.get("PREDICT_AMT_LOWER") or 0)
                                       if str(r.get("PREDICT_AMT_LOWER") or "") else 0.0))

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
        # 0) 先算当日权益：强制平仓需要按当日价格判断回撤
        equity_open = cash + sum(h.shares * closes.get(h.code, {}).get(day, h.buy_price)
                                 for h in holdings)
        if config.force_exit_on_drawdown and holdings and peak > 0:
            if 1 - equity_open / peak >= config.max_drawdown:
                remaining = []
                for trade in holdings:
                    sell = closes.get(trade.code, {}).get(day)
                    if not sell:
                        # 当日无价（停牌或序列未覆盖）：不能当成已卖出，
                        # 否则持仓会凭空消失、权益虚降、回撤被算成假的
                        remaining.append(trade)
                        continue
                    if day in locked.get(trade.code, ()):
                        result.blocked_by_limit += 1
                        remaining.append(trade)
                        continue
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
                    if not result.halted_day:
                        result.halted_day = day
                        result.halted_state = "强制平仓"
                holdings = remaining
        # 1) 再卖出到期持仓
        still_open = []
        for trade in holdings:
            if index - day_index.get(trade.buy_day, index) >= config.hold_days:
                sell = closes.get(trade.code, {}).get(day)
                if sell and day in locked.get(trade.code, ()):
                    # 跌停收盘：卖不掉，顺延到下一个交易日
                    result.blocked_by_limit += 1
                    still_open.append(trade)
                elif sell:
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
            # 回撤状态：与账户层同一套规则，触及边界后停止新增仓位
            if config.apply_drawdown_rule:
                state = drawdown_state(1 - equity / peak if peak > 0 else 0.0,
                                       config.max_drawdown)
                if state in ("收缩", "暂停新增"):
                    result.blocked_by_drawdown += 1
                    if not result.halted_day:
                        result.halted_day = day
                        result.halted_state = state
                    continue
            # 两个约束取小：单只占权益上限；买完仍要留够现金下限
            budget = min(equity * config.position_pct,
                         cash - equity * config.cash_floor_pct)
            if regime_scaling and index_bars:
                # 建仓当日的市场状态（只用到当日之前的数据）
                from .regime import regime_at

                state = regime_at(index_bars, day)
                if state.down:
                    budget *= state.position_factor()
                    result.scaled_by_regime += 1
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
    result.win_rate = (sum(1 for t in closed if t.pnl > 0) / len(closed)) if closed else 0.0

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
        # 覆盖率与选择质量：错过率会被「最多持 N 只」的硬约束主导，
        # 所以必须同时看买到的比例，以及买到的那些挑得准不准
        result.coverage = sum(1 for _, _, flag in best_window if flag) / len(best_window)
        bought_values = [value for value, _, flag in best_window if flag]
        if bought_values:
            result.selection_median = statistics.median(bought_values)
        result.pool_median = statistics.median(value for value, _, _ in best_window)
    return result


def run_portfolio_test(periods=("2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30"),
                       threshold: float = 30.0, min_amount: float | None = 5e7,
                       config: PortfolioConfig | None = None,
                       seed: int = 20261029,
                       sort_key: str = "amount_desc",
                       prefilter_affordability: bool = False,
                       market_baseline: bool = False,
                       market_sample: int = 400,
                       regime_scaling: bool = False) -> dict:
    """组合层检验：事件组 vs 随机基线，报告净值、最差回撤、现金占比、错过率与费用。

    基线有两条，含义不同，别混：
    - **池内随机**：同报告期**发了预告但变动不达标**的公司。这批公司本身就是
      坏消息集合，拿它当基线会把信号优势高估。
    - **全市场随机**（market_baseline=True）：当期没发预告的 A 股，事件日从当期
      真实公告日分布里等概率抽，建仓时点与事件组可比。这才是「有没有 alpha」
      该问的基线。
    """
    from .backtest import (forecasts_by_report_period, index_bars, primary_rows,
                           forecasts_raw, legacy_primary_rows)

    config = config or PortfolioConfig()
    events: list[dict] = []
    pool: list[dict] = []
    period_rows: list[list[dict]] = []
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
        period_rows.append(rows)
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

    if prefilter_affordability:
        # 事前剔除「一手金额就超过单只仓位上限」的候选：在「买得进」的前提下
        # 才谈得上排序有没有价值（否则排序只是决定谁被跳过）
        def affordable(event):
            code = str(event.get("SECURITY_CODE") or "").strip()
            bars = series.get(code) or []
            notice = str(event.get("NOTICE_DATE") or "")[:10]
            bar = next((b for b in bars if b["day"] > notice), None)
            if not bar:
                return True
            return bar["close"] * config.lot_size <= config.initial_capital * config.position_pct
        events = [e for e in events if affordable(e)]

    event_result = simulate(events, series, trading_days, config, name="强预告组合",
                            sort_key=sort_key, index_bars=index,
                            regime_scaling=regime_scaling)

    # 基线：同一池子里随机抽等量的事件（同样按公告日建仓）
    rng = random.Random(seed)
    baseline_events = [row for row in pool if not is_strong(row, threshold, min_amount)]
    rng.shuffle(baseline_events)
    baseline_events = baseline_events[:len(events)]
    baseline_codes = sorted({str(e.get("SECURITY_CODE") or "").strip() for e in baseline_events})
    baseline_series = {code: load_prices(code, start, end) for code in baseline_codes}
    baseline_result = simulate(baseline_events, baseline_series, trading_days, config,
                               name="随机基线组合", sort_key="random")

    result = {
        "config": config,
        "events": len(events),
        "index_return": (index[-1]["close"] / index[0]["close"] - 1) if index else None,
        "event": event_result.to_dict(),
        "baseline": baseline_result.to_dict(),
        "event_detail": event_result,
        "baseline_detail": baseline_result,
    }

    if market_baseline:
        market_result = _market_portfolio(period_rows, trading_days, config,
                                          start, end, seed, market_sample,
                                          threshold, min_amount)
        if market_result is not None:
            result["market_baseline"] = market_result.to_dict()
            result["market_baseline_detail"] = market_result
    return result


def _market_portfolio(period_rows, trading_days, config, start, end,
                      seed: int, sample: int, threshold: float,
                      min_amount: float | None):
    """全市场随机组合：当期没发预告的 A 股，事件日按当期公告日分布抽。

    建仓时点与**每期买入机会的数量**都与事件组对齐（按期分别对齐），
    唯一的差别是标的从市场里随机抽。数量不对齐的话，两边的资金暴露时间
    就不同，收益差里混着暴露差，不是信号差。
    """
    from .industry import industry_map

    market_codes = set(industry_map("second").keys())
    rng = random.Random(seed + 1)
    market_events: list[dict] = []
    for rows in period_rows:
        forecasters = {str(row.get("SECURITY_CODE") or "").strip() for row in rows}
        notices = [str(row.get("NOTICE_DATE") or "")[:10] for row in rows
                   if str(row.get("NOTICE_DATE") or "")[:10]]
        if not notices:
            continue
        strong_here = sum(1 for row in rows if is_strong(row, threshold, min_amount))
        candidates = sorted(market_codes - forecasters)
        want = min(len(candidates), strong_here or sample)
        for code in rng.sample(candidates, want):
            market_events.append({
                "SECURITY_CODE": code,
                "NOTICE_DATE": rng.choice(notices),
                "ADD_AMP_LOWER": 0,
                "PREDICT_AMT_LOWER": 0,
            })
    if not market_events:
        return None
    codes = sorted({event["SECURITY_CODE"] for event in market_events})
    # 几千只股票串行取价要几十分钟，并行预取（限流友好的并发数）才跑得动
    prefetch_prices(codes, start, end)
    series = {code: load_prices(code, start, end) for code in codes}
    return simulate(market_events, series, trading_days, config,
                    name="全市场随机组合", sort_key="random")
