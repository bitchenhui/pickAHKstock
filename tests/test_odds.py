import unittest
from datetime import date, timedelta

from research.odds import (INFLECTION_TYPES, ForwardWindow, OddsHit, RunupResult,
                           annualized_track, cap_at, evaluate, forward_window,
                           inflection_type, prefilter_cap, runup, screen,
                           shares_from, what_needed)
from research.quotes import Snapshot


def series(closes: list[float], start="2026-01-01") -> list[dict]:
    """连续交易日的日线：close 与 high 同值，日期逐日 +1。"""
    day0 = date.fromisoformat(start)
    return [{"day": (day0 + timedelta(days=i)).isoformat(),
             "open": close, "close": close, "high": close, "low": close}
            for i, close in enumerate(closes)]


def snapshot(cap_yi: float, price: float = 10.0, code: str = "600000") -> Snapshot:
    return Snapshot(market="A", code=code, name="测试", price=price,
                    turnover=1e8, market_cap=cap_yi * 1e8, pe=None, pb=None,
                    change_pct=None, as_of="")


class InflectionTests(unittest.TestCase):
    def test_niukui_without_magnitude_is_classified(self):
        # 现有发现层会丢的 case：扭亏行幅度为空
        row = {"PREDICT_TYPE": "扭亏", "ADD_AMP_LOWER": None,
               "PREYEAR_SAME_PERIOD": -5e7, "PREDICT_AMT_LOWER": 6e7}
        self.assertEqual(inflection_type(row), "扭亏")

    def test_negative_prev_with_positive_lower_is_niukui(self):
        row = {"PREDICT_TYPE": "预增", "ADD_AMP_LOWER": 200,
               "PREYEAR_SAME_PERIOD": -3e7, "PREDICT_AMT_LOWER": 4e7}
        self.assertEqual(inflection_type(row), "扭亏")

    def test_declared_types_pass_through(self):
        self.assertEqual(inflection_type({"PREDICT_TYPE": "减亏"}), "减亏")
        self.assertEqual(inflection_type({"PREDICT_TYPE": "预增"}), "预增")

    def test_bad_news_is_other(self):
        for ftype in ("首亏", "增亏", "续亏", "预减", ""):
            self.assertEqual(inflection_type({"PREDICT_TYPE": ftype}), "其他")


class RunupTests(unittest.TestCase):
    def test_runup_over_full_window(self):
        closes = [10.0] * 250 + [15.0, 20.0]      # 250 根横盘 + 涨到 20
        notice = series(closes)[-1]["day"]
        result = runup(series(closes), notice)
        self.assertAlmostEqual(result.value, 15.0 / 10.0 - 1)
        self.assertFalse(result.short_lookback)

    def test_runup_marks_short_window(self):
        closes = [10.0] * 150 + [12.0, 12.0]      # 公告日前一根收在 12
        notice = series(closes)[-1]["day"]
        result = runup(series(closes), notice)
        self.assertAlmostEqual(result.value, 12.0 / 10.0 - 1)
        self.assertTrue(result.short_lookback)

    def test_runup_below_min_bars_is_none(self):
        closes = [10.0] * 100 + [12.0]
        notice = series(closes)[-1]["day"]
        self.assertIsNone(runup(series(closes), notice).value)

    def test_runup_without_prices_is_none(self):
        self.assertIsNone(runup([], "2026-01-01").value)


class ForwardWindowTests(unittest.TestCase):
    def test_t_plus_one_and_max_gain(self):
        # 公告日 2026-01-02；次一交易日 01-03 收盘 20 买入（T+1，买入当根不计入持有）
        # 持有 01-04（30）与 01-05（25）
        closes = [10.0, 10.0, 20.0, 30.0, 25.0]
        result = forward_window(series(closes), "2026-01-02", window=3)
        self.assertAlmostEqual(result.max_gain_close, 30.0 / 20.0 - 1)
        self.assertAlmostEqual(result.end_return, 25.0 / 20.0 - 1)
        self.assertEqual(result.days, 2)

    def test_high_uses_intraday(self):
        bars = series([10.0, 10.0, 20.0, 25.0])
        bars[3]["high"] = 40.0                 # 持有段第一根盘中摸到 40
        result = forward_window(bars, "2026-01-02", window=5)
        self.assertAlmostEqual(result.max_gain_high, 40.0 / 20.0 - 1)
        self.assertAlmostEqual(result.max_gain_close, 25.0 / 20.0 - 1)

    def test_no_bars_after_notice_is_empty(self):
        self.assertFalse(forward_window(series([10.0, 10.0]), "2026-01-05"))


class ScenarioTests(unittest.TestCase):
    def test_shares_from_a_snapshot_by_cap_over_price(self):
        snap = snapshot(100.0, price=10.0)
        self.assertAlmostEqual(shares_from(snap), 10e8)

    def test_cap_at(self):
        self.assertAlmostEqual(cap_at(10e8, 8.5), 85e8)

    def test_what_needed(self):
        # 100 亿市值 × 10 倍 / 25 倍 PE = 40 亿年化利润
        self.assertAlmostEqual(what_needed(100e8, 10.0, 25.0), 40e8)

    def test_annualized_track(self):
        self.assertAlmostEqual(annualized_track(9e8, "2026-09-30"), 12e8)   # 三季累计 × 12/9
        self.assertAlmostEqual(annualized_track(6e8, "2026-06-30"), 12e8)
        self.assertIsNone(annualized_track(-5e8, "2026-06-30"))             # 减亏路径无正基准
        self.assertIsNone(annualized_track(5e8, "2026-05-31"))              # 非标准报告期


# 251 根日线（2026-01-01 起）的最后一根：公告日落在序列末尾，
# runup 才有完整的回看窗口
NOTICE_DAY = (date(2026, 1, 1) + timedelta(days=250)).isoformat()


def make_rows():
    return [
        {"SECURITY_CODE": "600001", "SECURITY_NAME_ABBR": "甲", "PREDICT_TYPE": "扭亏",
         "ADD_AMP_LOWER": None, "PREYEAR_SAME_PERIOD": -5e7, "PREDICT_AMT_LOWER": 8e7,
         "NOTICE_DATE": NOTICE_DAY, "REPORT_DATE": "2026-09-30"},
        {"SECURITY_CODE": "600002", "SECURITY_NAME_ABBR": "乙", "PREDICT_TYPE": "预增",
         "ADD_AMP_LOWER": 60, "PREYEAR_SAME_PERIOD": 2e8, "PREDICT_AMT_LOWER": 3.2e8,
         "NOTICE_DATE": NOTICE_DAY, "REPORT_DATE": "2026-09-30"},
        {"SECURITY_CODE": "600003", "SECURITY_NAME_ABBR": "丙", "PREDICT_TYPE": "首亏",
         "ADD_AMP_LOWER": -200, "PREYEAR_SAME_PERIOD": 1e8, "PREDICT_AMT_LOWER": -8e7,
         "NOTICE_DATE": NOTICE_DAY, "REPORT_DATE": "2026-09-30"},
        {"SECURITY_CODE": "600004", "SECURITY_NAME_ABBR": "丁", "PREDICT_TYPE": "预增",
         "ADD_AMP_LOWER": 60, "PREYEAR_SAME_PERIOD": 1e8, "PREDICT_AMT_LOWER": 1.6e8,
         "NOTICE_DATE": NOTICE_DAY, "REPORT_DATE": "2026-09-30"},
    ]


def make_snapshots():
    # 公告日收盘 10 元（series 的回看段），市值 = 股本 × 10
    return {"600001": snapshot(60.0, price=12.0),    # 股本 5 亿股 → 公告日 50 亿：通过
            "600002": snapshot(400.0, price=8.0),    # 股本 50 亿股 → 500 亿：超带
            "600003": snapshot(60.0, price=12.0),
            "600004": snapshot(60.0, price=12.0)}


def make_prices_map():
    flat_then_up = series([10.0] * 250 + [12.0])     # 回看 +20%，通过 80% 阈值
    ramped = series([10.0] * 100 + [25.0] * 151)     # 已涨 150%，刷掉
    return {"600001": flat_then_up, "600002": flat_then_up,
            "600003": flat_then_up, "600004": ramped}


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.hits = evaluate(make_rows(), make_snapshots(), make_prices_map())

    def test_pass_candidate_has_no_failed(self):
        hit = next(h for h in self.hits if h.code == "600001")
        self.assertTrue(hit.passes)
        self.assertEqual(hit.inflection, "扭亏")
        # 股本 5 亿股 × 公告日收盘 12 元（notice 当根收盘计入市值）
        self.assertAlmostEqual(hit.cap_at, 60e8)
        self.assertAlmostEqual(hit.runup, 0.0)   # 回看段横盘，涨幅 0

    def test_cap_band_failure_recorded(self):
        hit = next(h for h in self.hits if h.code == "600002")
        self.assertFalse(hit.passes)
        self.assertTrue(any("市值" in f for f in hit.failed))

    def test_inflection_failure_recorded(self):
        hit = next(h for h in self.hits if h.code == "600003")
        self.assertFalse(hit.passes)
        self.assertTrue(any("拐点类型" in f for f in hit.failed))

    def test_runup_failure_recorded(self):
        hit = next(h for h in self.hits if h.code == "600004")
        self.assertFalse(hit.passes)
        self.assertTrue(any("已涨" in f for f in hit.failed))

    def test_screen_returns_only_passes(self):
        passed = screen(make_rows(), make_snapshots(), make_prices_map())
        self.assertEqual([h.code for h in passed], ["600001"])

    def test_gap_10x_computed_on_positive_track(self):
        hit = next(h for h in self.hits if h.code == "600001")
        # 公告日市值 60 亿：10x@25PE 需要年化 24 亿；三季累计 0.8 亿年化 ≈1.07 亿
        self.assertAlmostEqual(hit.needed_10x, 24e8)
        self.assertAlmostEqual(hit.annualized, 8e7 * 12 / 9)
        self.assertAlmostEqual(hit.gap_10x, 24e8 / (8e7 * 12 / 9))

    def test_gap_none_for_loss_track(self):
        hit = next(h for h in self.hits if h.code == "600003")
        self.assertIsNone(hit.annualized)
        self.assertIsNone(hit.gap_10x)


class PreFilterTests(unittest.TestCase):
    def test_prefilter_widens_band(self):
        # 带宽 30-150 亿放宽 1.6 倍 → 18.75-240 亿内的当前市值都进预筛
        snapshots = {"600001": snapshot(20.0),    # 20 亿 < 18.75*1e8? 否，20 > 18.75 → 保留
                     "600002": snapshot(230.0),   # 230 < 240 → 保留
                     "600003": snapshot(300.0),   # 超放宽带 → 剔除
                     "600004": snapshot(10.0)}    # 低于放宽带 → 剔除
        out = prefilter_cap(snapshots, list(snapshots), 30e8, 150e8)
        self.assertEqual(out, ["600001", "600002"])


if __name__ == "__main__":
    unittest.main()
