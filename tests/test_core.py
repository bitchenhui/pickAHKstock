import unittest
from datetime import date, timedelta

from retail_agent.core import Bar, Profile, Quote, analyze, rank, shortlist


DAY = date(2026, 10, 9)


def rising_bars(count=90, *, volume=1000, last_volume=1500):
    bars = []
    for i in range(count):
        close = 10 + i * 0.06
        vol = last_volume if i == count - 1 else volume
        bars.append(Bar(DAY - timedelta(days=count - 1 - i), close - 0.06,
                        close + 0.02, close - 0.12, close, vol, 50_000_000))
    return bars


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.bars = rising_bars()
        self.a = Quote("A", "002001", "测试股份", self.bars[-1].close, 80_000_000, 8_000_000_000)

    def test_small_cap_main_board_and_sample(self):
        others = [Quote("A", "688001", "科创", 10, 80_000_000, 8e9),
                  Quote("A", "002002", "ST某公司", 10, 80_000_000, 8e9),
                  Quote("A", "002003", "超大", 10, 80_000_000, 8e10)]
        self.assertEqual(shortlist(others + [self.a], "A"), [self.a])

    def test_breakout_and_budget_bound(self):
        c = analyze(self.a, self.bars, Profile(capital_cny=30_000))
        self.assertIsNotNone(c)
        self.assertEqual(c.signal, "放量突破")
        self.assertEqual(c.shares % 100, 0)
        self.assertLessEqual(c.notional, 6_000)
        self.assertLessEqual(c.risk_amount, 150)

    def test_hk_lot_unknown_and_oversized(self):
        hk = Quote("HK", "01234", "样本", self.a.price, 40_000_000)
        unknown = analyze(hk, self.bars, Profile())
        self.assertEqual(unknown.shares, 0)
        self.assertIn("未提供", unknown.status)
        oversized = analyze(hk, self.bars, Profile(hk_lots={"01234": 10000}))
        self.assertEqual(oversized.shares, 0)
        self.assertIn("一手", oversized.status)

    def test_reject_incomplete_or_mismatched_or_stale_history(self):
        self.assertIsNone(analyze(self.a, self.bars[-79:], Profile()))
        mismatch = Quote("A", self.a.code, self.a.name, self.a.price * 1.25, self.a.turnover, self.a.market_cap)
        self.assertIsNone(analyze(mismatch, self.bars, Profile()))
        older = [Bar(b.day - timedelta(days=2), b.open, b.high, b.low, b.close, b.volume, b.turnover)
                 for b in self.bars]
        result = rank([self.a, Quote("A", "600001", "旧记录", self.a.price, 80e6, 8e9)],
                      {"002001": self.bars, "600001": older}, Profile())
        self.assertEqual([c.quote.code for c in result], ["002001"])

    def test_reject_flat_untrending_and_corporate_action(self):
        flat = [Bar(b.day, 10, 10.1, 9.9, 10, 1000, 50e6) for b in self.bars]
        self.assertIsNone(analyze(Quote("A", self.a.code, self.a.name, 10, 80e6, 8e9), flat, Profile()))
        broken = list(self.bars)
        b = broken[-3]
        broken[-3] = Bar(b.day, b.open, b.high, b.low, b.close * 0.5, b.volume, b.turnover)
        self.assertIsNone(analyze(self.a, broken, Profile()))


if __name__ == "__main__":
    unittest.main()
