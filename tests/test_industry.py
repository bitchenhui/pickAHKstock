import unittest
from unittest import mock

from research import industry


def _row(code, change):
    return {"SECURITY_CODE": code, "SECURITY_NAME_ABBR": f"股{code}",
            "NOTICE_DATE": "2026-07-15", "REPORT_DATE": "2026-06-30",
            "PREDICT_FINANCE": "归属于上市公司股东的净利润",
            "ADD_AMP_LOWER": change, "PREDICT_AMT_LOWER": 1e8}


MAP = {"600001": "半导体", "600002": "半导体", "600003": "半导体", "600004": "半导体",
       "600005": "钢铁", "600006": "钢铁", "600007": "钢铁", "600008": "钢铁",
       "600009": "钢铁", "600010": "钢铁", "600011": "钢铁"}


class CoOccurrenceTests(unittest.TestCase):
    def _run(self, code, rows, **kwargs):
        with mock.patch.object(industry, "industry_map", lambda level="second": MAP):
            return industry.co_occurrence(code, rows, **kwargs)

    def test_same_direction_peers_mean_industry_resonance(self):
        """同业全部同向 → 行业共振。"""
        rows = [_row("600001", 80.0), _row("600002", 120.0), _row("600003", 60.0),
                _row("600005", -40.0), _row("600006", 30.0), _row("600007", 20.0)]
        res = self._run("600001", rows)
        self.assertEqual(res.industry, "半导体")
        self.assertEqual(res.peers_with_forecast, 3)
        self.assertEqual(res.peers_same_sign, 3)
        self.assertEqual(res.verdict, "行业共振")
        self.assertGreater(res.lift, 1.0)

    def test_opposite_direction_peers_mean_company_specific(self):
        """同业方向相反 → 公司特有。"""
        rows = [_row("600001", 80.0), _row("600002", -50.0), _row("600003", -20.0),
                _row("600005", 10.0), _row("600006", 20.0), _row("600007", 30.0)]
        res = self._run("600001", rows)
        self.assertEqual(res.verdict, "公司特有")
        self.assertEqual(res.peers_same_sign, 1)

    def test_too_few_peers_is_undecidable(self):
        """同行业样本太少 → 明说判不了，不硬下结论。"""
        rows = [_row("600001", 80.0), _row("600005", 10.0), _row("600006", 20.0)]
        res = self._run("600001", rows)
        self.assertEqual(res.verdict, "单点不可辨")
        self.assertIn("1 家", res.note)

    def test_company_not_in_pool(self):
        res = self._run("600999", [_row("600001", 80.0)])
        self.assertEqual(res.verdict, "不在预告池")

    def test_company_without_industry(self):
        res = self._run("700001", [_row("700001", 80.0)])
        self.assertEqual(res.verdict, "行业未知")

    def test_penetration_reflects_industry_size(self):
        rows = [_row("600001", 80.0), _row("600002", 120.0), _row("600003", 60.0),
                _row("600004", 40.0)]
        res = self._run("600001", rows)
        self.assertEqual(res.industry_members, 4)
        self.assertAlmostEqual(res.penetration, 1.0)
        self.assertLess(res.market_penetration, 1.0)


class ResonanceReportTests(unittest.TestCase):
    def test_report_lists_industries_with_direction_and_penetration(self):
        rows = [_row("600001", 80.0), _row("600002", 120.0), _row("600003", 60.0),
                _row("600004", 40.0), _row("600005", -50.0), _row("600006", -20.0),
                _row("600007", -10.0)]
        with mock.patch.object(industry, "industry_map", lambda level="second": MAP):
            report = industry.resonance_report(__import__("datetime").date(2026, 6, 30), rows)
        self.assertEqual(report["pool"], 7)
        self.assertEqual(len(report["industries"]), 2)
        top = report["industries"][0]
        self.assertEqual(top["industry"], "半导体")
        self.assertEqual(top["up"], 4)
        self.assertEqual(top["down"], 0)
        self.assertGreater(top["lift"], 1.0)


if __name__ == "__main__":
    unittest.main()
