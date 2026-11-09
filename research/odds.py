# -*- coding: utf-8 -*-
"""高赔率漏斗：面向「1 年内 5-10 倍」的结构条件筛选。

定位（必须先说清楚，所有输出都带这句话）：这不是 alpha 信号——v5 十三期检验
已证明业绩预告信号没有选股能力。这一层回答另一件事：**一只票要在 1 年内走完
5-10 倍，结构上需要什么前提**，把具备前提的候选摆到人面前，并给出
「10 倍需要什么为真」的情景拆解。它不预测概率，也不排序「谁最可能」。

三个结构条件（主口径固定，先验理由如下；敏感性在基础率脚本里报告，不做数据挑选）：

1. **市值带 30-150 亿**（公告日口径）：10 倍后 300-1500 亿仍在可解释区间；
   150 亿以上走完 10 倍需要超级周期配合，30 亿以下流动性与治理风险陡增。
2. **价格位置**：公告前近一年涨幅 < 80%——已主升过的票，空间被前置消耗。
3. **拐点类型**：扭亏 / 减亏 / 预增。注意**扭亏行的同比幅度字段常为空**，
   现有发现层的幅度门槛会把它们系统性丢掉——而困境反转恰恰是 5-10 倍
   最常见的历史形态。这里按绝对额与上年同期值判定，不看幅度。

市值是近似口径：当前总股本（快照市值/快照价反推）× 公告日前复权收盘。
前复权已消除送转影响，剩余偏差来自增发/回购的股本变动与纯现金分红——
30-150 亿这种宽带筛选下足够用，精确到个位数没有意义。

「10 倍需要什么为真」的拆解（strategy-v1 的公式）：
    needed_profit = 市值 × 倍数 / 目标PE
    gap = needed_profit / 年化预告利润
首版稀释按 1 计（未含增发稀释），未盈利路径必须人工补融资情景——工具只摆数字。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .quotes import Snapshot, fetch_snapshots

CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "odds"

INFLECTION_TYPES = ("扭亏", "减亏", "预增")
DEFAULT_CAP_MIN = 30e8
DEFAULT_CAP_MAX = 150e8
DEFAULT_RUNUP_MAX = 0.80
LOOKBACK = 250          # 回看窗口：约一年
MIN_LOOKBACK = 120      # 少于此根数不算（2022-09 起的缓存对最早一期不够 250）
FORWARD_WINDOW = 250    # 前向窗口：约一年，对应「1 年内」的目标时限
DEFAULT_TARGET_PE = 25.0


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:      # NaN
        return None
    return out


# ---------------------------------------------------------------- 拐点分类


def inflection_type(row: dict) -> str:
    """扭亏 / 减亏 / 预增 / 其他 —— 按绝对额与上年同期判定，不看同比幅度。

    扭亏：PREDICT_TYPE 直接写「扭亏」，或上年同期为负且本期预告下限为正。
    幅度字段（ADD_AMP_LOWER）对扭亏行常为空，这是现有发现层丢掉它们的
    原因，也是这一层必须绕开幅度的原因。
    """
    ftype = str(row.get("PREDICT_TYPE") or "").strip()
    prev = _num(row.get("PREYEAR_SAME_PERIOD"))
    lower = _num(row.get("PREDICT_AMT_LOWER"))
    if ftype == "扭亏" or (prev is not None and prev < 0
                           and lower is not None and lower > 0):
        return "扭亏"
    if ftype in ("减亏", "预增"):
        return ftype
    return "其他"


# ---------------------------------------------------------------- 价格位置与前向窗口


@dataclass
class RunupResult:
    value: float | None = None
    short_lookback: bool = False   # 回看不足 LOOKBACK 根，用了可得窗口

    def __bool__(self) -> bool:
        return self.value is not None


def runup(prices: list[dict], notice: str, lookback: int = LOOKBACK,
          min_bars: int = MIN_LOOKBACK) -> RunupResult:
    """公告日之前近一年的涨幅：判断「空间是否已被前置消耗」。

    prices 必须已覆盖回看窗口（调用方用 load_prices 取够宽的区间）。
    可得根数 < min_bars 时返回 None——短窗口的涨幅会低估主升程度，
    宁可标缺失也不给一个看起来正常的数。
    """
    if not prices:
        return RunupResult()
    index = None
    for position, bar in enumerate(prices):
        if bar["day"] >= notice:
            index = position
            break
    if index is None or index == 0:
        return RunupResult()
    available = index            # 公告日之前的根数
    if available < min_bars:
        return RunupResult()
    start = max(0, index - lookback)
    base = prices[start]["close"]
    if base <= 0:
        return RunupResult()
    return RunupResult(value=(prices[index - 1]["close"] / base) - 1,
                       short_lookback=available < lookback)


@dataclass
class ForwardWindow:
    max_gain_close: float | None = None   # 窗口内最高收盘 / 买入价 - 1（可兑现口径）
    max_gain_high: float | None = None    # 窗口内最高价 / 买入价 - 1（摸到过口径）
    end_return: float | None = None       # 窗口末根收盘 / 买入价 - 1
    days: int = 0                         # 实际覆盖的交易日数（不足窗口时会 < window）

    def __bool__(self) -> bool:
        return self.max_gain_close is not None


def forward_window(prices: list[dict], notice: str,
                   window: int = FORWARD_WINDOW) -> ForwardWindow:
    """公告日后第一根收盘买入（与 backtest.forward_return 同口径），一年窗口内
    能摸到的最大涨幅与期末收益。

    最大涨幅用 close 口径为主（可兑现），high 口径并列——盘中最高的
    「摸到过」多数人拿不到，但它是「确实走出过」的证据。
    """
    if not prices:
        return ForwardWindow()
    start = None
    for index, bar in enumerate(prices):
        if bar["day"] > notice:
            start = index
            break
    if start is None:
        return ForwardWindow()
    entry = prices[start]["close"]
    if entry <= 0:
        return ForwardWindow()
    bars = prices[start + 1: start + 1 + window]   # T+1：买入当根不计入持有
    if not bars:
        return ForwardWindow()
    closes = [bar["close"] for bar in bars if bar.get("close")]
    highs = [bar.get("high") for bar in bars if bar.get("high")]
    return ForwardWindow(
        max_gain_close=(max(closes) / entry) - 1 if closes else None,
        max_gain_high=(max(highs) / entry) - 1 if highs else None,
        end_return=(closes[-1] / entry) - 1,
        days=len(bars))


# ---------------------------------------------------------------- 市值与情景拆解


def shares_from(snapshot: Snapshot) -> float | None:
    """总股本（股）。A 股快照没有 shares 字段，用 市值/现价 反推（同一快照口径）。"""
    if snapshot.shares:
        return snapshot.shares
    if snapshot.market_cap and snapshot.price:
        return snapshot.market_cap / snapshot.price
    return None


def cap_at(shares: float, notice_close: float) -> float:
    """公告日市值 ≈ 当前总股本 × 公告日前复权收盘。"""
    return shares * notice_close


def what_needed(market_cap: float, multiple: float = 10.0,
                target_pe: float = DEFAULT_TARGET_PE) -> float:
    """「市值再走 multiple 倍」需要的年化正常化净利润（元）。

    needed = 市值 × multiple / 目标PE。目标PE 首版用固定档（25 倍），
    卡片层后续可用同业中枢替换。稀释按 1 计——乐观情景若依赖永不融资，
    那是「空间未证实」，由人补融资情景，工具不假装算过。
    """
    return market_cap * multiple / target_pe


def annualized_track(predict_lower: float | None, report_period: str) -> float | None:
    """预告净利润年化：累计值 × 12 / 报告期月数。下限口径，保守。"""
    if predict_lower is None or predict_lower <= 0:
        return None          # 减亏/亏损路径没有正利润基准，gap 无从谈起
    months = {"03-31": 3, "06-30": 6, "09-30": 9, "12-31": 12}.get(
        str(report_period)[5:10])
    if not months:
        return None
    return predict_lower * 12 / months


# ---------------------------------------------------------------- 快照缓存（公告日市值用）


def load_snapshots(codes: list[tuple[str, str]], max_age_days: int = 3) -> dict[str, Snapshot]:
    """带落盘缓存的批量快照：总股本基本只随增发/回购变化，几天内复用没有问题。

    缓存按日期分文件，过期重取。行情失败的代码不进缓存，下次还能补。
    """
    wanted = {code for _, code in codes}
    cache_file = CACHE_DIR / f"snapshots_{date.today().isoformat()}.json"
    out: dict[str, Snapshot] = {}
    if cache_file.exists():
        try:
            age = date.today() - date.fromtimestamp(cache_file.stat().st_mtime)
            if age.days <= max_age_days:
                for row in json.loads(cache_file.read_text(encoding="utf-8")):
                    snapshot = Snapshot(**row)
                    if snapshot.code in wanted:
                        out[snapshot.code] = snapshot
        except (ValueError, OSError, TypeError):
            out = {}
    missing = [(market, code) for market, code in codes if code not in out]
    if missing:
        fetched = fetch_snapshots(missing)
        out.update(fetched)
        if fetched:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            merged: dict[str, dict] = {}
            if cache_file.exists():
                try:
                    merged = {row["code"]: row for row in
                              json.loads(cache_file.read_text(encoding="utf-8"))}
                except (ValueError, OSError):
                    merged = {}
            for snapshot in fetched.values():
                merged[snapshot.code] = {
                    "market": snapshot.market, "code": snapshot.code,
                    "name": snapshot.name, "price": snapshot.price,
                    "turnover": snapshot.turnover, "market_cap": snapshot.market_cap,
                    "pe": snapshot.pe, "pb": snapshot.pb,
                    "change_pct": snapshot.change_pct, "as_of": snapshot.as_of,
                    "turnover_rate": snapshot.turnover_rate,
                    "lot_size": snapshot.lot_size, "shares": snapshot.shares}
            cache_file.write_text(
                json.dumps(list(merged.values()), ensure_ascii=False), encoding="utf-8")
    return {code: snapshot for code, snapshot in out.items() if code in wanted}


# ---------------------------------------------------------------- 筛选


@dataclass
class OddsHit:
    code: str
    name: str
    inflection: str                 # 扭亏 / 减亏 / 预增
    notice: str
    report_period: str
    cap_at: float | None            # 公告日市值（元）
    runup: float | None
    short_lookback: bool
    predict_lower: float | None     # 预告净利润下限（元）
    annualized: float | None        # 年化预告利润（元）；减亏路径为 None
    needed_10x: float | None        # 10 倍需要的年化正常化净利润（元）
    gap_10x: float | None           # needed / annualized：利润还要涨几倍
    target_pe: float = DEFAULT_TARGET_PE
    failed: list[str] = field(default_factory=list)   # 空 = 通过；对照 A 按此分解

    @property
    def passes(self) -> bool:
        return not self.failed


def evaluate(rows: list[dict], snapshots: dict[str, Snapshot],
             prices_map: dict[str, list[dict]],
             cap_min: float = DEFAULT_CAP_MIN, cap_max: float = DEFAULT_CAP_MAX,
             runup_max: float = DEFAULT_RUNUP_MAX,
             target_pe: float = DEFAULT_TARGET_PE,
             multiple: float = 10.0) -> list[OddsHit]:
    """对主指标行逐条评估三个结构条件，通过与否都返回（failed 带原因）。

    基础率脚本需要对照组：不符合条件的行按 failed 分解是哪个条件刷掉的，
    所以不做提前过滤。发现层 CLI 只取 passes。
    """
    hits: list[OddsHit] = []
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        if not code:
            continue
        inflection = inflection_type(row)
        notice = str(row.get("NOTICE_DATE") or "")[:10]
        period = str(row.get("REPORT_DATE") or "")[:10]
        failed: list[str] = []
        if inflection == "其他":
            failed.append(f"拐点类型 {str(row.get('PREDICT_TYPE') or '空')} 不在 {INFLECTION_TYPES}")
        if not notice:
            failed.append("公告日缺失")

        prices = prices_map.get(code) or []
        snapshot = snapshots.get(code)
        shares = shares_from(snapshot) if snapshot else None
        notice_close = _close_before(prices, notice) if (shares and notice) else None
        cap_value = cap_at(shares, notice_close) if (shares and notice_close) else None
        runup_result = runup(prices, notice) if notice else RunupResult()

        if snapshot is None:
            failed.append("快照缺失，无法定总股本")
        elif cap_value is None:
            failed.append("公告日收盘缺失，无法定市值")
        else:
            if cap_value < cap_min:
                failed.append(f"公告日市值 {cap_value / 1e8:.0f} 亿 < {cap_min / 1e8:.0f} 亿")
            elif cap_value > cap_max:
                failed.append(f"公告日市值 {cap_value / 1e8:.0f} 亿 > {cap_max / 1e8:.0f} 亿")
        if runup_result.value is None:
            failed.append("回看涨幅缺失（行情不足或公告日无效）")
        elif runup_result.value > runup_max:
            failed.append(f"近一年已涨 {runup_result.value * 100:.0f}% > {runup_max * 100:.0f}%")

        predict_lower = _num(row.get("PREDICT_AMT_LOWER"))
        annualized = annualized_track(predict_lower, period)
        needed = what_needed(cap_value, multiple, target_pe) if cap_value else None
        gap = (needed / annualized) if (needed and annualized) else None
        hits.append(OddsHit(
            code=code, name=str(row.get("SECURITY_NAME_ABBR") or "").strip(),
            inflection=inflection, notice=notice, report_period=period,
            cap_at=cap_value, runup=runup_result.value,
            short_lookback=runup_result.short_lookback,
            predict_lower=predict_lower, annualized=annualized,
            needed_10x=needed, gap_10x=gap, target_pe=target_pe,
            failed=failed))
    return hits


def _close_before(prices: list[dict], notice: str) -> float | None:
    """公告日（含当日）之前最近一根收盘。"""
    close = None
    for bar in prices:
        if bar["day"] > notice:
            break
        close = bar.get("close")
    return close


def screen(rows: list[dict], snapshots: dict[str, Snapshot],
           prices_map: dict[str, list[dict]], **kwargs) -> list[OddsHit]:
    """evaluate 的通过集。"""
    return [hit for hit in evaluate(rows, snapshots, prices_map, **kwargs)
            if hit.passes]


# ---------------------------------------------------------------- 发现层入口


def price_window(notice: date) -> tuple[date, date]:
    """回看一年 + 前向缓冲的取价窗口：公告日 -400 天 → 公告日 + 20 天。

    前向收益由基础率脚本用更宽的窗口另取；这里只需要「公告日市值 + 回看涨幅」。
    """
    return notice - timedelta(days=400), notice + timedelta(days=20)


def prefilter_cap(snapshots: dict[str, Snapshot], codes: list[str],
                  cap_min: float, cap_max: float,
                  widen: float = 1.6) -> list[str]:
    """按**当前**市值做预筛（放宽 widen 倍带宽），减少要取历史行情的代码数。

    当前市值只是预筛：终判用公告日市值。放宽带是为了不把「公告日到现在
    涨了一截」的真候选误杀——代价是多取一些行情，值得。
    """
    lo = cap_min / widen
    hi = cap_max * widen
    out = []
    for code in codes:
        snapshot = snapshots.get(code)
        if snapshot and snapshot.market_cap and lo <= snapshot.market_cap <= hi:
            out.append(code)
    return out
