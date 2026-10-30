"""卡片数字的回归核对：把卡片里写下的分产品数据，回到数据源重算。

这是**研究判断的准确性检验**——不是收益检验（那要等 60 个交易日）。

为什么值得常驻：卡片里的每个数字都是手抄的，抄错一次结论就歪了。
本项目已经犯过一次量纲错误（把比率当百分点，+17.34pct 写成 +0.17pct，
导致四象限判断全部失真）。这类错误肉眼很难发现，但重算一次就能拦住。

数据源变化时（例如同花顺补上了新的报告期），测试会失败并说明「卡片需要更新」，
这正是它该做的事——卡片结论变了要有人知道，而不是悄悄过期。
"""

import re
import unittest

from research.card import Card
from research.segments import compare, fetch_segments

# 每张卡片写下的分产品结论：{产品名: (收入同比%, 毛利率变动pct)}
CLAIMS = {
    "002132": {
        "化学产品": (32.1, 17.34),
        "金刚线": (48.6, 12.88),
        "其他产品": (257.0, -41.98),
        "钢帘线": (-1.7, -0.79),
        "预应力钢绞线": (-0.3, -1.16),
        "胶管钢丝": (-2.8, -0.17),
    },
    "002044": {
        "体检服务": (-8.1, 0.20),
        "其他服务": (30.3, -0.63),
    },
    "605358": {
        "半导体硅片": (28.6, 8.68),
        "化合物半导体射频和光电芯片": (59.0, -14.80),
        "半导体功率器件芯片": (8.8, -7.37),
    },
    "000626": {
        "特种油脂": (7.6, 1.65),
        "作物科学": (-0.8, 0.06),
    },
}


class CardClaimTests(unittest.TestCase):
    def test_segment_claims_match_source(self):
        """卡片里写的分产品数字必须与数据源重算一致。"""
        for code, claims in CLAIMS.items():
            with self.subTest(code=code):
                rows = fetch_segments(code)
                changes = {c.item: c for c in compare(rows, "按产品")}
                if not changes:
                    self.skipTest(f"{code} 没有可比的分产品数据")
                for item, (income_yoy, margin_change) in claims.items():
                    actual = changes.get(item)
                    self.assertIsNotNone(actual, f"{code} 缺少 {item} 的对比数据")
                    self.assertAlmostEqual(
                        round(actual.income_yoy, 1), income_yoy, places=1,
                        msg=f"{code} {item} 收入同比与卡片不符（卡片写 {income_yoy}）")
                    self.assertAlmostEqual(
                        round(actual.margin_change, 2), margin_change, places=1,
                        msg=f"{code} {item} 毛利率变动与卡片不符（卡片写 {margin_change}）")

    def test_card_text_contains_the_claimed_numbers(self):
        """卡片正文里出现的数字必须与 CLAIMS 一致，防止改表不改文。"""
        for code, claims in CLAIMS.items():
            with self.subTest(code=code):
                card = Card.load("A", code)
                text = " ".join(e.claim for e in card.evidence)
                for item, (income_yoy, margin_change) in claims.items():
                    if item not in text:
                        continue
                    pattern = re.compile(
                        rf"{re.escape(item)}[^。；]{{0,60}}?{abs(income_yoy):.1f}")
                    self.assertTrue(pattern.search(text),
                                    f"{code} 正文里找不到 {item} 的 {income_yoy}% 表述")

    def test_no_comparable_data_cases(self):
        """000626 的贸易业务、603991 的全部产品都应「无同比可比数据」。"""
        for code, items in (("000626", ("商品贸易", "贸易业务", "物流代理")),
                            ("603991", ("引线框架", "半导体专用设备"))):
            with self.subTest(code=code):
                rows = fetch_segments(code)
                changes = {c.item: c for c in compare(rows, "按产品")}
                for item in items:
                    if item in changes:
                        self.assertIsNone(changes[item].income_yoy,
                                          f"{code} {item} 应无同比可比数据")


if __name__ == "__main__":
    unittest.main()
