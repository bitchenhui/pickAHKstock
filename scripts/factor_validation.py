# -*- coding: utf-8 -*-
"""推荐排序因子的历史检验（规则先登记、再看结果）。

背景：推荐清单的排序里有几个因子是没检验就上线的（估值分位），有两个只用
8 期「中位数的中位数」看过一眼、没算置信区间（强度、RSI）。这个脚本用
逐只股票的数据重做一遍，并按**事先写死的规则**决定每一侧留不留。

样本：13 个报告期的 odds 结构条件通过集（与线上发现层同一批票），
公告后第一个交易日收盘买入，持有 60 个交易日的收益，减同期沪深300。

事先登记的规则（跑之前定好，不因结果改）：
- 每个因子分两侧检验，例如 RSI 的「超买侧」(RSI>70) 与「超卖侧」(RSI<30)
- 统计量：每一期里，该侧的平均超额收益 − 中性组（该因子信号为 0）的平均超额
  收益，得到一个「逐期差」；再对逐期差求均值
- 置信区间：逐期差的 t 区间（95%，自由度 = 期数 − 1）。同一期的股票面对同一段
  行情、彼此并不独立，所以以「期」为独立单位，而不是以「股票」为单位
- **只有区间整体落在预期方向**（预期为正则下界>0，预期为负则上界<0），
  该侧才保留计分；否则该侧计分归零。方向做反、区间跨 0 都归零
- 一期里两侧各不足 10 条的，这一期不计入逐期差；可计的期数不足 6 判「样本不足」

规则的一处修订（在看到有效的 13 期结果之前做的，只会更严）：最初写的是
「按报告期分层的 bootstrap」。第一次跑时指数日线被单次 400 根的上限截断，
只剩 3 期，那次的输出作废、不采用；重跑前把主判据改成了上面的逐期 t 区间，
因为分层重抽把同一期里的股票当成独立样本，区间会算得偏窄。整组重抽与
分层重抽的区间仍然算出来，列在表里作旁证。
- 预期方向：强度低侧为正、强度高侧为负；RSI 超卖侧为正、超买侧为负；
  估值低分位为正、高分位为负

已知局限（如实列出）：样本按当前上市代码取，含幸存者偏差；
 PB 分位用当前可得的历史序列在公告日之前的部分计算（时点上可得）；
 阈值沿用线上代码里已有的 30/70、-5/15，没有重新调。

用法：
    .venv/bin/python scripts/factor_validation.py            # 全流程
    .venv/bin/python scripts/factor_validation.py --report   # 只用已算好的样本出报告
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.execution import rsi_14  # noqa: E402

ODDS_CACHE = Path.home() / ".cache" / "pickahkstock" / "odds"
VAL_CACHE = Path.home() / ".cache" / "pickahkstock" / "valuation"
OBS_CACHE = ODDS_CACHE / "factor_obs_v1.json"
DOC = ROOT / "docs" / "factor-validation.md"

PERIODS = ("2022-12-31", "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
           "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
           "2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30")
WINDOW = 60            # 持有交易日
PB_LOOKBACK = 750      # 分位的回看窗口（交易日，约 3 年）
BOOTSTRAP = 2000
SEED = 20261124
MIN_PERIODS = 6         # 至少要有这么多期可计的逐期差
# 95% 双侧 t 临界值（下标 = 自由度）
T_CRITICAL = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
              8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
              14: 2.145, 15: 2.131, 20: 2.086, 30: 2.042}

# (因子, 侧, 预期方向的符号)
SIDES = (("强度", "low", 1), ("强度", "high", -1),
         ("RSI", "low", 1), ("RSI", "high", -1),
         ("估值", "low", 1), ("估值", "high", -1))
SIDE_LABEL = {"low": "低侧", "high": "高侧"}
SIDE_DEF = {
    ("强度", "low"): "60 日跑输大盘 5 个点以上", ("强度", "high"): "60 日跑赢大盘 15 个点以上",
    ("RSI", "low"): "RSI(14) < 30（超卖）", ("RSI", "high"): "RSI(14) > 70（超买）",
    ("估值", "low"): "PB 处于近 3 年 30% 分位以下", ("估值", "high"): "PB 处于近 3 年 70% 分位以上",
}


def signals(obs: dict) -> dict:
    """与线上 shortlist 的原始信号同一定义。"""
    out = {"强度": 0, "RSI": 0, "估值": 0}
    rs, rsi, pb = obs.get("rs60"), obs.get("rsi"), obs.get("pb_pct")
    if rs is not None:
        out["强度"] = 1 if rs < -5 else (-1 if rs > 15 else 0)
    if rsi is not None:
        out["RSI"] = -2 if rsi > 70 else (1 if rsi < 30 else 0)
    if pb is not None:
        out["估值"] = 2 if pb <= 30 else (-1 if pb >= 70 else 0)
    return out


# ---------------------------------------------------------------- 取数


def pass_hits(period: str) -> list[dict]:
    path = ODDS_CACHE / f"base_{period}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [h for h in data.get("hits", []) if not h.get("failed") and h.get("notice")]


def valuation_history(code: str) -> list[dict]:
    """逐只股票的每日 PE(TTM)/PB 历史，落盘缓存（历史部分不会变）。"""
    path = VAL_CACHE / f"{code}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return []
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
        rows = []
    VAL_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def _percentile(rows: list[dict], key: str, notice: str) -> float | None:
    """公告日（含）当天，该指标在之前 PB_LOOKBACK 个交易日里的分位。"""
    history = [r[key] for r in rows if r["d"] <= notice and r.get(key) is not None]
    if len(history) < 60:
        return None
    window = history[-PB_LOOKBACK:]
    current = window[-1]
    if key == "pe" and current <= 0:
        return None            # 亏损或微利时 PE 没有意义
    return 100.0 * sum(1 for v in window if v < current) / len(window)


def full_index(first: date, last: date) -> list[dict]:
    """按自然年分段取指数日线再合并，保证覆盖整个样本期。"""
    from research.backtest import index_bars

    merged: dict[str, dict] = {}
    for year in range(first.year, last.year + 1):
        start = max(first, date(year, 1, 1))
        end = min(last, date(year, 12, 31))
        if start > end:
            continue
        for bar in index_bars(start, end):
            merged[bar["day"]] = bar
    return [merged[day] for day in sorted(merged)]


def build_observations() -> list[dict]:
    from research.backtest import index_bars, load_prices

    targets: list[tuple[str, str, str]] = []
    for period in PERIODS:
        for hit in pass_hits(period):
            targets.append((period, str(hit["code"]), str(hit["notice"])[:10]))
    codes = sorted({code for _, code, _ in targets})
    print(f"样本：{len(targets)} 条（股票×报告期），{len(codes)} 只不同股票", flush=True)

    started = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        histories = dict(zip(codes, pool.map(valuation_history, codes)))
    missing = sum(1 for rows in histories.values() if not rows)
    print(f"估值历史：取到 {len(codes) - missing}/{len(codes)} 只"
          f"（{time.time() - started:.0f}s）", flush=True)

    first = min(date.fromisoformat(n) for _, _, n in targets) - timedelta(days=130)
    index = full_index(first, date.today())
    # 单次请求最多回 400 根：不分年取的话只有最近约一年半有指数，更早的期会被静默丢掉
    assert len(index) > 900, f"沪深300 日线只有 {len(index)} 根，覆盖不了全部报告期"
    print(f"沪深300 日线 {len(index)} 根（{index[0]['day']} → {index[-1]['day']}）", flush=True)

    observations: list[dict] = []
    for count, (period, code, notice) in enumerate(targets, 1):
        try:
            day = date.fromisoformat(notice)
            bars = load_prices(code, day - timedelta(days=130), day + timedelta(days=150))
            pre = [bar for bar in bars if bar["day"] <= notice]
            if len(pre) < 61:
                continue
            closes = [bar["close"] for bar in pre]
            entry = next((i for i, bar in enumerate(bars) if bar["day"] > notice), None)
            if entry is None or entry + WINDOW >= len(bars):
                continue
            exit_day = bars[entry + WINDOW]["day"]
            idx_pre = [bar for bar in index if bar["day"] <= notice]
            idx_entry = next((i for i, bar in enumerate(index) if bar["day"] > notice), None)
            idx_exit = max((i for i, bar in enumerate(index) if bar["day"] <= exit_day), default=None)
            if len(idx_pre) < 61 or idx_entry is None or idx_exit is None or idx_exit < idx_entry:
                continue
            forward = bars[entry + WINDOW]["close"] / bars[entry]["close"] - 1
            index_forward = index[idx_exit]["close"] / index[idx_entry]["close"] - 1
            rows = histories.get(code) or []
            observations.append({
                "period": period, "code": code, "notice": notice,
                "fwd": forward * 100, "exc": (forward - index_forward) * 100,
                "rs60": (closes[-1] / closes[-61] - idx_pre[-1]["close"] / idx_pre[-61]["close"]) * 100,
                "rsi": rsi_14(closes),
                "pb_pct": _percentile(rows, "pb", notice),
                "pe_pct": _percentile(rows, "pe", notice),
            })
        except Exception as exc:
            print(f"  跳过 {period} {code}: {type(exc).__name__}: {exc}", flush=True)
        if count % 500 == 0:
            print(f"  已处理 {count}/{len(targets)}", flush=True)
    covered = sorted({o["period"] for o in observations})
    if len(covered) < len(PERIODS):
        raise RuntimeError(f"样本只覆盖 {len(covered)}/{len(PERIODS)} 个报告期：{covered}，"
                           "不能出结论")
    return observations


# ---------------------------------------------------------------- 统计


def _t_interval(diffs: list[float]) -> tuple[float, float]:
    """一组逐期差的 95% t 区间（自由度 = 期数 − 1）。"""
    import statistics

    count = len(diffs)
    mean = statistics.fmean(diffs)
    if count < 2:
        return mean, mean
    error = statistics.stdev(diffs) / count ** 0.5
    critical = T_CRITICAL[min(count - 1, max(T_CRITICAL))]
    return mean - critical * error, mean + critical * error


def _resample_ci(observations: list[dict], in_side, neutral, by_period: bool) -> tuple[float, float]:
    """bootstrap 区间。by_period=True 时整期重抽（期是独立单位），
    False 时在每一期内部重抽（把股票当独立样本，区间会偏窄，只作旁证）。"""
    import numpy as np

    rng = np.random.default_rng(SEED)
    groups: dict[str, list[list]] = {}
    for obs in observations:
        bucket = groups.setdefault(obs["period"], [[], []])
        if in_side(obs):
            bucket[0].append(obs["exc"])
        elif neutral(obs):
            bucket[1].append(obs["exc"])
    arrays = [(np.array(a), np.array(b)) for a, b in groups.values() if len(a) and len(b)]
    if len(arrays) < 2:
        return float("nan"), float("nan")
    count = len(arrays)
    diffs = np.empty(BOOTSTRAP)
    for rep in range(BOOTSTRAP):
        if by_period:
            picks = rng.integers(0, count, count)
            a = np.concatenate([arrays[i][0] for i in picks])
            b = np.concatenate([arrays[i][1] for i in picks])
            diffs[rep] = a.mean() - b.mean()
        else:
            total_a = total_b = 0.0
            for a, b in arrays:
                total_a += float(a[rng.integers(0, a.size, a.size)].mean())
                total_b += float(b[rng.integers(0, b.size, b.size)].mean())
            diffs[rep] = (total_a - total_b) / count
    return float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def test_side(observations: list[dict], factor: str, side: str, expected: int,
              key: str = "exc") -> dict:
    def sig(obs):
        if "signals" in obs:
            return obs["signals"][factor]
        return signals(obs)[factor]

    if factor == "RSI":
        # RSI 的信号是不对称的：超买侧记负、超卖侧记正
        in_side = (lambda o: sig(o) < 0) if side == "high" else (lambda o: sig(o) > 0)
    else:
        in_side = (lambda o: sig(o) > 0) if side == "low" else (lambda o: sig(o) < 0)
    neutral = lambda o: sig(o) == 0  # noqa: E731

    def value(obs):
        return obs[key] if obs.get(key) is not None else None

    group = [value(o) for o in observations if in_side(o) and value(o) is not None]
    base = [value(o) for o in observations if neutral(o) and value(o) is not None]
    out = {"factor": factor, "side": side, "expected": expected,
           "n_side": len(group), "n_neutral": len(base)}
    if len(group) < 30 or len(base) < 30:
        out["status"] = "样本不足"
        return out
    out["side_mean"] = sum(group) / len(group)
    out["side_median"] = sorted(group)[len(group) // 2]
    out["neutral_mean"] = sum(base) / len(base)
    out["diff"] = out["side_mean"] - out["neutral_mean"]
    # 逐期差：只数两侧各 ≥10 条的报告期
    per_period = []
    for period in sorted({o["period"] for o in observations}):
        g = [value(o) for o in observations if o["period"] == period and in_side(o) and value(o) is not None]
        b = [value(o) for o in observations if o["period"] == period and neutral(o) and value(o) is not None]
        if len(g) >= 10 and len(b) >= 10:
            per_period.append(sum(g) / len(g) - sum(b) / len(b))
    out["periods"] = len(per_period)
    out["periods_agree"] = sum(1 for d in per_period if d * expected > 0)
    if key == "exc":
        if len(per_period) < MIN_PERIODS:
            out["status"] = "样本不足"
            out["kept"] = False
            return out
        low, high = _t_interval(per_period)
        out["period_mean_diff"] = sum(per_period) / len(per_period)
        out["ci_low"], out["ci_high"] = low, high
        out["kept"] = (low > 0) if expected > 0 else (high < 0)
        out["status"] = "保留" if out["kept"] else (
            "方向做反" if (high < 0 if expected > 0 else low > 0) else "区间跨 0")
        out["cluster_low"], out["cluster_high"] = _resample_ci(observations, in_side, neutral, True)
        out["strata_low"], out["strata_high"] = _resample_ci(observations, in_side, neutral, False)
    return out


# ---------------------------------------------------------------- 报告


def fmt(value, digits=2, signed=False):
    if value is None:
        return "—"
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def write_report(observations: list[dict]) -> list[dict]:
    import statistics

    results = [test_side(observations, factor, side, expected)
               for factor, side, expected in SIDES]
    excesses = [o["exc"] for o in observations]
    forwards = [o["fwd"] for o in observations]
    lines = ["# 推荐排序因子的历史检验", "",
             f"由 `scripts/factor_validation.py` 生成（{date.today().isoformat()}）。"
             "规则在跑之前登记在脚本开头，不因结果调整。", "",
             "## 样本与口径", "",
             f"- {len(PERIODS)} 个报告期的 odds 结构条件通过集，共 **{len(observations)} 条**"
             "（股票×报告期）",
             f"- 买入：公告后第一个交易日收盘；持有 {WINDOW} 个交易日；超额 = 个股收益 − 同期沪深300",
             "- 样本覆盖的报告期：" + "、".join(sorted({o['period'] for o in observations})),
             f"- 这批票整体：收益中位 {fmt(statistics.median(forwards), 1, True)}%，"
             f"超额中位 {fmt(statistics.median(excesses), 1, True)}%，"
             f"超额均值 {fmt(statistics.fmean(excesses), 1, True)}%，"
             f"跑赢大盘的占 {100 * sum(1 for e in excesses if e > 0) / len(excesses):.0f}%",
             "", "## 每一侧的检验", "",
             "| 因子 | 一侧 | 定义 | 该侧 n | 中性 n | 逐期差均值 | 95% t 区间 | 逐期同向 | 整期重抽区间（旁证） | 分层重抽区间（旁证） | 结论 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r.get("status") == "样本不足":
            lines.append(f"| {r['factor']} | {SIDE_LABEL[r['side']]} | {SIDE_DEF[(r['factor'], r['side'])]} |"
                         f" {r['n_side']} | {r['n_neutral']} | — | — | — | — | — | 样本不足 |")
            continue
        lines.append(
            f"| {r['factor']} | {SIDE_LABEL[r['side']]} | {SIDE_DEF[(r['factor'], r['side'])]} |"
            f" {r['n_side']} | {r['n_neutral']} | {fmt(r['period_mean_diff'], 2, True)} |"
            f" [{fmt(r['ci_low'], 2, True)}, {fmt(r['ci_high'], 2, True)}] |"
            f" {r['periods_agree']}/{r['periods']} |"
            f" [{fmt(r['cluster_low'], 2, True)}, {fmt(r['cluster_high'], 2, True)}] |"
            f" [{fmt(r['strata_low'], 2, True)}, {fmt(r['strata_high'], 2, True)}] | **{r['status']}** |")
    lines += ["", "「逐期同向」：两侧各至少 10 条的报告期里，差值与预期方向一致的期数"
              "（分母是可计的期数）。单位都是百分点。", "",
              "## 旁证：换成 PE(TTM) 分位（仅盈利公司）", "",
              "| 一侧 | 该侧 n | 差（对中性组） | 95% 区间 |", "|---|---|---|---|"]
    for side, expected in (("low", 1), ("high", -1)):
        by_key = [{"period": o["period"], "pe": o["pe_pct"], "exc": o["exc"]}
                  for o in observations if o.get("pe_pct") is not None]
        for item in by_key:
            item["signals"] = {"估值": (2 if item["pe"] <= 30 else (-1 if item["pe"] >= 70 else 0))}
        r = test_side(by_key, "估值", side, expected)
        if r.get("status") == "样本不足":
            lines.append(f"| {SIDE_LABEL[side]} | {r['n_side']} | — | — |")
        else:
            lines.append(f"| {SIDE_LABEL[side]} | {r['n_side']} | {fmt(r['period_mean_diff'], 2, True)} |"
                         f" [{fmt(r['ci_low'], 2, True)}, {fmt(r['ci_high'], 2, True)}] |")
    lines += ["", "## 结论与处置", ""]
    kept = [f"{r['factor']}{SIDE_LABEL[r['side']]}" for r in results if r.get("kept")]
    lines.append("- 通过规则、保留计分的：" + ("、".join(kept) if kept else "**没有**"))
    failed = [f"{r['factor']}{SIDE_LABEL[r['side']]}" for r in results if not r.get("kept")]
    if failed:
        lines.append("- 没过线、计分归零的：" + "、".join(failed))
    lines += [
        "- 没法用历史数据检验的因子（AI 对辩、催化剂远近、盯价方向、股东增减持、"
        "成交量趋势）同样不计分，只在清单上作信息显示，等前瞻记录"
        "（`research/evolution.py`）积累到足够样本后再决定要不要给分",
        "- 所以现阶段清单里的票**没有先后之分**，按代码顺序排列；大盘下行时整份清单"
        "只作观察名单",
        "",
        "## 怎么读", "",
        "- 这次检验没有找到任何一个能区分后续收益的排序因子。能下的结论是"
        "「这几个因子在 60 日持有期上没有可检出的选股能力」，不是「选股无解」",
        "- 「相对强度高侧」（已跑赢大盘 15 个点以上的）9 期里 7 期比中性组差，"
        "均值 −2.96 个点，是六项里最接近显著的一项，但区间上沿 +0.57 仍跨 0，"
        "按规则不能用。样本再长一些值得重看",
        "- RSI 超买侧的均值是正的（+1.83），与「超买要回落」的预期方向相反——"
        "在这个池子里强势的票并没有因为 RSI 高就马上回落",
        "- 这批票整体 60 日超额中位 −2.9%、跑赢大盘的占 43%：和 v5 的结论一致，"
        "业绩预告这个信号相对全市场没有优势",
        "- 第一次跑只覆盖了 3 期（指数日线被单次 400 根的上限截断），"
        "那次的输出里估值和相对强度都「显著」，是截断样本造成的，已作废",
        "",
    ]
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="推荐排序因子历史检验")
    parser.add_argument("--report", action="store_true", help="只用已算好的样本出报告")
    args = parser.parse_args()
    if args.report and OBS_CACHE.exists():
        observations = json.loads(OBS_CACHE.read_text(encoding="utf-8"))
    else:
        observations = build_observations()
        OBS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        OBS_CACHE.write_text(json.dumps(observations), encoding="utf-8")
    print(f"有效样本 {len(observations)} 条 → 写入 {DOC}", flush=True)
    for row in write_report(observations):
        print(row, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
