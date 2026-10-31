import unittest
from datetime import date, timedelta

from research.board import BoardItem, build_board, new_candidates
from research.card import Card, Catalyst, Evidence, LogEntry

TODAY = date(2026, 10, 29)


def card(status="观察", catalysts=None, evidence=None, log=None) -> Card:
    c = Card(market="A", code="002132", name="恒星科技", status=status,
             evidence=evidence or [], catalysts=catalysts or [], log=log or [])
    return c


class BoardTests(unittest.TestCase):
    def test_overdue_goes_to_due_bucket(self):
        due = (TODAY - timedelta(days=3)).isoformat()
        items = build_board([card(catalysts=[Catalyst(event="三季报", date=due)])],
                            today=TODAY)
        self.assertEqual(items[0].bucket, "到期复核")
        self.assertTrue(items[0].overdue)
        self.assertIn("已过期 3 天", items[0].reason)

    def test_near_due_goes_to_near_bucket(self):
        due = (TODAY + timedelta(days=10)).isoformat()
        items = build_board([card(catalysts=[Catalyst(event="年报", date=due)])],
                            today=TODAY, horizon_days=30)
        self.assertEqual(items[0].bucket, "临近复核")
        self.assertFalse(items[0].overdue)

    def test_far_due_stays_visible_in_pool(self):
        due = (TODAY + timedelta(days=200)).isoformat()
        items = build_board([card(catalysts=[Catalyst(event="年报", date=due)])],
                            today=TODAY, horizon_days=30)
        self.assertEqual(items[0].bucket, "观察池")
        self.assertIn("200 天后", items[0].reason)

    def test_card_without_dates_asks_for_a_schedule(self):
        items = build_board([card()], today=TODAY)
        self.assertEqual(items[0].bucket, "观察池")
        self.assertIn("没有排期日期", items[0].reason)

    def test_evidence_next_check_is_a_schedule(self):
        due = (TODAY + timedelta(days=5)).isoformat()
        evidence = [Evidence(id="E1", claim="c", status="已核实", published="2026-01-01",
                             url="http://x", next_check=due)]
        items = build_board([card(evidence=evidence)], today=TODAY)
        self.assertEqual(items[0].bucket, "临近复核")
        self.assertIn("E1", items[0].reason)

    def test_downgrade_reason_uses_the_transition_entry(self):
        log = [LogEntry(at="2026-10-01", event="建立卡片", to_status="观察"),
               LogEntry(at="2026-10-10", event="Q3 单季转亏，从观察降级",
                        from_status="观察", to_status="降级"),
               LogEntry(at="2026-10-20", event="补入分产品拆分",
                        from_status="降级", to_status="降级")]
        items = build_board([card(status="降级", log=log)], today=TODAY)
        self.assertEqual(items[0].bucket, "已降级或淘汰")
        self.assertIn("Q3 单季转亏", items[0].reason)
        self.assertNotIn("补入分产品", items[0].reason)

    def test_new_candidates_filter_blocked(self):
        class Fake:
            def __init__(self, code, notes, unaffordable=False):
                self.code, self.notes = code, notes
                self.name, self.market = code, "A"
                self.headline = "h"
                self.unaffordable = unaffordable
        # 建不了仓、买不进（流动性）的挡掉
        items = new_candidates([Fake("000001", []), Fake("000002", ["成交额不足"])])
        self.assertEqual([i.code for i in items], ["000001"])
        self.assertEqual(items[0].bucket, "待立项")
        items = new_candidates([Fake("000001", []), Fake("000003", [], unaffordable=True)])
        self.assertEqual([i.code for i in items], ["000001"])

    def test_soft_notes_do_not_hide_candidates(self):
        """软提示（口径提醒、极端幅度）不该让候选整条消失。"""
        class Fake:
            def __init__(self, code, notes):
                self.code, self.notes = code, notes
                self.name, self.market = code, "A"
                self.headline = "h"
                self.unaffordable = False
        items = new_candidates([Fake("000001", ["主指标为「扣除非经常性损益后的净利润」"]),
                                Fake("000002", ["同比幅度 320% 属极端值，多为低基数所致"])])
        self.assertEqual([i.code for i in items], ["000001", "000002"])
        self.assertIn("主指标", items[0].reason)


if __name__ == "__main__":
    unittest.main()
