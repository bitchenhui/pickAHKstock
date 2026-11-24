# -*- coding: utf-8 -*-
"""AI 迭代进化：预测 → 跟踪 → 归因 → 调权 → 下一轮预测。

这是简化版的在线学习：
1. **预测日志**：每天清单生成时，记录每只票当时各因子的**原始信号**
2. **结果跟踪**：满 60 个交易日后，对照实际收益与指数超额
3. **因子归因**：哪个因子的正信号组真的比负信号组/中性组好
4. **权重调整**：方向对的加权，方向反的减权，看不出的减半
5. **循环**：shortlist 直接读这里的权重，所以调整会反映到下一轮排序

权重的唯一来源是 DEFAULT_WEIGHTS（经历史检验定下的初始值）叠加
evolution_state.json 里的历次调整。shortlist 用 得分 = 权重 × 原始信号，
所以调权不需要改排序代码。

诚实边界：
- 首轮归因要等 60 个交易日（最早 2027-02）
- 归因是最简单的分组均值比较，不做回归，保证可解释
- 每轮每个因子最多调 0.5，且每组至少 15 条样本才动，防止小样本乱调
- 每一代的调整原因都记在 history 里，可回退
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "evolution_state.json"
JOURNAL_DIR = Path(__file__).resolve().parent.parent / "cards" / "_journal"

# 因子的初始权重。**现在全部是 0**：2026-11-24 用 13 个报告期、3101 条样本做了
# 检验（scripts/factor_validation.py → docs/factor-validation.md），能检验的三个
# 因子（强度、RSI、估值）六个检验项的置信区间全部跨 0，没有一项通过事先登记
# 的规则；其余因子没法用历史数据检验。所以清单目前没有先后之分，各因子只作
# 信息显示。因子要拿回权重只有两条路：重跑检验并通过，或前瞻记录
# （compute_outcomes → attribute）显示它的方向对。
# 这张表不要凭手感改。
DEFAULT_WEIGHTS: dict[str, float] = {
    "共振": 0.0,
    "强度": 0.0,
    "RSI": 0.0,
    "估值": 0.0,
    "股东": 0.0,
    "量能": 0.0,
    "催化": 0.0,
    "AI": 0.0,
    "盯价": 0.0,
    "流动性": 0.0,
}

# 每个因子权重的依据（页面与 CLI 都会显示，避免把未检验的因子当成结论）
WEIGHT_EVIDENCE: dict[str, str] = {
    "共振": "8 期检验：公司特有与行业共振的后续收益无差异",
    "强度": "13 期检验未通过：跑输侧逐期差 −0.40 个点 [−3.01, +2.20]；"
            "跑赢侧 −2.96 [−6.49, +0.57]，区间都跨 0",
    "RSI": "13 期检验未通过：超卖侧 −0.40 [−3.59, +2.79]；超买侧 +1.83 [−2.53, +6.20]，"
           "区间都跨 0",
    "估值": "13 期检验未通过：PB 低分位侧 +0.46 [−1.15, +2.07]；"
            "高分位侧 −0.63 [−4.98, +3.71]，区间都跨 0",
    "股东": "未做历史检验（需要逐只抓历史公告），等前瞻记录",
    "量能": "未做历史检验，等前瞻记录",
    "催化": "没法用历史数据检验（催化剂日期来自卡片判断），等前瞻记录",
    "AI": "没法用历史数据检验（模型可能知道后来的走势，回测不可信），等前瞻记录",
    "盯价": "未做历史检验，等前瞻记录",
    "流动性": "不计分；进出困难的卡直接不进清单",
}

MAX_ADJUSTMENT = 0.5     # 每轮每个因子最多调多少
MIN_GROUP = 15           # 每组至少多少条到期结果才动权重
EFFECT_SIZE = 1.0        # 组间差超过多少个百分点才算「有方向」（超额收益 pct）
WEIGHT_CAP = 3.0


@dataclass
class Prediction:
    """一条预测记录：某天清单里的某只票。"""
    day: str                 # 记录日期
    code: str                # 股票代码
    name: str                # 名称
    market: str
    rank: int                # 当时的排名
    total_score: float       # 当时的总分
    factors: dict            # {因子名: 原始信号}，不含权重
    price_at_call: float     # 记录时的价格（非交易日可能是上一个交易日的）
    status: str              # 当时的卡片状态

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Outcome:
    """一条到期结果：买入价到持有满窗口那天的收盘价。"""
    prediction: Prediction
    return_pct: float
    excess_pct: float | None


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(state, dict) and isinstance(state.get("weights", {}), dict):
                return state
        except (ValueError, OSError):
            pass
    return {"generation": 0, "weights": {}, "history": []}


def current_weights() -> tuple[dict[str, float], int]:
    """排序该用的权重 = 初始权重 + 历次进化调整。返回 (权重, 代数)。"""
    state = _load_state()
    weights = dict(DEFAULT_WEIGHTS)
    weights.update({k: v for k, v in state["weights"].items() if k in weights})
    return weights, state.get("generation", 0)


class Evolution:
    """进化引擎：预测日志 → 到期结果 → 因子归因 → 调权。"""

    def __init__(self):
        self.state = _load_state()

    @property
    def weights(self) -> dict:
        weights, _ = current_weights()
        return weights

    @property
    def generation(self) -> int:
        return self.state.get("generation", 0)

    def save_state(self):
        STATE_FILE.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---------- 预测日志 ----------

    def log_predictions(self, entries: list[Prediction]):
        """把当天的清单写入日志（同一天同一只票只留最新一条）。"""
        if not entries:
            return
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        path = JOURNAL_DIR / f"{entries[0].day}.json"
        existing: dict[str, dict] = {}
        if path.exists():
            try:
                existing = {row["code"]: row
                            for row in json.loads(path.read_text(encoding="utf-8"))}
            except (ValueError, OSError, KeyError, TypeError):
                existing = {}
        for entry in entries:
            existing[entry.code] = entry.to_dict()
        path.write_text(
            json.dumps(list(existing.values()), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    def load_journal(self) -> list[Prediction]:
        rows: list[Prediction] = []
        if not JOURNAL_DIR.exists():
            return rows
        fields = Prediction.__dataclass_fields__
        for path in sorted(JOURNAL_DIR.glob("*.json")):
            try:
                for row in json.loads(path.read_text(encoding="utf-8")):
                    rows.append(Prediction(**{k: v for k, v in row.items() if k in fields}))
            except (ValueError, OSError, TypeError):
                continue
        return rows

    # ---------- 结果跟踪 ----------

    def compute_outcomes(self, predictions: list[Prediction],
                         window: int = 60) -> list[Outcome]:
        """满 window 个交易日的预测才算：买入后第 window 根日线的收盘价对买入价。

        买入价 = 记录日之后第一根日线的收盘价。没满 window 根的不计入，
        所以一条预测的结果是固定的，不会随着时间推移而变。
        """
        from .backtest import hk_index_bars, index_bars, load_prices

        if not predictions:
            return []
        today = date.today()
        earliest = min(date.fromisoformat(p.day) for p in predictions) - timedelta(days=10)
        index_cache: dict[str, list] = {}
        outcomes: list[Outcome] = []
        for pred in predictions:
            if pred.market not in index_cache:
                index_cache[pred.market] = (
                    hk_index_bars if pred.market == "HK" else index_bars)(earliest, today)
            index = index_cache[pred.market]
            pred_day = date.fromisoformat(pred.day)
            try:
                bars = load_prices(pred.code, pred_day - timedelta(days=10), today)
            except Exception:
                continue
            entry = next((i for i, bar in enumerate(bars) if bar["day"] > pred.day), None)
            if entry is None or entry + window >= len(bars):
                continue                        # 还没持有满 window 个交易日
            exit_day = bars[entry + window]["day"]
            entry_price = bars[entry]["close"]
            if entry_price <= 0:
                continue
            ret = bars[entry + window]["close"] / entry_price - 1
            idx_entry = next((i for i, bar in enumerate(index) if bar["day"] > pred.day), None)
            idx_exit = max((i for i, bar in enumerate(index) if bar["day"] <= exit_day), default=None)
            excess = None
            if idx_entry is not None and idx_exit is not None and idx_exit >= idx_entry:
                base = index[idx_entry]["close"]
                if base > 0:
                    excess = (index[idx_exit]["close"] / base - 1)
            outcomes.append(Outcome(
                prediction=pred, return_pct=round(ret * 100, 2),
                excess_pct=round((ret - excess) * 100, 2) if excess is not None else None))
        return outcomes

    # ---------- 因子归因 ----------

    def attribute(self, outcomes: list[Outcome]) -> dict:
        """每个因子：正信号组、负信号组、中性组，谁的后续超额收益更高。

        有正有负时比「正 − 负」；只有一侧时比「那一侧 − 中性」。
        组间差 ≥ +1 个百分点算方向对，≤ −1 算方向反，中间算看不出。
        """
        if len(outcomes) < MIN_GROUP:
            return {"error": f"到期结果只有 {len(outcomes)} 条，"
                             f"至少 {MIN_GROUP} 条才做归因"}
        names: set[str] = set()
        for outcome in outcomes:
            names.update(outcome.prediction.factors.keys())
        attribution: dict = {}
        for name in sorted(names):
            positive, negative, neutral = [], [], []
            for outcome in outcomes:
                excess = outcome.excess_pct
                if excess is None:
                    continue
                signal = outcome.prediction.factors.get(name, 0)
                (positive if signal > 0 else negative if signal < 0 else neutral).append(excess)
            if len(positive) >= MIN_GROUP and len(negative) >= MIN_GROUP:
                first, second, label = positive, negative, "正信号 − 负信号"
            elif len(positive) >= MIN_GROUP and len(neutral) >= MIN_GROUP:
                first, second, label = positive, neutral, "正信号 − 中性"
            elif len(negative) >= MIN_GROUP and len(neutral) >= MIN_GROUP:
                first, second, label = neutral, negative, "中性 − 负信号"
            else:
                attribution[name] = {"status": "样本少", "n_pos": len(positive),
                                     "n_neg": len(negative), "n_neu": len(neutral)}
                continue
            diff = sum(first) / len(first) - sum(second) / len(second)
            attribution[name] = {
                "status": "方向对" if diff >= EFFECT_SIZE else
                          ("方向反" if diff <= -EFFECT_SIZE else "看不出"),
                "compare": label, "n_pos": len(positive), "n_neg": len(negative),
                "n_neu": len(neutral), "diff": round(diff, 2),
            }
        return attribution

    # ---------- 权重调整 ----------

    def adjust_weights(self, attribution: dict) -> dict:
        """按归因结果调权：方向对 +0.5，方向反 −0.5，看不出 ×0.5。样本少不动。"""
        if "error" in attribution:
            return {"adjusted": False, "error": attribution["error"]}
        weights = dict(self.weights)
        changes = {}
        for name, result in attribution.items():
            if result.get("status") == "样本少" or name not in weights:
                continue
            old = weights[name]
            if result["status"] == "方向对":
                new = min(old + MAX_ADJUSTMENT, WEIGHT_CAP)
            elif result["status"] == "方向反":
                new = max(old - MAX_ADJUSTMENT, -WEIGHT_CAP)
            else:
                new = round(old * 0.5, 2)
            if abs(new - old) > 1e-9:
                weights[name] = new
                changes[name] = {"from": old, "to": new, "why": result["status"],
                                 "diff": result["diff"]}
        if not changes:
            return {"adjusted": False, "changes": {}, "generation": self.generation}
        self.state["generation"] = self.generation + 1
        self.state["weights"] = weights
        self.state.setdefault("history", []).append({
            "date": date.today().isoformat(),
            "generation": self.state["generation"],
            "outcomes": sum(max(r.get("n_pos", 0) + r.get("n_neg", 0) + r.get("n_neu", 0), 0)
                            for r in attribution.values()) // max(1, len(attribution)),
            "changes": changes,
        })
        self.save_state()
        return {"adjusted": True, "changes": changes, "generation": self.generation}
