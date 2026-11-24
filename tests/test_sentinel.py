import tempfile
import unittest
import unittest.mock
from datetime import date, timedelta
from pathlib import Path

from research import sentinel
from research.card import Card, Catalyst
from research.quotes import Snapshot

TODAY = date.today()
TODAY_STAMP = TODAY.strftime("%Y%m%d%H%M%S")


def card(code="600001", stop=9.0, rs_prev=5.0, catalyst_due=False, commodity=False) -> Card:
    card = Card(market="A", code=code, name=f"股{code}", status="观察", view="判断")
    card.market_context = {"stop_reference": stop} if stop else {}
    card.execution = {"strength": {"rs": rs_prev}} if rs_prev is not None else {}
    if catalyst_due:
        card.catalysts = [Catalyst(event="年报预告窗口", date="2026-01-31",
                                   status="待验证")]
    if commodity:
        card.commodities = {"items": {"LC": {"symbol": "LC", "name": "碳酸锂"}}}
    return card


def snapshot(code="600001", price=10.5, as_of=TODAY_STAMP, change=None, volume=None) -> Snapshot:
    return Snapshot(market="A", code=code, name=f"股{code}", price=price,
                    turnover=1e8, market_cap=60e8, pe=None, pb=None,
                    change_pct=change, as_of=as_of, volume=volume)


class CheckTests(unittest.TestCase):
    """每个用例都用独立的哨兵状态文件，不碰真实的状态。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = unittest.mock.patch.object(sentinel, "STATE_FILE",
                                             Path(self.tmp.name) / "state.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        # 清单状态：默认没有清单（测试卡不过质量门），避免第一条就推「清单变化」
        sentinel._write_state({"shortlist": {}})
        # 现算异动线要联网：测试里关掉，退回用卡片里存的线
        fresh = unittest.mock.patch.object(sentinel, "_fresh_stop", lambda code: None)
        fresh.start()
        self.addCleanup(fresh.stop)

    def _run(self, cards, prices=None, strength=None, commodity_stats=None,
             as_of=TODAY_STAMP, change=None, volume=None):
        from research import backtest, commodity, execution

        prices = prices or {}
        patches = [
            unittest.mock.patch.object(sentinel, "fetch_snapshots", lambda codes: {
                code: snapshot(code, prices.get(code, 10.5), as_of, change, volume)
                for code in prices or {"600001": 10.5}}),
            unittest.mock.patch.object(backtest, "prefetch_prices", lambda *a, **k: 0),
            unittest.mock.patch.object(backtest, "load_prices", lambda *a, **k: []),  # 不联网
        ]
        if strength is not None:
            patches.append(unittest.mock.patch.object(
                execution, "relative_strength", lambda code, market, window=60: strength))
        if commodity_stats is not None:
            patches.append(unittest.mock.patch.object(
                commodity, "changes", lambda rows: commodity_stats))
            patches.append(unittest.mock.patch.object(
                commodity, "series", lambda symbol, lookback_days=400: [{"day": "2026-11-11"}]))
        import contextlib

        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return sentinel.check_cards(cards)

    def test_stop_reference_triggers(self):
        alerts, checked = self._run([card()], prices={"600001": 8.8})
        self.assertEqual(checked, 1)
        self.assertEqual([a.kind for a in alerts], ["异动线"])
        self.assertIn("8.8", alerts[0].detail)

    def test_no_trigger_above_stop(self):
        alerts, _ = self._run([card()])
        self.assertEqual(alerts, [])

    def test_near_stop_triggers(self):
        alerts, _ = self._run([card(stop=9.8)], prices={"600001": 10.0})   # 高出 2%
        self.assertEqual([a.kind for a in alerts], ["接近异动线"])

    def test_strength_flip_triggers(self):
        alerts, _ = self._run([card(rs_prev=5.0)], strength={"rs": -6.0})
        self.assertEqual([a.kind for a in alerts], ["强度转向"])

    def test_strength_small_wobble_not_triggered(self):
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

    def test_every_card_is_checked_not_only_the_last(self):
        # 回归：曾经有一版把催化剂/盯价/强度三项缩进到了「最后一张卡」的分支里，
        # 结果只有列表里的最后一张被检查
        cards = [card(code, commodity=True) for code in ("600001", "600002", "600003")]
        alerts, _ = self._run(cards, commodity_stats={"latest": 68000.0, "position_250d": 0.08},
                              strength={"rs": -6.0})
        kinds = sorted((a.code, a.kind) for a in alerts)
        self.assertEqual(kinds, sorted(
            (code, kind) for code in ("600001", "600002", "600003")
            for kind in ("强度转向", "盯价极值")))

    def test_no_new_quotes_means_no_check(self):
        # 休市或未开盘：快照日期不是今天，不检查也不推送
        stamp = (TODAY - timedelta(days=1)).strftime("%Y%m%d%H%M%S")
        alerts, checked = self._run([card()], prices={"600001": 8.8}, as_of=stamp)
        self.assertEqual((alerts, checked), ([], 0))

    def test_surge_needs_both_move_and_volume(self):
        with unittest.mock.patch.object(sentinel, "_avg_volume_20", lambda code: 1000.0):
            alerts, _ = self._run([card()], change=6.0, volume=3000.0)
            self.assertEqual([a.kind for a in alerts], ["异动放量"])
            alerts, _ = self._run([card()], change=6.0, volume=900.0)     # 没放量
            self.assertEqual(alerts, [])
            alerts, _ = self._run([card()], change=2.0, volume=3000.0)    # 没涨跌 5%
            self.assertEqual(alerts, [])

    def test_index_alert_links_to_summary_page(self):
        alert = sentinel.Alert("A", "INDEX", "推荐清单", "清单变化", "x", "y")
        self.assertTrue(sentinel._bark_alert_item(alert)["url"].endswith("pickAHKstock/"))


class ShortlistAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = unittest.mock.patch.object(sentinel, "STATE_FILE",
                                             Path(self.tmp.name) / "state.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        gate = unittest.mock.patch("research.shortlist.passes_gate", return_value=True)
        gate.start()
        self.addCleanup(gate.stop)

    @staticmethod
    def card(code="600001", regime="上行") -> Card:
        card = Card(market="A", code=code, name=f"股{code}", status="观察", view="判断")
        card.market_context = {"market_regime": {"state": regime, "day": date.today().isoformat()}}
        return card

    def test_called_watchlist_when_market_is_down(self):
        alert = sentinel.shortlist_alert([self.card(regime="下行")])
        self.assertEqual((alert.name, alert.kind), ("观察清单", "清单变化"))
        self.assertIn("观察名单", alert.action_hint)

    def test_called_recommendation_otherwise(self):
        alert = sentinel.shortlist_alert([self.card(regime="震荡")])
        self.assertEqual((alert.name, alert.kind), ("推荐清单", "清单变化"))

    def test_silent_when_list_unchanged(self):
        sentinel.shortlist_alert([self.card()])
        self.assertIsNone(sentinel.shortlist_alert([self.card()]))


class NotifyTests(unittest.TestCase):
    def test_notify_without_channels_is_silent(self):
        env = {k: v for k, v in __import__("os").environ.items()
               if not k.startswith("SENTINEL_")}
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(sentinel.notify("t", "b"), [])

    def test_bark_alerts_pushed_one_by_one(self):
        alerts = [sentinel.Alert("A", "603026", "石大胜华", "异动线",
                                 "现价 8.8 跌破异动线 9.0", "打开卡片核对"),
                  sentinel.Alert("HK", "03323", "中国建材", "盯价极值",
                                 "纯碱 1040 处一年最低", "对照失效条件")]
        with unittest.mock.patch.dict("os.environ",
                                      {"SENTINEL_BARK_KEY": "testkey"}, clear=False), \
             unittest.mock.patch.object(sentinel.requests, "post") as mock_post:
            mock_post.return_value.text = '{"code":200}'
            results = sentinel.notify_alerts(alerts)
        self.assertEqual(len(results), 2)
        self.assertEqual(mock_post.call_count, 2)
        first = mock_post.call_args_list[0].kwargs["json"]
        self.assertEqual(first["title"], "【异动线】石大胜华")
        self.assertEqual(first["device_key"], "testkey")
        self.assertEqual(first["group"], "研究台哨兵")
        self.assertEqual(first["level"], "timeSensitive")   # 异动线 = 时效性
        self.assertEqual(first["url"],
                         "https://bitchenhui.github.io/pickAHKstock/A_603026.html")
        second = mock_post.call_args_list[1].kwargs["json"]
        self.assertEqual(second["level"], "active")         # 盯价极值 = 普通

    def test_bark_push_capped_with_summary(self):
        alerts = [sentinel.Alert("A", f"6000{i:02d}", f"股{i}", "催化剂到期", "x", "y")
                  for i in range(10)]
        with unittest.mock.patch.dict("os.environ",
                                      {"SENTINEL_BARK_KEY": "testkey"}, clear=False), \
             unittest.mock.patch.object(sentinel.requests, "post") as mock_post:
            mock_post.return_value.text = '{"code":200}'
            sentinel.notify_alerts(alerts)
        # 逐条 8 条 + 汇总 1 条 = 9 次请求
        self.assertEqual(mock_post.call_count, 9)
        summary = mock_post.call_args_list[-1].kwargs["json"]
        self.assertIn("还有 2 条", summary["title"])


class DedupeTests(unittest.TestCase):
    def _state(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = unittest.mock.patch.object(sentinel, "STATE_FILE", Path(tmp.name) / "state.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_same_fingerprint_silent_until_state_clears(self):
        self._state()
        alert = sentinel.Alert("A", "600001", "测试", "盯价极值", "d", "h",
                               fingerprint="ext:LC:5-10%")
        fresh, silent = sentinel.dedupe([alert])
        self.assertEqual(len(fresh), 1)          # 第一次：推
        fresh, silent = sentinel.dedupe([alert])
        self.assertEqual((fresh, len(silent)), ([], 1))   # 状态持续：静默
        sentinel.dedupe([])                      # 状态消失后重新触发：再推
        fresh, _ = sentinel.dedupe([alert])
        self.assertEqual(len(fresh), 1)

    def test_dedupe_keeps_the_shortlist_bookkeeping(self):
        # 回归：去重曾经把整个状态文件改写成只有触发器指纹，
        # 清单的记录每轮被冲掉，于是每轮都推一次「清单从空变成现在这些」
        self._state()
        sentinel._write_state({"shortlist": {"600001": "股"}})
        sentinel.dedupe([sentinel.Alert("A", "600001", "股", "盯价极值", "d", "h",
                                        fingerprint="ext:LC:5-10%")])
        self.assertEqual(sentinel._read_state()["shortlist"], {"600001": "股"})

    def test_different_band_is_new_event(self):
        self._state()
        low = sentinel.Alert("A", "600001", "测试", "盯价极值", "d", "h",
                             fingerprint="ext:LC:5-10%")
        deeper = sentinel.Alert("A", "600001", "测试", "盯价极值", "d", "h",
                                fingerprint="ext:LC:<5%")
        sentinel.dedupe([low])
        fresh, _ = sentinel.dedupe([deeper])     # 跌进更深档位 = 新事件
        self.assertEqual(len(fresh), 1)


if __name__ == "__main__":
    unittest.main()
