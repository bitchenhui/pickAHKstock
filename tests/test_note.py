import unittest

from research.card import Card, CounterCase, Evidence, FinancialTrack, Scenario
from research.note import _num, notebook_fragment, note_fragment, quarterly


def card_with_metrics() -> Card:
    track = FinancialTrack(source="测试", periods=["2026-09-30", "2026-06-30",
                                                    "2026-03-31", "2025-12-31"])
    track.metrics = {
        "净利润": {"2026-03-31": "2.82亿", "2026-06-30": "4.50亿", "2026-09-30": "5.49亿",
                   "2025-12-31": "1589.85万", "2025-09-30": "-6220.18万"},
        "销售毛利率": {"2026-03-31": "23.37%", "2026-06-30": "19.59%", "2026-09-30": "16.92%"},
    }
    return Card(market="A", code="600001", name="测试股", status="观察", view="",
                financials=track,
                industry_context={"verdict": "行业共振", "industry": "电池",
                                  "peers_with_forecast": 4, "peers_same_sign": 4,
                                  "same_sign_rate": 1.0},
                capital={"operating_cf_label": "-11.13 亿", "fcf_label": "-13.25 亿",
                         "capex_label": "+2.12 亿"},
                expectations={"coverage": 0},
                scans={"as_of": "2026-11-10", "risks": [
                    {"category": "监管问询", "published": "2026-08-04", "title": "问询函回复"}],
                    "governance": [{"severity": "高", "category": "担保", "detail": "占净资产 137.99%"}]},
                market_context={"market_regime": {"state": "下行", "day": "2026-11-10",
                                                  "close": 4056}})


class ParseTests(unittest.TestCase):
    def test_num_units(self):
        self.assertAlmostEqual(_num("5.49亿"), 5.49e8)
        self.assertAlmostEqual(_num("-6220.18万"), -6.22018e7)
        self.assertAlmostEqual(_num("16.92%"), 16.92)
        self.assertIsNone(_num("—"))


class QuarterlyTests(unittest.TestCase):
    def test_derives_from_cumulative_within_year(self):
        quarters = quarterly(card_with_metrics().financials, "净利润")
        self.assertEqual([q[0] for q in quarters], ["2026Q1", "2026Q2", "2026Q3"])
        self.assertAlmostEqual(quarters[0][1], 2.82e8)
        self.assertAlmostEqual(quarters[1][1], 1.68e8)     # 4.50 − 2.82
        self.assertAlmostEqual(quarters[2][1], 0.99e8)     # 5.49 − 4.50

    def test_q1_resets_across_year(self):
        track = FinancialTrack(source="t", periods=["2026-03-31", "2025-12-31"])
        track.metrics = {"净利润": {"2025-09-30": "-1.0亿", "2025-12-31": "0.2亿",
                                   "2026-03-31": "1.5亿"}}
        quarters = quarterly(track)
        self.assertEqual([q[0] for q in quarters], ["2025Q4", "2026Q1"])
        self.assertAlmostEqual(quarters[0][1], 1.2e8)      # 0.2 − (−1.0)
        self.assertAlmostEqual(quarters[1][1], 1.5e8)


class RenderTests(unittest.TestCase):
    def test_note_fragment_renders_card_faces(self):
        fragment = note_fragment(card_with_metrics())
        for marker in ("测试股", "600001", "行业共振", "电池", "-11.13 亿",
                       "137.99%", "监管问询", "0.99 亿", "下行", "0.5×",
                       "非投资建议"):
            self.assertIn(marker, fragment)
        # 单季拆解是推算值，必须带核对提示
        self.assertIn("推算", fragment)

    def test_note_fragment_escapes_hostile_text(self):
        card = card_with_metrics()
        card.name = "<script>alert(1)</script>"
        fragment = note_fragment(card)
        self.assertNotIn("<script>", fragment)
        self.assertIn("&lt;script&gt;", fragment)

    def _full_card(self) -> Card:
        """一张真实通过质量门的观察卡（证据充分 + 零阻塞）。"""
        card = card_with_metrics()
        card.view = "测试判断"
        card.evidence = [Evidence(
            id="E1", claim="披露事件", kind="披露", publisher="巨潮", title="公告",
            published="2026-01-01", url="http://example.com", observed="2026-01-02",
            status="已核实")]
        card.scenarios = [Scenario(name=n, narrative="n", drivers=["d"])
                          for n in ("基准", "乐观", "失败")]
        card.counter = CounterCase(strongest_failure="很具体的失败路径含数字 100",
                                   exit_trigger="跌破 10 元", where_wrong="可能读错")
        card.invalidation = ["2026Q4 跌破 10 元"]
        card.financials.path = "已盈利"
        card.financials.recurring_judgment = "可重复"
        card.financials.cyclical_judgment = "不适用"
        return card

    def test_notebook_fragment_orders_and_counts(self):
        other = card_with_metrics()
        other.code, other.name, other.status = "600002", "降级股", "降级"
        fragment = notebook_fragment([self._full_card(), other])
        self.assertIn("在册 2 张", fragment)
        self.assertLess(fragment.index("600001"), fragment.index("600002"))  # 观察在前
        self.assertIn("红旗", fragment)
        self.assertIn("持仓候选", fragment)          # 深色终端视图的资格区块
        self.assertIn("最终裁决", fragment)          # 排序信号必须声明非买入指令

    def test_hold_candidates_require_full_evidence(self):
        from research.note import _card_summary, _hold_candidates

        weak = card_with_metrics()                    # 证据不足 → 不入候选
        out = _hold_candidates([_card_summary(self._full_card()),
                                _card_summary(weak)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["card"].code, "600001")
        self.assertIn("裁决", out[0]["gap"])


if __name__ == "__main__":
    unittest.main()
