import unittest
import unittest.mock
from pathlib import Path

from research import tracking
from research.card import Card, Judgment


def _card(code="002132", name="恒星科技", status="观察") -> Card:
    return Card(market="A", code=code, name=name, status=status,
                evidence=[], judgments=[Judgment(id="J1", text="t", value=1.0)])


class AutoSnapshotTests(unittest.TestCase):
    """卡片状态变了就自动记快照——检验集要能自己长大。"""

    def setUp(self):
        import tempfile

        import research.card as card_mod

        self.tmp = tempfile.TemporaryDirectory()
        self.original = tracking.SNAPSHOT_DIR
        self.original_cards = card_mod.CARD_DIR
        tracking.SNAPSHOT_DIR = Path(self.tmp.name) / "snaps"
        # 卡片目录也要挪走，否则测试会把仓库里的真卡片覆盖掉
        card_mod.CARD_DIR = Path(self.tmp.name) / "cards"

    def tearDown(self):
        import research.card as card_mod

        tracking.SNAPSHOT_DIR = self.original
        card_mod.CARD_DIR = self.original_cards
        self.tmp.cleanup()

    def test_record_and_latest_snapshot(self):
        card = _card()
        tracking.record_snapshot(card, day="2026-10-30")
        latest = tracking.latest_snapshot("A", "002132")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.status, "观察")
        self.assertEqual(latest.day, "2026-10-30")

    def test_latest_snapshot_is_none_when_absent(self):
        self.assertIsNone(tracking.latest_snapshot("A", "999999"))

    def test_status_change_records_new_snapshot(self):
        card = _card(status="观察")
        tracking.record_snapshot(card, day="2026-10-30")
        card.status = "降级"
        card.save()
        latest = tracking.latest_snapshot("A", "002132")
        self.assertEqual(latest.status, "降级")

    def test_same_status_does_not_record_again(self):
        card = _card(status="观察")
        tracking.record_snapshot(card, day="2026-10-30")
        card.save()          # 状态没变
        self.assertEqual(len(list(tracking.SNAPSHOT_DIR.glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
