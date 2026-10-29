import unittest

from research.hk_business import (BusinessReview, _mentions, _themes,
                                  diff_reviews)


def reviews() -> list[BusinessReview]:
    return [
        BusinessReview(period="2026-06-30",
                       review="全国固定资产投资同比下降5.7%，房地产开发投资同比下降18.0%。"
                              "全国水泥产量仅7.36亿吨，同比下降8%。公司持续降本节支，成本管控见效。"),
        BusinessReview(period="2025-12-31", review="全年收入下降1.9%，利润由盈转亏。"),
        BusinessReview(period="2025-06-30",
                       review="上半年固定资产投资同比增长2.8%，基建投资稳定增长4.6%。"
                              "公司持续降本节支，成本管控见效，需求保持稳定。"),
    ]


class BusinessTests(unittest.TestCase):
    def test_theme_counting(self):
        counts = _themes("价格下降，需求回暖，成本管控")
        self.assertGreaterEqual(counts["价格"], 1)
        self.assertGreaterEqual(counts["需求"], 1)
        self.assertGreaterEqual(counts["成本"], 1)

    def test_diff_matches_same_quarter_prior_year(self):
        diff = diff_reviews(reviews())
        self.assertIsNotNone(diff)
        self.assertEqual(diff.period, "2026-06-30")
        self.assertEqual(diff.prior_period, "2025-06-30")
        # 需求词从"保持稳定"变成"下降"，价格/需求类主题计数应有变化
        self.assertIn("需求", diff.theme_delta)

    def test_diff_extracts_numbers(self):
        diff = diff_reviews(reviews())
        self.assertTrue(any("5.7" in n for n in diff.numbers))

    def test_diff_without_prior_period_returns_none(self):
        only = [BusinessReview(period="2026-06-30", review="内容")]
        self.assertIsNone(diff_reviews(only))

    def test_mentions_detects_overlap(self):
        others = ["全国固定资产投资同比下降5.7%，房地产开发投资同比下降18.0%。"]
        self.assertTrue(_mentions("固定资产投资同比下降5.7%", others))
        self.assertFalse(_mentions("海外销量增长57%", others))


if __name__ == "__main__":
    unittest.main()
