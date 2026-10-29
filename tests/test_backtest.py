import unittest
from datetime import date

from research.backtest import (Group, _bootstrap_median_diff, _window_return,
                               forward_return, is_strong, _compare,
                               _notice_date, pre_announcement_runup)


def bars(closes: list[float], start: str = "2026-05-01") -> list[dict]:
    out = []
    for index, close in enumerate(closes):
        day = date.fromisoformat(start)
        out.append({"day": (day.replace(day=day.day)).isoformat() if False else
                    f"2026-05-{index + 1:02d}", "open": close, "close": close,
                    "high": close, "low": close})
    return out


class BacktestTests(unittest.TestCase):
    def test_is_strong_requires_type_and_magnitude(self):
        self.assertTrue(is_strong({"PREDICT_TYPE": "预增", "ADD_AMP_LOWER": 60}, 50))
        self.assertFalse(is_strong({"PREDICT_TYPE": "预增", "ADD_AMP_LOWER": 20}, 50))
        self.assertFalse(is_strong({"PREDICT_TYPE": "预减", "ADD_AMP_LOWER": 80}, 50))
        self.assertFalse(is_strong({"PREDICT_TYPE": "扭亏", "ADD_AMP_LOWER": None}, 50))

    def test_forward_return_enters_after_announcement(self):
        # 公告日 2026-05-03；次一交易日 05-04 收盘买入，持有 2 个交易日到 05-06
        series = [{"day": "2026-05-01", "close": 10.0}, {"day": "2026-05-03", "close": 10.0},
                  {"day": "2026-05-04", "close": 20.0}, {"day": "2026-05-05", "close": 22.0},
                  {"day": "2026-05-06", "close": 30.0}]
        value = forward_return(series, "2026-05-03", 2)
        self.assertAlmostEqual(value, 0.5)   # 20 -> 30

    def test_forward_return_without_bars_is_none(self):
        self.assertIsNone(forward_return([], "2026-05-03", 20))

    def test_notice_date_parsing(self):
        self.assertEqual(_notice_date({"NOTICE_DATE": "2026-04-28 00:00:00"}), date(2026, 4, 28))
        self.assertIsNone(_notice_date({"NOTICE_DATE": ""}))

    def test_compare_splits_cohorts_and_computes_excess(self):
        prepared = []
        for index in range(6):
            strong = index < 3
            row = {"PREDICT_TYPE": "预增" if strong else "预减",
                   "ADD_AMP_LOWER": 80 if strong else -80}
            series = [{"day": "2026-04-01", "close": 10.0},
                      {"day": "2026-04-02", "close": 10.0},
                      {"day": "2026-04-03", "close": 12.0 if strong else 9.0},
                      {"day": "2026-04-04", "close": 12.0 if strong else 9.0}]
            prepared.append((row, date(2026, 4, 1), series))
        stats, excess = _compare(prepared, 50.0, 2)
        self.assertEqual(stats["强预告组"]["n"], 3)
        self.assertEqual(stats["非强预告（对照）"]["n"], 3)
        self.assertGreater(stats["强预告组"]["median"], stats["非强预告（对照）"]["median"])
        self.assertIn("强预告 − 对照（中位数）", excess)

    def test_group_stats_on_empty(self):
        stats = Group("x").stats()
        self.assertEqual(stats["n"], 0)
        self.assertIsNone(stats["median"])

    def test_pre_announcement_runup(self):
        series = [{"day": "2026-04-01", "close": 10.0},
                  {"day": "2026-04-02", "close": 11.0},
                  {"day": "2026-04-03", "close": 12.0}]
        # 公告日之前的最后一根是 04-02（收盘 11），窗口起点是 04-01（收盘 10）→ +10%
        self.assertAlmostEqual(pre_announcement_runup(series, "2026-04-03"), 0.1)
        self.assertIsNone(pre_announcement_runup([], "2026-04-03"))

    def test_runup_filter_separates_already_run_up(self):
        prepared = []
        for index in range(4):
            row = {"PREDICT_TYPE": "预增", "ADD_AMP_LOWER": 80}
            # 前两只在公告前涨了 30%，后两只没涨
            up = index < 2
            closes = [10.0, 13.0 if up else 10.0, 13.0 if up else 10.0,
                      13.0 if up else 10.0, 13.0 if up else 10.0, 12.0 if up else 11.0]
            series = [{"day": f"2026-04-0{day}", "close": close}
                      for day, close in enumerate(closes, start=1)]
            prepared.append((row, date(2026, 4, 3), series))
        stats, excess = _compare(prepared, 50.0, 2, None, 0.15)
        self.assertEqual(stats["强预告组"]["n"], 2)
        self.assertEqual(stats["强预告但公告前已涨透"]["n"], 2)

    def test_bootstrap_ci_contains_zero_for_noisy_samples(self):
        strong = [0.10, -0.05, 0.02, 0.11, -0.03, 0.08, 0.01, -0.02, 0.05, 0.03]
        control = [0.0, 0.01, -0.02, 0.03, 0.0, 0.02, -0.01, 0.01, 0.04, -0.03]
        low, high = _bootstrap_median_diff(strong, control)
        self.assertIsNotNone(low)
        self.assertLessEqual(low, high)

    def test_window_return_matches_forward_return(self):
        bars = [{"day": "2026-05-01", "close": 10.0}, {"day": "2026-05-02", "close": 10.0},
                {"day": "2026-05-03", "close": 20.0}, {"day": "2026-05-04", "close": 30.0}]
        # 从 05-01 之后的第一根（05-02 收盘 10）持有到 05-04（收盘 30）
        self.assertAlmostEqual(_window_return(bars, "2026-05-01", 2), 2.0)
        self.assertIsNone(_window_return([], "2026-05-01", 2))


if __name__ == "__main__":
    unittest.main()
