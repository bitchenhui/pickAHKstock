import unittest

from research.portfolio import limit_down_days, limit_pct


class LimitDownTests(unittest.TestCase):
    def test_limit_pct_by_board(self):
        self.assertAlmostEqual(limit_pct("600000"), 10.0)   # 主板
        self.assertAlmostEqual(limit_pct("000001"), 10.0)
        self.assertAlmostEqual(limit_pct("300750"), 20.0)   # 创业板
        self.assertAlmostEqual(limit_pct("688981"), 20.0)   # 科创板
        self.assertAlmostEqual(limit_pct("830799"), 30.0)   # 北交所

    def _series(self):
        return [{"day": "2026-01-02", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0},
                {"day": "2026-01-05", "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0},
                {"day": "2026-01-06", "open": 9.0, "high": 9.6, "low": 9.0, "close": 9.4}]

    def test_limit_down_close_is_locked(self):
        """收在跌停（收盘=最低）→ 那天卖不掉。"""
        locked = limit_down_days(self._series(), "600000")
        self.assertIn("2026-01-05", locked)

    def test_limit_down_that_opened_up_is_sellable(self):
        """跌停开盘但盘中打开、当日有成交 → 按能卖处理。"""
        locked = limit_down_days(self._series(), "600000")
        self.assertNotIn("2026-01-06", locked)

    def test_growth_board_uses_twenty_percent(self):
        """创业板跌 10% 不算跌停。"""
        series = [{"day": "2026-01-02", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0},
                  {"day": "2026-01-05", "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0}]
        self.assertNotIn("2026-01-05", limit_down_days(series, "300750"))
        self.assertIn("2026-01-05", limit_down_days(series, "600000"))

    def test_normal_day_is_not_locked(self):
        series = [{"day": "2026-01-02", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0},
                  {"day": "2026-01-05", "open": 10.1, "high": 10.3, "low": 9.9, "close": 10.2}]
        self.assertEqual(limit_down_days(series, "600000"), set())


if __name__ == "__main__":
    unittest.main()
