import unittest
from pathlib import Path

from research import excluded


class ExclusionTests(unittest.TestCase):
    def setUp(self):
        self.original = excluded.STORE
        excluded.STORE = Path("/tmp/pickahkstock_excluded_test.json")
        if excluded.STORE.exists():
            excluded.STORE.unlink()

    def tearDown(self):
        if excluded.STORE.exists():
            excluded.STORE.unlink()
        excluded.STORE = self.original

    def test_exclude_and_check(self):
        excluded.exclude("A", "600888", reason="铝价周期股")
        self.assertTrue(excluded.is_excluded("A", "600888"))
        self.assertFalse(excluded.is_excluded("A", "600392"))

    def test_unexclude_restores(self):
        excluded.exclude("A", "600888", reason="x")
        self.assertTrue(excluded.unexclude("A", "600888"))
        self.assertFalse(excluded.is_excluded("A", "600888"))
        self.assertFalse(excluded.unexclude("A", "600888"))

    def test_reason_and_day_are_kept(self):
        item = excluded.exclude("A", "600888", reason="等回调")
        self.assertEqual(item.reason, "等回调")
        self.assertTrue(item.day)
        listed = excluded.list_exclusions()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].code, "600888")

    def test_market_is_part_of_the_key(self):
        excluded.exclude("A", "000001")
        excluded.exclude("HK", "000001")
        self.assertEqual(len(excluded.list_exclusions()), 2)


if __name__ == "__main__":
    unittest.main()
