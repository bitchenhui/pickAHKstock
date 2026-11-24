"""线上出过的问题的回归测试。

这里的每一条都对应一次「本地看起来是好的、云端实际没生效」：
RSI 取数没接上、新卡缺出处链接、AI 把整句话写进路径字段、港股公告窗口每天重抓、
akshare 升级后盯价接口报错、扫描失败把旧结果冲掉、数据面失败被超时一起丢掉、
点名建卡找不到代码、健康检查不报错、对辩步骤把行业共振的字段当成了对辩结论、
港股公告按天缓存每个文件装了两天。
"""

import argparse
import itertools
import json
import os
import tempfile
import unittest
import unittest.mock
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from research import cli, commodity, deepdive, execution, hk, inputs, llm, workflow
from research.card import Card, blocking, validate


class ScaffoldTests(unittest.TestCase):
    @staticmethod
    def candidate(**overrides):
        from research.discover import Candidate

        fields = dict(code="002845", name="同兴达", forecast_type="扭亏", change_lower=None,
                      metric="归属于上市公司股东的净利润", notice_date=date(2026, 10, 15),
                      reason="主营改善", industry="电池", resonance="行业共振",
                      resonance_peers=4, resonance_same_sign=4)
        fields.update(overrides)
        return Candidate(**fields)

    def test_new_card_resonance_evidence_has_a_source_link(self):
        # 回归：E2 标了「已核实」却没有链接，质量门拦下，新卡永远进不了清单
        with unittest.mock.patch.object(workflow, "_regime_note", lambda market="A": {}):
            card = workflow.scaffold(self.candidate())
        second = next(e for e in card.evidence if e.id == "E2")
        self.assertEqual(second.url, workflow.FORECAST_SOURCE_URL)
        self.assertFalse(any("E2" in str(finding) for finding in blocking(validate(card))))

    def test_forecast_without_magnitude_can_be_scaffolded(self):
        # 扭亏类预告常常不披露同比幅度：以前拿 None 去格式化会直接抛异常
        with unittest.mock.patch.object(workflow, "_regime_note", lambda market="A": {}):
            card = workflow.scaffold(self.candidate())
        self.assertIn("未披露同比幅度", card.evidence[0].claim)
        self.assertNotIn("None", card.evidence[0].claim)


class DiscoveryTests(unittest.TestCase):
    def test_named_code_bypasses_the_magnitude_gate(self):
        from research.discover import build_candidates
        from research.events import Forecast
        from research.quotes import Snapshot

        turn = Forecast(code="002845", name="同兴达", notice_date=date(2026, 10, 15),
                        report_period="2026-09-30", metric="归属于上市公司股东的净利润",
                        forecast_type="扭亏", change_lower=None, change_upper=None, reason="",
                        source_url="", amount_lower=2e8, amount_upper=3e8)
        snap = Snapshot(market="A", code="002845", name="同兴达", price=10.0, turnover=5e8,
                        market_cap=80e8, pe=20.0, pb=2.0, change_pct=0.0, as_of="2026-10-28")
        without, _ = build_candidates([turn], {"002845": snap}, [])
        with_name, _ = build_candidates([turn], {"002845": snap}, [],
                                        include_codes=("002845",))
        self.assertEqual(without, [])
        self.assertEqual([c.code for c in with_name], ["002845"])
        self.assertIsNone(with_name[0].change_lower)

    def test_new_does_not_overwrite_an_existing_card(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "A_600001.json").write_text("{}", encoding="utf-8")
        candidate = SimpleNamespace(code="600001", name="测试")
        with unittest.mock.patch.object(cli, "CARD_DIR", root), \
             unittest.mock.patch.object(cli, "discover", lambda *a, **k: ([candidate], {})), \
             unittest.mock.patch.object(cli, "scaffold") as scaffold:
            code = cli.cmd_new(argparse.Namespace(market="A", codes="600001",
                                                  period="2026-09-30", since_days=90, limit=3))
        self.assertEqual(code, 0)
        scaffold.assert_not_called()
        self.assertEqual((root / "A_600001.json").read_text(encoding="utf-8"), "{}")


class LlmJudgmentTests(unittest.TestCase):
    def test_path_is_normalized_to_two_allowed_values(self):
        # 回归：模型把整句话写进 path，质量门的路径检查永远过不了
        self.assertEqual(llm._normalize_path("已盈利：Q1、Q2、Q3 净利润分别为 2.6 亿元"), "已盈利")
        self.assertEqual(llm._normalize_path("未盈利，现金流为负"), "未盈利")
        self.assertEqual(llm._normalize_path("不好判断"), "未分类")
        self.assertEqual(llm._normalize_path(None), "未分类")


class HkCacheTests(unittest.TestCase):
    """港股公告按天缓存、必须取全。以前一次要 120 天却只拿到最近约 28 天，没有任何提示。"""

    _fetch_day_orig = staticmethod(hk._fetch_day)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.days: list = []
        patchers = [
            unittest.mock.patch.object(hk, "CACHE_DIR", Path(tmp.name)),
            unittest.mock.patch.object(hk, "_fetch_day", self.fetch_day),
            unittest.mock.patch.object(hk, "_to_event", lambda item: SimpleNamespace(
                published=date.fromisoformat(item["notice_date"]), category="盈警")),
            unittest.mock.patch.object(hk.time, "sleep", lambda seconds: None),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        hk._EVENTS_MEM.clear()
        self.addCleanup(hk._EVENTS_MEM.clear)

    def fetch_day(self, day):
        self.days.append(day.isoformat())
        return [{"notice_date": day.isoformat()}]

    @staticmethod
    def row(day="2026-11-20"):
        return {"art_code": "AN1", "notice_date": day, "title": "盈利预警",
                "codes": [{"stock_code": "03323", "short_name": "中国建材",
                           "ann_type": "H", "inner_code": "x"}],
                "columns": [{"column_code": "001", "column_name": "盈利警告"}],
                "title_en": "", "language": 0}

    @staticmethod
    def session_for(pages, total, seen=None):
        payloads = iter({"data": {"total_hits": total, "list": page}} for page in pages)

        def get(url, params, timeout):
            if seen is not None:
                seen.update(params)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: next(payloads))

        return SimpleNamespace(headers={}, get=get)

    def test_each_past_day_is_fetched_for_its_own_date_only(self):
        yesterday = date.today() - timedelta(days=1)
        first = yesterday - timedelta(days=2)
        self.assertEqual(len(hk.fetch_events(first, yesterday, categories=())), 3)
        self.assertEqual(self.days, [(first + timedelta(days=i)).isoformat() for i in range(3)])

    def test_cached_days_are_not_fetched_again_by_a_later_run(self):
        # 这一条要用真的按天缓存逻辑，只把最底层的网络请求换成假的
        yesterday = date.today() - timedelta(days=1)
        requested: list = []
        with unittest.mock.patch.object(hk, "_fetch_day", self._fetch_day_orig), \
             unittest.mock.patch.object(hk, "_fetch_events_raw",
                                        lambda start, end, max_pages=400, **kwargs:
                                        requested.append(start.isoformat())
                                        or [{"notice_date": start.isoformat()}]):
            hk.fetch_events(yesterday, yesterday, categories=())
            hk._EVENTS_MEM.clear()                               # 相当于换了一个进程
            hk.fetch_events(yesterday - timedelta(days=1), yesterday, categories=())
        self.assertEqual(requested, [yesterday.isoformat(),
                                     (yesterday - timedelta(days=1)).isoformat()])   # 只补了缺的那天

    def test_events_outside_the_requested_dates_are_dropped(self):
        yesterday = date.today() - timedelta(days=1)
        with unittest.mock.patch.object(hk, "_fetch_day", lambda day: [
                {"notice_date": (day + timedelta(days=5)).isoformat()}]):
            self.assertEqual(hk.fetch_events(yesterday, yesterday, categories=()), [])

    def test_an_announcement_shared_by_two_cache_files_appears_once(self):
        # 旧的按天缓存每个文件装了两天，同一条公告在相邻两个文件里各有一份
        yesterday = date.today() - timedelta(days=1)
        with unittest.mock.patch.object(hk, "_fetch_day", lambda day: [
                {"art_code": f"OWN-{day.isoformat()}", "notice_date": day.isoformat()},
                {"art_code": "AN1", "notice_date": yesterday.isoformat()}]):
            events = hk.fetch_events(yesterday - timedelta(days=1), yesterday, categories=())
        self.assertEqual(len(events), 3)                         # 各自当天的两条，AN1 只算一条

    def test_a_day_file_keeps_only_that_day(self):
        # 接口的起止日期两端都含：查 [day, day] 回来的行里混着次日的，不能存进去
        day = date(2026, 11, 19)                                 # 周四；过去的某一天，缓存长期有效
        page = [dict(self.row("2026-11-19"), art_code="A1"), dict(self.row("2026-11-20"), art_code="A2")]
        seen: dict = {}
        session = self.session_for([page], total=2, seen=seen)
        with unittest.mock.patch.object(hk, "_fetch_day", self._fetch_day_orig), \
             unittest.mock.patch.object(hk.requests, "Session", lambda: session):
            rows = hk._fetch_day(day)
        self.assertEqual(seen["begin_time"], "2026-11-19")
        self.assertEqual(seen["end_time"], "2026-11-19")         # 只查这一天
        self.assertEqual([row["art_code"] for row in rows], ["A1"])
        cached = json.loads((hk.CACHE_DIR / "ann_day_v2_2026-11-19.json").read_text(encoding="utf-8"))
        self.assertEqual([row["art_code"] for row in cached], ["A1"])

    def test_a_weekday_with_nothing_is_asked_again_with_a_padded_end(self):
        # 工作日一条都没有，不能直接当成「没有公告」：可能是接口把 end_time 当成了不含当天
        day = date(2026, 11, 18)                                 # 周三
        asks: list = []
        later = [dict(self.row("2026-11-18"), art_code="B1"), dict(self.row("2026-11-19"), art_code="B2")]

        def get(url, params, timeout):
            asks.append(params["end_time"])
            page = [] if params["end_time"] == params["begin_time"] else later
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"data": {"total_hits": len(page), "list": page}})

        with unittest.mock.patch.object(hk, "_fetch_day", self._fetch_day_orig), \
             unittest.mock.patch.object(hk.requests, "Session", lambda: SimpleNamespace(headers={}, get=get)):
            rows = hk._fetch_day(day)
        self.assertEqual(asks, ["2026-11-18", "2026-11-19"])
        self.assertEqual([row["art_code"] for row in rows], ["B1"])

    def test_raw_fetch_requests_only_listed_shares_with_a_padded_end(self):
        for pad, expected in ((True, "2026-11-21"), (False, "2026-11-20")):
            seen = {}
            session = self.session_for([[self.row()]], total=1, seen=seen)
            with unittest.mock.patch.object(hk.requests, "Session", lambda: session):
                hk._fetch_events_raw(date(2026, 11, 20), date(2026, 11, 20), pad_end=pad)
            self.assertEqual(seen["ann_type"], "H")
            self.assertEqual(seen["begin_time"], "2026-11-20")
            self.assertEqual(seen["end_time"], expected)        # 默认多传一天；pad_end=False 时不补

    def test_stock_fetch_sends_the_filter_and_keeps_only_this_stock_in_the_window(self):
        seen = {}
        session = self.session_for([[self.row()]], total=1, seen=seen)
        with unittest.mock.patch.object(hk.requests, "Session", lambda: session):
            hk._fetch_events_raw(date(2026, 11, 1), date(2026, 11, 20), stock="03323")
        # 三个参数要一起给，少任何一个接口都还是返回全市场（实测）
        self.assertEqual((seen["f_node"], seen["s_node"], seen["stock_list"]), ("0", "0", "03323"))
        rows = [dict(self.row("2026-11-20"), art_code="S1"), dict(self.row("2026-11-20"), art_code="S1"),
                dict(self.row("2026-11-21"), art_code="S2"),                  # end_time 多传的那一天
                dict(self.row("2026-10-30"), art_code="S0"),                  # 窗口之前
                dict(self.row("2026-11-05"), art_code="S3")]                  # 别的股票
        rows[4]["codes"][0]["stock_code"] = "09696"

        def to_event(item):
            return SimpleNamespace(published=date.fromisoformat(item["notice_date"]),
                                   code=item["codes"][0]["stock_code"])

        with unittest.mock.patch.object(hk, "_fetch_events_raw",
                                        lambda *a, **k: rows), \
             unittest.mock.patch.object(hk, "_to_event", to_event):
            events = hk.fetch_stock_events("03323", date(2026, 11, 1), date(2026, 11, 20))
        self.assertEqual([e.published for e in events], [date(2026, 11, 20)])

    def test_card_scan_uses_the_per_stock_fetch_not_the_whole_market(self):
        # 单卡扫描以前翻全市场按天的公告流：冷缓存时 240 天一千多页，云端二十分钟跑不完一张卡
        from research import risk

        asked: list = []

        def fake(code, start, end, max_pages=50):
            asked.append(code)
            return [SimpleNamespace(title="有關仲裁及訴訟的自願公告",
                                    published=date.today() - timedelta(days=3),
                                    detail_url="http://x")]

        with unittest.mock.patch.object(hk, "fetch_stock_events", fake), \
             unittest.mock.patch.object(hk, "fetch_events",
                                        lambda *a, **k: self.fail("不该翻全市场的公告流")):
            scan = risk.scan_announcements("03323", "中国建材", 240, "HK")
        self.assertEqual(asked, ["03323"])
        self.assertEqual(len(scan["risks"]), 1)

    def test_raw_fetch_stops_when_the_reported_total_is_reached(self):
        page = [self.row()] * 100
        session = self.session_for([page, page[:50]], total=150)
        with unittest.mock.patch.object(hk.requests, "Session", lambda: session):
            rows = hk._fetch_events_raw(date(2026, 11, 20), date(2026, 11, 20))
        self.assertEqual(len(rows), 150)
        self.assertEqual(rows[0]["codes"][0]["stock_code"], "03323")     # 精简后字段仍可用
        self.assertNotIn("inner_code", rows[0]["codes"][0])

    def test_raw_fetch_raises_instead_of_returning_a_truncated_window(self):
        page = [self.row()] * 100
        session = self.session_for([page, page, []], total=500)
        with unittest.mock.patch.object(hk.requests, "Session", lambda: session):
            with self.assertRaises(hk.HkError) as caught:
                hk._fetch_events_raw(date(2026, 11, 20), date(2026, 11, 20))
        self.assertIn("没取全", str(caught.exception))


class CompatTests(unittest.TestCase):
    def test_commodity_fetch_survives_a_renamed_akshare_argument(self):
        # 云端装到的 akshare 1.20 不再接受 vars_list：退回取全部品种再按代码筛
        rows = [{"date": "20261120", "symbol": "LC", "spot_price": 90000.0},
                {"date": "20261120", "symbol": "SI", "spot_price": 9500.0}]

        def new_signature(start_day, end_day):
            return SimpleNamespace(to_dict=lambda orient: rows)

        def strict(*args, **kwargs):
            if "vars_list" in kwargs:
                raise TypeError("unexpected keyword argument 'vars_list'")
            return new_signature(*args)

        fake = SimpleNamespace(futures_spot_price_daily=strict)
        with unittest.mock.patch.dict(sys.modules, {"akshare": fake}):
            out = commodity._fetch_daily("20261120", "20261120", ["LC"])
        self.assertEqual([(r["symbol"], r["price"]) for r in out], [("LC", 90000.0)])


class ExecutionTests(unittest.TestCase):
    def test_rsi_is_written_to_the_card(self):
        # 回归：取数那一步没接上，线上所有 A 股卡的 RSI 都是空
        card = Card(market="A", code="600001", name="测试", status="观察", view="")
        with unittest.mock.patch.object(execution, "avg_turnover_a",
                                        lambda code: {"avg_turnover": 5e7}), \
             unittest.mock.patch.object(execution, "relative_strength",
                                        lambda code, market, window=60: {"rs": 3.0}), \
             unittest.mock.patch.object(execution, "rsi_current",
                                        lambda code, market: 57.5), \
             unittest.mock.patch.dict("os.environ", {"PICKAHK_STAKE_CNY": "20000"}), \
             unittest.mock.patch.object(execution, "ACCOUNT_FILE", Path("/nonexistent/account.json")):
            out = execution.fill_execution(card)
        self.assertEqual(out["rsi"], 57.5)
        self.assertEqual(out["liquidity"]["level"], "无忧")     # 云端没有 account.json 也能评估
        self.assertEqual(out["stake"], 20000.0)

    def test_rsi_failure_does_not_take_down_the_other_checks(self):
        card = Card(market="A", code="600001", name="测试", status="观察", view="")

        def boom(code, market):
            raise RuntimeError("no bars")

        with unittest.mock.patch.object(execution, "avg_turnover_a", lambda code: None), \
             unittest.mock.patch.object(execution, "relative_strength",
                                        lambda code, market, window=60: {"rs": 3.0}), \
             unittest.mock.patch.object(execution, "rsi_current", boom):
            out = execution.fill_execution(card)
        self.assertIsNone(out["rsi"])
        self.assertEqual(out["strength"]["rs"], 3.0)


class ScanRetentionTests(unittest.TestCase):
    def card(self):
        card = Card(market="A", code="600001", name="测试", status="观察", view="")
        card.scans = {"as_of": "2026-11-20", "days": 240,
                      "risks": [{"title": "上次的扫描结果"}], "holders": [],
                      "segment_as_of": {"risks": "2026-11-20", "holders": "2026-11-20"}}
        return card

    def test_failed_scan_keeps_the_previous_result(self):
        # 回归：今天扫描失败时用空结果把旧数据冲掉，空结果会被读成「没有风险」
        def boom(*args):
            raise RuntimeError("巨潮超时")

        with unittest.mock.patch("research.risk.scan_announcements", boom), \
             unittest.mock.patch("research.governance.scan", lambda code, name: []):
            scans, steps = deepdive.collect_scans(self.card())
        self.assertEqual(scans["risks"], [{"title": "上次的扫描结果"}])
        self.assertEqual(scans["segment_as_of"]["risks"], "2026-11-20")
        failed = next(s for s in steps if s.face == "风险公告与增减持扫描")
        self.assertEqual(failed.status, "fail")
        self.assertIn("沿用 2026-11-20", failed.note)

    def test_successful_scan_replaces_and_adds_holders(self):
        found = {"risks": [], "holders": [{"side": "减持", "stage": "计划", "title": "t",
                                          "date": "2026-10-01", "url": ""}]}
        with unittest.mock.patch("research.risk.scan_announcements", lambda *a: found), \
             unittest.mock.patch("research.governance.scan", lambda code, name: []):
            scans, steps = deepdive.collect_scans(self.card())
        self.assertEqual(scans["risks"], [])
        self.assertEqual(scans["holders"][0]["side"], "减持")
        self.assertEqual(scans["as_of"], date.today().isoformat())


class IncrementalSaveTests(unittest.TestCase):
    def test_each_successful_face_is_saved_even_if_a_later_face_fails(self):
        # 回归：云端跑港股被超时杀掉时，只在最后存一次会让已取到的部分一起丢掉
        card = SimpleNamespace(code="000000", save=unittest.mock.Mock(), market="A")
        with unittest.mock.patch.object(deepdive, "Card") as card_class, \
             unittest.mock.patch.object(deepdive, "fill_quote", lambda card: None), \
             unittest.mock.patch.object(deepdive, "fill_financials",
                                        unittest.mock.Mock(side_effect=RuntimeError("接口挂了"))), \
             unittest.mock.patch.object(deepdive, "fill_reference", lambda card: None), \
             unittest.mock.patch.object(deepdive, "fill_expectations", lambda card: None), \
             unittest.mock.patch.object(deepdive, "fill_context", lambda card: None), \
             unittest.mock.patch.object(deepdive, "fill_industry", lambda card, period="": None), \
             unittest.mock.patch("research.execution.fill_execution", lambda card: {}), \
             unittest.mock.patch("research.inputs.fill_inputs", lambda card: {}):
            card_class.load.return_value = card
            result = deepdive.deep_dive("A", "000000", with_scan=False)
        self.assertEqual([s.face for s in result.failed], ["财务序列"])
        self.assertGreaterEqual(card.save.call_count, 7)


class HealthTests(unittest.TestCase):
    def setUp(self):
        import research.card as card_mod
        from research import tracking

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "cards"
        self.root.mkdir()
        # 卡片目录和快照目录都挪到临时目录，不碰仓库里的真卡片
        for patcher in (unittest.mock.patch.object(card_mod, "CARD_DIR", self.root),
                        unittest.mock.patch.object(cli, "CARD_DIR", self.root),
                        unittest.mock.patch.object(tracking, "SNAPSHOT_DIR",
                                                   Path(tmp.name) / "snaps")):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def args():
        return argparse.Namespace(stale_days=4)

    def write(self, name="A_600001", **overrides):
        card = Card(market="A", code="600001", name="测试", status="观察", view="判断")
        card.market_context = {"as_of": date.today().strftime("%Y%m%d%H%M%S")}
        card.execution = {"rsi": 50.0}
        card.inputs = {"valuation": {"status": "有数据"}, "volume": {"trend": "持平"},
                       "holders": {"net_signal": 0}}
        card.scans = {"as_of": date.today().isoformat(), "days": 240}
        for field, value in overrides.items():
            setattr(card, field, value)
        card.save()
        return card

    def test_fresh_complete_card_passes(self):
        self.write()
        with unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_health(self.args()), 0)

    def test_stale_quote_fails_the_check(self):
        self.write(market_context={"as_of": "20261111150000"})
        with unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_health(self.args()), 1)

    def test_missing_inputs_fail_the_check(self):
        self.write(execution={}, inputs={})
        with unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_health(self.args()), 1)

    def test_listed_card_without_a_fresh_debate_fails_the_check(self):
        import research.shortlist as shortlist

        self.write()
        env = dict(os.environ, LLM_API_KEY="k")
        with unittest.mock.patch.dict("os.environ", env), \
             unittest.mock.patch.object(shortlist, "passes_gate", return_value=True), \
             unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_health(self.args()), 1)        # 清单里的卡没有 AI 对辩
            card = Card.load("A", "600001")
            card.debate = {"verdict": "维持观察", "as_of": date.today().isoformat()}
            card.save()
            self.assertEqual(cli.cmd_health(self.args()), 0)

    def test_debate_is_not_required_without_a_model_key(self):
        import research.shortlist as shortlist

        self.write()
        env = {k: v for k, v in os.environ.items() if k != "LLM_API_KEY"}
        with unittest.mock.patch.dict("os.environ", env, clear=True), \
             unittest.mock.patch.object(shortlist, "passes_gate", return_value=True), \
             unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_health(self.args()), 0)


class DebateTests(unittest.TestCase):
    """回归：流水线在卡片文件里 grep "verdict" 来判断有没有对辩，撞上了行业共振的
    同名字段——16 张卡里 11 张没有对辩却被当成已有，对辩步骤整段被跳过。"""

    def setUp(self):
        import research.card as card_mod
        from research import tracking

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "cards"
        self.root.mkdir()
        for patcher in (unittest.mock.patch.object(card_mod, "CARD_DIR", self.root),
                        unittest.mock.patch.object(cli, "CARD_DIR", self.root),
                        unittest.mock.patch.object(tracking, "SNAPSHOT_DIR",
                                                   Path(tmp.name) / "snaps")):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def args(budget_minutes=20):
        return argparse.Namespace(max_age_days=3, budget_minutes=budget_minutes)

    def write(self, code, status="观察", verdict="", as_of=""):
        card = Card(market="A", code=code, name=f"股{code}", status=status, view="判断")
        card.industry_context = {"verdict": "行业共振", "industry": "电池"}
        card.debate = {"verdict": verdict, "as_of": as_of} if verdict else {}
        card.save()
        return card

    def test_industry_verdict_is_not_a_debate(self):
        card = self.write("600001")
        # 卡片文件里确实有一个非空的 verdict：流水线那句 grep 就是被它骗过的
        self.assertRegex(card.path.read_text(encoding="utf-8"), r'"verdict": "[^"]')
        self.assertEqual(llm.debate_state(card, today="2026-11-24"), "missing")
        self.assertEqual([c.code for c in llm.cards_needing_debate([card], today="2026-11-24")],
                         ["600001"])

    def test_debates_expire_and_downgraded_cards_are_skipped(self):
        today = "2026-11-24"
        fresh = self.write("600001", verdict="维持观察", as_of="2026-11-22")
        undated = self.write("600002", verdict="维持观察")           # 旧版本写的，没有日期
        old = self.write("600003", verdict="维持观察", as_of="2026-11-10")
        older = self.write("600004", verdict="维持观察", as_of="2026-11-01")
        missing = self.write("600005")
        downgraded = self.write("600006", status="降级")
        self.assertEqual(llm.debate_state(fresh, today=today), "fresh")
        self.assertEqual(llm.debate_state(old, today=today), "stale")
        self.assertEqual(llm.debate_state(undated, today=today), "stale")
        order = llm.cards_needing_debate([fresh, old, undated, downgraded, older, missing],
                                         today=today)
        # 缺失的最先，其余最旧的先跑；在有效期内和已降级的不跑
        self.assertEqual([c.code for c in order], ["600005", "600002", "600004", "600003"])

    def test_new_debates_are_stamped_with_the_date(self):
        self.write("600001")
        card = Card.load("A", "600001")
        answers = ["多方论证", "空方论证", '{"conclusion": "维持观察", "checks": []}']
        with unittest.mock.patch.dict("os.environ", {"LLM_API_KEY": "k"}), \
             unittest.mock.patch.object(llm, "_call", side_effect=answers):
            result = llm.debate(card)
        self.assertEqual(result.as_of, date.today().isoformat())
        self.assertEqual(result.to_dict()["as_of"], result.as_of)

    def test_command_runs_what_is_needed_and_reports_failures(self):
        self.write("600001")
        self.write("600002")
        self.write("600003", verdict="可进持仓研究", as_of=date.today().isoformat())
        calls = []

        def fake_analyze(card):
            calls.append(card.code)
            if card.code == "600002":
                return {"plain": {}, "debate": None, "debate_error": "LLMError: 超时"}
            return {"plain": {}, "debate": {"verdict": "可进持仓研究", "bull": "多",
                                             "bear": "空", "checks": [], "source": "t",
                                             "as_of": date.today().isoformat()}}

        with unittest.mock.patch.dict("os.environ", {"LLM_API_KEY": "k"}), \
             unittest.mock.patch.object(llm, "analyze", fake_analyze), \
             unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_debate(self.args()), 1)        # 有一张没跑成
            self.assertEqual(calls, ["600001", "600002"])          # 第三张在有效期内，不跑
            self.assertEqual(cli.cmd_debate(self.args()), 1)       # 再跑：只剩失败的那张，仍失败
        self.assertEqual(calls, ["600001", "600002", "600002"])
        self.assertEqual(Card.load("A", "600001").debate["verdict"], "可进持仓研究")
        self.assertEqual(Card.load("A", "600002").debate, {})      # 失败不写半截结果

    def test_command_stops_when_the_time_budget_is_used_up(self):
        for code in ("600001", "600002", "600003"):
            self.write(code)
        calls = []
        clock = itertools.chain([0.0, 0.0], itertools.repeat(10_000.0))

        def fake_analyze(card):
            calls.append(card.code)
            return {"plain": {}, "debate": {"verdict": "维持观察",
                                             "as_of": date.today().isoformat()}}

        with unittest.mock.patch.dict("os.environ", {"LLM_API_KEY": "k"}), \
             unittest.mock.patch.object(llm, "analyze", fake_analyze), \
             unittest.mock.patch("time.monotonic", side_effect=lambda: next(clock)), \
             unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_debate(self.args(budget_minutes=5)), 0)
        self.assertEqual(calls, ["600001"])                        # 其余留到下次

    def test_command_skips_without_a_key(self):
        self.write("600001")
        env = {k: v for k, v in os.environ.items() if k != "LLM_API_KEY"}
        with unittest.mock.patch.dict("os.environ", env, clear=True), \
             unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_debate(self.args()), 0)

    def test_empty_model_output_is_a_failure_not_a_blank_verdict(self):
        # 线上出过：命令显示成功、结论却是空的，还把卡片上原来完整的对辩覆盖掉了
        self.write("600001")
        card = Card.load("A", "600001")
        good = ["多方论证", "空方论证",
                '{"conclusion": "维持观察", "checks": ["核对现金流"], "caveat": "价格假设不成立"}']
        with unittest.mock.patch.dict("os.environ", {"LLM_API_KEY": "k"}), \
             unittest.mock.patch.object(llm, "_call", side_effect=good):
            card.debate = llm.debate(card).to_dict()
        self.assertEqual(card.debate["verdict"], "维持观察")
        self.assertIn("价格假设不成立", card.debate["checks"])
        card.debate = {"verdict": "原来的结论"}                  # 没有日期 → 需要重跑
        card.save()
        for answers in (["多方论证", "", "不该走到这一步"],       # 空方论证为空
                        ["多方论证", "空方论证", ""],            # 合成结果是空串（推理型模型）
                        ["多", "空", '{"conclusion": "", "checks": []}'],
                        ["多", "空", '{"checks": ["没有结论字段"]}']):
            with self.subTest(answers=answers), \
                 unittest.mock.patch.dict("os.environ", {"LLM_API_KEY": "k"}), \
                 unittest.mock.patch.object(llm, "_call", side_effect=answers):
                with self.assertRaises(llm.LLMError):
                    llm.debate(card)

        def blank(card):
            return {"plain": {}, "debate": {"verdict": "  ", "as_of": date.today().isoformat()}}

        with unittest.mock.patch.dict("os.environ", {"LLM_API_KEY": "k"}), \
             unittest.mock.patch.object(llm, "analyze", blank), \
             unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_debate(self.args()), 1)      # 空结果算失败
        self.assertEqual(Card.load("A", "600001").debate["verdict"], "原来的结论")   # 没被覆盖


class DiscoveryCapTests(unittest.TestCase):
    """回归：卡片上限按全部卡片数（含降级）算，卡片一满清单只会缩、进不了新票。"""

    def setUp(self):
        import research.card as card_mod
        from research import tracking

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "cards"
        self.root.mkdir()
        # 快照目录也要挪走：建卡时会打快照，不然测试会往真快照目录里漏文件
        for patcher in (unittest.mock.patch.object(card_mod, "CARD_DIR", self.root),
                        unittest.mock.patch.object(cli, "CARD_DIR", self.root),
                        unittest.mock.patch.object(tracking, "SNAPSHOT_DIR",
                                                   Path(tmp.name) / "snaps")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, code, status="观察"):
        card = Card(market="A", code=code, name=f"股{code}", status=status, view="判断")
        card.save()
        return card

    @staticmethod
    def args(max_cards=2):
        return argparse.Namespace(period="2026-09-30", a_limit=1, hk_limit=1,
                                  max_cards=max_cards, hk_max=6)

    def test_downgraded_cards_do_not_take_a_slot(self):
        for code in ("600001", "600002", "600003"):
            self.write(code, status="降级")
        self.write("600100")
        screens = []

        def fake_screen(*args, **kwargs):
            screens.append(1)
            return {"hits": []}

        with unittest.mock.patch.object(cli, "_odds_screen", fake_screen), \
             unittest.mock.patch.object(hk, "discover", return_value=[]), \
             unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_discover_new(self.args()), 0)
        self.assertEqual(screens, [1])        # 在册的只有 1 张、没到上限，照常找新候选

    def test_cap_counts_only_cards_that_are_still_in_play(self):
        self.write("600001")
        self.write("600002")
        self.write("600003", status="降级")
        screens = []

        def fake_screen(*args, **kwargs):
            screens.append(1)
            return {"hits": []}

        with unittest.mock.patch.object(cli, "_odds_screen", fake_screen), \
             unittest.mock.patch.object(hk, "discover", return_value=[]), \
             unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_discover_new(self.args()), 0)
        self.assertEqual(screens, [])         # 在册 2 张已到上限，不找新候选

    def test_intake_never_overshoots_the_cap(self):
        self.write("600100")                  # 在册 1 张，只剩 1 个名额
        hits = [SimpleNamespace(code="600001", inflection="扭亏", cap_at=5e9),
                SimpleNamespace(code="600002", inflection="扭亏", cap_at=6e9)]
        candidates = [SimpleNamespace(code="600002", name="股600002")]   # 市值大的排在前面
        made = []

        def fake_card(candidate):
            made.append(candidate.code)
            return Card(market="A", code=candidate.code, name=candidate.name, view="判断")

        with unittest.mock.patch.object(cli, "_odds_screen", return_value={"hits": hits}), \
             unittest.mock.patch.object(cli, "discover", return_value=(candidates, {})) as found, \
             unittest.mock.patch.object(workflow, "scaffold", fake_card), \
             unittest.mock.patch.object(hk, "discover", return_value=[]), \
             unittest.mock.patch("sys.stdout"):
            self.assertEqual(cli.cmd_discover_new(self.args()), 0)
        self.assertEqual(found.call_args.kwargs["only_codes"], ("600002",))   # 只问名额内的
        self.assertEqual(made, ["600002"])


if __name__ == "__main__":
    unittest.main()
