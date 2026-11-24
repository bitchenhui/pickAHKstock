import unittest
import unittest.mock
from datetime import date
from types import SimpleNamespace

from research import risk


class HolderClassifyTests(unittest.TestCase):
    def test_sell_plan(self):
        self.assertEqual(
            risk.classify_holder("关于持股5%以上股东减持股份计划的公告"),
            {"side": "减持", "stage": "计划"})

    def test_buy_done_beats_plan_in_title(self):
        self.assertEqual(
            risk.classify_holder("关于控股股东增持公司股份计划实施完成的公告"),
            {"side": "增持", "stage": "完成"})

    def test_cancelled_or_promised_non_sale_is_not_a_sale(self):
        self.assertIsNone(risk.classify_holder("关于终止减持计划的公告"))
        self.assertIsNone(risk.classify_holder("关于股东承诺不减持公司股份的公告"))

    def test_equity_incentive_cancellation_is_not_a_buyback(self):
        self.assertIsNone(risk.classify_holder("关于回购注销部分限制性股票的公告"))

    def test_buyback_progress(self):
        self.assertEqual(risk.classify_holder("关于回购公司股份的进展公告"),
                         {"side": "回购", "stage": "进展"})

    def test_unrelated_title(self):
        self.assertIsNone(risk.classify_holder("关于2026年第三季度报告的公告"))

    def test_traditional_chinese(self):
        self.assertEqual(risk.classify_holder("自願公告 - 股東減持計劃")["side"], "减持")


class ScanTests(unittest.TestCase):
    def test_one_fetch_feeds_both_risk_and_holder_lists(self):
        items = [
            SimpleNamespace(code="600001", title="关于持股5%以上股东减持股份计划的公告",
                            published=date(2026, 10, 1), url="u1"),
            SimpleNamespace(code="600001", title="关于收到上海证券交易所问询函的公告",
                            published=date(2026, 10, 2), url="u2"),
            SimpleNamespace(code="600001", title="关于2026年第三季度报告的公告",
                            published=date(2026, 10, 3), url="u3"),
            SimpleNamespace(code="600999", title="关于另一家公司股东减持计划的公告",
                            published=date(2026, 10, 4), url="u4"),
        ]
        with unittest.mock.patch.object(risk, "fetch_announcements", lambda *a, **k: items):
            found = risk.scan_announcements("600001", "测试", 240, "A")
        self.assertEqual([e.category for e in found["risks"]], ["监管问询"])
        self.assertEqual([(h["side"], h["date"]) for h in found["holders"]],
                         [("减持", "2026-10-01")])
        self.assertEqual(found["holders"][0]["url"], "u1")

    def test_scan_risks_still_returns_only_risks(self):
        items = [SimpleNamespace(code="600001", title="关于股东减持计划的公告",
                                 published=date(2026, 10, 1), url="u1")]
        with unittest.mock.patch.object(risk, "fetch_announcements", lambda *a, **k: items):
            self.assertEqual(risk.scan_risks("600001", "测试", 240, "A"), [])


if __name__ == "__main__":
    unittest.main()
