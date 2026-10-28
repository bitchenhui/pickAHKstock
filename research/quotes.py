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
    fields = payload.split("~")
    if len(fields) < 38:
        return None
    price = _num(_f(fields, 3))
    if price is None or price <= 0:
        return None
    turnover = _num(_f(fields, 37))
    return Snapshot(
        market="HK",
        code=code,
        name=_f(fields, 1).replace(" ", ""),
        price=price,
        turnover=turnover or 0.0,
        market_cap=None,
        pe=_num(_f(fields, 39)),
        pb=_num(_f(fields, 46)),
        change_pct=_num(_f(fields, 32)),
        as_of=_f(fields, 30),
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
