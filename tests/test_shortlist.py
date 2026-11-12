import unittest
import unittest.mock

from research.card import Card, Catalyst
from research.shortlist import MAX_SHORTLIST, build_shortlist


def qualified_card(code="600001", name="测试", rs=9.0, verdict="公司特有",
                   catalyst_days=20, debate="可进持仓研究", commodity_up=True):
    card = Card(market="A", code=code, name=name, status="观察", view="判断" * 10)
    # 直接 patch derived_level 和 validate（测试排序逻辑，不测质量门）
    card.derived_level = lambda: "证据充分"
    card.industry_context = {"verdict": verdict}
    card.execution = {
        "strength": {"rs": rs},
        "liquidity": {"level": "无忧"},
    }
    from datetime import date, timedelta
    due = (date.today() + timedelta(days=catalyst_days)).isoformat()
    card.catalysts = [Catalyst(event="年报", date=due)]
    if commodity_up:
        card.commodities = {"items": {"LC": {"symbol": "LC", "name": "碳酸锂",
                                            "chg_30d": 0.05}}}
    if debate:
        card.debate = {"verdict": debate}
    return card


def downgraded_card(code="600999"):
    card = qualified_card(code)
    card.status = "降级"
    return card


class ShortlistTests(unittest.TestCase):
    def setUp(self):
        # 排序逻辑的测试不测质量门：patch validate 让所有卡过门
        patcher = unittest.mock.patch("research.shortlist.validate", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_hard_gate_blocks_downgraded(self):
        result = build_shortlist([qualified_card(), downgraded_card()])
        codes = [e.card.code for e in result]
        self.assertNotIn("600999", codes)

    def test_company_specific_beats_industry(self):
        company = qualified_card(code="600001", verdict="公司特有")
        industry = qualified_card(code="600002", verdict="行业共振")
        result = build_shortlist([company, industry])
        self.assertEqual(result[0].card.code, "600001")
        self.assertEqual(result[1].card.code, "600002")

    def test_ai_negative_pushes_down(self):
        good = qualified_card(code="600001", debate="可进持仓研究")
        bad = qualified_card(code="600002", debate="维持观察，不进持仓研究")
        result = build_shortlist([good, bad])
        self.assertEqual(result[0].card.code, "600001")
        self.assertEqual(result[-1].card.code, "600002")

    def test_max_size(self):
        cards = [qualified_card(code=f"6000{i:02d}") for i in range(10)]
        result = build_shortlist(cards)
        self.assertLessEqual(len(result), MAX_SHORTLIST)

    def test_factors_are_transparent(self):
        result = build_shortlist([qualified_card()])
        self.assertTrue(result)
        entry = result[0]
        self.assertTrue(entry.factors)             # 每个因子都有名、分、理由
        for name, score, why in entry.factors:
            self.assertTrue(name)
            self.assertIsInstance(score, int)
            self.assertTrue(why)
        self.assertTrue(entry.plain)               # 人话理由非空


if __name__ == "__main__":
    unittest.main()
