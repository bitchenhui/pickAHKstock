import unittest
from datetime import date

from research.portfolio import PortfolioConfig, Trade, simulate


def series(closes: list[float], start_day: int = 1) -> list[dict]:
    out = []
    for offset, close in enumerate(closes):
        out.append({"day": f"2026-01-{start_day + offset:02d}", "open": close,
                    "close": close, "high": close, "low": close})
    return out


def event(code: str, notice: str, change: float = 60.0, amount: float = 5e8) -> dict:
    return {"SECURITY_CODE": code, "NOTICE_DATE": notice, "ADD_AMP_LOWER": change,
            "PREDICT_TYPE": "预增", "PREDICT_AMT_LOWER": amount,
            "PREDICT_FINANCE": "归属于上市公司股东的净利润"}


class PortfolioTests(unittest.TestCase):
    def test_buys_then_sells_and_realizes_pnl(self):
        days = [f"2026-01-{d:02d}" for d in range(1, 12)]
        prices = {"002132": series([10.0] * 11)}
        config = PortfolioConfig(initial_capital=100000.0, max_positions=2,
                                 position_pct=0.2, cash_floor_pct=0.0, hold_days=3,
                                 commission_rate=0.0, stamp_duty=0.0, slippage=0.0)
        result = simulate([event("002132", "2026-01-02")], prices, days, config)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.shares % 100, 0)     # 整手
        self.assertEqual(trade.shares, 2000)        # 2 万 / 10 元
        self.assertEqual(trade.sell_day, "2026-01-06")

    def test_lot_size_and_cash_floor_limit_exposure(self):
        days = [f"2026-01-{d:02d}" for d in range(1, 12)]
        prices = {"002132": series([10.0] * 11)}
        config = PortfolioConfig(initial_capital=10000.0, max_positions=5,
                                 position_pct=0.9, cash_floor_pct=0.5, hold_days=5,
                                 commission_rate=0.0, stamp_duty=0.0, slippage=0.0)
        result = simulate([event("002132", "2026-01-02")], prices, days, config)
        self.assertLessEqual(result.trades[0].shares * 10.0, 5000.0)

    def test_fees_are_charged_on_both_sides(self):
        days = [f"2026-01-{d:02d}" for d in range(1, 12)]
        prices = {"002132": series([10.0] * 11)}
        config = PortfolioConfig(initial_capital=100000.0, hold_days=3,
                                 commission_rate=0.00025, min_commission=5.0,
                                 stamp_duty=0.001, slippage=0.0)
        result = simulate([event("002132", "2026-01-02")], prices, days, config)
        trade = result.trades[0]
        self.assertGreater(trade.fees, 0)
        self.assertAlmostEqual(result.fee_total, trade.fees, places=6)

    def test_max_positions_is_respected(self):
        days = [f"2026-01-{d:02d}" for d in range(1, 12)]
        prices = {code: series([10.0] * 11) for code in ("000001", "000002", "000003")}
        config = PortfolioConfig(initial_capital=1000000.0, max_positions=2,
                                 position_pct=0.2, cash_floor_pct=0.0, hold_days=5,
                                 commission_rate=0.0, stamp_duty=0.0, slippage=0.0)
        result = simulate([event("000001", "2026-01-02"), event("000002", "2026-01-02"),
                           event("000003", "2026-01-02")], prices, days, config)
        self.assertLessEqual(len([t for t in result.trades if not t.sell_day]), 2)

    def test_unaffordable_position_is_skipped_not_forced(self):
        days = [f"2026-01-{d:02d}" for d in range(1, 12)]
        prices = {"002132": series([5000.0] * 11)}     # 一手就超预算
        config = PortfolioConfig(initial_capital=10000.0, position_pct=0.2,
                                 cash_floor_pct=0.0, hold_days=3,
                                 commission_rate=0.0, stamp_duty=0.0, slippage=0.0)
        result = simulate([event("002132", "2026-01-02")], prices, days, config)
        self.assertEqual(result.skipped_unaffordable, 1)
        self.assertEqual(result.trades, [])

    def test_missed_share_counts_unbought_top_performers(self):
        days = [f"2026-01-{d:02d}" for d in range(1, 12)]
        prices = {"000001": series([10.0] * 11),
                  "000002": series([10.0] * 11)}
        config = PortfolioConfig(initial_capital=100000.0, hold_days=3,
                                 commission_rate=0.0, stamp_duty=0.0, slippage=0.0)
        # 只买 000001；000002 涨幅更高但没买
        prices["000002"] = series([10.0, 10.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0])
        result = simulate([event("000001", "2026-01-02"), event("000002", "2026-01-02")],
                          prices, days, config)
        self.assertGreaterEqual(result.missed_top_share, 0.0)


if __name__ == "__main__":
    unittest.main()
