"""研究看板：回答「单股研究的对象谁来定」。

研究对象有三个来源，优先级不同：

1. **到期必看**（规则定，最高优先级）：卡片里的催化剂日期、证据的下一验证节点
   已经到期或临近。不看它等于主动放弃对风险的处置权。
2. **观察池待复核**（日期定）：已立项的卡片，按下一个节点排。
3. **新候选**（机器提议、人裁决）：发现层输出的新对象。可投资性合格优先，
   但立项与否由人决定——人单独定会陷入确认偏误，机器单独定会漏掉你关心的。

所以答案不是「谁来定」，而是**规则定优先级、人定是否立项、卡片定证据门槛**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .card import Card

# 看板分组，按这个顺序输出
BUCKETS = ("已降级或淘汰", "到期复核", "临近复核", "观察池", "待立项")


@dataclass
class BoardItem:
    market: str
    code: str
    name: str
    bucket: str
    reason: str
    due: str = ""
    status: str = ""
    overdue: bool = False
    card_status: str = ""

    def to_dict(self) -> dict:
        return {"market": self.market, "code": self.code, "name": self.name,
                "bucket": self.bucket, "reason": self.reason, "due": self.due,
                "status": self.status, "overdue": self.overdue,
                "card_status": self.card_status}


def _parse(value: str) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    return None


def _upcoming_dates(card: Card) -> list[tuple[date, str]]:
    """卡片上所有可用于排期的日期：催化剂日期与证据的下一验证节点。"""
    dates: list[tuple[date, str]] = []
    for catalyst in card.catalysts:
        parsed = _parse(catalyst.date)
        if parsed:
            dates.append((parsed, f"催化剂：{catalyst.event[:28]}"))
    for evidence in card.evidence:
        parsed = _parse(evidence.next_check)
        if parsed:
            dates.append((parsed, f"{evidence.id} 下一验证：{evidence.next_check}"))
    dates.sort()
    return dates


def build_board(cards: list[Card], today: date | None = None,
                horizon_days: int = 30) -> list[BoardItem]:
    """把已有卡片排成待办：先到期，再临近，最后是还在等的观察池。"""
    today = today or date.today()
    horizon = today + timedelta(days=horizon_days)
    items: list[BoardItem] = []
    for card in cards:
        upcoming = _upcoming_dates(card)
        if card.status in ("降级", "淘汰"):
            items.append(BoardItem(
                market=card.market, code=card.code, name=card.name,
                bucket="已降级或淘汰",
                reason=_state_change_reason(card),
                due=upcoming[0][0].isoformat() if upcoming else "",
                status=card.status, card_status=card.status))
            continue
        if not upcoming:
            if card.status == "观察":
                items.append(BoardItem(
                    market=card.market, code=card.code, name=card.name,
                    bucket="观察池",
                    reason="没有排期日期：先定下一个验证节点，否则会一直挂着",
                    status=card.status, card_status=card.status))
            continue
        due, reason = upcoming[0]
        if due <= today:
            items.append(BoardItem(
                market=card.market, code=card.code, name=card.name,
                bucket="到期复核", reason=f"{reason}（已过期 {(today - due).days} 天）",
                due=due.isoformat(), overdue=True, status=card.status, card_status=card.status))
        elif due <= horizon:
            items.append(BoardItem(
                market=card.market, code=card.code, name=card.name,
                bucket="临近复核", reason=f"{reason}（{(due - today).days} 天后）",
                due=due.isoformat(), status=card.status, card_status=card.status))
        else:
            # 下次复核在窗口之外：仍要出现在看板上，否则卡片会静默消失
            items.append(BoardItem(
                market=card.market, code=card.code, name=card.name,
                bucket="观察池", reason=f"{reason}（{(due - today).days} 天后）",
                due=due.isoformat(), status=card.status, card_status=card.status))
    items.sort(key=lambda i: (BUCKETS.index(i.bucket), i.due or "9999"))
    return items


def _state_change_reason(card: Card) -> str:
    """取状态**真正发生变化**的那一条日志，而不是最后一条。

    后续补证据、补拆分的日志 to_status 与当前状态相同，from_status 也相同，
    不能当作降级理由——只有 from_status != to_status 的那条才是状态变更。
    """
    for entry in reversed(card.log):
        if entry.to_status == card.status and entry.from_status != entry.to_status:
            return entry.event
    for entry in reversed(card.log):
        if entry.to_status == card.status:
            return entry.event
    return f"状态为{card.status}，需处置"


# 硬挡：建不了仓或买不进——这些候选进了清单也是噪音
BLOCKING_MARKERS = ("超过单只上限", "成交额不足", "买不进")


def _blocked(candidate) -> bool:
    """这条候选是不是「真该挡掉」。

    挡的只有一种：当前账户规模建不了仓（整手金额超过单只仓位上限）。
    以前是把**任何**提示都当挡掉的理由——「主指标是扣非口径」「幅度属极端值」
    这类软提示也会让候选整条消失，结果好候选在看板上看都看不到。
    """
    if getattr(candidate, "unaffordable", False):
        return True
    notes = getattr(candidate, "notes", [])
    return any(any(marker in note for marker in BLOCKING_MARKERS) for note in notes)


def new_candidates(a_candidates=None, hk_candidates=None,
                   include_blocked: bool = False) -> list[BoardItem]:
    """把发现层的新候选转成看板条目（待立项）。

    建不了仓的默认不进清单——除非显式要求，否则只是噪音。软提示不挡条目，
    只作为原因附在条目上。
    """
    items: list[BoardItem] = []
    for candidate in (a_candidates or []) + (hk_candidates or []):
        if not include_blocked and _blocked(candidate):
            continue
        reason = getattr(candidate, "headline", "") or getattr(candidate, "event", "")
        cautions = [note for note in getattr(candidate, "notes", [])
                    if not any(marker in note for marker in BLOCKING_MARKERS)]
        if cautions:
            reason = f"{reason}｜{cautions[0]}"
        industry = getattr(candidate, "industry", "")
        resonance = getattr(candidate, "resonance", "")
        if industry and resonance:
            # 同业共振是发现层已经算出来的结论，带进看板省得重算
            reason = f"{reason}｜{industry}·{resonance}"
        items.append(BoardItem(
            market=getattr(candidate, "market", "A"),
            code=candidate.code, name=candidate.name,
            bucket="待立项",
            reason=reason,
            due="", status="未立项", card_status=""))
    return items
