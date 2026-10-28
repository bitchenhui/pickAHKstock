import unittest
from datetime import date

from research.card import (AccountCheck, Card, Catalyst, CounterCase, Evidence,
                           FinancialTrack, LogEntry, Risk, Scenario, blocking,
                           quality_grade, validate)


def filled_financials() -> FinancialTrack:
    return FinancialTrack(
        path="已盈利",
        source="https://basic.10jqka.com.cn/api/stock/finance/002132_main.json",
        periods=["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31"],
        metrics={"营业总收入": {"2026-09-30": "40.46亿"}},
        recurring_judgment="收入仅 +5.84%，利润改善来自毛利率，不是放量",
        cyclical_judgment="强周期行业，价格红利，Q3 毛利率已回落至 3.96%")


def filled_card() -> Card:
    """一张通过全部阻塞校验的卡片。"""
    return Card(
        market="A", code="002132", name="恒星科技", status="持仓研究",
        view="有机硅价格回升带动毛利修复，尚未看到持续性证据",
        horizon="2026 年报披露前验证毛利是否连续两个季度为正",
        evidence=[Evidence(id="E1", claim="Q3 预告扣非净利润转正", kind="披露",
                           publisher="公司公告", title="前三季度业绩预告",
                           published="2026-10-15", url="http://static.cninfo.com.cn/x.PDF",
                           observed="2026-10-15", alternative="行业价格反弹而非份额提升",
                           status="已核实"),
              Evidence(id="E2", claim="单季毛利率 15.56% → 11.47% → 3.96%", kind="财务",
                       publisher="同花顺 F10", title="002132 财务主表", published="2026-10-28",
                       url="http://basic.10jqka.com.cn/api/stock/finance/002132_main.json",
                       observed="2026-10-28", alternative="二手加工数据，需与三季报核对",
                       status="已核实"),
              Evidence(id="E3", claim="Q3 单季扣非净利润 -4990.96 万元", kind="财务",
                       publisher="同花顺 F10", title="002132 单季度序列", published="2026-10-28",
                       url="http://basic.10jqka.com.cn/api/stock/finance/002132_main.json",
                       observed="2026-10-28", alternative="累计值相减得出，需与季报核对",
                       status="已核实")],
        scenarios=[Scenario(name="基准", narrative="价格回落但维持微利", drivers=["有机硅价"]),
                   Scenario(name="乐观", narrative="价格与份额同升", drivers=["价", "份额"]),
                   Scenario(name="失败", narrative="新增产能投放价格转跌", drivers=["产能"])],
        counter=CounterCase(strongest_failure="有机硅新增产能在四季度集中投放，"
                                             "现货价回落至现金成本以下，公司毛利再次转负",
                            exit_trigger="四季度扣非净利润再度为负，或现货价跌破现金成本",
                            where_wrong="最脆弱的一环：毛利改善全部来自有机硅价格，"
                                        "若价格回落而成本未降，盈利假设不成立"),
        invalidation=["2026 年报扣非净利润为负", "现货价连续两个月低于现金成本"],
        catalysts=[Catalyst(event="2026 年三季报正式披露", date="2026-10-30",
                            direction="双向", magnitude="单季扣非 -4991 万元")],
        risks=[Risk(category="价格", severity="高",
                    mechanism="有机硅价格回落使毛利率回到 3.96% 以下",
                    trigger="单季毛利率连续两个季度低于 5%")],
        provenance={"providers": ["巨潮", "同花顺 F10"], "snapshot": "2026-10-28"},
        account_checks=[AccountCheck(name="交易权限", result="通过", note="A 股主板"),
                        AccountCheck(name="整手金额", result="通过", note="100 股一手"),
                        AccountCheck(name="流动性", result="通过", note="日成交 1.2 亿")],
        gaps=["分产品毛利率未取得原始拆分", "客户集中度未核实"],
        financials=filled_financials(),
        log=[LogEntry(at="2026-10-20", event="建立卡片", to_status="观察"),
             LogEntry(at="2026-10-21", event="证据齐备", from_status="观察", to_status="持仓研究")],
        created="2026-10-20", updated="2026-10-21")


class CardTests(unittest.TestCase):
    def test_complete_card_passes(self):
        findings = validate(filled_card())
        self.assertEqual(blocking(findings), [])

    def test_level_is_derived_not_declared(self):
        card = filled_card()
        self.assertEqual(card.derived_level(), "证据充分")
        for evidence in card.evidence:
            evidence.status = "待核实"
        self.assertEqual(card.derived_level(), "证据不足")

    def test_vague_counter_is_blocked(self):
        card = filled_card()
        card.counter.strongest_failure = "存在风险"
        self.assertTrue(any("泛泛" in f.message for f in blocking(validate(card))))

    def test_evidence_without_source_cannot_be_verified(self):
        card = filled_card()
        card.evidence[0].url = ""
        messages = [f.message for f in blocking(validate(card))]
        self.assertTrue(any("出处链接" in m for m in messages))

    def test_incomplete_card_cannot_reach_research_stage(self):
        card = filled_card()
        card.invalidation = []
        self.assertTrue(any("invalidation" == f.field for f in blocking(validate(card))))

    def test_insufficient_evidence_blocks_position_stage(self):
        card = filled_card()
        for evidence in card.evidence:
            evidence.status = "待核实"
        self.assertTrue(any(f.field == "status" for f in blocking(validate(card))))

    def test_unverified_account_check_blocks_position_stage(self):
        card = filled_card()
        card.account_checks[1].result = "未核实"
        self.assertTrue(any(f.field == "account_checks" for f in blocking(validate(card))))

    def test_downgrade_requires_log_entry(self):
        card = filled_card()
        card.status = "降级"
        self.assertTrue(any(f.field == "log" for f in blocking(validate(card))))

    def test_invalidation_must_be_concrete(self):
        card = filled_card()
        card.invalidation = ["情况可能变差"]
        self.assertTrue(any("失效条件" in f.message for f in blocking(validate(card))))

    def test_missing_scenario_blocks(self):
        card = filled_card()
        card.scenarios = [s for s in card.scenarios if s.name != "失败"]
        self.assertTrue(any("失败" in f.message for f in blocking(validate(card))))

    # ---------- 财务路径（§30 / §34） ----------

    def test_unclassified_path_cannot_reach_full_evidence(self):
        card = filled_card()
        card.financials = FinancialTrack()
        self.assertEqual(card.derived_level(), "部分证据")
        self.assertTrue(any(f.field == "financials" for f in blocking(validate(card))))

    def test_profitable_path_requires_both_judgements(self):
        card = filled_card()
        card.financials.cyclical_judgment = ""
        messages = [f.message for f in blocking(validate(card))]
        self.assertTrue(any("景气反弹" in m for m in messages))
        card = filled_card()
        card.financials.recurring_judgment = ""
        messages = [f.message for f in blocking(validate(card))]
        self.assertTrue(any("可重复的业务" in m for m in messages))

    def test_unprofitable_path_requires_path_evidence(self):
        card = filled_card()
        card.financials.path = "未盈利"
        card.financials.recurring_judgment = ""
        card.financials.cyclical_judgment = ""
        card.financials.path_evidence = []
        messages = [f.message for f in blocking(validate(card))]
        self.assertTrue(any("现金续航" in m for m in messages))

    def test_fewer_than_four_periods_blocks_position_stage(self):
        card = filled_card()
        card.financials.periods = ["2026-09-30", "2026-06-30"]
        self.assertTrue(any(f.field == "financials" for f in blocking(validate(card))))

    def test_financials_without_source_blocks_position_stage(self):
        card = filled_card()
        card.financials.source = ""
        self.assertTrue(any(f.field == "financials" for f in blocking(validate(card))))

    # ---------- 催化剂 / 风险 / 我可能错在哪 / 质量门 ----------

    def test_position_stage_requires_catalyst_and_risk(self):
        card = filled_card()
        card.catalysts = []
        card.risks = []
        fields = {f.field for f in blocking(validate(card))}
        self.assertIn("catalysts", fields)
        self.assertIn("risks", fields)

    def test_risk_without_mechanism_is_blocked(self):
        card = filled_card()
        card.risks = [Risk(category="价格", severity="高")]
        self.assertTrue(any("作用机制" in f.message for f in blocking(validate(card))))

    def test_catalyst_without_date_is_warning_only(self):
        card = filled_card()
        card.catalysts = [Catalyst(event="行业景气回升", date="")]
        findings = validate(card)
        self.assertEqual(blocking(findings), [])
        self.assertTrue(any("没有日期" in f.message for f in findings))

    def test_where_wrong_required_for_position_stage(self):
        card = filled_card()
        card.counter.where_wrong = ""
        self.assertTrue(any("我可能错在哪" in f.message for f in blocking(validate(card))))

    def test_quality_grade_levels(self):
        card = filled_card()
        self.assertEqual(quality_grade(card)[0], "完整")
        card.evidence = [e for e in card.evidence if e.status != "已核实"]
        self.assertEqual(quality_grade(card)[0], "不可用")
        card = filled_card()
        card.counter.where_wrong = ""
        self.assertEqual(quality_grade(card)[0], "不完整")

    def test_stale_evidence_triggers_warning(self):
        card = filled_card()
        card.evidence[0].published = "2020-01-01"
        findings = validate(card)
        self.assertTrue(any("天" in f.message and f.severity == "警告" for f in findings))


if __name__ == "__main__":
    unittest.main()
