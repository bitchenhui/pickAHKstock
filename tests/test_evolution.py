import tempfile
import unittest
import unittest.mock
from datetime import date, timedelta
from pathlib import Path

from research import evolution
from research.evolution import Evolution, Outcome, Prediction, current_weights


def bars(count: int, base: float = 10.0):
    """连续 count 根日线，收盘价 base + i：第 i 根的收盘一眼能对上。"""
    day0 = date(2026, 1, 5)
    return [{"day": (day0 + timedelta(days=i)).isoformat(), "close": base + i}
            for i in range(count)]


def prediction(factors, day="2026-01-01", code="600001"):
    return Prediction(day=day, code=code, name=code, market="A", rank=1, total_score=0,
                      factors=factors, price_at_call=1.0, status="观察")


def outcome(signal, excess):
    return Outcome(prediction({"强度": signal}), 0.0, excess)


class OutcomeTests(unittest.TestCase):
    def test_exit_is_the_close_sixty_bars_after_entry_not_the_latest(self):
        # 回归：以前取的是「最新收盘价」，一条预测的收益会随时间一直变
        with unittest.mock.patch("research.backtest.load_prices", lambda *a, **k: bars(100)), \
             unittest.mock.patch("research.backtest.index_bars", lambda *a, **k: bars(100, 100.0)):
            [result] = Evolution().compute_outcomes([prediction({"强度": 1})])
        self.assertEqual(result.return_pct, 600.0)      # 第 0 根 10 → 第 60 根 70
        self.assertEqual(result.excess_pct, 540.0)      # 指数同期 100 → 160，即 +60%

    def test_result_does_not_drift_as_more_bars_arrive(self):
        prices = bars(61)
        with unittest.mock.patch("research.backtest.load_prices", lambda *a, **k: prices), \
             unittest.mock.patch("research.backtest.index_bars", lambda *a, **k: bars(61, 100.0)):
            [first] = Evolution().compute_outcomes([prediction({"强度": 1})])
        with unittest.mock.patch("research.backtest.load_prices", lambda *a, **k: bars(300)), \
             unittest.mock.patch("research.backtest.index_bars", lambda *a, **k: bars(300, 100.0)):
            [later] = Evolution().compute_outcomes([prediction({"强度": 1})])
        self.assertEqual((later.return_pct, later.excess_pct), (first.return_pct, first.excess_pct))

    def test_not_matured_is_left_out(self):
        with unittest.mock.patch("research.backtest.load_prices", lambda *a, **k: bars(60)), \
             unittest.mock.patch("research.backtest.index_bars", lambda *a, **k: bars(60)):
            self.assertEqual(Evolution().compute_outcomes([prediction({"强度": 1})]), [])


class StateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_file = Path(tmp.name) / "evolution_state.json"
        patcher = unittest.mock.patch.object(evolution, "STATE_FILE", self.state_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_when_no_adjustment_has_happened(self):
        weights, generation = current_weights()
        self.assertEqual(generation, 0)
        self.assertEqual(weights, evolution.DEFAULT_WEIGHTS)
        self.assertEqual(set(weights), set(evolution.WEIGHT_EVIDENCE))   # 每个因子都有依据说明

    def test_enough_outcomes_on_both_sides_means_direction_verdict(self):
        evo = Evolution()
        results = evo.attribute([outcome(1, 3.0)] * 20 + [outcome(-1, -3.0)] * 20)
        self.assertEqual(results["强度"]["status"], "方向对")
        self.assertEqual(results["强度"]["compare"], "正信号 − 负信号")
        adjusted = evo.adjust_weights(results)
        self.assertTrue(adjusted["adjusted"])
        self.assertEqual(adjusted["generation"], 1)
        self.assertEqual(evo.weights["强度"], evolution.DEFAULT_WEIGHTS["强度"] + 0.5)
        self.assertTrue(self.state_file.exists())

    def test_no_visible_effect_halves_the_weight(self):
        evo = Evolution()
        results = evo.attribute([outcome(1, 0.2)] * 20 + [outcome(0, 0.0)] * 20)
        self.assertEqual((results["强度"]["status"], results["强度"]["compare"]),
                         ("看不出", "正信号 − 中性"))
        evo.adjust_weights(results)
        self.assertEqual(evo.weights["强度"], evolution.DEFAULT_WEIGHTS["强度"] * 0.5)

    def test_small_samples_do_not_move_weights(self):
        evo = Evolution()
        results = evo.attribute([outcome(1, 9.0)] * 5 + [outcome(-1, -9.0)] * 5)
        self.assertIn("error", results)                  # 到期结果不足 15 条：不归因
        self.assertFalse(evo.adjust_weights(results)["adjusted"])
        self.assertEqual(evo.generation, 0)

    def test_one_sided_small_group_is_left_alone(self):
        evo = Evolution()
        results = evo.attribute([outcome(1, 9.0)] * 30 + [outcome(2, 9.0)] * 5)
        self.assertEqual(results["强度"]["status"], "样本少")   # 只有正信号，没有可比的组

    def test_too_few_outcomes_gives_no_attribution(self):
        self.assertIn("error", Evolution().attribute([outcome(1, 1.0)] * 5))

    def test_adjustments_accumulate_and_survive_reload(self):
        evo = Evolution()
        evo.adjust_weights(evo.attribute([outcome(1, 3.0)] * 20 + [outcome(-1, -3.0)] * 20))
        reloaded = Evolution()
        self.assertEqual(reloaded.generation, 1)
        self.assertEqual(reloaded.weights["强度"], evo.weights["强度"])
        self.assertEqual(len(reloaded.state["history"]), 1)


class JournalTests(unittest.TestCase):
    def test_log_keeps_latest_per_code_per_day(self):
        import json

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with unittest.mock.patch.object(evolution, "JOURNAL_DIR", Path(tmp.name)):
            evo = Evolution()
            evo.log_predictions([prediction({"强度": 1}, code="600001"),
                                 prediction({"强度": -1}, code="600002")])
            evo.log_predictions([prediction({"强度": 0}, code="600001")])
            rows = json.loads((Path(tmp.name) / "2026-01-01.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted((r["code"], r["factors"]["强度"]) for r in rows),
                         [("600001", 0), ("600002", -1)])


if __name__ == "__main__":
    unittest.main()
