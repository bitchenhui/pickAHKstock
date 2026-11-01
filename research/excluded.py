"""排除清单：人裁决过「这只不看了」的记录。

发现层是**提议器**，最终由人决定看哪几张卡片。但提议器没有记忆——今天排除的
候选，明天还会出现在待立项清单里，人得一遍遍重新裁决。

这个模块记下「已排除」与理由，让待立项清单只留下真正没裁决过的标的。
排除是**可撤销**的：改主意了用 `unexclude` 放回来，理由也留在记录里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "cards" / "_excluded.json"


@dataclass
class Exclusion:
    market: str
    code: str
    reason: str = ""
    day: str = ""

    @property
    def key(self) -> str:
        return f"{self.market}_{self.code}"

    def to_dict(self) -> dict:
        return {"market": self.market, "code": self.code, "reason": self.reason,
                "day": self.day}


def _load() -> dict:
    if not STORE.exists():
        return {}
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def exclude(market: str, code: str, reason: str = "", day: str | None = None) -> Exclusion:
    """排除一只候选（记录理由）。"""
    data = _load()
    item = Exclusion(market=market, code=code, reason=reason,
                     day=day or date.today().isoformat())
    data[item.key] = item.to_dict()
    _save(data)
    return item


def unexclude(market: str, code: str) -> bool:
    """撤销排除。返回是否真的移除了。"""
    data = _load()
    key = f"{market}_{code}"
    if key in data:
        del data[key]
        _save(data)
        return True
    return False


def is_excluded(market: str, code: str) -> bool:
    return f"{market}_{code}" in _load()


def excluded_keys() -> set:
    return set(_load().keys())


def list_exclusions() -> list[Exclusion]:
    data = _load()
    out = []
    for key, item in data.items():
        out.append(Exclusion(market=item.get("market", ""), code=item.get("code", ""),
                              reason=item.get("reason", ""), day=item.get("day", "")))
    return sorted(out, key=lambda x: (x.market, x.code))
