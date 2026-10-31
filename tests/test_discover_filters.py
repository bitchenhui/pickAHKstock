import unittest

from research.discover import MIN_FORECAST_AMOUNT, build_candidates
from research.events import Forecast, metric_rank
from research.quotes import Snapshot


def snapshot(code="002132", price=10.0, turnover=5e8, cap=8e9):
    return Snapshot(market="A", code=code, name="测试", price=price, turnover=turnover,
                    market_cap=cap, pe=20.0, pb=2.0, change_pct=0.0, as_of="2026-10-28",
                    turnover_rate=1.0, lot_size=100, shares=8e8)


def forecast(code="002132", metric="归属于上市公司股东的净利润", change=60.0,
            amount=5e8, kind="预增", day=15):
    return Forecast(code=code, name="测试",
                    notice_date=__import__("datetime").date(2026, 7, day),
                    report_period="2026-06-30", metric=metric, forecast_type=kind,
                    change_lower=change, change_upper=change + 10, reason="",
                    source_url="", amount_lower=amount, amount_upper=amount)


class DiscoveryFilterTests(unittest.TestCase):
    def test_metric_priority_prefers_parent_net_profit(self):
        self.assertEqual(metric_rank("归属于上市公司股东的净利润"), 0)
        self.assertEqual(metric_rank("扣除非经常性损益后的净利润"), 2)
        self.assertGreater(metric_rank("营业收入"), metric_rank("净利润"))

    def test_primary_metric_is_not_the_largest_change(self):
        """旧口径会选「幅度最大」的那条：营收 +30% / 归母 +500% → 归母暴增。

        新口径按固定口径选归母，那条 +500% 不该被当作主指标。
        """
        forecasts = [
            forecast(metric="营业收入", change=30.0, amount=5e9),
            forecast(metric="归属于上市公司股东的净利润", change=500.0, amount=3e8),
        ]
        candidates, _stats = build_candidates(forecasts, {"002132": snapshot()}, [])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].metric, "归属于上市公司股东的净利润")
        self.assertEqual(candidates[0].change_lower, 500.0)

    def test_small_absolute_amount_is_flagged_not_dropped(self):
        """绝对额过小的候选不剔除，只标记并降权排序——回测证据不支持硬剔除。"""
        forecasts = [
            forecast(code="002132", change=800.0, amount=1e6),   # 低基数暴增
            forecast(code="002044", change=40.0, amount=2e8),    # 正常
        ]
        quotes = {"002132": snapshot("002132"), "002044": snapshot("002044")}
        candidates, stats = build_candidates(forecasts, quotes, [])
        self.assertEqual(len(candidates), 2)
        self.assertEqual([c.code for c in candidates], ["002044", "002132"])
        self.assertTrue(candidates[1].small_amount)
        self.assertFalse(candidates[0].small_amount)
        self.assertEqual(stats["small_amount"], 1)
        self.assertTrue(any("低基数" in n for n in candidates[1].notes))

    def test_amount_threshold_of_zero_disables_the_flag(self):
        forecasts = [forecast(change=800.0, amount=1e6)]
        candidates, stats = build_candidates(forecasts, {"002132": snapshot()}, [],
                                            min_amount=0)
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0].small_amount)
        self.assertEqual(stats["small_amount"], 0)

    def test_same_tier_is_ordered_by_recency_not_by_amount(self):
        """同档之内按**公告日新的先看**（工作流顺序，不是收益预测）。

        早先这里按「预告绝对额降序」排，理由是 v3 的检验支持；完整池的对照
        把那条否掉了——四种排序里随机反而最好（docs/backtest-v5.md），
        所以不该再用收益的语言包装排序规则。
        """
        forecasts = [
            forecast(code="002132", change=800.0, amount=1e8, day=10),  # 早公告、金额小
            forecast(code="002044", change=35.0, amount=9e8, day=20),   # 晚公告、金额大
        ]
        quotes = {"002132": snapshot("002132"), "002044": snapshot("002044")}
        candidates, _stats = build_candidates(forecasts, quotes, [])
        self.assertEqual([c.code for c in candidates], ["002044", "002132"])

    def test_extreme_change_gets_a_note(self):
        forecasts = [forecast(change=900.0, amount=5e8)]
        candidates, _stats = build_candidates(forecasts, {"002132": snapshot()}, [])
        self.assertTrue(any("极端值" in n for n in candidates[0].notes))


if __name__ == "__main__":
    unittest.main()


class OneOffOrderingTests(unittest.TestCase):
    def test_one_off_candidates_are_downweighted_not_dropped(self):
        """一次性因素（重组/处置/补助）的候选应排后面，但仍然保留。"""
        from research.events import Forecast
        import datetime

        def make(code, reason, amount):
            return Forecast(code=code, name="测试",
                            notice_date=datetime.date(2026, 7, 15), report_period="2026-06-30",
                            metric="归属于上市公司股东的净利润", forecast_type="预增",
                            change_lower=50.0, change_upper=60.0, reason=reason,
                            source_url="", amount_lower=amount, amount_upper=amount)

        forecasts = [
            make("002132", "公司完成重大资产重组，置入新业务", 9e8),
            make("002044", "主营业务收入增长，销量提升", 9e8),
        ]
        quotes = {"002132": snapshot("002132"), "002044": snapshot("002044")}
        candidates, _stats = build_candidates(forecasts, quotes, [])
        self.assertEqual(len(candidates), 2)
        self.assertEqual([c.code for c in candidates], ["002044", "002132"])
        self.assertTrue(candidates[1].one_off)


class AffordabilityTests(unittest.TestCase):
    def test_lot_cost_above_position_cap_is_flagged_and_sorted_last(self):
        """一手金额超过单只仓位上限 = 账户建不了仓，应标记并排后。"""
        import datetime

        from research.events import Forecast

        def make(code, price):
            return Forecast(code=code, name="测试", notice_date=datetime.date(2026, 7, 15),
                            report_period="2026-06-30", metric="归属于上市公司股东的净利润",
                            forecast_type="预增", change_lower=40.0, change_upper=50.0,
                            reason="", source_url="", amount_lower=2e8, amount_upper=2e8)

        forecasts = [make("002132", 10.0), make("600519", 300.0)]
        quotes = {"002132": snapshot("002132", price=10.0),
                  "600519": snapshot("600519", price=300.0)}
        # 本金 10 万、单只上限 5% → 一手 30000 元的（占 30%）买不进
        candidates, _stats = build_candidates(forecasts, quotes, [], capital=100000.0,
                                              position_pct=0.05)
        self.assertEqual([c.code for c in candidates], ["002132", "600519"])
        self.assertFalse(candidates[0].unaffordable)
        self.assertTrue(candidates[1].unaffordable)
        self.assertAlmostEqual(candidates[1].lot_cost_pct, 0.30, places=2)
        self.assertTrue(any("买不进" in n for n in candidates[1].notes))

    def test_without_capital_no_affordability_judgement(self):
        forecasts = [forecast(code="600519", change=40.0, amount=2e8)]
        quotes = {"600519": snapshot("600519", price=300.0)}
        candidates, _stats = build_candidates(forecasts, quotes, [], capital=0.0)
        self.assertFalse(candidates[0].unaffordable)
        self.assertIsNone(candidates[0].lot_cost_pct)


class SortingTests(unittest.TestCase):
    def test_mild_change_candidates_come_first(self):
        """同档之内按公告日新的先看（工作流顺序）。

        早先这里是「温和优先」（幅度小的排前面）。那条规则来自 v3 的事件研究，
        完整池的排序对照把它否掉了：四种排序里随机反而最好
        （docs/backtest-v5.md），所以不再用收益的语言排工作流。
        """
        from research.events import Forecast
        import datetime

        def make(code, change, day):
            return Forecast(code=code, name="测试", notice_date=datetime.date(2026, 7, day),
                            report_period="2026-06-30", metric="归属于上市公司股东的净利润",
                            forecast_type="预增", change_lower=change, change_upper=change + 5,
                            reason="", source_url="", amount_lower=2e8, amount_upper=2e8)

        forecasts = [make("002132", 900.0, 10), make("002044", 35.0, 20),
                     make("600519", 120.0, 15)]
        quotes = {c: snapshot(c, price=10.0) for c in ("002132", "002044", "600519")}
        candidates, _stats = build_candidates(forecasts, quotes, [], capital=1000000.0)
        self.assertEqual([c.code for c in candidates], ["002044", "600519", "002132"])

    def test_unaffordable_comes_first_in_sort_key(self):
        from research.events import Forecast
        import datetime

        def make(code, change):
            return Forecast(code=code, name="测试", notice_date=datetime.date(2026, 7, 15),
                            report_period="2026-06-30", metric="归属于上市公司股东的净利润",
                            forecast_type="预增", change_lower=change, change_upper=change + 5,
                            reason="", source_url="", amount_lower=2e8, amount_upper=2e8)

        forecasts = [make("600519", 35.0), make("002132", 36.0)]
        quotes = {"600519": snapshot("600519", price=300.0), "002132": snapshot("002132", price=10.0)}
        candidates, _stats = build_candidates(forecasts, quotes, [], capital=100000.0,
                                              position_pct=0.05)
        # 幅度更小的 600519 买不进 → 排到后面
        self.assertEqual([c.code for c in candidates], ["002132", "600519"])
