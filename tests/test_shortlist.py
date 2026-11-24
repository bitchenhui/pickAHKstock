import unittest
import unittest.mock
from datetime import date, timedelta

from research.card import Card, Catalyst
from research.shortlist import build_shortlist, is_down, market_regimes, score_card

ONE = {"共振": 0, "强度": 1, "RSI": 1, "估值": 1, "股东": 1, "量能": 1,
       "催化": 1, "AI": 1, "盯价": 1, "流动性": 1}


def make_card(code="600001", rs=None, rsi=None, pb_pct=None, pe_pct=None,
              catalyst_days=None, catalyst_status="待验证", debate="",
              holders=None, volume=None, level=None, regime="上行") -> Card:
    """只填排序会用到的字段；质量门在测试里统一放行。"""
    card = Card(market="A", code=code, name=f"股{code}", status="观察", view="判断")
    card.derived_level = lambda: "证据充分"       # 只测排序；质量门本身另有测试
    card.execution = {"strength": {"rs": rs} if rs is not None else {},
                      "rsi": rsi,
                      "liquidity": {"level": level} if level else {}}
    valuation = {}
    if pb_pct is not None:
        valuation["pb_percentile"], valuation["pb"] = pb_pct, 1.2
    if pe_pct is not None:
        valuation["pe_percentile"], valuation["pe"] = pe_pct, 15.0
    card.inputs = {"valuation": valuation, "holders": holders or {}, "volume": volume or {}}
    if catalyst_days is not None:
        card.catalysts = [Catalyst(
            event="年报", date=(date.today() + timedelta(days=catalyst_days)).isoformat(),
            status=catalyst_status)]
    if debate:
        card.debate = {"verdict": debate}
    card.market_context = {"market_regime": {"state": regime, "day": date.today().isoformat()}}
    return card


def factor(entry, name):
    return next(f for f in entry.factors if f.name == name)


class SignalTests(unittest.TestCase):
    def score(self, card, weights=ONE):
        return score_card(card, weights)

    def test_weak_relative_strength_is_positive(self):
        self.assertEqual(factor(self.score(make_card(rs=-10)), "强度").signal, 1)
        self.assertEqual(factor(self.score(make_card(rs=20)), "强度").signal, -1)
        self.assertEqual(factor(self.score(make_card(rs=0)), "强度").signal, 0)

    def test_rsi_signals(self):
        self.assertEqual(factor(self.score(make_card(rsi=80)), "RSI").signal, -2)
        self.assertEqual(factor(self.score(make_card(rsi=20)), "RSI").signal, 1)
        self.assertEqual(factor(self.score(make_card(rsi=50)), "RSI").signal, 0)

    def test_score_is_weight_times_signal(self):
        entry = self.score(make_card(rsi=80), {**ONE, "RSI": 2})
        self.assertEqual(entry.score, -4)
        self.assertEqual(factor(entry, "RSI").points, -4)

    def test_catalyst_windows(self):
        self.assertEqual(factor(self.score(make_card(catalyst_days=20)), "催化").signal, 2)
        self.assertEqual(factor(self.score(make_card(catalyst_days=60)), "催化").signal, 1)
        self.assertEqual(factor(self.score(make_card(catalyst_days=200)), "催化").signal, 0)

    def test_overdue_catalyst_scores_zero(self):
        # 回归：过期没核对的催化剂曾被记成「-85 天后到期」还加 2 分
        entry = self.score(make_card(catalyst_days=-85))
        self.assertEqual(factor(entry, "催化").signal, 0)
        self.assertIn("已过期", factor(entry, "催化").why)

    def test_finished_catalyst_is_ignored(self):
        entry = self.score(make_card(catalyst_days=20, catalyst_status="已发生"))
        self.assertEqual([f.name for f in entry.factors if f.name == "催化"], [])

    def test_valuation_label_names_the_metric(self):
        # 回归：取的是 PB 分位却写成「PE 处于历史 x% 分位」
        low = factor(self.score(make_card(pb_pct=8)), "估值")
        self.assertEqual((low.signal, low.points), (2, 2))
        self.assertTrue(low.why.startswith("PB（市净率）"), low.why)
        high = factor(self.score(make_card(pb_pct=85)), "估值")
        self.assertEqual((high.signal, high.points), (-1, -1))
        self.assertTrue(high.why.startswith("PB（市净率）"), high.why)

    def test_pe_fallback_is_labelled_pe(self):
        entry = self.score(make_card(pe_pct=12))
        self.assertTrue(factor(entry, "估值").why.startswith("PE（市盈率）"))

    def test_ai_wording_does_not_assert_holding(self):
        entry = self.score(make_card(debate="可进持仓研究"))
        self.assertEqual(factor(entry, "AI").signal, 2)
        self.assertNotIn("可进持仓", factor(entry, "AI").why)

    def test_every_way_of_saying_no_counts_as_negative(self):
        # 模型实际写法：只认「不进持仓」会把后两种漏成中性
        for text in ("维持观察，不进持仓研究", "暂不进入持仓研究", "暂不可进持仓研究",
                     "不宜进持仓；待验证"):
            self.assertEqual(factor(self.score(make_card(debate=text)), "AI").signal,
                             -2, text)

    def test_ai_factor_shows_when_the_debate_was_made(self):
        card = make_card(debate="可进持仓研究")
        card.debate["as_of"] = "2026-11-24"
        self.assertIn("AI 对辩（11-24）倾向正面", factor(self.score(card), "AI").why)
        del card.debate["as_of"]
        self.assertEqual(factor(self.score(card), "AI").why, "AI 对辩倾向正面")


class WeightTests(unittest.TestCase):
    def test_zero_weight_factor_is_listed_but_not_scored(self):
        entry = score_card(make_card(debate="可进持仓研究", rs=-10), {"AI": 0, "强度": 1})
        self.assertEqual(entry.score, 1)
        self.assertIn("未计分：AI", entry.factor_line())
        self.assertIn("强度+1", entry.factor_line())

    def test_plain_reason_says_so_when_nothing_is_weighted(self):
        entry = score_card(make_card(debate="可进持仓研究", catalyst_days=20),
                           {name: 0 for name in ONE})
        self.assertEqual(entry.score, 0)
        self.assertTrue(entry.plain.startswith("没有通过检验的排序依据"))
        self.assertIn("AI 对辩倾向正面", entry.plain)

    def test_build_shortlist_reads_current_weights(self):
        with unittest.mock.patch("research.shortlist.validate", return_value=[]), \
             unittest.mock.patch("research.shortlist.current_weights",
                                 return_value=({"强度": 5}, 3)):
            entry = build_shortlist([make_card(code="600001", rs=-10)])[0]
        self.assertEqual(entry.score, 5)


class GateAndRegimeTests(unittest.TestCase):
    def setUp(self):
        patcher = unittest.mock.patch("research.shortlist.validate", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_gate_blocks_downgraded_and_partial(self):
        down = make_card(code="600009")
        down.status = "降级"
        partial = make_card(code="600008")
        partial.derived_level = lambda: "部分证据"
        codes = [e.card.code for e in build_shortlist([make_card(), down, partial])]
        self.assertEqual(codes, ["600001"])

    def test_list_shows_every_qualified_card_up_to_the_cap(self):
        # 现在没有通过检验的排序：过门槛的卡都列出来（不是随便截 5 张），到上限才截断
        cards = [make_card(code=f"6000{i:02d}") for i in range(24)]
        self.assertEqual(len(build_shortlist(cards)), 20)
        self.assertEqual([e.card.code for e in build_shortlist(cards)],
                         [f"6000{i:02d}" for i in range(20)])

    def test_market_regimes_takes_the_freshest(self):
        old = make_card(code="600001", regime="上行")
        old.market_context["market_regime"]["day"] = "2026-01-01"
        fresh = make_card(code="600002", regime="下行")
        regimes = market_regimes([old, fresh])
        self.assertEqual(regimes["A"]["state"], "下行")
        self.assertTrue(is_down(fresh, regimes))
        self.assertTrue(is_down(old, regimes))      # 同一个市场，用最新那份状态
        self.assertFalse(is_down(make_card(code="600003"), {}))

    def test_list_is_not_sorted_by_signals_when_weights_are_all_zero(self):
        weights = {name: 0 for name in ONE}
        first = make_card(code="600002", rs=20, debate="不进持仓研究")
        second = make_card(code="600001", rs=-20, debate="可进持仓研究")
        entries = build_shortlist([first, second], weights)
        self.assertEqual([e.score for e in entries], [0, 0])       # 信号再强也不计分

    def test_ties_are_grouped_by_ai_stance_without_being_scored(self):
        weights = {name: 0 for name in ONE}
        negative = make_card(code="600001", debate="暂不进入持仓研究", rs=-30)
        neutral = make_card(code="600002", rs=30)
        positive = make_card(code="600003", debate="可进持仓研究")
        entries = build_shortlist([negative, neutral, positive], weights)
        # 正面 → 没表态 → 负面，组内按代码；相对强度不参与摆放
        self.assertEqual([e.card.code for e in entries], ["600003", "600002", "600001"])
        self.assertEqual([e.score for e in entries], [0, 0, 0])


if __name__ == "__main__":
    unittest.main()
