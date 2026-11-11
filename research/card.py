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
    kind: str = "披露"              # 披露 / 财务 / 行情 / 第三方 / 研报
    publisher: str = ""
    title: str = ""
    published: str = ""             # YYYY-MM-DD，原始出处日期
    url: str = ""
    observed: str = ""              # 我最早注意到它的日期
    value: str = ""                 # 关键数值，便于日后核对口径
    alternative: str = ""           # 可替代解释
    next_check: str = ""            # 下一验证节点
    status: str = "待核实"
    note: str = ""

    def is_verified(self) -> bool:
        return self.status == "已核实" and bool(self.published and self.url)

    def age_days(self, today: date | None = None) -> int | None:
        try:
            published = date.fromisoformat(self.published)
        except (TypeError, ValueError):
            return None
        return ((today or date.today()) - published).days


@dataclass
class Catalyst:
    """催化剂：事件、预计日期、方向与量级。没有日期的催化剂不算数。"""

    event: str
    date: str = ""
    direction: str = ""             # 正向 / 负向 / 双向
    magnitude: str = ""             # 量级或影响口径
    status: str = "待验证"          # 待验证 / 已发生 / 已证伪


@dataclass
class Risk:
    """风险：类型、严重度、作用机制、监测触发条件。

    「作用机制」是必填——只有类型和严重度而没有机制的，等于没写。
    """

    category: str
    severity: str = "中"            # 高 / 中 / 低
    mechanism: str = ""
    trigger: str = ""               # 什么现象出现说明风险在实现
    status: str = "开放"


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
    where_wrong: str = ""           # 「我可能错在哪」：最脆弱的一环


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
class FinancialTrack:
    """财务轨迹：策略稿 §30（已盈利）与 §34（未盈利）分别要求的证据。

    工具负责把数据取来，判断必须由人写；没有判断就不算完成这一关。
    """

    path: str = "未分类"            # 已盈利 / 未盈利 / 未分类
    source: str = ""               # 数据出处（接口地址）
    periods: list[str] = field(default_factory=list)
    quarter_periods: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)       # {科目: {报告期: 值}} 累计口径
    quarter_metrics: dict = field(default_factory=dict)  # {科目: {报告期: 值}} 单季口径
    recurring_judgment: str = ""    # 收入/毛利/现金流的变化是否由可重复的业务带来
    cyclical_judgment: str = ""     # 周期性行业：景气反弹还是竞争力提升（非周期行业可写"不适用"并说明理由）
    working_capital_note: str = ""  # 应收、库存、资本开支是否与增长相匹配
    path_evidence: list[str] = field(default_factory=list)  # 未盈利路径：现金续航、里程碑、融资情景
    notes: list[str] = field(default_factory=list)


@dataclass
class Judgment:
    """卡房里的一条可核对判断。

    散文里的数字没法回测。把关键判断结构化记下来（说了什么、数值多少、
    哪个期间、依据什么、什么时候核对的），以后才能回答两个问题：
    - 我当时说的数字和后来披露的对不对
    - 标记为「降级」的那些，后来是不是真的跑输
    """

    id: str
    text: str                      # 一句话结论
    metric: str = ""               # 指标名，便于核对
    value: float | None = None     # 数值
    unit: str = ""                 # 单位
    period: str = ""               # 期间，如 2026Q3 / 2026H1
    basis: str = ""                # 依据（哪份文件或哪个接口）
    checked: str = ""              # 核对日期
    confidence: str = "中"         # 高 / 中 / 低

    def to_dict(self) -> dict:
        return asdict(self)


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
    financials: FinancialTrack = field(default_factory=FinancialTrack)
    catalysts: list[Catalyst] = field(default_factory=list)
    judgments: list[Judgment] = field(default_factory=list)
    expectations: dict = field(default_factory=dict)   # 分析师预期（第三方证据）
    capital: dict = field(default_factory=dict)         # 资本轨迹（资本开支/经营现金流/在建工程）
    peers: dict = field(default_factory=dict)           # 同业对照（估值分位与远期 PE）
    industry_context: dict = field(default_factory=dict)  # 同业共振（行业 beta vs 公司特有）
    commodities: dict = field(default_factory=dict)  # 盯的价格（行业共振指向的商品现货，代理关系注明）
    execution: dict = field(default_factory=dict)   # 执行层：流动性门槛 + 相对强度（deep-dive 写入）
    scans: dict = field(default_factory=dict)   # 风险公告/治理（港股为经营回顾）扫描留痕，deep-dive 写入
    risks: list[Risk] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)   # 数据来源与快照哈希
    account_checks: list[AccountCheck] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    log: list[LogEntry] = field(default_factory=list)
    created: str = ""
    updated: str = ""

    # ---------- 结论等级：由证据推导 ----------
    def financial_path_complete(self) -> bool:
        """§30/§34 的路径证据是否写完。工具只负责取数，判断必须由人写。"""
        track = self.financials
        if track.path not in ("已盈利", "未盈利") or not track.source:
            return False
        if len(track.periods) < 4:
            return False
        if track.path == "已盈利":
            return bool(track.recurring_judgment.strip() and track.cyclical_judgment.strip())
        return bool(track.path_evidence)

    def derived_level(self) -> str:
        verified = [e for e in self.evidence if e.is_verified()]
        if not verified:
            return "证据不足"
        if (self.scenarios and all(s.is_filled() for s in self.scenarios)
                and len(self.scenarios) >= 3
                and self.counter.strongest_failure.strip()
                and any(CONCRETE.search(t) for t in self.invalidation)
                and self.gaps is not None
                and self.financial_path_complete()):
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
        data["financials"] = FinancialTrack(**data.get("financials", {}))
        data["catalysts"] = [Catalyst(**c) for c in data.get("catalysts", [])]
        data["judgments"] = [Judgment(**j) for j in data.get("judgments", [])]
        data["risks"] = [Risk(**r) for r in data.get("risks", [])]
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
        _snapshot_if_status_changed(self)
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


def _snapshot_if_status_changed(card: "Card") -> None:
    """卡片状态变了就自动记一个快照。

    卡片检验集（「卡片状态 vs 后续表现」）要能自己长大，靠人记得跑
    `card-snapshot` 是长不起来的。状态变更是研究结论更新的时刻，也正是
    最该被检验的时刻——所以在这里自动记。

    只记状态变化的那些（同一个状态反复补证据不算新信息）；不联网取价，
    价格由 card-snapshot 命令补。
    """
    try:
        from .tracking import latest_snapshot, record_snapshot

        previous = latest_snapshot(card.market, card.code)
        if previous and previous.status == card.status:
            return
        record_snapshot(card, day=date.today().isoformat())
    except Exception:
        pass  # 快照失败不该影响卡片保存


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

    # 财务路径（§30 已盈利 / §34 未盈利）
    track = card.financials
    if track.path == "未分类":
        warn("financials", "尚未把该公司归入「已盈利」或「未盈利」路径，两条路径要求的证据不同")
    if track.path == "已盈利":
        if not track.recurring_judgment.strip():
            block("financials", "已盈利路径必须判断：收入、毛利或现金流的变化是否由可重复的业务带来")
        if not track.cyclical_judgment.strip():
            block("financials", "已盈利路径必须区分：景气反弹还是长期竞争力提升")
    if track.path == "未盈利" and not track.path_evidence:
        block("financials", "未盈利路径必须记录现金续航、里程碑与融资情景")

    # 催化剂与风险
    for catalyst in card.catalysts:
        if not catalyst.event.strip():
            block("catalysts", "催化剂缺少事件描述")
        elif not catalyst.date.strip():
            warn("catalysts", f"催化剂「{catalyst.event[:20]}」没有日期：没有日期的催化剂没法复核")
    for risk in card.risks:
        if not risk.category.strip():
            block("risks", "风险缺少类别")
        if not risk.mechanism.strip():
            block(f"risks[{risk.category}]", "风险缺少作用机制：只有类别和严重度等于没写")
        if risk.severity not in ("高", "中", "低"):
            block(f"risks[{risk.category}]", f"严重度 '{risk.severity}' 非法")
    if card.status == "持仓研究":
        if not card.catalysts:
            block("catalysts", "进入持仓研究必须至少有一个带日期的催化剂或复核事件")
        if not card.risks:
            block("risks", "进入持仓研究必须列出风险及其作用机制")
        if not card.counter.where_wrong.strip():
            block("counter", "必须写明「我可能错在哪」：最脆弱的那一环")

    # 数据出处
    if not card.provenance:
        warn("provenance", "未记录数据来源清单：日后无法判断结论依赖哪些接口与快照")

    # 证据新鲜度：太旧的「已核实」证据需要重新核对
    for evidence in card.evidence:
        age = evidence.age_days()
        if age is not None and age > 365 and evidence.status == "已核实":
            warn(f"evidence[{evidence.id}]", f"证据已过去 {age} 天，结论前需重新核对")

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
        if not card.financial_path_complete():
            block("financials", "财务路径证据未完成：需要归类路径、至少 4 个报告期的数据，并写明路径判断")
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


def quality_grade(card: Card) -> tuple[str, list[str]]:
    """质量门：给卡片一个整体等级，避免「每项都填了但都填得很浅」。

    借鉴同类项目「必采清单 + 质量门」的做法：
    - 完整：零阻塞，且证据新、情景与反证都站得住
    - 可用：零阻塞，但有陈旧或单薄之处
    - 不完整：还有阻塞项
    - 不可用：连「观察」都不成立
    """
    findings = validate(card)
    blockers = blocking(findings)
    reasons = [f"{f.field}：{f.message}" for f in findings]
    if not [e for e in card.evidence if e.is_verified()]:
        return "不可用", ["没有任何已核实的证据"]
    if blockers:
        return "不完整", reasons
    fresh = [e for e in card.evidence if e.is_verified() and (e.age_days() or 0) <= 180]
    thin = []
    if len(fresh) < 2:
        thin.append("近半年内可核实的证据少于 2 条")
    if len(card.evidence) < 3:
        thin.append("证据少于 3 条")
    if not card.counter.where_wrong.strip():
        thin.append("未写明「我可能错在哪」")
    if not card.risks:
        thin.append("未列出风险机制")
    if thin:
        return "可用", thin
    return "完整", []
