"""分析个股的补充证据：资本轨迹与同业对照。

这两块是卡片里一直没接、但对判断很关键的输入：

**资本轨迹**（同花顺 F10 现金流量表与资产负债表）
- 资本开支（购建固定资产、无形资产和其他长期资产支付的现金）
- 经营活动现金流净额
- 在建工程、固定资产合计
- 自由现金流 = 经营现金流 − 资本开支

重资本公司（立昂微、恒星科技）光看利润会失真：利润在涨、现金流在流出、
在建工程在扩张，三者合起来才是「这门生意在不在变好」。

**同业对照**（东方财富估值比较）
- 该股在其同业中的排名与分位
- 市盈率 TTM 与远期（25E/26E/27E，基于分析师一致预期）
- 市净率、市销率、市现率、PEG

远期 PE 那一串尤其有用：它把「分析师预期」换算成了「市场愿意为这个预期付多少倍」。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "context"

CAPEX_KEYS = ("购建固定资产、无形资产和其他长期资产支付的现金",)
OPERATING_KEYS = ("经营活动产生的现金流量净额",)
CONSTRUCTION_KEYS = ("在建工程合计",)
FIXED_ASSET_KEYS = ("固定资产合计",)


class ContextError(RuntimeError):
    pass


@dataclass
class CapitalTrack:
    code: str
    periods: list = field(default_factory=list)
    capex: list = field(default_factory=list)
    operating_cf: list = field(default_factory=list)
    construction: list = field(default_factory=list)
    fixed_assets: list = field(default_factory=list)
    source: str = ""
    notes: list = field(default_factory=list)

    def _value(self, series: list, index: int) -> float | None:
        if index >= len(series):
            return None
        return _parse_amount(series[index])

    def capex_at(self, index: int = 0):
        return self._value(self.capex, index)

    def operating_cf_at(self, index: int = 0):
        return self._value(self.operating_cf, index)

    def fcf_at(self, index: int = 0):
        """自由现金流 = 经营现金流 − 资本开支。"""
        capex = self.capex_at(index)
        operating = self.operating_cf_at(index)
        if capex is None or operating is None:
            return None
        return operating - capex

    def to_dict(self) -> dict:
        return {"code": self.code, "periods": self.periods, "capex": self.capex,
                "operating_cf": self.operating_cf, "construction": self.construction,
                "fixed_assets": self.fixed_assets, "source": self.source, "notes": self.notes}


@dataclass
class PeerValuation:
    code: str
    name: str = ""
    market: str = "A"
    rank: str = ""
    rows: dict = field(default_factory=dict)
    source: str = ""
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "market": self.market,
                "rank": self.rank, "rows": self.rows, "source": self.source,
                "notes": self.notes}


def _parse_amount(text: str) -> float | None:
    """同花顺金额字符串：'-15.95亿' / '3242.01万' / '1234.56'。"""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw or raw in ("False", "True", "-"):
        return None
    multiplier = 1.0
    if raw.endswith("亿"):
        multiplier, raw = 1e8, raw[:-1]
    elif raw.endswith("万"):
        multiplier, raw = 1e4, raw[:-1]
    elif raw.endswith("元"):
        raw = raw[:-1]
    raw = raw.replace(",", "")
    try:
        return float(raw) * multiplier
    except ValueError:
        return None


def _f10_statement(code: str, kind: str) -> dict:
    """同花顺 F10 报表：cash=现金流量表，debt=资产负债表。"""
    url = f"https://basic.10jqka.com.cn/api/stock/finance/{code}_{kind}.json"
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ContextError(f"同花顺 F10 {kind} 获取失败 {code}：{exc}") from exc
    flash = payload.get("flashData")
    if isinstance(flash, str):
        try:
            flash = json.loads(flash)
        except ValueError as exc:
            raise ContextError(f"F10 {kind} 内容无法解析") from exc
    if not isinstance(flash, dict):
        raise ContextError(f"F10 {kind} 结构异常")
    return flash


def fetch_capital(code: str, periods: int = 6) -> CapitalTrack:
    """资本开支、经营现金流、在建工程与固定资产（最近若干期）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"capital_{code}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data:
                return CapitalTrack(**{k: data[k] for k in
                                      ("code", "periods", "capex", "operating_cf",
                                       "construction", "fixed_assets", "source", "notes")})
        except (ValueError, KeyError):
            pass
    track = CapitalTrack(code=code, source="同花顺 F10 现金流量表与资产负债表")
    try:
        cash = _f10_statement(code, "cash")
        debt = _f10_statement(code, "debt")
    except ContextError as exc:
        track.notes.append(str(exc))
        return track

    def extract(statement: dict, keys: tuple[str, ...]) -> list:
        title = statement.get("title") or []
        report = statement.get("report") or []
        if not report:
            return []
        periods = list(report[0])
        rows = report[1:]
        names = [x[0] if isinstance(x, list) and x else "" for x in title[1:]]
        for index, name in enumerate(names):
            if name in keys and index < len(rows):
                return [str(v) for v in rows[index]]
        return []

    track.periods = list(cash.get("report", [[]])[0])[:periods]
    track.capex = extract(cash, CAPEX_KEYS)[:periods]
    track.operating_cf = extract(cash, OPERATING_KEYS)[:periods]
    track.construction = extract(debt, CONSTRUCTION_KEYS)[:periods]
    track.fixed_assets = extract(debt, FIXED_ASSET_KEYS)[:periods]
    cache_file.write_text(json.dumps(track.to_dict(), ensure_ascii=False), encoding="utf-8")
    return track


def fetch_peers(code: str, market: str = "A") -> PeerValuation:
    """同业对照：估值分位与远期 PE（基于分析师一致预期）。"""
    import akshare as ak

    valuation = PeerValuation(code=code, market=market)
    try:
        if market == "HK":
            frame = ak.stock_hk_valuation_comparison_em(symbol=code)
            valuation.source = "东方财富 港股估值比较"
        else:
            prefix = "SH" if code.startswith("6") else "SZ"
            frame = ak.stock_zh_valuation_comparison_em(symbol=f"{prefix}{code}")
            valuation.source = "东方财富 A股估值比较"
    except Exception as exc:
        valuation.notes.append(f"同业对照取不到：{type(exc).__name__}")
        return valuation
    if frame is None or len(frame) == 0:
        valuation.notes.append("没有同业对照数据")
        return valuation
    row = frame.iloc[0].to_dict()
    valuation.name = str(row.get("简称") or "")
    valuation.rank = str(row.get("排名") or "")
    valuation.rows = {key: row.get(key) for key in row.keys() if key not in ("代码", "简称", "排名")}
    return valuation
