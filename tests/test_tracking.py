import json
import unittest
from datetime import date, timedelta

from research.card import Card, Judgment
from research.tracking import (SNAPSHOT_DIR, Snapshot, card_hash, load_snapshots,
                               record, score)


def card(code="002132", name="恒星科技", status="观察") -> Card:
    return Card(market="A", code=code, name=name, status=status,
                evidence=[], judgments=[Judgment(id="J1", text="t", value=1.0)])


class TrackingTests(unittest.TestCase):
    def test_card_hash_changes_with_content(self):
        first = card()
        second = card()
        self.assertEqual(card_hash(first), card_hash(second))
        second.judgments[0].value = 2.0
        self.assertNotEqual(card_hash(first), card_hash(second))

    def test_record_writes_snapshot_with_status_and_price(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            original = SNAPSHOT_DIR
            try:
                import research.tracking as tracking
                tracking.SNAPSHOT_DIR = Path(tmp)
                snapshots = record([card(status="降级")], date(2026, 10, 30),
                                   lambda code, market: 10.5)
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0].status, "降级")
                self.assertEqual(snapshots[0].price, 10.5)
                path = Path(tmp) / "2026-10-30_A_002132.json"
                self.assertTrue(path.exists())
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["card_hash"], card_hash(card(status="降级")))
            finally:
                import research.tracking as tracking
                tracking.SNAPSHOT_DIR = original

    def test_score_groups_by_snapshot_status(self):
        snapshots = [
            Snapshot(market="A", code="002132", name="恒星科技", status="降级",
                     card_hash="x", day="2026-10-01"),
            Snapshot(market="A", code="002044", name="美年健康", status="观察",
                     card_hash="y", day="2026-10-01"),
        ]
        series = {
            "002132": [{"day": f"2026-10-{d:02d}", "close": 10.0 + d * 0.1}
                        for d in range(1, 20)],
            "002044": [{"day": f"2026-10-{d:02d}", "close": 10.0 - d * 0.1}
                        for d in range(1, 20)],
        }
        result = score(snapshots, series, holds=(5,))
        self.assertIn("降级", result["summary"])
        self.assertIn("观察", result["summary"])
        down = result["summary"]["降级"]
        up = result["summary"]["观察"]
        self.assertGreater(down["median_5"], up["median_5"])

    def test_load_snapshots_ignores_broken_files(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            import research.tracking as tracking
            original = tracking.SNAPSHOT_DIR
            try:
                tracking.SNAPSHOT_DIR = Path(tmp)
                self.assertEqual(load_snapshots(), [])
            finally:
                tracking.SNAPSHOT_DIR = original


if __name__ == "__main__":
    unittest.main()
