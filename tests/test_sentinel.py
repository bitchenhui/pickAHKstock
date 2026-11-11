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
        from research import backtest, commodity, execution

        patches = [unittest.mock.patch.object(sentinel, "fetch_snapshots",
                                              lambda codes: {"600001": snapshot(10.5)}),
                   unittest.mock.patch.object(backtest, "prefetch_prices",
                                              lambda *args, **kwargs: 0)]
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
        from research import backtest

        with unittest.mock.patch.object(sentinel, "fetch_snapshots",
                                        lambda codes: {"600001": snapshot(8.8)}), \
             unittest.mock.patch.object(backtest, "prefetch_prices",
                                        lambda *args, **kwargs: 0):
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

    def test_bark_alerts_pushed_one_by_one(self):
        import os

        alerts = [sentinel.Alert("A", "603026", "石大胜华", "失效线",
                                 "现价 8.8 跌破参考线 9.0", "打开卡片核对"),
                  sentinel.Alert("HK", "03323", "中国建材", "盯价极值",
                                 "纯碱 1040 处一年最低", "对照失效条件")]
        with unittest.mock.patch.dict("os.environ",
                                      {"SENTINEL_BARK_KEY": "testkey"}, clear=False), \
             unittest.mock.patch.object(sentinel.requests, "post") as mock_post:
            mock_post.return_value.text = '{"code":200}'
            results = sentinel.notify_alerts(alerts)
        self.assertEqual(len(results), 2)
        self.assertEqual(mock_post.call_count, 2)
        first = mock_post.call_args_list[0]
        item = first.kwargs.get("json")
        self.assertEqual(item["title"], "【失效线】石大胜华")
        self.assertEqual(item["group"], "研究台哨兵")
        self.assertEqual(item["level"], "timeSensitive")   # 失效线=时效性
        self.assertEqual(item["url"],
                         "https://bitchenhui.github.io/pickAHKstock/A_603026.html")
        second = mock_post.call_args_list[1].kwargs["json"]
        self.assertEqual(second["level"], "active")        # 盯价极值=普通

    def test_bark_push_capped_with_summary(self):
        import os

        alerts = [sentinel.Alert("A", f"6000{i:02d}", f"股{i}", "催化剂到期", "x", "y")
                  for i in range(10)]
        with unittest.mock.patch.dict("os.environ",
                                      {"SENTINEL_BARK_KEY": "testkey"}, clear=False), \
             unittest.mock.patch.object(sentinel.requests, "post") as mock_post:
            mock_post.return_value.text = '{"code":200}'
            sentinel.notify_alerts(alerts)
        # 逐条 8 + 汇总 1 = 9 次 POST
        self.assertEqual(mock_post.call_count, 9)
        summary = mock_post.call_args_list[-1].kwargs["json"]
        self.assertIn("还有 2 条", summary["title"])


if __name__ == "__main__":
    unittest.main()


class DedupeTests(unittest.TestCase):
    def test_same_fingerprint_silent_until_state_clears(self):
        import tempfile
        from pathlib import Path

        alert = sentinel.Alert("A", "600001", "测试", "盯价极值", "d", "h",
                               fingerprint="ext:LC:5-10%")
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            with unittest.mock.patch.object(sentinel, "STATE_FILE", state):
                fresh, silent = sentinel.dedupe([alert])
                self.assertEqual(len(fresh), 1)          # 第一次：推
                fresh, silent = sentinel.dedupe([alert])
                self.assertEqual(len(silent), 1)         # 状态持续：静默
                self.assertEqual(fresh, [])
                # 状态消失（本轮无该指纹）后重新触发：再推
                sentinel.dedupe([])
                fresh, silent = sentinel.dedupe([alert])
                self.assertEqual(len(fresh), 1)

    def test_different_band_is_new_event(self):
        import tempfile
        from pathlib import Path

        low = sentinel.Alert("A", "600001", "测试", "盯价极值", "d", "h",
                             fingerprint="ext:LC:5-10%")
        deeper = sentinel.Alert("A", "600001", "测试", "盯价极值", "d", "h",
                                fingerprint="ext:LC:<5%")
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            with unittest.mock.patch.object(sentinel, "STATE_FILE", state):
                sentinel.dedupe([low])
                fresh, _ = sentinel.dedupe([deeper])     # 跌进更深档位 = 新事件
                self.assertEqual(len(fresh), 1)
