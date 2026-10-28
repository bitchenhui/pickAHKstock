import unittest

from research.hk import (CATEGORY_ALERT, CATEGORY_RESULTS, GEM_PREFIX, HkEvent,
                          _dedupe, event_direction)
from research.quotes import Snapshot


def snapshot(code="00700", price=380.0, turnover=5e8, cap=3.5e12, lot=100):
    return Snapshot(market="HK", code=code, name="测试", price=price,
                    turnover=turnover, market_cap=cap, pe=13.2, pb=2.69,
                    change_pct=0.5, as_of="2026-10-28", turnover_rate=0.15,
                    lot_size=lot, shares=9e9)


class HkTests(unittest.TestCase):
    def test_alert_and_results_categories_are_distinct(self):
        self.assertIn(CATEGORY_ALERT, ("盈利警告",))
        self.assertNotIn(CATEGORY_ALERT, CATEGORY_RESULTS)
        self.assertIn("季度業績", CATEGORY_RESULTS)

    def test_dedupe_keeps_matching_report_period(self):
        rows = [
            {"SECURITY_CODE": "00700", "REPORT_DATE": "2026-06-30 00:00:00", "HOLDER_PROFIT": 100},
            {"SECURITY_CODE": "00700", "REPORT_DATE": "2025-06-30 00:00:00", "HOLDER_PROFIT": 50},
        ]
        result = _dedupe(rows, "2026-06-30")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["HOLDER_PROFIT"], 100)

    def test_gem_prefix_is_eight(self):
        self.assertEqual(GEM_PREFIX, "08")

    def test_event_direction_reads_titles(self):
        self.assertEqual(event_direction("正面盈利预告 - 转亏为盈")[0], "正向")
        self.assertEqual(event_direction("盈利警告")[0], "负向")
        self.assertEqual(event_direction("内幕消息盈利警告")[0], "负向")
        self.assertEqual(event_direction("盈利预喜")[0], "正向")
        self.assertEqual(event_direction("2026年第三季度报告")[0], "待判读")
        self.assertEqual(event_direction("正面盈利预告及盈利警告")[0], "混合")

    def test_event_to_candidate_filters_gem_and_illiquid(self):
        from research.hk import build_candidates

        events = [
            HkEvent(code="08001", name="GEM股", title="业绩公告", published=None,
                    category="季度業績", art_code="A1"),
            HkEvent(code="00700", name="腾讯", title="业绩公告", published=None,
                    category="季度業績", art_code="A2"),
            HkEvent(code="00001", name="长和", title="业绩公告", published=None,
                    category="季度業績", art_code="A3"),
        ]
        quotes = {"00700": snapshot("00700"), "00001": snapshot("00001", turnover=1e6),
                  "08001": snapshot("08001")}
        candidates = build_candidates(events, quotes)
        codes = {c.code for c in candidates}
        self.assertNotIn("08001", codes)          # GEM 排除
        self.assertIn("00700", codes)
        illiquid = [c for c in candidates if c.code == "00001"][0]
        self.assertTrue(any("成交额" in n for n in illiquid.notes))


if __name__ == "__main__":
    unittest.main()
