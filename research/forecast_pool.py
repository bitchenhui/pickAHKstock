"""业绩预告**全量**取数。

东财数据中心 `RPT_PUBLIC_OP_NEWPREDICT` 有两个坑，都踩过：

1. **接口会忽略 pageIndex**——第 1、2、3 页返回同一批行，所以「翻页取全量」
   这条路是死的。返回体里的 `count` 才是真实总量（例如 2026-06-30 报告期
   真实 4928 行，第一页只有 500 行）。
2. **pageSize 封顶 500**，调大无效。

早先的取数只拿到第一页，等于**每期只看了全量的约 7%**：发现层的候选池、回测的
事件组与对照组，全都建立在 7% 的样本上。

## 这里的做法：用「切分」代替「翻页」

能用的过滤条件只有数值字段（字符串字段如 SECURITY_CODE 一加就报
antlr 解析错误），所以切分维度只有三个可用量：

- `ADD_AMP_LOWER`（预告净利润变动幅度下限，单位 %）
- `PREDICT_AMT_LOWER`（预告净利润下限，单位元）
- `ADD_AMP_UPPER` / `PREDICT_AMT_UPPER`

做法是**两遍取数取并集**：

- 第一遍按变动幅度递归二分 → 覆盖所有「变动幅度非空」的行（归母、扣非、营收口径）
- 第二遍按预告金额递归二分 → 覆盖所有「金额非空」的行，把变动幅度为空的行
  （每股收益口径为主）补回来

两遍的并集覆盖 99% 以上的行，剩下的缺口（幅度与金额同时为空）会在自检里显示，
不会被悄悄吞掉。递归切分的收敛办法：从样本里取该字段的中位数做切点，
切不开（值全相同）就换字段，都不行才认缺口。

代价是请求变多（每期约 20 次），换来的是**池子完整**。结果按报告期落盘缓存，
缓存带版本号，取数方式变了缓存自动失效。
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 Chrome/120 Safari/537.36"}
CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "forecast_pool"
POOL_VERSION = 3
PAGE_LIMIT = 500
# 数值切分的主字段与备用字段（备用用于「主字段值全相同、切不开」的情况）
PRIMARY_FIELD = "ADD_AMP_LOWER"
FALLBACK_FIELD = "PREDICT_AMT_LOWER"
BIG = 1e15


class PoolError(RuntimeError):
    pass


def _get(report_period: str, extra_filter: str = "", page_size: int = PAGE_LIMIT):
    """返回 (数据行, 服务端总数)。"""
    params = {"reportName": "RPT_PUBLIC_OP_NEWPREDICT", "columns": "ALL",
              "pageSize": str(page_size), "pageIndex": "1", "client": "WEB",
              "sortColumns": "NOTICE_DATE", "sortTypes": "1",
              "filter": f"(REPORT_DATE='{report_period}'){extra_filter}"}
    try:
        response = requests.get(DC, headers=HEADERS, params=params, timeout=200)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PoolError(f"业绩预告取数失败 {report_period} {extra_filter}：{exc}") from exc
    result = payload.get("result") or {}
    return result.get("data") or [], result.get("count")


def _atoms_str(atoms: list[tuple[str, str, float]]) -> str:
    return "".join(f"({field}{op}{value})" for field, op, value in atoms)


def _count(report_period: str, atoms: list[tuple[str, str, float]]) -> int:
    _, total = _get(report_period, _atoms_str(atoms), page_size=1)
    return int(total or 0)


def _key(row: dict) -> tuple:
    return (str(row.get("SECURITY_CODE") or "").strip(),
            str(row.get("NOTICE_DATE") or "")[:10],
            str(row.get("REPORT_DATE") or "")[:10],
            str(row.get("PREDICT_FINANCE") or ""),
            row.get("ADD_AMP_LOWER"),
            row.get("PREDICT_AMT_LOWER"))


def _partition(report_period: str, atoms: list[tuple[str, str, float]],
               collected: dict, field: str = PRIMARY_FIELD,
               fallback: str = FALLBACK_FIELD, depth: int = 0) -> None:
    """把一个数值区间递归切分到每片 ≤ 500 行为止。

    field 是这一遍主用的切分维度；该维度切不开（值全相同）时改用 fallback。
    """
    total = _count(report_period, atoms)
    if total == 0:
        return
    if total <= PAGE_LIMIT:
        data, _ = _get(report_period, _atoms_str(atoms))
        for row in data:
            collected[_key(row)] = row
        return
    if depth >= 24:
        return
    sample, _ = _get(report_period, _atoms_str(atoms))
    if not sample:
        return
    for cut_field in (field, fallback):
        values = sorted({row.get(cut_field) for row in sample
                         if isinstance(row.get(cut_field), (int, float))})
        if len(values) < 2:
            continue
        for candidate in (values[len(values) // 2], values[0], values[-1]):
            left_atoms = atoms + [(cut_field, ">=", -BIG), (cut_field, "<", candidate)]
            right_atoms = atoms + [(cut_field, ">=", candidate), (cut_field, "<", BIG)]
            left = _count(report_period, left_atoms)
            right = _count(report_period, right_atoms)
            if left and right:
                _partition(report_period, left_atoms, collected, field, fallback, depth + 1)
                _partition(report_period, right_atoms, collected, field, fallback, depth + 1)
                return
    # 切不开（值全相同）：先收下这一格的样本，缺口在自检里报出来
    for row in sample:
        collected[_key(row)] = row


def fetch_pool(report_period: str, refresh: bool = False) -> list[dict]:
    """取一个报告期的全部业绩预告行。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"pool_v{POOL_VERSION}_{report_period}.json"
    if cache_file.exists() and not refresh:
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("rows"):
                return cached["rows"]
        except ValueError:
            pass

    collected: dict = {}
    total = _count(report_period, [])
    if not total:
        return []

    # 第一遍：按变动幅度切分，覆盖归母／扣非／营收口径的行
    _partition(report_period, [(PRIMARY_FIELD, ">=", -BIG), (PRIMARY_FIELD, "<", BIG)],
               collected, field=PRIMARY_FIELD, fallback=FALLBACK_FIELD)
    # 第二遍：按预告金额切分，把变动幅度为空的行补回来
    _partition(report_period, [(FALLBACK_FIELD, ">=", -BIG), (FALLBACK_FIELD, "<", BIG)],
               collected, field=FALLBACK_FIELD, fallback=PRIMARY_FIELD)

    rows = list(collected.values())
    rows.sort(key=lambda row: (str(row.get("NOTICE_DATE") or ""), str(row.get("SECURITY_CODE") or "")))
    cache_file.write_text(json.dumps(
        {"version": POOL_VERSION, "report_period": report_period,
         "server_total": total, "fetched_rows": len(rows),
         "coverage_pct": round(len(rows) / total * 100, 1) if total else 0.0,
         "rows": rows}, ensure_ascii=False), encoding="utf-8")
    return rows


def pool_summary(report_period: str) -> dict:
    """缓存的自检信息：服务端总数 vs 实取行数。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"pool_v{POOL_VERSION}_{report_period}.json"
    if not cache_file.exists():
        return {}
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return {key: cached.get(key) for key in
            ("report_period", "server_total", "fetched_rows", "coverage_pct", "version")}
