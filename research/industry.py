"""同业共振：分清「这是行业性变化」还是「这是公司自己的变化」。

卡片里最容易出错的一句话是「业绩大幅改善」——它到底说明这家公司变好了，
还是说明整个行业变好了？这两件事对持仓的含义完全不同：

- **公司特有**：同业没有同样的变化 → 是这家公司自己的经营变化，可以当成个股逻辑
- **行业共振**：同一行业里好几家同时发强预告 → 更可能是行业性 beta，
  公司的「好」是行业给的，不是它挣的；这种信号对整个候选池都成立，
  选谁的区别变小，而且行业一旦反转，所有人一起跌

数据口径（都是点对点可核对的）：
- 行业分类：申万一级 / 申万二级（成分股接口，全市场 5221 只）
- 预告池：东方财富业绩预告按报告期取（与回测、发现层同一批数据同一口径）

**比的是方向，不是幅度。** 池子里 85% 的行「|变动| ≥ 30%」——因为预告本来就只
对大幅变动的公司披露，涨幅小的公司压根不在池子里，拿这个当指标等于没筛。
真正能区分的是**业绩变动方向是否与同业一致**：同向说明行业在同向变（共振），
逆向说明它是公司自己的事。

分母的选择很关键：A 股业绩预告只对大幅变动者强制披露，所以「发预告」这件事
本身已经是一次自选择。共振的分母用的是**同行业里发了预告的公司数**，不是行业
全部公司数——否则会把「行业里大部分公司压根不需要发预告」错记成「行业很冷清」。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "industry"

LEVELS = {"first": "一级", "second": "二级"}
# 行业样本少于这个数就说不了话（一家公司没有「共振」可言）
MIN_PEERS = 3
# 判定阈值：同向率达到这个比例就算行业共振
RESONANCE_RATE = 0.85
# 相对倍数（行业同向率 / 全市场同向率）达到这个值也算共振
RESONANCE_LIFT = 1.5
# 相对倍数低于这个值，且同向率不高，就是公司自己的事
IDIOSYNCRATIC_LIFT = 0.67
# 同向率这么低时，即使倍数不低也按「公司特有」处理
LOW_RATE = 0.5


class IndustryError(RuntimeError):
    pass


def _sw_lists() -> dict[str, list[tuple[str, str]]]:
    """申万一级 / 二级：行业代码 → 行业名称。"""
    import akshare as ak

    out = {}
    for level, func_name in (("first", "sw_index_first_info"),
                              ("second", "sw_index_second_info")):
        frame = getattr(ak, func_name)()
        rows = []
        for row in frame.to_dict("records"):
            code = str(row.get("行业代码") or "").split(".")[0]
            name = str(row.get("行业名称") or "")
            if code and name:
                rows.append((code, name))
        out[level] = rows
    return out


def _sw_constituents(index_code: str) -> list[str]:
    import akshare as ak

    frame = ak.index_component_sw(symbol=index_code)
    if frame is None or len(frame) == 0:
        return []
    return [str(code).strip() for code in frame["证券代码"].tolist() if str(code).strip()]


def industry_map(level: str = "second", refresh: bool = False) -> dict[str, str]:
    """全市场 code → 申万行业名。落盘缓存，只建一次。"""
    if level not in LEVELS:
        raise IndustryError(f"行业层级只支持 {sorted(LEVELS)}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"sw_{level}_map.json"
    if cache_file.exists() and not refresh:
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data:
                return data
        except ValueError:
            pass

    import akshare as ak
    from concurrent.futures import ThreadPoolExecutor

    lists = _sw_lists()
    mapping: dict[str, str] = {}
    errors: list[str] = []

    def one(item: tuple[str, str]) -> tuple[str, str, list[str], str]:
        code, name = item
        for attempt in range(3):
            try:
                return code, name, _sw_constituents(code), ""
            except Exception as exc:  # 网络抖动重试
                if attempt == 2:
                    return code, name, [], f"{code} {name}：{type(exc).__name__}"
                time.sleep(1.5 * (attempt + 1))
        return code, name, [], "unreachable"

    items = lists.get(level) or []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for code, name, members, err in pool.map(one, items):
            if err:
                errors.append(err)
                continue
            for member in members:
                mapping[member] = name

    if not mapping:
        raise IndustryError("行业成分股一个都没取到")
    cache_file.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    if errors:
        (CACHE_DIR / f"sw_{level}_errors.txt").write_text("\n".join(errors), encoding="utf-8")
    return mapping


def industry_of(code: str, level: str = "second") -> str:
    return industry_map(level).get(code, "")


@dataclass
class Resonance:
    """一家公司所处行业的预告共振情况。"""
    code: str
    name: str = ""
    industry: str = ""
    level: str = "second"
    report_period: str = ""
    industry_members: int = 0        # 申万该行业的成分股总数
    peers_with_forecast: int = 0      # 同行业里发了预告的公司数
    peers_same_sign: int = 0          # 其中业绩变动方向与本公司一致的
    peers_strong: int = 0             # 其中幅度达标的（不论方向）
    peers_up: int = 0                 # 其中业绩改善的
    peers_down: int = 0               # 其中业绩恶化的
    same_sign_rate: float = 0.0       # 同行业同向比例
    market_same_sign_rate: float = 0.0  # 全市场同向比例
    lift: float = 0.0                 # 同行业同向比例 / 全市场同向比例
    penetration: float = 0.0          # 行业发预告比例（行业内发预告数 / 行业成分数）
    market_penetration: float = 0.0    # 全市场发预告比例
    median_change_industry: float | None = None   # 同行业同向公司的变动中位数
    median_change_market: float | None = None     # 全市场同向公司的变动中位数
    same_sign_peers: list[dict] = field(default_factory=list)
    background_period: str = ""       # 行业背景用的另一个（更完整）报告期
    background_up_rate: float = 0.0    # 该期行业改善率
    background_market_up_rate: float = 0.0  # 该期全市场改善率
    background_lift: float = 0.0       # 行业改善率 / 全市场改善率
    background_note: str = ""
    verdict: str = ""
    note: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def _change_of(row: dict) -> float | None:
    try:
        return float(row.get("ADD_AMP_LOWER"))
    except (TypeError, ValueError):
        return None


def _classify(rate: float, lift: float, peers: int, same_sign: int) -> tuple[str, str]:
    """共振判定：以**同向率**为主、相对倍数为辅。

    为什么同向率优先于相对倍数：市场整体改善时（比如全市场 52% 改善），
    一个「全部改善」的行业相对倍数只有 1.9 左右，看着像「介于两者之间」，
    但它其实是共振的极端情形——行业里没有一家不同向。
    """
    if peers < MIN_PEERS:
        return ("单点不可辨", f"同行业只有 {peers} 家发预告，说不清是共振还是特有")
    if same_sign == 0:
        return ("公司特有", "同行业没有一家业绩变动方向相同，这是它自己的事")
    if rate >= RESONANCE_RATE:
        return ("行业共振", f"同行业 {same_sign}/{peers} 家同向，几乎一致，"
                            "更像行业 beta 而不是公司特有")
    if rate <= LOW_RATE and lift <= 1.0:
        return ("公司特有", f"同行业只有 {same_sign}/{peers} 家同向，明显弱于全市场平均")
    if lift >= RESONANCE_LIFT:
        return ("行业共振", f"同行业同向率 {rate * 100:.0f}%，明显高于全市场平均，"
                            "更像行业 beta")
    if lift <= IDIOSYNCRATIC_LIFT:
        return ("公司特有", f"同行业同向率 {rate * 100:.0f}%，明显低于全市场平均")
    return ("介于两者之间", f"同行业 {same_sign}/{peers} 家同向，共振度居中")


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 1)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 1)


def resonance_from_pairs(code: str, pairs: list[tuple[str, float]],
                          industry: str = "", report_period: str = "",
                          threshold: float = 30.0, level: str = "second",
                          name: str = "",
                          background_pairs: list[tuple[str, float]] | None = None,
                          background_period: str = "",
                          company_change: float | None = None) -> Resonance:
    """核心计算：给定 (代码, 业绩变动幅度) 对，算某只票的行业共振。

    pairs 是**主指标口径**下的全池数据（每家公司一条），行业归属从申万成分表查。
    """
    mapping = industry_map(level)
    result = Resonance(code=code, name=name, level=level, industry=industry,
                        report_period=report_period or "",
                        source="申万%s行业成分股 + 东方财富业绩预告" % LEVELS[level])
    if company_change is None:
        for pair_code, change in pairs:
            if pair_code == code:
                company_change = change
                break
    if company_change is None:
        result.verdict = "不在预告池"
        result.note = "该公司该报告期没有预告记录，无法判断共振"
        return result
    if not industry:
        industry = mapping.get(code, "")
        result.industry = industry
    if not industry:
        result.verdict = "行业未知"
        result.note = f"申万{LEVELS[level]}行业里找不到 {code}"
        return result

    def same_sign(value: float) -> bool:
        return (value >= 0) if company_change >= 0 else (value < 0)

    peers = [(pair_code, change) for pair_code, change in pairs
             if mapping.get(pair_code) == industry]
    result.peers_with_forecast = len(peers)
    result.peers_same_sign = sum(1 for _, change in peers if same_sign(change))
    result.peers_strong = sum(1 for _, change in peers if abs(change) >= threshold)
    result.peers_up = sum(1 for _, change in peers if change >= 0)
    result.peers_down = sum(1 for _, change in peers if change < 0)
    result.same_sign_peers = [
        {"code": peer_code, "change": change}
        for peer_code, change in sorted(peers, key=lambda x: -abs(x[1]))
        if same_sign(change) and peer_code != code][:12]

    market_same = sum(1 for _, change in pairs if same_sign(change))
    result.market_same_sign_rate = market_same / len(pairs) if pairs else 0.0
    result.same_sign_rate = (result.peers_same_sign / result.peers_with_forecast
                             if result.peers_with_forecast else 0.0)
    result.lift = (result.same_sign_rate / result.market_same_sign_rate
                   if result.market_same_sign_rate else 0.0)

    members = sum(1 for name in mapping.values() if name == industry)
    result.industry_members = members
    result.penetration = (result.peers_with_forecast / members) if members else 0.0
    result.market_penetration = len(pairs) / len(mapping) if mapping else 0.0
    result.median_change_industry = _median([change for _, change in peers
                                              if same_sign(change)])
    result.median_change_market = _median([change for _, change in pairs
                                            if same_sign(change)])
    result.verdict, result.note = _classify(
        result.same_sign_rate, result.lift, result.peers_with_forecast,
        result.peers_same_sign)

    if background_pairs:
        result.background_period = background_period
        bg_industry = [change for pair_code, change in background_pairs
                       if mapping.get(pair_code) == industry]
        if bg_industry:
            result.background_up_rate = sum(1 for c in bg_industry if c >= 0) / len(bg_industry)
            market_up = sum(1 for _, change in background_pairs if change >= 0) / len(background_pairs)
            result.background_market_up_rate = market_up
            result.background_lift = (result.background_up_rate / market_up) if market_up else 0.0
            result.background_note = (f"{background_period} 该行业 {len(bg_industry)} 家发预告，"
                                      f"改善率 {result.background_up_rate * 100:.0f}%"
                                      f"（全市场 {market_up * 100:.0f}%），"
                                      f"相对倍数 {result.background_lift:.2f}")
    return result


def co_occurrence(code: str, rows: list[dict], report_period: str = "",
                  threshold: float = 30.0, level: str = "second",
                  name: str = "", background_rows: list[dict] | None = None,
                  background_period: str = "") -> Resonance:
    """按原始行算某只票的行业共振。

    rows：同一报告期的预告行；每家公司按**主指标口径**取一条
    （归母 > 扣非 > 营收），与发现层、回测层同一口径。
    """
    pairs = _pairs_from_rows(rows)
    background = _pairs_from_rows(background_rows) if background_rows else None
    return resonance_from_pairs(code, pairs, report_period=report_period,
                                threshold=threshold, level=level, name=name,
                                background_pairs=background,
                                background_period=background_period)


def _pairs_from_rows(rows: list[dict]) -> list[tuple[str, float]]:
    """原始行 → (代码, 变动幅度) 对：每家公司只留变动幅度最大的那条。"""
    best: dict[str, tuple[str, float]] = {}
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").strip()
        change = _change_of(row)
        if not code or change is None:
            continue
        if code not in best or abs(change) > abs(best[code][1]):
            best[code] = (code, change)
    return list(best.values())


def resonance_report(report_period: date, rows: list[dict], threshold: float = 30.0,
                     level: str = "second") -> dict:
    """整份预告池的行业地图：每个行业的预告数、同向率、渗透率排序。

    看这个地图是为了回答：「最近这一期，行业里哪儿在集体变？」——
    集体变化的方向对**所有**候选都成立，选谁的区别变小。
    """
    mapping = industry_map(level)
    by_industry: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        rcode = str(row.get("SECURITY_CODE") or "").strip()
        change = _change_of(row)
        if not rcode or change is None:
            continue
        industry = mapping.get(rcode)
        if not industry:
            continue
        by_industry.setdefault(industry, []).append((rcode, change))
    members = {}
    for name in mapping.values():
        members[name] = members.get(name, 0) + 1
    total = sum(len(items) for items in by_industry.values())
    up_total = sum(1 for items in by_industry.values() for _, change in items if change >= 0)
    market_up_rate = up_total / total if total else 0.0

    industries = []
    for industry, items in by_industry.items():
        up = sum(1 for _, change in items if change >= 0)
        down = len(items) - up
        strong = sum(1 for _, change in items if abs(change) >= threshold)
        up_rate = up / len(items)
        industries.append({
            "industry": industry,
            "with_forecast": len(items),
            "members": members.get(industry, 0),
            "strong": strong,
            "up": up,
            "down": down,
            "up_rate": round(up_rate * 100, 1),
            "lift": round(up_rate / market_up_rate, 2) if market_up_rate else 0.0,
            "penetration": round(len(items) / members[industry] * 100, 1)
            if members.get(industry) else 0.0,
        })
    industries.sort(key=lambda item: (-item["lift"], -item["with_forecast"]))
    return {"report_period": report_period.isoformat(), "threshold": threshold,
            "level": level, "pool": total, "up_pool": up_total,
            "market_up_rate": round(market_up_rate * 100, 1),
            "market_penetration": round(total / len(mapping) * 100, 1) if mapping else 0.0,
            "industries": industries,
            "source": "申万%s行业成分股 + 东方财富业绩预告" % LEVELS[level]}
