import unittest

from research.segments import SegmentRow, _period_key, _verdict, compare, secucode


class SegmentTests(unittest.TestCase):
    def test_secucode_suffix(self):
        self.assertEqual(secucode("002132"), "002132.SZ")
        self.assertEqual(secucode("603991"), "603991.SH")
        self.assertEqual(secucode("00700", "HK"), "00700.HK")

    def test_period_key_maps_to_year_quarter(self):
        self.assertEqual(_period_key("2026-06-30"), "2026Q2")
        self.assertEqual(_period_key("2025-12-31"), "2025Q4")
        self.assertEqual(_period_key("2026-03-31"), "2026Q1")

    def test_negative_margin_is_flagged(self):
        verdict = _verdict(48.0, 12.0, -5.26)
        self.assertIn("仍在亏损", verdict)

    def test_four_quadrant_verdicts(self):
        self.assertIn("提价", _verdict(30.0, 2.0))
        self.assertIn("以量换价", _verdict(30.0, -2.0))
        self.assertIn("收缩", _verdict(-30.0, 2.0))
        self.assertIn("双杀", _verdict(-30.0, -2.0))
        self.assertEqual(_verdict(1.0, 0.1), "变化不显著")
        self.assertEqual(_verdict(None, None), "无可比数据")

    def test_compare_matches_same_quarter_prior_year(self):
        rows = [
            SegmentRow("2026-06-30", "2026中报", "按产品", "有机硅", 100.0, 60.0, 30.0),
            SegmentRow("2025-06-30", "2025中报", "按产品", "有机硅", 80.0, 60.0, 20.0),
            SegmentRow("2026-06-30", "2026中报", "按产品", "金属制品", 50.0, 40.0, 10.0),
            SegmentRow("2025-06-30", "2025中报", "按产品", "金属制品", 60.0, 40.0, 15.0),
        ]
        changes = {c.item: c for c in compare(rows, "按产品")}
        self.assertAlmostEqual(changes["有机硅"].income_yoy, 25.0)
        self.assertAlmostEqual(changes["有机硅"].margin_change, 10.0)
        self.assertIn("提价", changes["有机硅"].verdict)
        self.assertAlmostEqual(changes["金属制品"].income_yoy, -10 / 60 * 100, places=4)
        self.assertIn("双杀", changes["金属制品"].verdict)

    def test_new_item_without_prior_period_is_not_a_change(self):
        rows = [
            SegmentRow("2026-06-30", "2026中报", "按产品", "新业务", 10.0, 20.0, 40.0),
            SegmentRow("2025-06-30", "2025中报", "按产品", "旧业务", 90.0, 80.0, 20.0),
        ]
        changes = {c.item: c for c in compare(rows, "按产品")}
        self.assertIsNone(changes["新业务"].income_yoy)
        self.assertEqual(changes["新业务"].verdict, "无可比数据")


if __name__ == "__main__":
    unittest.main()
