import unittest
import unittest.mock

from research import execution
from research.card import Card


class LiquidityGateTests(unittest.TestCase):
    def test_levels_by_ratio(self):
        self.assertEqual(execution.liquidity_gate(1e6, 1e4)["level"], "无忧")       # 100 倍
        self.assertEqual(execution.liquidity_gate(5e6, 1e5)["level"], "谨慎")        # 50 倍
        self.assertEqual(execution.liquidity_gate(1e5, 1e4)["level"], "危险")        # 10 倍

    def test_without_stake_reports_rule_only(self):
        out = execution.liquidity_gate(2e8, None)
        self.assertEqual(out["level"], "未评估")
        self.assertIn("5%", out["note"])

    def test_without_data_is_unknown(self):
        self.assertEqual(execution.liquidity_gate(None, 1000.0)["level"], "未知")


class RelativeStrengthTests(unittest.TestCase):
    def test_rs_is_stock_minus_index(self):
        stock = [{"day": f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}", "close": 10.0}
                 for i in range(60)] + [{"day": "2026-12-01", "close": 12.0}]
        index = [{"day": f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}", "close": 100.0}
                 for i in range(60)] + [{"day": "2026-12-01", "close": 95.0}]
        from research import backtest

        with unittest.mock.patch.object(backtest, "load_prices", return_value=stock), \
             unittest.mock.patch.object(backtest, "index_bars", return_value=index):
            out = execution.relative_strength("600001", "A")
        self.assertAlmostEqual(out["stock_return"], 20.0)      # 10 → 12
        self.assertAlmostEqual(out["index_return"], -5.0)     # 100 → 95
        self.assertAlmostEqual(out["rs"], 25.0)
        self.assertIn("跑赢大盘", out["note"])

    def test_short_series_returns_none(self):
        from research import backtest

        with unittest.mock.patch.object(backtest, "load_prices", return_value=[]):
            self.assertIsNone(execution.relative_strength("600001", "A"))


class FillTests(unittest.TestCase):
    def test_fill_writes_execution(self):
        card = Card(market="A", code="600001", name="测试", status="观察", view="")
        with unittest.mock.patch.object(execution, "avg_turnover_a",
                                        lambda code: {"avg_turnover": 5e7, "window": 20}), \
             unittest.mock.patch.object(execution, "relative_strength",
                                        lambda code, market, window=60: {"rs": 3.0}), \
             unittest.mock.patch.object(execution, "_stake_from_account", lambda: 1500.0):
            out = execution.fill_execution(card)
        self.assertEqual(out["liquidity"]["level"], "无忧")   # 5e7 / 1500 = 33333 倍
        self.assertEqual(card.execution["strength"]["rs"], 3.0)


if __name__ == "__main__":
    unittest.main()
