import unittest
import unittest.mock

from research import evolution
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
    def test_debate_heading_shows_when_the_debate_was_made(self):
        card = card_with_metrics()
        card.debate = {"verdict": "维持观察", "bull": "多", "bear": "空", "as_of": "2026-11-24"}
        self.assertIn("多空对辩（模型，只用卡片事实 · 11-24 的对辩）", note_fragment(card))
        card.debate["as_of"] = "2026-11-24<script>"
        self.assertNotIn("<script>", note_fragment(card))

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
        self.assertIn("排序权重的依据", fragment)      # 每个因子为什么计分要摆出来

    def test_down_market_turns_list_into_watchlist(self):
        # _full_card 的市场状态是「下行」：清单降级为观察名单，不出持仓候选
        fragment = notebook_fragment([self._full_card()])
        self.assertIn("观察名单", fragment)
        self.assertIn("大盘下行，本轮不出推荐", fragment)
        self.assertIn("大盘下行：暂停给出持仓候选", fragment)
        self.assertNotIn("今日推荐", fragment)
        # 单卡页也要提示：AI 的「可进持仓」没有考虑大盘
        page = note_fragment(self._full_card())
        self.assertIn("大盘下行中，本页不构成建仓依据", page)

    def test_normal_market_keeps_recommendation_and_candidates(self):
        card = self._full_card()
        card.market_context = {"market_regime": {"state": "上行", "day": "2026-11-24"}}
        # 有计分因子时才叫「推荐」；这里临时给一个非零权重来测这个分支
        weights = {name: 0.0 for name in evolution.DEFAULT_WEIGHTS}
        weights["估值"] = 1.0
        with unittest.mock.patch("research.note.current_weights", return_value=(weights, 0)):
            fragment = notebook_fragment([card])
        self.assertIn("今日推荐", fragment)
        self.assertIn("持仓候选", fragment)
        self.assertNotIn("观察名单 · 大盘下行", fragment)
        self.assertNotIn("大盘下行中", note_fragment(card))

    def test_without_scored_factors_list_is_not_called_recommendation(self):
        # 默认权重全为 0（都没有通过检验）：清单没有先后之分，不能叫推荐
        card = self._full_card()
        card.market_context = {"market_regime": {"state": "上行", "day": "2026-11-24"}}
        fragment = notebook_fragment([card])
        self.assertIn("过了质量门的卡片（按代码顺序）", fragment)
        self.assertIn("没有任何排序因子通过历史检验", fragment)
        self.assertIn("还没有排序因子通过历史检验", fragment)
        self.assertNotIn("今日推荐", fragment)
        self.assertIn("13 期检验未通过", fragment)          # 权重依据表
        self.assertIn("各因子均未计分", fragment)

    def test_unranked_list_shows_ai_stance_instead_of_a_rank(self):
        # 没有名次时行首不能写 1、2、3（会被读成先后）；改显示 AI 对辩的表态，正面的在前
        negative = self._full_card()
        negative.code, negative.name = "600001", "负面股"
        negative.debate = {"verdict": "暂不进入持仓研究", "as_of": "2026-11-24"}
        positive = self._full_card()
        positive.code, positive.name = "600002", "正面股"
        positive.debate = {"verdict": "可进持仓研究", "as_of": "2026-11-24"}
        for card in (negative, positive):
            card.market_context = {"market_regime": {"state": "上行", "day": "2026-11-24"}}
        fragment = notebook_fragment([negative, positive])
        self.assertIn("AI 对辩倾向正面的在前", fragment)
        self.assertLess(fragment.index("正面股"), fragment.index("负面股"))
        self.assertIn('<div class="sl-rank stance-pos">▲</div>', fragment)
        self.assertIn('<div class="sl-rank stance-neg">▼</div>', fragment)
        self.assertNotIn('<div class="sl-rank">1</div>', fragment)
        self.assertIn("这个先后同样没有经过历史检验", fragment)
        self.assertNotIn("今日推荐", fragment)

    def test_hold_candidates_skip_down_market_and_incomplete_cards(self):
        from research.note import _hold_candidates

        full = self._full_card()
        self.assertEqual([e.card.code for e in _hold_candidates([full], {})], ["600001"])
        down = {"A": {"state": "下行", "day": "2026-11-24"}}
        self.assertEqual(_hold_candidates([full], down), [])        # 下行市不出持仓候选
        weak = card_with_metrics()                                  # 证据不足 → 不入候选
        self.assertEqual([e.card.code for e in _hold_candidates([weak], {})], [])

    def test_card_page_shows_trading_inputs_and_holders(self):
        card = card_with_metrics()
        card.inputs = {"valuation": {"pb": 1.2, "pb_percentile": 8.0, "pe": 51.0,
                                     "pe_percentile": 12.0, "status": "有数据"},
                       "volume": {"trend": "显著放量", "vol_ratio_20_60": 1.8},
                       "holders": {}}
        card.execution = {"rsi": 57.0, "strength": {"rs": -3.2}, "liquidity": {"level": "无忧"}}
        card.scans = dict(card.scans, holders=[{"side": "减持", "date": "2026-10-01",
                                                "title": "关于股东减持计划的公告"}])
        page = note_fragment(card)
        self.assertIn("市净率 1.20，在近 3 年的 8% 分位，偏低", page)   # 标签写的是 PB 不是 PE
        self.assertIn("RSI（14 日）57", page)
        self.assertIn("近 60 日相对大盘 -3.2 个点", page)
        self.assertIn("成交量显著放量", page)
        self.assertIn("减持类 1 条、增持类 0 条", page)


if __name__ == "__main__":
    unittest.main()
