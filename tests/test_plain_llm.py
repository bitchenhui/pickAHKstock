import unittest
import unittest.mock

from research import llm, plain
from research.card import Card, FinancialTrack


def card_with_data() -> Card:
    track = FinancialTrack(source="测试", periods=["2026-09-30", "2026-06-30",
                                                    "2026-03-31", "2025-12-31"])
    track.metrics = {
        "净利润": {"2026-03-31": "2.82亿", "2026-06-30": "4.50亿",
                   "2026-09-30": "5.49亿", "2025-12-31": "1589.85万"},
        "销售毛利率": {"2026-03-31": "23.37%", "2026-06-30": "19.59%",
                       "2026-09-30": "16.92%"},
    }
    card = Card(market="A", code="600001", name="测试股", status="观察",
                view="专业结论：涨价驱动的扭亏", financials=track)
    card.industry_context = {"verdict": "行业共振", "industry": "电池"}
    card.capital = {"operating_cf_label": "-11.13 亿", "fcf_label": "-13.25 亿"}
    card.market_context = {"price": 12.0, "stop_reference": 10.5,
                           "market_regime": {"state": "下行", "index": "沪深300"}}
    card.commodities = {"items": {"LC": {"symbol": "LC", "name": "碳酸锂（代理）",
                                        "latest": 90000.0, "chg_30d": -0.328}}}
    card.scans = {"risks": [{"title": "问询函", "category": "监管问询"}],
                  "governance": [{"severity": "高", "category": "担保",
                                  "detail": "占净资产 137.99%"}]}
    return card


class PlainTests(unittest.TestCase):
    def test_one_line_uses_facts_not_prose(self):
        result = plain.plain_conclusion(card_with_data())
        line = result["one_line"]
        self.assertIn("碳酸锂", line)          # 靠什么赚钱
        self.assertIn("变弱", line)            # 动能方向
        self.assertIn("行业", line)            # 行业 vs 公司
        self.assertIn("-11.13 亿", line)       # 最硬的反证
        self.assertIn("打折", line)            # 环境与动作
        self.assertNotIn("业绩预告", line)     # 不是专业散文的术语搬运

    def test_facts_watch_decide_present(self):
        result = plain.plain_conclusion(card_with_data())
        self.assertTrue(result["facts"])
        self.assertTrue(result["watch"])
        self.assertTrue(result["decide"])

    def test_plain_text_is_short(self):
        text = plain.plain_text(card_with_data())
        self.assertLess(len(text), 400)
        self.assertIn("该看", text)

    def test_empty_card_does_not_crash(self):
        result = plain.plain_conclusion(Card(market="A", code="1", name="空"))
        self.assertTrue(result["one_line"])


class LLMTests(unittest.TestCase):
    def test_not_configured_means_no_debate(self):
        import os

        env = {k: v for k, v in os.environ.items() if k != "LLM_API_KEY"}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            self.assertFalse(llm.configured())
            result = llm.analyze(card_with_data())
        self.assertIsNone(result["debate"])
        self.assertTrue(result["plain"]["one_line"])

    def test_facts_only_include_card_content(self):
        facts = llm._card_facts(card_with_data())
        self.assertIn("600001", facts)
        self.assertIn("-11.13 亿", facts)
        self.assertNotIn("目标价", facts)     # 仓库边界：不出现目标价

    def test_debate_parses_json_verdict(self):
        import os

        answers = [
            "多方：涨价驱动，弹性大。\n多头最怕的是：价格回落。",
            "空方：现金流与利润背离。",
            '{"conclusion": "维持观察", "checks": ["核对现金流"], '
            '"abandon_if": "Q4 转负", "caveat": "价格假设不成立"}',
        ]
        with unittest.mock.patch.dict("os.environ", {"LLM_API_KEY": "k"}, clear=False), \
             unittest.mock.patch.object(llm, "_call", side_effect=answers):
            result = llm.debate(card_with_data())
        self.assertEqual(result.verdict, "维持观察")
        self.assertIn("核对现金流", result.checks)
        self.assertIn("Q4 转负", result.checks)

    def test_debate_without_key_raises(self):
        import os

        env = {k: v for k, v in os.environ.items() if k != "LLM_API_KEY"}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaises(llm.LLMError):
                llm.debate(card_with_data())


if __name__ == "__main__":
    unittest.main()
