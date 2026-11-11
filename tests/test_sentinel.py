import unittest
import unittest.mock

from research import sentinel
from research.card import Card, Catalyst
from research.quotes import Snapshot


def card(price_stop=True, rs_prev=5.0, catalyst_due=False, commodity=False) -> Card:
    card = Card(market="A", code="600001", name="测试股", status="观察", view="判断")
    context = {"stop_reference": 9.0} if price_stop else {}
    card.market_context = context
    card.execution = {"strength": {"rs": rs_prev}} if rs_prev is not None else {}
    if catalyst_due:
        card.catalysts = [Catalyst(event="年报预告窗口", date="2026-01-31",
                                   status="待验证")]
    if commodity:
        card.commodities = {"items": {"LC": {"symbol": "LC", "name": "碳酸锂"}}}
    return card


def snapshot(price: float) -> Snapshot:
    return Snapshot(market="A", code="600001", name="测试股", price=price,
                    turnover=1e8, market_cap=60e8, pe=None, pb=None,
                    change_pct=None, as_of="")


class CheckTests(unittest.TestCase):
    def _run(self, cards, strength=None, commodity_stats=None):
        """统一打桩：快照价格 10.5（不触失效线），按需注入强度/盯价。"""
        from research import commodity, execution

        patches = [unittest.mock.patch.object(sentinel, "fetch_snapshots",
                                              lambda codes: {"600001": snapshot(10.5)})]
        if strength is not None:
            patches.append(unittest.mock.patch.object(
                execution, "relative_strength",
                lambda code, market, window=60: strength))
        if commodity_stats is not None:
            patches.append(unittest.mock.patch.object(
                commodity, "changes", lambda rows: commodity_stats))
            patches.append(unittest.mock.patch.object(
                commodity, "series",
                lambda symbol, lookback_days=400: [{"day": "2026-11-11"}]))
        import contextlib

        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return sentinel.check_cards(cards)

    def test_stop_reference_triggers(self):
        with unittest.mock.patch.object(sentinel, "fetch_snapshots",
                                        lambda codes: {"600001": snapshot(8.8)}):
            alerts, checked = sentinel.check_cards([card()])
        self.assertEqual(checked, 1)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].kind, "失效线")
        self.assertIn("8.8", alerts[0].detail)

    def test_no_trigger_above_stop(self):
        alerts, _ = self._run([card()])
        self.assertEqual(alerts, [])

    def test_strength_flip_triggers(self):
        # 卡上 +5.0pct，现算 -6.0pct：双侧过确认幅度，真转向
        alerts, _ = self._run([card(rs_prev=5.0)], strength={"rs": -6.0})
        self.assertEqual([a.kind for a in alerts], ["强度转向"])

    def test_strength_small_wobble_not_triggered(self):
        # +0.5 → -0.5：没过 ±2 确认幅度，是噪音
        alerts, _ = self._run([card(rs_prev=0.5)], strength={"rs": -0.5})
        self.assertEqual(alerts, [])

    def test_overdue_catalyst_triggers(self):
        alerts, _ = self._run([card(catalyst_due=True)])
        self.assertEqual([a.kind for a in alerts], ["催化剂到期"])

    def test_commodity_extreme_triggers(self):
        alerts, _ = self._run([card(commodity=True)],
                              commodity_stats={"latest": 68000.0, "position_250d": 0.08})
        self.assertEqual([a.kind for a in alerts], ["盯价极值"])
        self.assertIn("一年最低附近", alerts[0].detail)


class NotifyTests(unittest.TestCase):
    def test_notify_without_channels_is_silent(self):
        env = {k: v for k, v in __import__("os").environ.items()
               if not k.startswith("SENTINEL_")}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(sentinel.notify("t", "b"), [])

    def test_notify_posts_to_bark(self):
        import os

        with unittest.mock.patch.dict("os.environ",
                                      {"SENTINEL_BARK_KEY": "testkey"}, clear=False), \
             unittest.mock.patch.object(sentinel.requests, "get") as mock_get:
            mock_get.return_value.text = '{"code":200}'
            results = sentinel.notify("标题", "内容")
        self.assertEqual(len(results), 1)
        self.assertIn("Bark", results[0])
        url = mock_get.call_args[0][0]
        self.assertIn("api.day.app/testkey", url)


if __name__ == "__main__":
    unittest.main()
