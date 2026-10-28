"""研究卡片：单股研究的载体，字段来自策略稿 v1 的研究漏斗与卡片要求。

纪律（对本模块最重要的一条）：
- 结论等级由证据推导，不能自己声明。证据不足就只能是「观察」。
- 缺字段要显式记为「未核实」并进 gaps，绝不猜补。
- 反证必须指向具体失败机制，「有风险」这种泛泛说法会被判为不合格。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

CARD_DIR = Path(__file__).resolve().parent.parent / "cards"

STATUSES = ("观察", "持仓研究", "降级", "淘汰", "空仓")
LEVELS = ("证据充分", "部分证据", "证据不足")
EVIDENCE_STATUSES = ("已核实", "待核实", "已证伪")
CHECK_RESULTS = ("通过", "未核实", "不通过")

# 反证的最低要求：指向具体机制而不是免责声明。
VAGUE_COUNTER = re.compile(r"(有风险|存在风险|市场波动|不确定|需谨慎|注意风险|谨慎|可能会|有可能)")
# 失效条件必须锚定到具体数值或时间。
CONCRETE = re.compile(r"[0-9]|[0-9]{4}-[0-9]{2}-[0-9]{2}|季|年|公告|披露|停牌")


class CardError(ValueError):
    pass


@dataclass
class Evidence:
    id: str
    claim: str                       # 可证伪的一句话
    kind: str = "披露"              # 披露 / 财务 / 行情 / 第三方
    publisher: str = ""
    title: str = ""
    published: str = ""             # YYYY-MM-DD，原始出处日期
    url: str = ""
    observed: str = ""              # 我最早注意到它的日期
    alternative: str = ""           # 可替代解释
    next_check: str = ""            # 下一验证节点
    status: str = "待核实"
    note: str = ""

    def is_verified(self) -> bool:
        return self.status == "已核实" and bool(self.published and self.url)


@dataclass
class Scenario:
    name: str
    narrative: str = ""
    drivers: list[str] = field(default_factory=list)

    def is_filled(self) -> bool:
        return bool(self.narrative.strip()) and bool(self.drivers)


@dataclass
class CounterCase:
    strongest_failure: str = ""
    exit_trigger: str = ""


@dataclass
class AccountCheck:
    name: str
    result: str = "未核实"
    note: str = ""
    source: str = ""


@dataclass
class LogEntry:
    at: str
    event: str
    from_status: str = ""
    to_status: str = ""


@dataclass
class Card:
    market: str
    code: str
    name: str
    status: str = "观察"
    view: str = ""                          # 当前判断，一句话
    horizon: str = ""                       # 以什么事件/里程碑为主线
    evidence: list[Evidence] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    counter: CounterCase = field(default_factory=CounterCase)
    invalidation: list[str] = field(default_factory=list)
    market_context: dict = field(default_factory=dict)
    account_checks: list[AccountCheck] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    log: list[LogEntry] = field(default_factory=list)
    created: str = ""
    updated: str = ""

    # ---------- 结论等级：由证据推导 ----------
    def derived_level(self) -> str:
        verified = [e for e in self.evidence if e.is_verified()]
        if not verified:
            return "证据不足"
        if (self.scenarios and all(s.is_filled() for s in self.scenarios)
                and len(self.scenarios) >= 3
                and self.counter.strongest_failure.strip()
                and any(CONCRETE.search(t) for t in self.invalidation)
                and self.gaps is not None):
            return "证据充分"
        return "部分证据"

    def level(self) -> str:
        return self.derived_level()

    # ---------- 序列化 ----------
    def to_dict(self) -> dict:
        data = asdict(self)
        data["derived_level"] = self.derived_level()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Card":
        data = dict(data)
        data.pop("derived_level", None)
        data["evidence"] = [Evidence(**e) for e in data.get("evidence", [])]
        data["scenarios"] = [Scenario(**s) for s in data.get("scenarios", [])]
        data["counter"] = CounterCase(**data.get("counter", {}))
        data["account_checks"] = [AccountCheck(**c) for c in data.get("account_checks", [])]
        data["log"] = [LogEntry(**l) for l in data.get("log", [])]
        return cls(**data)

    @property
    def path(self) -> Path:
        return CARD_DIR / f"{self.market}_{self.code}.json"

    def save(self) -> Path:
        CARD_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                             encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, market: str, code: str) -> "Card":
        path = CARD_DIR / f"{market}_{code}.json"
        if not path.exists():
            raise CardError(f"没有这张卡片：{path}")
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class Finding:
    severity: str   # 阻塞 / 警告
    field: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.field}：{self.message}"


def validate(card: Card) -> list[Finding]:
    """返回问题清单。没有阻塞项 = 卡片可以进入下一状态。"""
    findings: list[Finding] = []

    def block(field_name: str, message: str) -> None:
        findings.append(Finding("阻塞", field_name, message))

    def warn(field_name: str, message: str) -> None:
        findings.append(Finding("警告", field_name, message))

    if card.status not in STATUSES:
        block("status", f"状态 '{card.status}' 不在允许集合 {STATUSES}")
    if not card.view.strip():
        block("view", "缺少当前判断")
    if not card.horizon.strip():
        warn("horizon", "未写明研究主线（以什么事件或里程碑为轴）")

    # 证据
    seen_ids = set()
    for evidence in card.evidence:
        if evidence.id in seen_ids:
            block(f"evidence[{evidence.id}]", "证据编号重复")
        seen_ids.add(evidence.id)
        if not evidence.claim.strip():
            block(f"evidence[{evidence.id}]", "主张为空")
        if evidence.status not in EVIDENCE_STATUSES:
            block(f"evidence[{evidence.id}]", f"证据状态 '{evidence.status}' 非法")
        if evidence.status == "已核实":
            if not evidence.published:
                block(f"evidence[{evidence.id}]", "标记已核实但没有出处日期")
            if not evidence.url:
                block(f"evidence[{evidence.id}]", "标记已核实但没有出处链接")
        if evidence.status == "待核实" and not evidence.alternative.strip():
            warn(f"evidence[{evidence.id}]", "待核实证据缺少可替代解释")
    if not card.evidence:
        block("evidence", "没有任何证据：状态只能是「观察」")

    # 情景
    names = {s.name for s in card.scenarios}
    for required in ("基准", "乐观", "失败"):
        if required not in names:
            block("scenarios", f"缺少「{required}」情景")
    for scenario in card.scenarios:
        if not scenario.is_filled():
            block(f"scenarios[{scenario.name}]", "情景缺少叙述或驱动因素")

    # 反证
    counter = card.counter.strongest_failure.strip()
    if not counter:
        block("counter", "缺少最强失败路径")
    elif VAGUE_COUNTER.search(counter) and len(counter) < 40:
        block("counter", "反证是泛泛表述，必须指向具体失败机制（谁、什么环节、什么数据会暴露）")
    if not card.counter.exit_trigger.strip():
        block("counter", "缺少退出触发条件：什么事实出现就放弃这只")

    # 失效条件
    if not card.invalidation:
        block("invalidation", "缺少失效条件")
    elif not any(CONCRETE.search(t) for t in card.invalidation):
        block("invalidation", "失效条件没有锚定到具体数值或时间")

    # 账户准入
    for check in card.account_checks:
        if check.result not in CHECK_RESULTS:
            block(f"account_checks[{check.name}]", f"检查结果 '{check.result}' 非法")
    if card.status == "持仓研究":
        if not card.account_checks:
            block("account_checks", "进入持仓研究必须有账户检查记录")
        elif any(c.result != "通过" for c in card.account_checks):
            block("account_checks", "仍有未通过或未核实的账户检查，不能进入持仓研究")
        if card.derived_level() != "证据充分":
            block("status", f"证据等级为「{card.derived_level()}」，不足以进入持仓研究")
    if card.derived_level() == "证据不足" and card.status != "观察":
        block("status", "证据不足时状态只能是「观察」")
    if card.status in ("降级", "淘汰") and not any(
            entry.to_status == card.status for entry in card.log):
        block("log", f"状态为「{card.status}」但没有对应的状态变更记录")

    # 数据缺口必须显式存在（空列表是合法答案，但字段不能缺失）
    if card.gaps is None:
        block("gaps", "数据缺口字段缺失")
    return findings


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "阻塞"]
