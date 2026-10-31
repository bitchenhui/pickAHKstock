import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research.account import (Account, AccountError, Position, plan_for, portfolio_plan)
from research.card import Card, CounterCase, Evidence, FinancialTrack, Scenario

DAY = "2026-10-28"


def research_card(code="002132", name="恒星科技", status="持仓研究", price=3.51,
                  stop=3.24, checks=(("交易权限", "通过"), ("整手金额", "通过")),
                  sector="") -> Card:
    return Card(
        market="A", code=code, name=name, status=status,
        view="测试用卡片", horizon="测试",
        evidence=[Evidence(id="E1", claim="c", kind="披露", publisher="p", title="t",
                           published="2026-10-15", url="http://x/y.PDF", status="已核实")],
        scenarios=[Scenario(name=n, narrative="n", drivers=["d"]) for n in ("基准", "乐观", "失败")],
        counter=CounterCase(strongest_failure="具体失败机制：价格回落导致毛利转负，阈值低于 3.2 元"),
        invalidation=["2026 年报扣非为负"],
        market_context={"price": price, "stop_reference": stop, "sector": sector},
        account_checks=[type("C", (), {"name": n, "result": r, "note": "", "source": ""})()
                        for n, r in checks],
        financials=FinancialTrack(path="已盈利", source="http://x", periods=["a", "b", "c", "d"],
                                  recurring_judgment="j", cyclical_judgment="k"),
        created=DAY, updated=DAY)


class AccountTests(unittest.TestCase):
    def account(self, **kwargs) -> Account:
        base = dict(capital_cny=30000, capital_hkd=30000, peak_equity=60000)
        base.update(kwargs)
        return Account(**base)

    def test_missing_config_refuses_to_guess(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "account.json"
            with self.assertRaises(AccountError) as ctx:
                Account.load(missing)
            self.assertIn("account.example.json", str(ctx.exception))

    def test_saved_config_roundtrip(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "account.json"
            account = self.account(positions=[Position("A", "002132", "恒星科技", 1000, 3.51)])
            account.save(path)
            loaded = Account.load(path)
            self.assertEqual(loaded.positions[0].code, "002132")
            self.assertEqual(loaded.positions[0].shares, 1000)

    def test_drawdown_state_does_not_rewrite_judgement(self):
        account = self.account(peak_equity=100000)
        self.assertGreater(account.drawdown(), 0.3)
        self.assertEqual(account.drawdown_state(), "暂停新增")

    def test_only_research_stage_can_add(self):
        account = self.account()
        plan = plan_for(research_card(status="观察"), account)
        self.assertEqual(plan.action, "仅观察")
        self.assertTrue(any("研究关" in r for r in plan.reasons))

    def test_failed_account_check_blocks(self):
        account = self.account()
        plan = plan_for(research_card(checks=(("整手金额", "不通过"),)), account)
        self.assertEqual(plan.action, "仅观察")

    def test_position_sizing_respects_risk_and_lot(self):
        account = self.account(capital_cny=100000, risk_per_trade=0.01, max_position_pct=0.2)
        plan = plan_for(research_card(price=10.0, stop=9.0), account)
        self.assertEqual(plan.action, "可加仓")
        self.assertEqual(plan.shares % 100, 0)
        # 单笔风险 1000 元 / 每手风险 100 元 = 10 手；单只上限 20% / 1000 元一手 = 20 手
        self.assertEqual(plan.lots, 10)

    def test_single_lot_too_large_yields_no_action(self):
        account = self.account(capital_cny=30000)
        plan = plan_for(research_card(price=130.08, stop=109.28), account)
        self.assertEqual(plan.action, "仅观察")
        self.assertEqual(plan.lots, 0)

    def test_cash_floor_limits_new_positions(self):
        account = self.account(capital_cny=100000, cash_floor_pct=0.5,
                               positions=[Position("A", "000001", "某", 6000, 10.0)])
        plan = plan_for(research_card(price=10.0, stop=9.0), account)
        # 可用现金 40,000 − 现金下限 50,000 = 0，因此没有加仓空间
        self.assertEqual(plan.action, "仅观察")
        self.assertTrue(any("现金下限" in r for r in plan.reasons))

    def test_markets_do_not_share_cash(self):
        account = self.account(capital_cny=30000, capital_hkd=30000,
                               positions=[Position("HK", "00700", "腾讯", 100, 400.0, "HKD")])
        self.assertAlmostEqual(account.cash("HK"), 30000 - 40000)
        self.assertAlmostEqual(account.cash("A"), 30000)

    def test_drawdown_state_blocks_new_positions(self):
        account = self.account(capital_cny=30000, peak_equity=100000)
        plan = plan_for(research_card(), account)
        self.assertEqual(plan.action, "仅观察")

    def test_hk_without_lot_size_is_not_traded(self):
        account = self.account()
        card = research_card()
        card.market = "HK"
        plan = plan_for(card, account)
        self.assertEqual(plan.action, "仅观察")
        self.assertTrue(any("每手股数" in r for r in plan.reasons))

    def test_sector_limit_uses_card_industry(self):
        """行业暴露上限要用卡片里的 industry——旧字段 sector 没人填过，
        于是 35% 的上限一直是空转的。"""
        card = research_card(code="605358", name="立昂微", sector="")
        card.market_context["industry"] = "半导体"
        account = self.account(max_position_pct=0.5, max_sector_pct=0.35)
        # 已持有同行业的票，占掉 40% 的资金 → 新增同行业应被行业上限挡住
        account.positions.append(Position(market="A", code="603991", name="领先股份",
                                          shares=1000, cost=13.0, currency="CNY",
                                          sector="半导体"))
        plan = plan_for(card, account)
        self.assertTrue(any("行业" in reason for reason in plan.reasons),
                        plan.reasons)

    def test_portfolio_plan_covers_all_cards(self):
        account = self.account()
        plans = portfolio_plan([research_card(), research_card(code="002044", name="美年健康", status="观察")], account)
        self.assertEqual(len(plans), 2)

    def test_downgraded_holding_triggers_reduction(self):
        account = self.account(capital_cny=30000,
                               positions=[Position("A", "002132", "恒星科技", 1000, 3.5)])
        card = research_card(status="降级")
        card.log = [type("L", (), {"at": DAY, "event": "Q3 单季扣非转负，降级", "from_status": "观察", "to_status": "降级"})()]
        plan = plan_for(card, account)
        self.assertEqual(plan.action, "减仓")
        self.assertTrue(any("已持有" in r for r in plan.reasons))

    def test_eliminated_holding_triggers_exit(self):
        account = self.account(capital_cny=30000,
                               positions=[Position("A", "002132", "恒星科技", 1000, 3.5)])
        plan = plan_for(research_card(status="淘汰"), account)
        self.assertEqual(plan.action, "清仓")

    def test_summary_reports_card_states(self):
        from research.account import account_summary

        account = self.account()
        summary = account_summary([research_card(status="观察"), research_card(code="002044", status="降级")], account)
        self.assertEqual(summary["card_states"], {"观察": 1, "降级": 1})
        self.assertEqual(summary["planned_additions"], 0)


if __name__ == "__main__":
    unittest.main()
