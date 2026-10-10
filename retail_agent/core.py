"""Pure, testable screening and position arithmetic; no external market calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import floor, isfinite
from statistics import median, mean


@dataclass(frozen=True)
class Quote:
    market: str
    code: str
    name: str
    price: float
    turnover: float
    market_cap: float | None = None


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float


@dataclass(frozen=True)
class Profile:
    capital_cny: float = 30000
    capital_hkd: float = 30000
    risk_fraction: float = 0.005
    max_allocation: float = 0.2
    a_lot: int = 100
    hk_lots: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Candidate:
    quote: Quote
    day: date
    signal: str
    score: float
    close: float
    ma20: float
    ma60: float
    prior_high: float
    volume_ratio: float
    median_turnover: float
    atr_pct: float
    stop_reference: float
    lot: int | None
    lots: int
    shares: int
    notional: float
    risk_amount: float
    status: str


def eligible_quote(q: Quote) -> bool:
    if not q.code.isdigit() or not isfinite(q.price) or not isfinite(q.turnover):
        return False
    if q.price <= 0 or q.turnover < 0:
        return False
    if q.market == "A":
        if q.name.upper().startswith(("ST", "*ST")) or "退" in q.name:
            return False
        # Main board only: exclude GEM/STAR/BSE and their access requirements.
        return (len(q.code) == 6 and q.code.startswith(("00", "60"))
                and q.price >= 2 and q.market_cap is not None
                and isfinite(q.market_cap) and 2e9 <= q.market_cap <= 3e10)
    if q.market == "HK":
        if any(x in q.name.upper() for x in ("ETF", "基金", "REIT")):
            return False
        # HK snapshot has no capitalization; turnover is NOT a cap proxy.
        return len(q.code) == 5 and q.price >= 1 and 5e6 <= q.turnover <= 3e8
    return False


def shortlist(quotes: list[Quote], market: str, limit: int = 35) -> list[Quote]:
    pool = [q for q in quotes if q.market == market and eligible_quote(q)]
    # Deterministic limited API budget; this is a sampled universe, not a full-market rank.
    target = 8e7 if market == "A" else 4e7
    return sorted(pool, key=lambda q: (abs(q.turnover - target), q.code))[:limit]


def _atr(bars: list[Bar]) -> float:
    recent = bars[-15:]
    ranges = [max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close))
              for prev, b in zip(recent, recent[1:])]
    return mean(ranges)


def analyze(q: Quote, bars: list[Bar], profile: Profile) -> Candidate | None:
    if not eligible_quote(q):
        return None
    ordered = sorted(bars, key=lambda b: b.day)
    if len(ordered) < 80 or len({b.day for b in ordered}) != len(ordered):
        return None
    if any(not all(isfinite(v) and v > 0 for v in (b.open, b.high, b.low, b.close, b.volume, b.turnover))
           or b.low > min(b.open, b.close) or b.high < max(b.open, b.close) for b in ordered):
        return None
    last = ordered[-1]
    if abs(q.price / last.close - 1) > 0.15:
        return None  # snapshot/history mismatch, stale quote or unadjusted corporate action
    if any(abs(b.close / prev.close - 1) > (0.3 if q.market == "A" else 0.5)
           for prev, b in zip(ordered[-61:-1], ordered[-60:])):
        return None  # potential corporate action: do not misread an unadjusted chart
    med_turnover = median(b.turnover for b in ordered[-20:])
    if med_turnover < (2e7 if q.market == "A" else 5e6):
        return None
    ma20 = mean(b.close for b in ordered[-20:])
    ma60 = mean(b.close for b in ordered[-60:])
    prior_high = max(b.high for b in ordered[-21:-1])
    avg_vol = mean(b.volume for b in ordered[-21:-1])
    ratio = last.volume / avg_vol
    atr = _atr(ordered)
    atr_pct = atr / last.close
    if not 0 < atr_pct <= 0.08 or last.close <= ma60 or ma20 <= ma60:
        return None
    if last.close > prior_high and ratio >= 1.2 and last.close / ma20 <= 1.12:
        signal = "放量突破"
        signal_points = 45
    elif ma20 <= last.close <= ma20 * 1.06 and ratio <= 1.1:
        signal = "趋势回踩"
        signal_points = 37
    else:
        return None
    # This is a reference for a later review, not a guaranteed executable stop.
    stop = last.close - 2 * atr
    if stop <= 0:
        return None
    capital = profile.capital_cny if q.market == "A" else profile.capital_hkd
    lot = profile.a_lot if q.market == "A" else profile.hk_lots.get(q.code)
    valid_lot = isinstance(lot, int) and not isinstance(lot, bool) and lot > 0
    if not valid_lot:
        lot = None
        lots = 0
        status = "未提供港股每手股数；仅观察"
    else:
        risk_lots = floor(capital * profile.risk_fraction / ((last.close - stop) * lot))
        allocation_lots = floor(capital * profile.max_allocation / (last.close * lot))
        lots = max(0, min(risk_lots, allocation_lots))
        status = "可观察，等待次日核验" if lots else "一手超出风险或仓位预算；仅观察"
    shares = lots * (lot or 0)
    score = signal_points + min(18, 4 * (last.close / ma60 - 1) * 100) + min(15, 5 * (med_turnover / 1e8))
    score += max(0, 12 - 150 * atr_pct)
    return Candidate(q, last.day, signal, round(score, 1), last.close, ma20, ma60,
                     prior_high, round(ratio, 2), med_turnover, round(atr_pct * 100, 2),
                     round(stop, 3), lot, lots, shares, round(shares * last.close, 2),
                     round(shares * (last.close - stop), 2), status)


def rank(quotes: list[Quote], histories: dict[str, list[Bar]], profile: Profile) -> list[Candidate]:
    found = [c for q in quotes if (c := analyze(q, histories.get(q.code, []), profile)) is not None]
    if not found:
        return []
    # Compare freshness within the same exchange, not against a calendar weekend/holiday.
    latest = max(c.day for c in found)
    return sorted((c for c in found if c.day == latest), key=lambda c: (-c.score, c.quote.code))
