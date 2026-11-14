# -*- coding: utf-8 -*-
"""AI 自我迭代进化：预测 → 跟踪 → 归因 → 调权 → 更好的预测。

这就是一个简化版的在线学习系统：
1. **预测日志**：每天推荐清单生成时，记录当时每只票的因子分数
2. **结果跟踪**：60 个交易日后，对照实际收益
3. **因子归因**：哪些因子分数高的票真的表现好？哪些因子是噪音？
4. **权重调整**：有效的因子加权，无效的降权，反向的翻号
5. **循环**：调整后的权重用于下一轮推荐

诚实边界：
- 需要至少 60 个交易日的数据才能做第一轮归因（现在刚开始积累）
- 简单线性归因（不用神经网络），保证可解释
- 每轮调整有幅度上限（防过拟合）
- 所有调整留痕，用户可回退

权重存储在 evolution_state.json，格式：
{
  "generation": 1,
  "weights": {"共振": 0, "强度": 1, "RSI": -2, "催化": 1.5, "AI": 2, ...},
  "history": [{...归因记录...}]
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "evolution_state.json"
JOURNAL_DIR = Path(__file__).resolve().parent.parent / "cards" / "_journal"

# 初始权重（与 shortlist.py 的硬编码一致，进化会从这里出发）
DEFAULT_WEIGHTS = {
    "共振": 0,        # factor_check：无差异
    "强度": 1,         # factor_check：跑输的反而好（方向已翻）
    "RSI": -2,         # rsi_check：超买扣分
    "催化": 2,         # 逻辑合理（未单独检验）
    "AI": 2,           # 前瞻验证中
    "盯价": 1,         # 未检验
    "流动性": 1,       # 逻辑合理
}

MAX_ADJUSTMENT = 0.5     # 每轮权重调整上限（防过拟合）
MIN_SAMPLE = 10          # 归因需要的最少样本数


@dataclass
class Prediction:
    """一条预测记录：某天推荐清单里的某只票。"""
    day: str                 # 推荐日期
    code: str                # 股票代码
    name: str                # 名称
    market: str
    rank: int                # 当时的排名
    total_score: float       # 当时的总分
    factors: dict            # {因子名: 分}（不含权重）
    price_at_call: float     # 推荐当日价格
    status: str              # 当时的卡片状态

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class Outcome:
    """一条结果记录：预测之后实际发生了什么。"""
    prediction: Prediction
    days_later: int
    return_pct: float | None      # 绝对收益
    excess_pct: float | None      # 相对指数超额


class Evolution:
    """进化引擎：管理预测日志 → 归因 → 调权。"""

    def __init__(self):
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        return {"generation": 0, "weights": dict(DEFAULT_WEIGHTS), "history": []}

    def save_state(self):
        STATE_FILE.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    @property
    def weights(self) -> dict:
        return self.state["weights"]

    @property
    def generation(self) -> int:
        return self.state["generation"]

    # ---------- 预测日志 ----------

    def log_predictions(self, entries: list[Prediction]):
        """把当天的推荐清单写入日志。"""
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        day = entries[0].day if entries else date.today().isoformat()
        path = JOURNAL_DIR / f"{day}.json"
        existing = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                existing = []
        # 按 code 去重（同一天只留最新）
        by_code = {e["code"]: e for e in existing}
        for entry in entries:
            by_code[entry.code] = entry.to_dict()
        path.write_text(
            json.dumps(list(by_code.values()), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    def load_journal(self) -> list[Prediction]:
        """读全部预测日志。"""
        all_preds = []
        if not JOURNAL_DIR.exists():
            return all_preds
        for path in sorted(JOURNAL_DIR.glob("*.json")):
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
                for row in rows:
                    all_preds.append(Prediction(**{
                        k: v for k, v in row.items()
                        if k in Prediction.__dataclass_fields__}))
            except (ValueError, OSError, TypeError):
                continue
        return all_preds

    # ---------- 结果跟踪 ----------

    def compute_outcomes(self, predictions: list[Prediction],
                         window: int = 60) -> list[Outcome]:
        """计算每条预测的前瞻收益。"""
        from .backtest import load_prices, index_bars, hk_index_bars

        outcomes = []
        for pred in predictions:
            pred_date = date.fromisoformat(pred.day)
            expiry = pred_date + timedelta(days=int(window * 1.6))
            if date.today() < expiry:
                continue    # 还没到期，跳过
            bars = load_prices(pred.code, pred_date - timedelta(days=5), date.today())
            if not bars:
                continue
            entry_bars = [b for b in bars if b["day"] > pred.day]
            if not entry_bars:
                continue
            entry = entry_bars[0]["close"]
            exit_bars = bars[-window:] if len(bars) >= window else bars
            if not exit_bars or entry <= 0:
                continue
            exit_price = exit_bars[-1]["close"]
            ret = exit_price / entry - 1
            # 指数基准
            index = (hk_index_bars if pred.market == "HK" else index_bars)(
                pred_date - timedelta(days=5), date.today())
            idx_entry = [b for b in index if b["day"] > pred.day]
            idx_exit = index[-window:] if len(index) >= window else index
            if idx_entry and idx_exit and idx_entry[0]["close"] > 0:
                idx_ret = idx_exit[-1]["close"] / idx_entry[0]["close"] - 1
                excess = ret - idx_ret
            else:
                excess = None
            outcomes.append(Outcome(
                prediction=pred, days_later=len(exit_bars),
                return_pct=round(ret * 100, 2),
                excess_pct=round(excess * 100, 2) if excess is not None else None))
        return outcomes

    # ---------- 因子归因 ----------

    def attribute(self, outcomes: list[Outcome]) -> dict:
        """哪些因子的分数与实际收益正相关？哪些是噪音？

        方法：对每个因子，比较「该因子高分」vs「该因子低分/零分」的
        平均超额收益差。正相关 → 该因子有效，负相关 → 方向反了。
        """
        if len(outcomes) < MIN_SAMPLE:
            return {"error": f"样本不足（{len(outcomes)}/{MIN_SAMPLE}），"
                             f"需等检验集积累到至少 {MIN_SAMPLE} 条到期预测"}

        factor_names = set()
        for o in outcomes:
            factor_names.update(o.prediction.factors.keys())
        factor_names.discard("流动性")  # 流动性是门槛不是信号

        attribution = {}
        for name in sorted(factor_names):
            high, low = [], []
            for o in outcomes:
                score = o.prediction.factors.get(name, 0)
                excess = o.excess_pct if o.excess_pct is not None else o.return_pct
                if excess is None:
                    continue
                if score > 0:
                    high.append(excess)
                else:
                    low.append(excess)
            if len(high) < 3 or len(low) < 3:
                attribution[name] = {"status": "样本少", "high_n": len(high), "low_n": len(low)}
                continue
            high_avg = sum(high) / len(high)
            low_avg = sum(low) / len(low)
            diff = high_avg - low_avg
            attribution[name] = {
                "status": "有效" if diff > 0.5 else ("反向" if diff < -0.5 else "噪音"),
                "high_n": len(high), "low_n": len(low),
                "high_avg": round(high_avg, 2), "low_avg": round(low_avg, 2),
                "diff": round(diff, 2),
                "recommendation": "加权" if diff > 0.5 else
                                  ("翻转" if diff < -0.5 else "降权"),
            }
        return attribution

    # ---------- 权重调整 ----------

    def adjust_weights(self, attribution: dict) -> dict:
        """根据归因结果调整权重（有幅度上限）。"""
        if "error" in attribution:
            return {"error": attribution["error"], "adjusted": False}
        old_weights = dict(self.weights)
        adjustments = {}
        for name, result in attribution.items():
            if result.get("status") == "样本少":
                continue
            rec = result.get("recommendation", "")
            current = self.weights.get(name, 0)
            if rec == "加权":
                new_w = current + MAX_ADJUSTMENT
            elif rec == "翻转":
                new_w = -current  # 方向翻转
            elif rec == "降权":
                new_w = current * 0.5  # 减半
            else:
                new_w = current
            # 限制幅度
            new_w = max(-5, min(5, new_w))
            if new_w != current:
                adjustments[name] = {"from": current, "to": round(new_w, 2),
                                     "reason": rec,
                                     "diff": result.get("diff")}
                self.weights[name] = round(new_w, 2)
        if adjustments:
            self.state["generation"] += 1
            self.state["history"].append({
                "date": date.today().isoformat(),
                "generation": self.state["generation"],
                "sample_size": sum(r.get("high_n", 0) + r.get("low_n", 0)
                                   for r in attribution.values()),
                "adjustments": adjustments,
            })
            self.save_state()
        return {"adjusted": bool(adjustments), "changes": adjustments,
                "generation": self.state["generation"]}
