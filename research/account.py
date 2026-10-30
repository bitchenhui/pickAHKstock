"""L3 账户层：把单股研究结论与账户约束合并，产出账户级动作。

边界（对应策略稿 §26、§44、§45）：
- 这里输出的是**账户动作**（加/不加/减/空仓），不是股票名单。
- 单笔风险与仓位上限是占位参数，策略稿尚未确认；默认值来自原型的交互默认值，
  必须由使用者自己确认后再用。
- 止损参考线不保证成交：跳空、停牌、跌停、A 股 T+1 都可能让参考价失效。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .card import Card

ACCOUNT_PATH = Path(__file__).resolve().parent.parent / "account.json"
EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "account.example.json"


class AccountError(ValueError):
    pass


@dataclass
class Position:
    market: str
    code: str
    name: str = ""
    shares: int = 0
    cost: float = 0.0
    currency: str = "CNY"
    sector: str = ""
    card: str = ""          # 对应的研究卡片（市场_代码）


@dataclass
class Account:
    capital_cny: float = 0.0
    capital_hkd: float = 0.0
    risk_per_trade: float = 0.005       # 单笔参考风险占本币资金比例
    max_position_pct: float = 0.05     # 单只上限（§51 检验后从 20% 降到 5%）
    max_positions: int = 20            # 最多同时持有几只
    max_total_pct: float = 0.60        # 总仓位上限
    max_sector_pct: float = 0.35       # 单一行业上限
    cash_floor_pct: float = 0.20       # 现金下限
    max_drawdown_pct: float = 0.20     # 账户峰值回撤设计边界
    peak_equity: float = 0.0           # 历史峰值权益（两币合计口径）
    fx_cny_per_hkd: float = 0.92       # 港币折人民币，仅用于合并计算权益
    hk_lots: dict = field(default_factory=dict)   # 港股每手股数
    positions: list[Position] = field(default_factory=list)
    notes: str = ""
    confirmed_by_user: bool = False    # 使用者是否确认过这些参数

    # ---------- 序列化 ----------
    def to_dict(self) -> dict:
        data = asdict(self)
        data["positions"] = [asdict(p) if not isinstance(p, dict) else p for p in self.positions]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        data = dict(data)
        positions = data.pop("positions", None) or []
        account = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        account.positions = [Position(**p) if isinstance(p, dict) else p for p in positions]
        return account

    def save(self, path: Path = ACCOUNT_PATH) -> Path:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path = ACCOUNT_PATH) -> "Account":
        if not path.exists():
            raise AccountError(
                f"没有账户配置 {path}。先复制 {EXAMPLE_PATH.name} 为 account.json 并填入你的真实参数，"
                "再运行。参数由使用者确认，工具不替你猜持仓、费率与资金。")
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, TypeError) as exc:
            raise AccountError(f"账户配置无法解析：{exc}") from exc

    # ---------- 衍生量 ----------
    def capital(self, market: str) -> float:
        return self.capital_cny if market == "A" else self.capital_hkd

    def currency(self, market: str) -> str:
        return "CNY" if market == "A" else "HKD"

    def market_position_value(self, market: str) -> float:
        """单一市场的持仓市值（本币口径）。两地资金不自动换汇，因此分池计算。"""
        return sum(p.shares * p.cost for p in self.positions
                   if p.market == market and p.currency == self.currency(market))

    def cash(self, market: str) -> float:
        """该市场的可用现金 = 该市场总权益 − 该市场持仓市值。

        capital_cny / capital_hkd 含义是「该市场账户总权益（含持仓）」。
        """
        return self.capital(market) - self.market_position_value(market)

    def position_value(self) -> float:
        """持仓市值，折算成人民币合计（港币按 fx 折算）。"""
        total = 0.0
        for position in self.positions:
            value = position.shares * position.cost
            total += value / self.fx_cny_per_hkd if position.currency == "HKD" else value
        return total

    def equity(self) -> float:
        """账户总权益（人民币口径）。持仓按成本计价，未按市价浮动。"""
        return self.capital_cny + self.capital_hkd * self.fx_cny_per_hkd

    def drawdown(self) -> float:
        """当前相对历史峰值的回撤（正数表示回撤幅度）。"""
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, 1 - self.equity() / self.peak_equity)

    def drawdown_state(self) -> str:
        """回撤只触发风险状态，不改写研究判断（策略稿 §46）。"""
        drawdown = self.drawdown()
        if drawdown >= self.max_drawdown_pct:
            return "暂停新增"
        if drawdown >= self.max_drawdown_pct * 0.75:
            return "收缩"
        if drawdown >= self.max_drawdown_pct * 0.5:
            return "警戒"
        return "正常"

    def sector_exposure(self, sector: str, market: str = "A") -> float:
        if not sector:
            return 0.0
        total = sum(p.shares * p.cost for p in self.positions
                    if p.sector == sector and p.market == market)
        return total / self.capital(market) if self.capital(market) else 0.0

    def holding_in(self, market: str, code: str) -> Position | None:
        for position in self.positions:
            if position.market == market and position.code == code:
                return position
        return None


@dataclass
class Plan:
    code: str
    name: str
    action: str            # 可加仓 / 不加仓 / 仅观察 / 减仓 / 空仓
    lots: int = 0
    shares: int = 0
    notional: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def lot_size(card: Card, account: Account) -> int | None:
    """每手股数。

    A 股固定 100 股；港股优先用卡片市场数据段里从行情取到的每手股数
    （腾讯行情 [60] 字段，与东财 TRADE_UNIT 逐只互验一致），其次才是
    账户配置里的手填值——两者都没有时按 0 处理。
    """
    if card.market == "A":
        return 100
    from_context = (card.market_context or {}).get("lot_size")
    if isinstance(from_context, int) and from_context > 0:
        return from_context
    lot = account.hk_lots.get(card.code)
    return lot if isinstance(lot, int) and lot > 0 else None


def price_of(card: Card) -> float | None:
    value = (card.market_context or {}).get("price")
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def stop_of(card: Card) -> float | None:
    value = (card.market_context or {}).get("stop_reference")
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def plan_for(card: Card, account: Account) -> Plan:
    """对一张卡片给出账户级动作。判断链：研究状态 → 账户状态 → 仓位约束。"""
    plan = Plan(code=card.code, name=card.name, action="仅观察")
    price = price_of(card)
    lot = lot_size(card, account)
    capital = account.capital(card.market)

    # 0) 已持仓的卡片状态变化：研究结论被推翻时，账户要给出减仓动作
    holding = account.holding_in(card.market, card.code)
    if holding and card.status in ("降级", "淘汰"):
        plan.action = "清仓" if card.status == "淘汰" else "减仓"
        plan.reasons.append(
            f"已持有 {holding.shares} 股，但卡片状态已变为「{card.status}」，"
            f"按研究结论应减仓或清仓（{card.log[-1].event if card.log else '状态变更'}）")
        plan.shares = holding.shares
        plan.notional = round(holding.shares * (price or holding.cost), 2)
        return plan

    # 1) 研究状态：只有进入「持仓研究」才谈得上加仓
    if card.status != "持仓研究":
        plan.reasons.append(f"卡片状态为「{card.status}」，未通过研究关")
        return plan
    if any(c.result != "通过" for c in card.account_checks):
        plan.reasons.append("仍有未通过的账户检查")
        return plan

    # 2) 账户风险状态：回撤只影响新增，不改写判断
    state = account.drawdown_state()
    if state in ("收缩", "暂停新增"):
        plan.reasons.append(f"账户回撤状态为「{state}」，不新增仓位")
        return plan

    # 3) 资金与价格
    if price is None:
        plan.reasons.append("卡片缺少最新价，无法估算仓位")
        return plan
    if lot is None:
        plan.reasons.append("缺少港股每手股数，未核实前不建仓")
        return plan
    if capital <= 0:
        plan.reasons.append("该市场资金为 0")
        return plan

    # 4) 仓位约束：单笔风险、单只上限、现金下限、行业暴露
    stop = stop_of(card)
    if stop is not None and stop < price:
        risk_lots = int((capital * account.risk_per_trade) // ((price - stop) * lot))
    else:
        risk_lots = int((capital * account.max_position_pct) // (price * lot))
    alloc_lots = int((capital * account.max_position_pct) // (price * lot))
    available_cash = account.cash(card.market) - capital * account.cash_floor_pct
    cash_lots = int(max(0.0, available_cash) // (price * lot))
    lots = max(0, min(risk_lots, alloc_lots, cash_lots))

    sector = (card.market_context or {}).get("sector") or ""
    if sector:
        sector_room = int((capital * (account.max_sector_pct - account.sector_exposure(sector, card.market)))
                           // (price * lot))
        lots = min(lots, max(0, sector_room))
    else:
        plan.reasons.append("卡片未标注行业分类，单一行业上限未生效")

    if lots <= 0:
        plan.reasons.append("受风险预算、单只上限、现金下限或行业暴露约束，本轮不加仓")
        return plan

    holding = account.holding_in(card.market, card.code)
    if holding:
        plan.reasons.append(f"已持有 {holding.shares} 股，本次为加仓上限")
    plan.action = "可加仓"
    plan.lots = lots
    plan.shares = lots * lot
    plan.notional = round(plan.shares * price, 2)
    return plan


def portfolio_plan(cards: list[Card], account: Account) -> list[Plan]:
    return [plan_for(card, account) for card in cards]


def account_summary(cards: list[Card], account: Account) -> dict:
    """账户层汇总：现金占比、行业暴露与卡片状态分布。

    状态分布是研究纪律的体现：卡片还在「观察」时，现金占比高是正常结果，
    不是"没找到机会"。
    """
    plans = portfolio_plan(cards, account)
    states: dict[str, int] = {}
    for card in cards:
        states[card.status] = states.get(card.status, 0) + 1
    planned = sum(p.notional for p in plans if p.action == "可加仓")
    return {
        "equity": account.equity(),
        "position_value": account.position_value(),
        "cash_cny": account.cash("A"),
        "cash_hkd": account.cash("HK"),
        "drawdown": account.drawdown(),
        "drawdown_state": account.drawdown_state(),
        "planned_additions": planned,
        "card_states": states,
        "actions": {p.action for p in plans},
    }
