import unittest

from research.discover import MIN_FORECAST_AMOUNT, build_candidates
from research.events import Forecast, metric_rank
from research.quotes import Snapshot


def snapshot(code="002132", price=10.0, turnover=5e8, cap=8e9):
    return Snapshot(market="A", code=code, name="测试", price=price, turnover=turnover,
                    market_cap=cap, pe=20.0, pb=2.0, change_pct=0.0, as_of="2026-10-28",
                    turnover_rate=1.0, lot_size=100, shares=8e8)


def forecast(code="002132", metric="归属于上市公司股东的净利润", change=60.0,
            amount=5e8, kind="预增"):
    return Forecast(code=code, name="测试", notice_date=__import__("datetime").date(2026, 7, 15),
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

    def test_small_absolute_amount_is_dropped_and_counted(self):
        forecasts = [
            forecast(code="002132", change=800.0, amount=1e6),   # 低基数暴增
            forecast(code="002044", change=40.0, amount=2e8),    # 正常
        ]
        quotes = {"002132": snapshot("002132"), "002044": snapshot("002044")}
        candidates, stats = build_candidates(forecasts, quotes, [])
        self.assertEqual([c.code for c in candidates], ["002044"])
        self.assertEqual(stats["dropped_small_amount"], 1)

    def test_amount_filter_can_be_disabled(self):
        forecasts = [forecast(change=800.0, amount=1e6)]
        candidates, stats = build_candidates(forecasts, {"002132": snapshot()}, [],
                                            min_amount=0)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(stats["dropped_small_amount"], 0)

    def test_sorting_is_by_forecast_amount_not_by_change(self):
        forecasts = [
            forecast(code="002132", change=800.0, amount=1e8),   # 幅度大但金额小
            forecast(code="002044", change=35.0, amount=9e8),    # 幅度小但金额大
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
