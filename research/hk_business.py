"""港股经营回顾正文：港股没有可程序化取得的分部数字，但经营回顾是中文可读的。

A 股的「经营回顾」正文没有结构化接口，港股这边东方财富把报告里的文字抽了出来
（BUSINESS_REVIEW / FUTURE_EXPECT）。里面常常含有分部级描述与行业量价数据——
例如建材类公司会写出固定资产投资、地产投资、水泥产量与单位成本的变化，
这些正是「增长从哪来」在分部层面的证据。

局限：只有部分公司认真写经营回顾；写得敷衍的公司这里拿不到什么；
抽取质量取决于原始报告格式，数字要回原文核对。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "business"

# 主题词表：用来比较两期经营回顾的侧重变化
THEMES: dict[str, tuple[str, ...]] = {
    "价格": ("价格", "售价", "涨价", "下降", "回升", "单价", "跌"),
    "需求": ("需求", "订单", "开工", "销量", "销售量", "利用率", "景气"),
    "成本": ("成本", "降本", "减支", "原材料", "煤", "能耗", "费用", "管控"),
    "供给": ("产能", "产量", "供给", "库存", "错峰", "新增产能", "限产"),
    "盈利": ("亏损", "扭亏", "毛利", "利润", "减值", "盈利"),
    "竞争": ("份额", "竞争", "减量博弈", "存量", "内卷", "价格战"),
}


class BusinessError(RuntimeError):
    pass


@dataclass
class BusinessReview:
    period: str
    review: str
    outlook: str = ""

    def to_dict(self) -> dict:
        return {"period": self.period, "review": self.review, "outlook": self.outlook}


@dataclass
class ReviewDiff:
    period: str
    prior_period: str
    length_change: int
    theme_counts: dict = field(default_factory=dict)
    theme_delta: dict = field(default_factory=dict)
    new_phrases: list = field(default_factory=list)
    dropped_phrases: list = field(default_factory=list)
    numbers: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"period": self.period, "prior_period": self.prior_period,
                "length_change": self.length_change, "theme_counts": self.theme_counts,
                "theme_delta": self.theme_delta, "new_phrases": self.new_phrases,
                "dropped_phrases": self.dropped_phrases, "numbers": self.numbers}


def fetch_reviews(code: str, market: str = "HK") -> list[BusinessReview]:
    """取历期经营回顾正文（按报告期倒序）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{code}_{market}.json"
    if cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
        except ValueError:
            raw = None
    else:
        raw = None
    if raw is None:
        params = {"reportName": "RPT_HKF10_ORG_BUSSINESS", "columns": "ALL",
                  "pageSize": "30", "pageIndex": "1", "client": "WEB",
                  "sortColumns": "REPORT_DATE", "sortTypes": "-1",
                  "filter": f"(SECUCODE=\"{code}.{market.upper()}\")"}
        try:
            response = requests.get(DC, headers=HEADERS, params=params, timeout=200)
            response.raise_for_status()
            raw = (response.json().get("result") or {}).get("data") or []
        except (requests.RequestException, ValueError) as exc:
            raise BusinessError(f"经营回顾获取失败 {code}：{exc}") from exc
        cache_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    reviews = [BusinessReview(period=str(item.get("REPORT_DATE") or "")[:10],
                               review=str(item.get("BUSINESS_REVIEW") or "").strip(),
                               outlook=str(item.get("FUTURE_EXPECT") or "").strip())
               for item in raw]
    reviews.sort(key=lambda r: r.period, reverse=True)
    return reviews


def _themes(text: str) -> dict[str, int]:
    return {name: sum(text.count(word) for word in words) for name, words in THEMES.items()}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[。；;\n]", text)
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def diff_reviews(reviews: list[BusinessReview], current_index: int = 0) -> ReviewDiff | None:
    """比较最近一期与去年同期（按报告期季度对齐）的经营回顾。"""
    if len(reviews) < 2:
        return None
    current = reviews[current_index]
    period = current.period
    if len(period) < 10:
        return None
    target = f"{int(period[:4]) - 1}{period[4:7]}"
    prior = next((r for r in reviews if r.period[:7] == target), None)
    if prior is None:
        return None
    current_counts, prior_counts = _themes(current.review), _themes(prior.review)
    current_sentences, prior_sentences = _sentences(current.review), _sentences(prior.review)
    new_phrases = [s for s in current_sentences if not _mentions(s, prior_sentences)]
    dropped = [s for s in prior_sentences if not _mentions(s, current_sentences)]
    numbers = re.findall(r"[^\s，。；]{0,12}(?:同比)?(?:下降|减少|增长|上升|增加|提高|降低)[^\s，。；]{0,10}"
                         r"(?:\d+(?:\.\d+)?%|亏损|盈利|转亏为盈)", current.review)[:12]
    return ReviewDiff(
        period=period, prior_period=prior.period,
        length_change=len(current.review) - len(prior.review),
        theme_counts=current_counts,
        theme_delta={name: current_counts[name] - prior_counts[name] for name in current_counts},
        new_phrases=new_phrases[:8], dropped_phrases=dropped[:8], numbers=numbers)


def _mentions(sentence: str, others: list[str]) -> bool:
    """粗略判断一句话是否在另一组句子里出现过（用数字与专有词做锚）。"""
    anchors = set(re.findall(r"\d+(?:\.\d+)?%?|[一-鿿]{2,6}(?:分部|行业|价格|成本|产量)", sentence))
    if not anchors:
        return False
    joined = " ".join(others)
    return sum(1 for a in anchors if a in joined) >= max(1, len(anchors) // 2)
