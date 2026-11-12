import unittest

from research.card import Card, Catalyst
from research.shortlist import MAX_SHORTLIST, build_shortlist


def qualified_card(code="600001", name="测试", rs=9.0, verdict="公司特有",
                   catalyst_days=20, debate="可进持仓研究", commodity_up=True):
    card = Card(market="A", code=code, name=name, status="观察", view="判断" * 10)
    card.evidence = [type("E", (), {
        "id": "E1", "claim": "c", "kind": "披露", "publisher": "p", "title": "t",
        "published": "2026-01-01", "url": "http://x", "observed": "2026-01-02",
        "status": "已核实", "alternative": "", "next_check": "", "note": ""})()]
    card.scenarios = [type("S", (), {"name": n, "narrative": "n" * 5, "drivers": ["d"],
                                     "is_filled": lambda: True})()
                      for n in ("基准", "乐观", "失败")]
    card.counter = type("C", (), {"strongest_failure": "x" * 20, "exit_trigger": "跌破 10",
                                  "where_wrong": "y"})()
    card.invalidation = ["2026Q4 跌破 10 元"]
    card.financials = type("F", (), {
        "path": "已盈利", "source": "test", "periods": ["a", "b", "c", "d"],
        "recurring_judgment": "r", "cyclical_judgment": "c",
        "quarter_periods": [], "metrics": {}, "quarter_metrics": {},
        "working_capital_note": "", "path_evidence": [], "notes": []})()
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
