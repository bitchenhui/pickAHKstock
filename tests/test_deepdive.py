import argparse
import json
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from research import deepdive
from research.card import Card
from research.quotes import Snapshot


def _fake_snapshot(code="600001", price=12.0, cap=60e8) -> Snapshot:
    return Snapshot(market="A", code=code, name="测试", price=price,
                    turnover=1e8, market_cap=cap, pe=20.0, pb=2.0,
                    change_pct=1.0, as_of="2026/11/07 15:00:00")


class _DictObj:
    """带 to_dict 的最小替身（analysts/context 的返回对象）。"""

    def __init__(self, data: dict, **attrs):
        self._data = data
        for key, value in attrs.items():
            setattr(self, key, value)

    def to_dict(self) -> dict:
        return dict(self._data)


def _financials_data():
    return {"periods": ["2026-09-30", "2026-06-30"], "simple_periods": ["2026-09-30"],
            "report": {"净利润": {"2026-09-30": "1.0亿", "2026-06-30": "0.6亿"}},
            "simple": {"净利润": {"2026-09-30": "0.4亿"}},
            "source": "测试接口"}


def _series_stub(financials, block="report", subjects=()):
    return {name: series for name, series in financials[block].items()}


def _capital():
    return _DictObj({"periods": ["2026-09-30"], "capex": 1.0},
                    periods=["2026-09-30"], notes=[],
                    capex_at=lambda: 7.09e8, operating_cf_at=lambda: 6.05e8,
                    fcf_at=lambda: -1.04e8, construction=["5.0亿"], fixed_assets=["20亿"])


def _peers():
    return _DictObj({"rank": 3}, rank="3/12", rows={"市盈率-TTM": 30.0}, notes=[])


class DeepDiveTests(unittest.TestCase):
    """一键灌卡：各面独立成败，部分成功也落盘。"""

    def setUp(self):
        import tempfile

        import research.card as card_mod
        from research import tracking

        self.tmp = tempfile.TemporaryDirectory()
        self.original_cards = card_mod.CARD_DIR
        self.original_snaps = tracking.SNAPSHOT_DIR
        card_mod.CARD_DIR = Path(self.tmp.name) / "cards"    # 不碰仓库真卡片
        card_mod.CARD_DIR.mkdir(parents=True)
        # 快照目录也要挪走：save() 的自动快照会写 tracking.SNAPSHOT_DIR
        tracking.SNAPSHOT_DIR = Path(self.tmp.name) / "snaps"
        self.card_mod = card_mod
        self.tracking = tracking
        Card(market="A", code="600001", name="测试股", status="观察", view="测试").save()

    def tearDown(self):
        self.card_mod.CARD_DIR = self.original_cards
        self.tracking.SNAPSHOT_DIR = self.original_snaps
        self.tmp.cleanup()

    def _patch_all(self, **overrides):
        patches = {
            "fetch_snapshots": lambda codes: {"600001": _fake_snapshot()},
            "fetch_financials": lambda code: _financials_data(),
            "key_series": _series_stub,
            "reference_levels": lambda code: {"close": 11.0, "atr": 0.5, "atr_pct": 4.5,
                                              "stop_reference": 10.0, "bars": 120,
                                              "last_day": "2026-11-06", "source": "测试"},
            "_regime_note": lambda: {"state": "下行", "day": "2026-11-07"},
            "fetch_expectations": lambda code, name, market: _DictObj(
                {"coverage": 3}, coverage=3, eps_forecast=[], ratings=[], notes=[]),
            "fetch_capital": lambda code: _capital(),
            "fetch_peers": lambda code, market: _peers(),
            "forecasts_by_report_period": lambda period: [
                {"SECURITY_CODE": "600001", "REPORT_DATE": "2026-09-30",
                 "PREDICT_FINANCE": "归属于上市公司股东的净利润", "PREDICT_TYPE": "预增",
                 "ADD_AMP_LOWER": 60, "NOTICE_DATE": "2026-10-15"}],
            "co_occurrence": lambda code, rows, **kw: _DictObj({"verdict": "行业共振"}),
        }
        patches.update(overrides)
        manager = unittest.mock.patch.multiple(deepdive, **patches)
        manager.start()
        self.addCleanup(manager.stop)

    def test_all_faces_filled_and_saved(self):
        self._patch_all()
        result = deepdive.deep_dive("A", "600001", with_scan=False)
        self.assertTrue(all(s.status == "ok" for s in result.steps), result.steps)
        card = Card.load("A", "600001")
        self.assertEqual(card.financials.metrics["净利润"]["2026-09-30"], "1.0亿")
        self.assertEqual(card.market_context["close"], 11.0)
        self.assertEqual(card.market_context["market_regime"]["state"], "下行")
        self.assertEqual(card.expectations["coverage"], 3)
        self.assertEqual(card.capital["fcf_label"], "-1.04 亿")
        self.assertEqual(card.peers["rank"], 3)
        self.assertEqual(card.industry_context["verdict"], "行业共振")

    def test_single_failure_does_not_block_others(self):
        def boom(code):
            raise RuntimeError("接口限流")

        self._patch_all(fetch_financials=boom)
        result = deepdive.deep_dive("A", "600001", with_scan=False)
        faces = {s.face: s.status for s in result.steps}
        self.assertEqual(faces["财务序列"], "fail")
        self.assertIn("接口限流", next(s.note for s in result.steps if s.face == "财务序列"))
        self.assertEqual(faces["分析师预期"], "ok")
        card = Card.load("A", "600001")           # 部分成功也落盘
        self.assertEqual(card.expectations["coverage"], 3)

    def test_industry_period_inferred_from_financials(self):
        captured = {}

        def fake_co(code, rows, **kw):
            captured["report_period"] = kw.get("report_period")
            return _DictObj({"verdict": "单点不可辨"})

        self._patch_all(co_occurrence=fake_co)
        deepdive.deep_dive("A", "600001", with_scan=False)
        self.assertEqual(captured["report_period"], "2026-09-30")   # 财务序列最新一期

    def test_hk_path_skips_reference_and_uses_hk_resonance(self):
        Card(market="HK", code="03333", name="港股测试", status="观察", view="测试").save()
        patches = {
            "fetch_snapshots": lambda codes: {"03333": Snapshot(
                market="HK", code="03333", name="港股测试", price=2.0, turnover=1e7,
                market_cap=40e8, pe=None, pb=None, change_pct=None, as_of="",
                lot_size=2000, shares=20e9)},
            "fetch_stock_financials": lambda code: {
                "source": "港股测试接口", "periods": ["2026-06-30"],
                "metrics": {"HOLDER_PROFIT": {"2026-06-30": "-1.0亿"}}},
            "fetch_expectations": lambda code, name, market: _DictObj(
                {"coverage": 0, "notes": ["该接口只覆盖 A 股"]}, coverage=0,
                eps_forecast=[], ratings=[], notes=["该接口只覆盖 A 股"]),
            "fetch_capital": lambda code: _capital(),
            "fetch_peers": lambda code, market: _peers(),
            "hk_discover": lambda since_days=120, limit=20: [],
            "hk_resonance": lambda code, peers, days=120: {
                "verdict": "单点不可辨", "industry": "建筑", "level": "hk"},
        }
        manager = unittest.mock.patch.multiple(deepdive, **patches)
        manager.start()
        self.addCleanup(manager.stop)
        result = deepdive.deep_dive("HK", "03333", with_scan=True)
        faces = {s.face: s.status for s in result.steps}
        self.assertEqual(faces["参考线与市场状态"], "skip")
        self.assertEqual(faces["行业共振"], "ok")
        self.assertIn("经营回顾对比", faces)          # HK 的扫描段换成了经营回顾
        card = Card.load("HK", "03333")
        self.assertEqual(card.industry_context["level"], "hk")
        self.assertEqual(card.market_context["lot_size"], 2000)
        # HK 科目名经 HK_METRIC_LABELS 映射成中文口径
        self.assertIn("归母净利润", card.financials.metrics)


class ListRegressionTests(unittest.TestCase):
    """cmd_list 不能再把 _excluded.json 当卡片解析（之前的崩溃点）。"""

    def test_list_skips_underscore_files(self):
        import tempfile

        from research import cli

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "A_600001.json").write_text(json.dumps(
                {"market": "A", "code": "600001", "name": "测试", "status": "观察",
                 "view": "x", "evidence": [], "log": []}, ensure_ascii=False),
                encoding="utf-8")
            (root / "_excluded.json").write_text("{}", encoding="utf-8")
            original = cli.CARD_DIR
            cli.CARD_DIR = root
            try:
                code = cli.cmd_list(argparse.Namespace())
            finally:
                cli.CARD_DIR = original
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
