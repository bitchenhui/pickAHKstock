"""Tencent quote snapshots: batch, no account, explicit field mapping."""

from __future__ import annotations

from dataclasses import dataclass

import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
QUOTE_URL = "https://qt.gtimg.cn/q="


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    market: str
    code: str
    name: str
    price: float
    turnover: float          # 元
    market_cap: float | None  # 元；港股快照无此字段
    pe: float | None
    pb: float | None
    change_pct: float | None
    as_of: str               # 交易所时间戳，原始文本
    turnover_rate: float | None = None   # 换手率 %
    lot_size: int | None = None          # 每手股数（港股）
    shares: float | None = None          # 总股本（港股）


def _f(fields: list[str], index: int) -> str:
    return fields[index] if index < len(fields) else ""


def _num(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_a(code: str, payload: str) -> Snapshot | None:
    fields = payload.split("~")
    if len(fields) < 46:
        return None
    price = _num(_f(fields, 3))
    if price is None or price <= 0:
        return None
    turnover_wan = _num(_f(fields, 37))          # 成交额，万元
    total_cap_yi = _num(_f(fields, 45))          # 总市值，亿元
    return Snapshot(
        market="A",
        code=code,
        name=_f(fields, 1).replace(" ", ""),
        price=price,
        turnover=(turnover_wan or 0.0) * 1e4,
        market_cap=(total_cap_yi * 1e8) if total_cap_yi else None,
        pe=_num(_f(fields, 39)),
        pb=_num(_f(fields, 46)),
        change_pct=_num(_f(fields, 32)),
        as_of=_f(fields, 30),
    )


def _parse_hk(code: str, payload: str) -> Snapshot | None:
    """港股字段（腾讯行情，实测 78 段）：

    [3] 最新价 · [6] 成交量 · [37] 成交额(元) · [44] 流通市值(亿) ·
    [45] 总市值(亿) · [39] 市盈率(TTM) · [58] 市净率 · [59] 换手率% ·
    [60] 每手股数 · [69] 总股本

    总市值取 [45]：00939 的流通市值与总市值不同，用「总股本 × 最新价」可验证。
    """
    fields = payload.split("~")
    if len(fields) < 61:
        return None
    price = _num(_f(fields, 3))
    if price is None or price <= 0:
        return None
    turnover = _num(_f(fields, 37))
    total_cap_yi = _num(_f(fields, 45))
    shares = _num(_f(fields, 69))
    lot = _num(_f(fields, 60))
    # 用总股本 × 最新价交叉验证总市值口径，验不过就不填，避免挂错数
    market_cap = None
    if total_cap_yi:
        market_cap = total_cap_yi * 1e8
        if shares and abs(shares * price - market_cap) / market_cap > 0.02:
            market_cap = None
    return Snapshot(
        market="HK",
        code=code,
        name=_f(fields, 1).replace(" ", ""),
        price=price,
        turnover=turnover or 0.0,
        market_cap=market_cap,
        pe=_num(_f(fields, 39)),
        pb=_num(_f(fields, 58)),
        change_pct=_num(_f(fields, 32)),
        as_of=_f(fields, 30),
        turnover_rate=_num(_f(fields, 59)),
        lot_size=int(lot) if lot and lot > 0 else None,
        shares=shares,
    )


def tencent_symbol(market: str, code: str) -> str:
    """腾讯行情的 A 股代码需要交易所前缀；港股用 r_hk 前缀。"""
    if market == "HK":
        return f"r_hk{code}"
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8", "9")):
        return f"bj{code}"
    return code


def fetch_snapshots(codes: list[tuple[str, str]]) -> dict[str, Snapshot]:
    """codes: [(market, code), ...] -> {code: Snapshot}. 行情接口偶有单只返回空，缺失即跳过。"""
    if not codes:
        return {}
    out: dict[str, Snapshot] = {}
    for start in range(0, len(codes), 40):
        chunk = codes[start:start + 40]
        query = ",".join(tencent_symbol(market, code) for market, code in chunk)
        try:
            response = requests.get(QUOTE_URL + query, headers=HEADERS, timeout=15)
            response.encoding = "gbk"
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SnapshotError(f"行情快照获取失败：{exc}") from exc
        for (market, code), line in zip(chunk, _align(chunk, response.text)):
            if "=" not in line:
                continue
            payload = line.split("=", 1)[1].strip().strip(";").strip('"')
            snap = _parse_a(code, payload) if market == "A" else _parse_hk(code, payload)
            if snap:
                out[code] = snap
    return out


def _align(chunk: list[tuple[str, str]], text: str) -> list[str]:
    """腾讯接口按查询顺序返回行情行，但会跳过无数据标的；按 key 对齐而不是按行序。"""
    by_key: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("v_"):
            continue
        key, _, rest = line.partition("=")
        symbol = key[2:]
        by_key[symbol] = rest.strip().strip(";").strip('"')
    aligned = []
    for market, code in chunk:
        symbol = tencent_symbol(market, code)
        aligned.append(f"v_{symbol}={by_key.get(symbol, '')}")
    return aligned
