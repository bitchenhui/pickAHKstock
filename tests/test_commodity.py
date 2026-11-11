import unittest
import unittest.mock

from research import commodity
from research.card import Card


def rows(prices: list[float], start="2026-01-01") -> list[dict]:
    from datetime import date, timedelta

    day0 = date.fromisoformat(start)
    return [{"day": (day0 + timedelta(days=i)).isoformat(), "symbol": "LC", "price": p}
            for i, p in enumerate(prices)]


class ChangesTests(unittest.TestCase):
    def test_changes_full_window(self):
        stats = commodity.changes(rows([100.0] * 251 + [150.0]))
        self.assertEqual(stats["day"], "2026-09-09")   # 2026-01-01 + 251 天
        self.assertAlmostEqual(stats["latest"], 150.0)
        self.assertAlmostEqual(stats["chg_30d"], 0.5)      # 100 → 150
        self.assertAlmostEqual(stats["chg_250d"], 0.5)
        self.assertFalse(stats["chg_30d_short"])
        self.assertFalse(stats["chg_250d_short"])
        self.assertAlmostEqual(stats["position_250d"], 1.0)   # 在一年最高点

    def test_changes_short_window_flagged(self):
        stats = commodity.changes(rows([100.0] * 40 + [120.0]))
        self.assertTrue(stats["chg_250d_short"])          # 不足 250 日，用可得窗口
        self.assertFalse(stats["chg_30d_short"])
        self.assertAlmostEqual(stats["position_250d"], 1.0)

    def test_changes_empty(self):
        self.assertEqual(commodity.changes([]), {})


class FillTests(unittest.TestCase):
    def test_fill_writes_card_and_marks_proxies(self):
        card = Card(market="A", code="603026", name="石大胜华", status="观察", view="")
        with unittest.mock.patch.object(commodity, "series",
                                        lambda symbol, lookback_days=400: rows([80.0] * 200 + [90.0])):
            out = commodity.fill_commodities(card)
        self.assertIn("LC", out["items"])
        item = card.commodities["items"]["LC"]
        self.assertIn("代理", item["name"])               # 代理关系必须写明
        self.assertAlmostEqual(item["latest"], 90.0)
        self.assertEqual(len(item["series_tail"]), 10)

    def test_fill_raises_for_unmapped_code(self):
        card = Card(market="A", code="600999", name="无映射", status="观察", view="")
        with self.assertRaises(KeyError):
            commodity.fill_commodities(card)


if __name__ == "__main__":
    unittest.main()
