import unittest
import unittest.mock
from datetime import date

from research import inputs


def history(pb_values, pe_values=None):
    rows, day = [], date(2023, 1, 2)
    for index, pb in enumerate(pb_values):
        day = date(2023, 1, 2).toordinal() + index * 2
        rows.append({"d": date.fromordinal(day).isoformat(),
                     "pe": (pe_values[index] if pe_values else None), "pb": pb})
    return rows


class ValuationTests(unittest.TestCase):
    def test_percentile_within_recent_window(self):
        rows = history([float(i) for i in range(100)])
        with unittest.mock.patch.object(inputs, "valuation_history", lambda code: rows):
            out = inputs.valuation_percentile("600001")
        self.assertEqual(out["pb_percentile"], 99.0)       # 当前值是这 100 天里最高的
        self.assertEqual(out["pb"], 99.0)
        self.assertEqual(out["status"], "有数据")

    def test_negative_pe_has_no_percentile(self):
        rows = history([1.0] * 80, pe_values=[-5.0] * 80)
        with unittest.mock.patch.object(inputs, "valuation_history", lambda code: rows):
            out = inputs.valuation_percentile("600001")
        self.assertIsNone(out["pe_percentile"])
        self.assertIsNotNone(out["pb_percentile"])

    def test_hk_has_no_valuation_history(self):
        out = inputs.valuation_percentile("03323", market="HK")
        self.assertEqual(out["status"], "无数据")

    def test_missing_history_is_reported_not_guessed(self):
        with unittest.mock.patch.object(inputs, "valuation_history", lambda code: []):
            out = inputs.valuation_percentile("600999")
        self.assertEqual(out["status"], "无数据")
        self.assertIsNone(out.get("pb_percentile"))


class HolderTests(unittest.TestCase):
    def test_from_dedicated_holder_scan(self):
        scans = {"holders": [
            {"side": "减持", "stage": "计划", "title": "关于股东减持计划的公告", "date": "2026-10-01"},
            {"side": "减持", "stage": "进展", "title": "关于股东减持进展的公告", "date": "2026-10-15"},
            {"side": "增持", "stage": "完成", "title": "关于董事增持完成的公告", "date": "2026-10-20"},
            {"side": "回购", "stage": "", "title": "关于回购公司股份的公告", "date": "2026-10-22"},
        ]}
        out = inputs.holder_changes(scans)
        self.assertEqual((out["buy_count"], out["sell_count"], out["buyback_count"]), (1, 2, 1))
        self.assertEqual(out["net_signal"], -1)

    def test_no_holder_announcements(self):
        out = inputs.holder_changes({"risks": [{"title": "关于股东增持计划的公告"}]})
        self.assertEqual(out["net_signal"], 0)      # 风险扫描结果里提取不出增减持

    def test_equal_counts_are_neutral(self):
        scans = {"holders": [{"side": "增持"}, {"side": "减持"}]}
        self.assertEqual(inputs.holder_changes(scans)["net_signal"], 0)


class VolumeTests(unittest.TestCase):
    def _trend(self, last_twenty, base=100.0, count=100):
        from datetime import timedelta

        day0 = date(2026, 6, 1)
        volumes = [base] * (count - len(last_twenty)) + last_twenty
        bars = [{"day": (day0 + timedelta(days=i)).isoformat(), "volume": volume}
                for i, volume in enumerate(volumes)]
        with unittest.mock.patch("research.ths.fetch_daily_history", lambda code, years: bars):
            return inputs.volume_trend("600001")

    def test_heavy_volume(self):
        out = self._trend([200.0] * 20)
        self.assertEqual(out["trend"], "显著放量")
        self.assertAlmostEqual(out["vol_ratio_20_60"], 200 / ((40 * 100 + 20 * 200) / 60))

    def test_shrinking_volume(self):
        self.assertEqual(self._trend([50.0] * 20)["trend"], "显著缩量")
        self.assertEqual(self._trend([98.0] * 20)["trend"], "持平")

    def test_short_history_or_hk_is_empty(self):
        self.assertEqual(self._trend([100.0] * 20, count=40), {})
        self.assertEqual(inputs.volume_trend("03323", market="HK"), {})


class DemandTests(unittest.TestCase):
    def test_mapping_is_names_only(self):
        out = inputs.demand_check("电池")
        self.assertEqual(out["status"], "仅有指标名，未接数据")
        self.assertIn("动力电池", out["indicator"])

    def test_unmapped_industry(self):
        self.assertEqual(inputs.demand_check("未知行业")["status"], "未映射")


class FillTests(unittest.TestCase):
    def test_fill_does_not_claim_northbound(self):
        from research.card import Card

        card = Card(market="A", code="600001", name="测试", status="观察", view="")
        with unittest.mock.patch.object(inputs, "valuation_percentile",
                                        lambda code, market="A": {"status": "有数据"}):
            inputs.fill_inputs(card)
        self.assertEqual(sorted(card.inputs), ["as_of", "demand", "holders", "valuation", "volume"])


if __name__ == "__main__":
    unittest.main()
