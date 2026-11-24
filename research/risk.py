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

# 港股公告标题是繁体，用词也不同（例：冻结=凍結、减值=減值、供股=供股）
HK_RISK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "仲裁与诉讼": ("仲裁", "訴訟", "法律程序", "法律程序", "頒令", "起訴", "索償"),
    "账户与偿债": ("凍結", "冻结", "違約", "违约", "逾期", "債務重組", "债务重组",
                   "清盤", "清盘", "呈請", "呈请", "強制出售"),
    "股权质押": ("質押", "质押", "抵押"),
    "监管问询": ("聯交所", "联交所", "披露易", "制裁", "調查", "调查", "監管", "监管", "指令"),
    "资产质量": ("減值", "减值", "撇賬", "撇帳", "撇账", "應收", "呆賬"),
    "治理变动": ("辭任", "辞任", "辭職", "辞职", "離任", "离任", "罷免", "免職"),
    "停复牌与退市": ("停牌", "复牌", "復牌", "除牌", "短暫停牌", "短暂停牌"),
    "股本摊薄": ("供股", "配售", "認購", "认购", "發行新股", "发行新股", "股份合併", "股份合并"),
}

# 与研究无关的常规公告，不列入风险
ROUTINE_NOISE = ("会议决议", "法律意见书", "会议资料", "股东会决议", "提名", "候选人",
                  "会议通知", "股东大会资料", "董事会决议", "监事会决议", "选举",
                  "会计师事务所", "续聘", "募集资金存放", "募集资金使用情况",
                  "投资者关系", "调研", "独立董事", "更正", "补充更正", "更正后",
                  "月報表", "月报表", "翌日披露報表", "翌日披露报表")


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


def classify(title: str, market: str = "A") -> str | None:
    if any(noise in title for noise in ROUTINE_NOISE):
        return None
    table = RISK_KEYWORDS if market == "A" else HK_RISK_KEYWORDS
    for category, words in table.items():
        if any(word in title for word in words):
            return category
    return None

# 股东、董监高的增减持与公司回购：按标题分到一侧。排除词优先——
# 「终止减持」「承诺不减持」都不是减持；「回购注销限制性股票」是股权激励的
# 收尾，不是回购。风险关键词表里本来没有「增持/减持」，所以之前从风险扫描
# 结果里是提取不到增减持的，必须单独分类。
HOLDER_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("回购", ("回购", "回購"), ("终止", "不回购", "限制性股票", "股权激励", "可转债")),
    ("增持", ("增持",), ("不增持", "终止", "放弃", "取消", "未增持", "延期")),
    ("减持", ("减持", "減持"), ("不减持", "不减持", "终止", "取消", "放弃", "未减持", "延期")),
)


def classify_holder(title: str) -> dict | None:
    """标题属于增持/减持/回购的哪一侧，以及处于计划、进展还是完成阶段。"""
    for side, words, excluded in HOLDER_RULES:
        if not any(word in title for word in words) or any(word in title for word in excluded):
            continue
        # 「增持计划实施完成」既含「计划」又含「完成」：先判完成，再判计划
        if any(word in title for word in ("完成", "完毕", "结束", "結果", "结果")):
            stage = "完成"
        elif "计划" in title or "計劃" in title:
            stage = "计划"
        elif "进展" in title or "進展" in title:
            stage = "进展"
        else:
            stage = ""
        return {"side": side, "stage": stage}
    return None


def scan_announcements(code: str, name: str, since_days: int = 240,
                       market: str = "A") -> dict:
    """按公司名取全部公告，一次抓取同时做两件事：风险分类、增减持分类。

    A 股走巨潮；港股走东方财富公告流（HKEXnews 的检索接口实测取不到数据，
    已弃用）。港股的公告标题是繁体，用词也不同，见 HK_RISK_KEYWORDS。

    返回 {"risks": [RiskEvent], "holders": [{side, stage, title, date, url}]}。
    """
    today = date.today()
    start = today - timedelta(days=since_days)
    if market == "A":
        items = [(item.title, item.published, item.url)
                 for item in fetch_announcements(name, start, today, max_pages=30)
                 if item.code == code]
    else:
        from .hk import fetch_stock_events

        items = [(item.title, item.published, item.detail_url)
                 for item in fetch_stock_events(code, start, today)]
    risks: list[RiskEvent] = []
    holders: list[dict] = []
    for title, published, url in items:
        category = classify(title, market)
        if category:
            risks.append(RiskEvent(code, name, title, published, url, category))
        hit = classify_holder(title)
        if hit:
            holders.append({"side": hit["side"], "stage": hit["stage"],
                            "title": title[:80], "date": published.isoformat(),
                            "url": url or ""})
    risks.sort(key=lambda event: event.published, reverse=True)
    holders.sort(key=lambda item: item["date"], reverse=True)
    return {"risks": risks, "holders": holders}


def scan_risks(code: str, name: str, since_days: int = 240,
               market: str = "A") -> list[RiskEvent]:
    """只要风险类公告时用这个；需要增减持时用 scan_announcements（同一次抓取）。"""
    return scan_announcements(code, name, since_days, market)["risks"]
