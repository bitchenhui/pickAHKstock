import unittest
from io import StringIO

from retail_agent.data import DataError, import_csv, normalize_bars, normalize_quotes


class Frame:
    def __init__(self, rows):
        self.rows = rows
        self.columns = list(rows[0]) if rows else []

    def to_dict(self, orientation):
        assert orientation == "records"
        return self.rows


class DataTests(unittest.TestCase):
    def test_quote_schema_and_hk_cap_unknown(self):
        rows = [{"代码": "700", "名称": "样本", "最新价": "20.5", "成交额": "15,000,000"}]
        self.assertEqual(normalize_quotes(Frame(rows), "HK")[0].code, "00700")
        self.assertIsNone(normalize_quotes(Frame(rows), "HK")[0].market_cap)
        with self.assertRaises(DataError):
            normalize_quotes(Frame(rows), "A")

    def test_bars_schema(self):
        rows = [{"日期": "2026-10-09", "开盘": 10, "最高": 11, "最低": 9,
                 "收盘": 10.5, "成交量": 1000, "成交额": 10500}]
        self.assertEqual(normalize_bars(Frame(rows))[0].close, 10.5)
        with self.assertRaises(DataError):
            normalize_bars(Frame([{"日期": "2026-10-09"}]))

    def test_csv_preserves_leading_zeroes(self):
        quotes = StringIO("代码,名称,最新价,成交额,总市值\n002001,样本,12,80000000,8000000000\n")
        bars = StringIO("代码,日期,开盘,最高,最低,收盘,成交量,成交额\n"
                        "002001,2026-10-09,11,13,10,12,1000,1000000\n")
        normalized, histories = import_csv(quotes, bars, "A")
        self.assertEqual(normalized[0].code, "002001")
        self.assertEqual(histories["002001"][0].day.isoformat(), "2026-10-09")


if __name__ == "__main__":
    unittest.main()
