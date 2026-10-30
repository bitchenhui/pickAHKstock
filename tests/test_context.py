import unittest
from datetime import date

from research.analysts import change, history
from research.card import Card
from research.context import CapitalTrack, _parse_amount


class ContextTests(unittest.TestCase):
    def test_parse_amount_units(self):
        self.assertAlmostEqual(_parse_amount("-15.95亿"), -1.595e9)
        self.assertAlmostEqual(_parse_amount("3242.01万"), 3.24201e7)
        self.assertAlmostEqual(_parse_amount("1234.56"), 1234.56)
        self.assertIsNone(_parse_amount("False"))
        self.assertIsNone(_parse_amount(""))
        self.assertIsNone(_parse_amount("-"))

    def test_free_cash_flow_is_operating_minus_capex(self):
        track = CapitalTrack(code="002044", periods=["2026-09-30"],
                             capex=["4.13亿"], operating_cf=["-1.25亿"])
        self.assertAlmostEqual(track.capex_at(), 4.13e8)
        self.assertAlmostEqual(track.operating_cf_at(), -1.25e8)
        self.assertAlmostEqual(track.fcf_at(), -1.25e8 - 4.13e8)

    def test_free_cash_flow_none_when_incomplete(self):
        track = CapitalTrack(code="x", capex=["4.13亿"])
        self.assertIsNone(track.fcf_at())


class ExpectationHistoryTests(unittest.TestCase):
    def test_change_refuses_single_snapshot(self):
        import research.analysts as analysts

        original = analysts.HISTORY_DIR
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            try:
                analysts.HISTORY_DIR = Path(tmp)
                (Path(tmp) / "A_002044_2026-10-30.json").write_text(
                    '{"day": "2026-10-30", "code": "002044", "coverage": 6,'
                    ' "eps_forecast": [{"年度": "2026", "均值": 0.11}],'
                    ' "latest_rating": ""}', encoding="utf-8")
                result = change("002044")
                self.assertFalse(result["available"])
                self.assertIn("不足两份", result["note"])
            finally:
                analysts.HISTORY_DIR = original

    def test_change_computes_eps_revision(self):
        import research.analysts as analysts
        import tempfile
        from pathlib import Path

        original = analysts.HISTORY_DIR
        with tempfile.TemporaryDirectory() as tmp:
            try:
                analysts.HISTORY_DIR = Path(tmp)
                for day, mean in (("2026-10-30", 0.10), ("2026-11-30", 0.12)):
                    (Path(tmp) / f"A_002044_{day}.json").write_text(
                        '{"day": "%s", "code": "002044", "coverage": 6,'
                        ' "eps_forecast": [{"年度": "2026", "均值": %s}],'
                        ' "latest_rating": "买入"}' % (day, mean), encoding="utf-8")
                result = change("002044")
                self.assertTrue(result["available"])
                self.assertAlmostEqual(result["eps_mean_change_pct"], 20.0, places=1)
                self.assertEqual(result["rating_after"], "买入")
            finally:
                analysts.HISTORY_DIR = original


class CardContextTests(unittest.TestCase):
    def test_card_roundtrip_keeps_capital_and_peers(self):
        card = Card(market="A", code="002044", name="美年健康")
        card.capital = {"periods": ["2026-09-30"], "fcf_label": "-5.38 亿"}
        card.peers = {"rank": "16.0/54", "市盈率-TTM": 56.2}
        restored = Card.from_dict(card.to_dict())
        self.assertEqual(restored.capital["fcf_label"], "-5.38 亿")
        self.assertEqual(restored.peers["rank"], "16.0/54")


if __name__ == "__main__":
    unittest.main()
