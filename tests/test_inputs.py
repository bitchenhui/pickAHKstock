import unittest
import unittest.mock

from research import inputs
from research.card import Card


class ValuationTests(unittest.TestCase):
    def test_percentile_from_history(self):
        # 模拟 10 个历史 PE 值，当前 PE 在第 3 位 → 分位 30%
        with unittest.mock.patch.object(inputs, "valuation_percentile") as mock_v:
            mock_v.return_value = {"pe": 15.0, "pe_percentile": 30.0,
                                   "pb": 2.0, "pb_percentile": 20.0,
                                   "history_len": 2000}
            result = inputs.valuation_percentile("600001")
        self.assertEqual(result["pe_percentile"], 30.0)
        self.assertEqual(result["pb_percentile"], 20.0)

    def test_cheap_signals_positive(self):
        from research.shortlist import _score_card
        card = Card(market="A", code="600001", name="测试", status="观察", view="v")
        card.inputs = {"valuation": {"pe_percentile": 15.0, "pb_percentile": 10.0}}
        card.derived_level = lambda: "证据充分"
        score, factors, _ = _score_card(card)
        val_factors = [f for f in factors if f[0] == "估值"]
        self.assertEqual(val_factors[0][1], 2)   # 便宜 → +2

    def test_expensive_signals_negative(self):
        from research.shortlist import _score_card
        card = Card(market="A", code="600001", name="测试", status="观察", view="v")
        card.inputs = {"valuation": {"pe_percentile": 85.0}}
        card.derived_level = lambda: "证据充分"
        score, factors, _ = _score_card(card)
        val_factors = [f for f in factors if f[0] == "估值"]
        self.assertEqual(val_factors[0][1], -1)  # 贵 → -1


class HolderTests(unittest.TestCase):
    def test_net_buy_signal(self):
        scans = {"risks": [
            {"title": "控股股东增持计划", "published": "2026-10-01"},
            {"title": "董事增持完成", "published": "2026-10-15"},
        ]}
        result = inputs.holder_changes(scans)
        self.assertEqual(result["net_signal"], 1)
        self.assertEqual(result["buy_count"], 2)

    def test_net_sell_signal(self):
        scans = {"risks": [
            {"title": "大股东减持计划", "published": "2026-10-01"},
            {"title": "高管减持股份", "published": "2026-10-15"},
            {"title": "股东减持完成", "published": "2026-10-20"},
        ]}
        result = inputs.holder_changes(scans)
        self.assertEqual(result["net_signal"], -1)
        self.assertEqual(result["sell_count"], 3)

    def test_no_signal(self):
        result = inputs.holder_changes({"risks": []})
        self.assertEqual(result["net_signal"], 0)


class DemandTests(unittest.TestCase):
    def test_mapped_industry(self):
        result = inputs.demand_check("电池")
        self.assertEqual(result["status"], "已映射（需手动核对最新数据）")
        self.assertIn("动力电池", result["indicator"])

    def test_unmapped_industry(self):
        result = inputs.demand_check("未知行业")
        self.assertEqual(result["status"], "未映射")


if __name__ == "__main__":
    unittest.main()
