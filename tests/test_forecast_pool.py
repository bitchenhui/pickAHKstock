import unittest
from unittest import mock

from research import forecast_pool

TEST_CACHE = forecast_pool.Path("/tmp/pickahkstock_pool_test")


def _row(code, notice, amp, amount, metric="归属于上市公司股东的净利润"):
    return {"SECURITY_CODE": code, "SECURITY_NAME_ABBR": f"股{code}",
            "NOTICE_DATE": notice, "REPORT_DATE": "2026-06-30",
            "PREDICT_FINANCE": metric, "ADD_AMP_LOWER": amp,
            "PREDICT_AMT_LOWER": amount}


class FakeAPI:
    """模拟东财接口：支持数值区间过滤、pageSize 封顶 500、返回真实总数。"""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def get(self, report_period, extra_filter="", page_size=500):
        self.calls += 1
        atoms = []
        for chunk in extra_filter.split(")("):
            chunk = chunk.strip().strip("()")
            if not chunk:
                continue
            for op in (">=", "<"):
                if op in chunk:
                    field, value = chunk.split(op)
                    atoms.append((field.strip(), op, float(value)))
                    break
        matched = []
        for row in self.rows:
            keep = True
            for field, op, value in atoms:
                actual = row.get(field)
                if actual is None:
                    keep = False
                    break
                if op == ">=" and not actual >= value:
                    keep = False
                elif op == "<" and not actual < value:
                    keep = False
            if keep:
                matched.append(row)
        return matched[:page_size], len(matched)


class ForecastPoolTests(unittest.TestCase):
    def setUp(self):
        TEST_CACHE.mkdir(parents=True, exist_ok=True)
        for path in TEST_CACHE.glob("pool_v*_2026-06-30.json"):
            path.unlink()

    def _fetch(self, rows):
        """在假接口下取一期，返回 (取到的行, 调用次数)。"""
        api = FakeAPI(rows)
        with mock.patch.object(forecast_pool, "_get", api.get):
            with mock.patch.object(forecast_pool, "CACHE_DIR", TEST_CACHE):
                result = forecast_pool.fetch_pool("2026-06-30", refresh=True)
        return result, api.calls

    def test_fetches_all_rows_beyond_first_page(self):
        """接口封顶 500 行：1200 行必须全部取到，不能只拿第一页。"""
        rows = [_row(f"{i:06d}", "2026-07-15", i - 300, 1e6 + i) for i in range(1200)]
        result, calls = self._fetch(rows)
        self.assertEqual(len(result), 1200)
        self.assertEqual(len({r["SECURITY_CODE"] for r in result}), 1200)
        self.assertGreater(calls, 1)

    def test_rows_with_null_amplitude_are_covered_by_amount_pass(self):
        """变动幅度为空的行（每股收益口径）由金额那一遍补回来。"""
        rows = [_row(f"{i:06d}", "2026-07-15", None, 1e6 + i, metric="每股收益")
                for i in range(300)]
        result, _ = self._fetch(rows)
        self.assertEqual(len(result), 300)

    def test_rows_with_identical_amplitude_are_split_by_amount(self):
        """同一批行变动幅度完全相同时，切分要能转到金额维度。"""
        rows = [_row(f"{i:06d}", "2026-07-15", 100.0, 1e6 + i * 1000) for i in range(900)]
        result, _ = self._fetch(rows)
        self.assertEqual(len(result), 900)

    def test_dedupe_keeps_one_row_per_company_metric_day(self):
        rows = [_row("002132", "2026-07-15", 100.0, 1e6),
                _row("002132", "2026-07-15", 100.0, 1e6)]
        result, _ = self._fetch(rows)
        self.assertEqual(len(result), 1)

    def test_empty_period_returns_empty_list(self):
        result, _ = self._fetch([])
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
