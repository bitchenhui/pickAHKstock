import unittest
from datetime import date

from research.governance import (_severity_for_holders, latest_holder_period,
                                 recent_periods)


class GovernanceTests(unittest.TestCase):
    def test_holder_change_severity(self):
        self.assertEqual(_severity_for_holders(35.0), "高")
        self.assertEqual(_severity_for_holders(12.0), "中")
        self.assertEqual(_severity_for_holders(-0.35), "低")
        self.assertEqual(_severity_for_holders(None), "低")
        self.assertEqual(_severity_for_holders("bad"), "低")

    def test_recent_periods_are_quarters_in_desc_order(self):
        periods = recent_periods(date(2026, 10, 29), count=4)
        # 10 月时最新已披露的是三季度（年报要到次年 3–4 月）
        self.assertEqual(periods, ["20260930", "20260630", "20260331", "20251231"])
        self.assertEqual(recent_periods(date(2026, 2, 10), count=1), ["20251231"])
        self.assertEqual(recent_periods(date(2026, 5, 10), count=1), ["20260331"])

    def test_latest_holder_period_is_quarter_end(self):
        period = latest_holder_period(date(2026, 10, 29))
        self.assertEqual(period, "20260930")
        self.assertEqual(len(period), 8)


if __name__ == "__main__":
    unittest.main()
