"""风险公告扫描：候选被业绩预告挑出来之后，还要看它身上有没有法律、监管、
治理与资产质量的红旗。

003 领先股份的 1.44 亿元仲裁与银行账户冻结，就是从「按公司名取全部公告」
里翻出来的——只筛业绩预告会漏掉这一整类信息。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .events import fetch_announcements

# 标题关键词 → 风险类别。宁可多报，不漏报；最终判断留给卡片。
RISK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "仲裁与诉讼": ("仲裁", "诉讼", "起诉", "判决", "强制执行", "财产保全"),
    "账户与偿债": ("冻结", "逾期", "债务", "资金占用", "违规担保", "偿债", "破产", "重整"),
    "股权质押": ("质押", "解押", "平仓", "被动减持"),
    "监管问询": ("问询函", "关注函", "监管", "立案", "处罚", "警示函", "违规", "责令"),
    "审计意见": ("保留意见", "无法表示意见", "非标", "强调事项", "审计"),
    "重组变更": ("终止", "撤回", "中止", "变更", "重组", "购买资产", "并购"),
    "资产质量": ("减值", "计提", "商誉", "存货跌价", "坏账", "业绩预减", "业绩变脸", "下修"),
    "治理变动": ("辞职", "离任", "解聘", "免职", "换届", "更换", "追诉", "处罚"),
    "停复牌与风险警示": ("停牌", "复牌", "退市", "风险警示", "*ST", "其他风险警示"),
}

# 与研究无关的常规公告，不列入风险
ROUTINE_NOISE = ("会议决议", "法律意见书", "会议资料", "股东会决议", "提名", "候选人",
                  "会议通知", "股东大会资料", "董事会决议", "监事会决议", "选举",
                  "会计师事务所", "续聘", "募集资金存放", "募集资金使用情况",
                  "投资者关系", "调研", "独立董事", "更正", "补充更正", "更正后")


class RiskEvent:
    __slots__ = ("code", "name", "title", "published", "url", "category")

    def __init__(self, code: str, name: str, title: str, published: date, url: str, category: str):
        self.code, self.name, self.title = code, name, title
        self.published, self.url, self.category = published, url, category

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "title": self.title,
                "published": self.published.isoformat(), "url": self.url,
                "category": self.category}

    def __str__(self) -> str:
        return f"[{self.category}] {self.published} {self.title}  {self.url}"


def classify(title: str) -> str | None:
    if any(noise in title for noise in ROUTINE_NOISE):
        return None
    for category, words in RISK_KEYWORDS.items():
        if any(word in title for word in words):
            return category
    return None


def scan_risks(code: str, name: str, since_days: int = 240,
               market: str = "A") -> list[RiskEvent]:
    """按公司名取全部公告，再筛出风险类标题。

    A 股走巨潮；港股走 HKEXnews（若接口不可用则返回空并由调用方标注缺口）。
    """
    today = date.today()
    start = today - timedelta(days=since_days)
    events: list[RiskEvent] = []
    if market == "A":
        items = fetch_announcements(name, start, today, max_pages=30)
        for item in items:
            if item.code != code:
                continue
            category = classify(item.title)
            if category:
                events.append(RiskEvent(code, name, item.title, item.published, item.url, category))
    else:
        from .hk import fetch_hk_announcements

        for item in fetch_hk_announcements(start, today, title=""):
            if item.code != code:
                continue
            category = classify(item.title)
            if category:
                events.append(RiskEvent(code, name, item.title, item.published, item.url, category))
    return sorted(events, key=lambda e: e.published, reverse=True)
