import unittest
import unittest.mock
from datetime import date

from research import regime


class MarketRegimeTests(unittest.TestCase):
    def _regime(self, closes, as_of=date(2026, 10, 30)):
        """用一段收盘价序列构造市场状态（不联网）。"""
        bars = [{"day": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "close": close}
                for i, close in enumerate(closes)]
        with unittest.mock.patch.object(regime, "index_bars", lambda start, end: bars):
            return regime.market_regime(as_of)

    def test_down_market_when_below_ma_and_falling(self):
        closes = [100.0] * 80 + [90.0] * 20
        result = self._regime(closes)
        self.assertEqual(result.state, "下行")
        self.assertTrue(result.down)
        self.assertAlmostEqual(result.position_factor(), 0.5)

    def test_up_market_when_rising(self):
        closes = [100.0] * 80 + [120.0] * 20
        result = self._regime(closes)
        self.assertEqual(result.state, "上行")
        self.assertAlmostEqual(result.position_factor(), 1.0)

    def test_flat_market_is_neutral(self):
        closes = [100.0] * 100
        result = self._regime(closes)
        self.assertEqual(result.state, "震荡")
        self.assertAlmostEqual(result.position_factor(), 1.0)

    def test_insufficient_data_says_unknown(self):
        with unittest.mock.patch.object(regime, "index_bars", lambda start, end: []):
            result = regime.market_regime(date(2026, 10, 30))
        self.assertEqual(result.state, "未知")
        self.assertFalse(result.down)

    def _hk_regime(self, closes, as_of=date(2026, 11, 11)):
        bars = [{"day": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "close": close}
                for i, close in enumerate(closes)]
        with unittest.mock.patch.object(regime, "hk_index_bars", lambda start, end: bars):
            return regime.market_regime(as_of, market="HK")

    def test_hk_uses_hangseng_with_caveat(self):
        closes = [25000.0] * 80 + [22000.0] * 20
        result = self._hk_regime(closes)
        self.assertEqual(result.state, "下行")
        self.assertEqual(result.index, "恒生指数")
        self.assertIn("恒生指数", result.note)
        # 港股系数照搬 A 股，必须在口径里说清
        self.assertIn("未在港股事件池单独验证", result.caveat)
        self.assertAlmostEqual(result.position_factor(), 0.5)
        data = result.to_dict()
        self.assertEqual(data["index"], "恒生指数")
        self.assertTrue(data["caveat"])

    def test_hk_flat_market_no_caveat_trigger(self):
        result = self._hk_regime([24000.0] * 100)
        self.assertEqual(result.state, "震荡")
        self.assertEqual(result.position_factor(), 1.0)

    def test_a_share_has_no_caveat(self):
        result = self._regime([100.0] * 100)
        self.assertEqual(result.index, "沪深300")
        self.assertEqual(result.caveat, "")


if __name__ == "__main__":
    unittest.main()
