"""卡片检验集：把「卡片状态 vs 后续表现」变成可积累、可回看的东西。

这件事现在做不了——卡片才建了两天，没有"后续 60 日"。但它可以**从今天开始积累**：
每次复核时打一个快照（当时的卡片状态、当时的价格、卡片的哈希），到 60 个交易日后
就能自动算出「标记为观察/降级的那些，后来表现如何」。

这是唯一能验证**研究流程**（而不只是事件信号）的方法：事件信号的回测里没有
「读原文 → 单季拆解 → 写反证」这些步骤。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "cards" / "_snapshots"


@dataclass
class Snapshot:
    market: str
    code: str
    name: str
    status: str              # 快照当时的卡片状态
    card_hash: str           # 卡片内容哈希，用来发现「事后改过卡片」
    day: str                 # 快照日期
    price: float | None = None
    evidence_count: int = 0
    level: str = ""          # 当时的结论等级
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoreRow:
    market: str
    code: str
    name: str
    status: str
    day: str
    return_60: float | None = None
    return_20: float | None = None
    excess_60: float | None = None      # 相对同期沪深300
    excess_20: float | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("return_60", "return_20", "excess_60", "excess_20"):
            data[key] = None if data[key] is None else round(data[key] * 100, 2)
        return data


def card_hash(card) -> str:
    payload = json.dumps(card.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def record(cards, day: date, price_lookup=None) -> list[Snapshot]:
    """给每张卡片打一个快照。price_lookup(code, market) -> price | None。"""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshots: list[Snapshot] = []
    for card in cards:
        price = None
        if price_lookup:
            try:
                price = price_lookup(card.code, card.market)
            except Exception:
                price = None
        snapshot = Snapshot(
            market=card.market, code=card.code, name=card.name, status=card.status,
            card_hash=card_hash(card), day=day.isoformat(), price=price,
            evidence_count=len(card.evidence), level=card.derived_level())
        path = SNAPSHOT_DIR / f"{day.isoformat()}_{card.market}_{card.code}.json"
        path.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        snapshots.append(snapshot)
    return snapshots


def load_snapshots() -> list[Snapshot]:
    if not SNAPSHOT_DIR.exists():
        return []
    out: list[Snapshot] = []
    for path in sorted(SNAPSHOT_DIR.glob("*.json")):
        try:
            out.append(Snapshot(**json.loads(path.read_text(encoding="utf-8"))))
        except (ValueError, OSError, TypeError):
            continue
    return out


def score(snapshots: list[Snapshot], price_series: dict[str, list[dict]],
          holds=(20, 60), index_series: list[dict] | None = None) -> dict:
    """按快照时的状态分组，回看后续表现。

    price_series: {code: [{day, close, ...}]}，用前复权日线。
    index_series: 同期沪深300日线。**给了才算得清「跑赢还是跑输」**——
    没有基准，卡片后续跌 3% 这件事本身没有意义（同期市场可能跌 8%）。

    这条不是形式：v4 的回测里就是因为对照组选错，结论直接读反过
    （见 docs/backtest-v4.md）。卡片检验集也会犯同样的错，所以基线是必需品。
    """
    from .backtest import forward_return

    rows: list[ScoreRow] = []
    for snapshot in snapshots:
        series = price_series.get(snapshot.code) or []
        row = ScoreRow(market=snapshot.market, code=snapshot.code, name=snapshot.name,
                       status=snapshot.status, day=snapshot.day)
        for hold in holds:
            value = forward_return(series, snapshot.day, hold)
            setattr(row, f"return_{hold}", value)
            if value is not None and index_series:
                benchmark = forward_return(index_series, snapshot.day, hold)
                setattr(row, f"excess_{hold}",
                        None if benchmark is None else value - benchmark)
        rows.append(row)
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row.status, []).append(row)
    summary = {}
    for status, items in groups.items():
        entry = {"n": len(items)}
        for hold in holds:
            values = [getattr(item, f"return_{hold}") for item in items
                      if getattr(item, f"return_{hold}") is not None]
            values = [v for v in values if v is not None]
            entry[f"scored_{hold}"] = len(values)
            entry[f"median_{hold}"] = (None if not values
                                       else round(sorted(values)[len(values) // 2] * 100, 2))
            entry[f"win_rate_{hold}"] = (None if not values
                                         else round(sum(1 for v in values if v > 0)
                                                    / len(values) * 100, 1))
            # 相对基准：同期沪深300。样本极小（卡片检验集还没长成），
            # 所以给的是「跑赢几只」这种可数的读数，不是统计结论。
            excess = [getattr(item, f"excess_{hold}", None) for item in items]
            excess = [v for v in excess if v is not None]
            entry[f"excess_{hold}"] = (None if not excess
                                       else round(sorted(excess)[len(excess) // 2] * 100, 2))
            entry[f"beat_market_{hold}"] = (None if not excess else
                                            sum(1 for v in excess if v > 0))
        summary[status] = entry
    return {"rows": [r.to_dict() for r in rows], "summary": summary}
