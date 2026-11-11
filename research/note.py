# -*- coding: utf-8 -*-
"""小红书笔记样式的研究卡片渲染器。

为什么需要它：终端输出会滚走、不好转述。卡片才是资产（cards/*.json），
note 把卡片渲染成人愿意看的形态——每张卡一篇笔记，notebook 把全部卡片
汇总成一页信息流。

诚实边界与卡片一致：只渲染卡片里已有的数据，缺的面不编造；推算值
（单季拆解）显式标注；底部固定「研究工具输出，非投资建议」。
"""

from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path

from .card import Card, blocking, validate

NOTE_DIR = Path(__file__).resolve().parent.parent / "cards" / "notes"

_QUARTER_MONTHS = {"03": 1, "06": 2, "09": 3, "12": 4}


def _esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _num(text) -> float | None:
    """'5.49亿' / '-6220.18万' / '16.92%' / '73.68' → float（百分比按数值）。"""
    if text is None:
        return None
    match = re.match(r"^\s*(-?[\d.]+)\s*(亿|万|%)?\s*$", str(text))
    if not match:
        try:
            return float(text)
        except (TypeError, ValueError):
            return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "亿":
        value *= 1e8
    elif unit == "万":
        value *= 1e4
    return value


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value / 1e8:.2f} 亿"


def quarterly(track, metric: str = "净利润", count: int = 3) -> list[tuple[str, float]]:
    """由累计口径推算最近几个单季值：Q_n = 累计_n − 累计_{n-1}（年内）。

    THS 报告期序列是年内累计，Q1 就是自身；跨年在 03-31 重置。
    值带单位字符串（'5.49亿'），解析失败该期跳过。
    """
    series = track.metrics.get(metric) or {}
    parsed: list[tuple[str, int, int, int, float]] = []
    for period, value in series.items():
        number = _num(value)
        if number is None:
            continue
        match = re.match(r"^(\d{4})-(\d{2})-\d{2}$", str(period))
        if not match:
            continue
        year, month = int(match.group(1)), match.group(2)
        if month not in _QUARTER_MONTHS:
            continue
        parsed.append((period, year, _QUARTER_MONTHS[month], _QUARTER_MONTHS[month], number))
    parsed.sort(key=lambda row: (row[1], row[3]))
    quarters: list[tuple[str, float]] = []
    for index, (period, year, order, _, number) in enumerate(parsed):
        if order == 1:
            quarters.append((f"{year}Q1", number))
            continue
        prev = parsed[index - 1]
        if prev[1] == year and prev[3] == order - 1:
            value = number - prev[4]
            quarters.append((f"{year}Q{order}", value))
    return quarters[-count:]


def _meter_rows(quarters: list[tuple[str, float]]) -> str:
    if not quarters:
        return ""
    peak = max(abs(value) for _, value in quarters) or 1.0
    shades = ("", " dim", " dim2")
    rows = []
    for index, (label, value) in enumerate(quarters):
        width = max(2, round(abs(value) / peak * 100))
        shade = shades[min(index, len(shades) - 1)] if len(quarters) > 1 else ""
        negative = " neg" if value < 0 else ""
        rows.append(
            f'<div class="row"><span class="q">{_esc(label)}</span>'
            f'<span class="track"><span class="bar{shade}{negative}" style="width:{width}%"></span></span>'
            f'<span class="v">{_fmt(value)}</span></div>')
    return "".join(rows)


_CSS = """
  #xhs-root { max-width: 440px; margin: 0 auto; }
  /* 研究台视图：宽容器 + 深色金融终端风；xhs 视图保持浅色窄卡（导出格式） */
  #xhs-root[data-view="desk"] { max-width: 1280px; }
  #xhs-root { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI",
              "Microsoft YaHei", sans-serif; color: #333; line-height: 1.55;
              -webkit-font-smoothing: antialiased;
              -webkit-text-size-adjust: 100%; text-size-adjust: 100%;
              color-scheme: light; }
  #xhs-root .legal, #xhs-root .tags, #xhs-root .kv span,
  #xhs-root table.desk td, #xhs-root .sub { overflow-wrap: anywhere; }

  /* ============ 研究台 · 绯红终端主题（暖黑底+绛红交互，涨跌红绿保持最亮） ============ */
  #xhs-root[data-view="desk"] { color: #ece7e9; }
  #xhs-root[data-view="desk"] .desk-shell { background:
      radial-gradient(1200px 500px at 20% -10%, rgba(224,82,82,.14), transparent 60%),
      radial-gradient(900px 420px at 90% 0%, rgba(255,255,255,.03), transparent 55%),
      #150f11; border: 1px solid rgba(255,255,255,.07); border-radius: 16px;
      padding: 22px clamp(14px, 2.5vw, 30px) 26px; margin-top: 14px; }
  #xhs-root[data-view="desk"] .desk-topline { display: flex; justify-content: space-between;
      align-items: baseline; flex-wrap: wrap; gap: 8px;
      border-bottom: 1px solid rgba(255,255,255,.08); padding-bottom: 14px; }
  #xhs-root[data-view="desk"] .desk-brand { font-size: 20px; font-weight: 700; color: #f7f2f4;
      letter-spacing: .5px; }
  #xhs-root[data-view="desk"] .desk-brand::after { content: ""; display: block;
      width: 30px; height: 3px; background: #e05252; margin-top: 8px; }
  #xhs-root[data-view="desk"] .desk-brand small { display: block; font-size: 10.5px;
      font-weight: 500; color: #a8a0a4; letter-spacing: 2.5px; margin-top: 2px; }
  #xhs-root[data-view="desk"] .desk-status { font-family: ui-monospace, SFMono-Regular, Menlo,
      monospace; font-size: 11.5px; color: #a89aa0; letter-spacing: .3px; text-align: right; }
  #xhs-root[data-view="desk"] .desk-status b { color: #f7f2f4; font-weight: 600; }
  #xhs-root[data-view="desk"] .desk-status .up { color: #ff6b6b; }
  #xhs-root[data-view="desk"] .desk-status .down-t { color: #3fd6a4; }
  #xhs-root[data-view="desk"] .desk-title { font-size: 13px; font-weight: 600; color: #9fb2d0;
      padding: 16px 2px 8px; letter-spacing: 1px; }
  #xhs-root[data-view="desk"] .desk-card, #xhs-root[data-view="desk"] .desk-promo {
      background: #1a1315; border: 1px solid rgba(255,255,255,.07); border-radius: 12px;
      overflow: hidden; }
  #xhs-root[data-view="desk"] .desk-promo { border-color: rgba(224,82,82,.45);
      background: #1a1315; border-top: 2px solid #e05252;
      margin-bottom: 16px; padding: 14px 16px; }
  #xhs-root[data-view="desk"] .desk-promo h4 { margin: 0 0 4px; font-size: 13px;
      color: #ece7e9; letter-spacing: 1.5px; font-weight: 700; }
  #xhs-root[data-view="desk"] .desk-promo .promo-row { display: flex; gap: 14px;
      flex-wrap: wrap; padding: 8px 0; border-top: 1px dashed rgba(224,82,82,.28); }
  #xhs-root[data-view="desk"] .desk-promo .promo-row:first-of-type { border-top: 0; }
  #xhs-root[data-view="desk"] .promo-name { min-width: 150px; }
  #xhs-root[data-view="desk"] .promo-name b { color: #f7f2f4; font-size: 14px; }
  #xhs-root[data-view="desk"] .promo-why { flex: 1; font-size: 12.5px; color: #a8b8d4; }
  #xhs-root[data-view="desk"] .promo-why .gap { color: #d98f8f; }
  #xhs-root[data-view="desk"] .promo-none { font-size: 12.5px; color: #a89aa0; padding: 4px 0; }
  #xhs-root[data-view="desk"] table.desk th { background: #20171a; color: #b0a6ab;
      border-bottom: 1px solid rgba(255,255,255,.09); }
  #xhs-root[data-view="desk"] table.desk td { border-bottom: 1px solid rgba(255,255,255,.055); }
  #xhs-root[data-view="desk"] table.desk tbody tr:hover td { background: rgba(224,82,82,.05); }
  #xhs-root[data-view="desk"] .desk-title, #xhs-root[data-view="desk"] .muted { color: #a8a0a4; }
  #xhs-root[data-view="desk"] .mono { font-family: ui-monospace, SFMono-Regular, Menlo,
      monospace; }
  #xhs-root[data-view="desk"] td b { color: #eef3fa; }
  #xhs-root[data-view="desk"] .pos { color: #ff6b6b; }
  #xhs-root[data-view="desk"] .neg { color: #3fd6a4; }
  #xhs-root[data-view="desk"] .badge { border: 1px solid; background: transparent; }
  #xhs-root[data-view="desk"] .badge.watch { color: #ff8f8f; border-color: rgba(255,107,107,.45); }
  #xhs-root[data-view="desk"] .badge.down { color: #b0a6ab; border-color: rgba(163,149,154,.35); }
  #xhs-root[data-view="desk"] .desk-foot { font-size: 11px; color: #8f8a8d; text-align: center;
      padding-top: 14px; letter-spacing: .5px; }
  #xhs-root[data-view="desk"] .toolbar .seg { background: #1a1315;
      border: 1px solid rgba(255,255,255,.08); box-shadow: none; }
  #xhs-root[data-view="desk"] .toolbar .seg button { color: #b0a6ab; }
  #xhs-root[data-view="desk"] .toolbar .seg button[aria-pressed="true"] { background: #ece7e9;
      color: #150f11; }
  #xhs-root[data-view="desk"] .toolbar .export { background: transparent; color: #ece7e9;
      border: 1px solid rgba(236,231,233,.4); box-shadow: none; }
  #xhs-root[data-view="desk"] .nb-stats .chip { background: #1a1315; color: #9fb2d0;
      border: 1px solid rgba(255,255,255,.08); box-shadow: none; }
  #xhs-root[data-view="desk"] .nb-stats .chip b { color: #ece7e9; }
  #xhs-root[data-view="desk"] .nb-head .title { color: #f7f2f4; }
  #xhs-root[data-view="desk"] .nb-head .sub { color: #a8a0a4; }

  @media (max-width: 760px) {
    #xhs-root[data-view="desk"] table.desk tr { background: #1a1315;
        border: 1px solid rgba(255,255,255,.07); box-shadow: none; }
    #xhs-root[data-view="desk"] .desk-promo .promo-row { flex-direction: column; gap: 4px; }
  }
  #xhs-root .feed { display: flex; flex-direction: column; gap: 12px; }
  #xhs-root .note { background: #fff; border-radius: 14px; padding: 18px 16px 12px;
                    box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  #xhs-root .author { display: flex; align-items: center; gap: 10px; }
  #xhs-root .avatar { width: 34px; height: 34px; border-radius: 50%;
                      background: linear-gradient(135deg, #ff2442, #ff7a9c); color: #fff;
                      font-size: 16px; font-weight: 600; display: flex; align-items: center;
                      justify-content: center; flex: none; }
  #xhs-root .author .who { font-size: 14px; font-weight: 600; }
  #xhs-root .author .meta { font-size: 12px; color: #999; }
  #xhs-root .cover { margin: 14px 0 4px; background: linear-gradient(160deg, #fff0f2, #fff);
                     border-radius: 12px; padding: 16px 14px; }
  #xhs-root .cover h2 { margin: 0 0 6px; font-size: 19px; line-height: 1.4; color: #1f1f1f; }
  #xhs-root .cover h2 .hl { color: #ff2442; }
  #xhs-root .cover .sub { font-size: 13px; color: #666; margin: 0; }
  #xhs-root .tags { margin: 10px 0 4px; font-size: 13px; color: #ff2442; font-weight: 500; }
  #xhs-root section { margin-top: 16px; }
  #xhs-root h3 { margin: 0 0 8px; font-size: 15px; font-weight: 700; color: #1f1f1f; }
  #xhs-root .fact { font-size: 13.5px; color: #444; margin: 3px 0; }
  #xhs-root .fact b { color: #1f1f1f; }
  #xhs-root .num { color: #ff2442; font-weight: 700; }
  #xhs-root .warn { color: #c46a00; font-weight: 700; }
  #xhs-root .meter .row { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
  #xhs-root .meter .q { font-size: 12.5px; color: #666; width: 44px; flex: none; }
  #xhs-root .meter .track { flex: 1; height: 14px; background: #f2f2f4; border-radius: 7px;
                            overflow: hidden; }
  #xhs-root .meter .bar { display: block; height: 100%; background: #ff2442; border-radius: 7px; }
  #xhs-root .meter .bar.dim { background: #ff8fa3; }
  #xhs-root .meter .bar.dim2 { background: #ffc2ce; }
  #xhs-root .meter .bar.neg { background: #8e8e93; }
  #xhs-root .meter .v { font-size: 12.5px; color: #1f1f1f; width: 74px; flex: none;
                        text-align: right; font-variant-numeric: tabular-nums; }
  #xhs-root .derived { font-size: 11.5px; color: #999; margin-top: 6px; }
  #xhs-root .quote { background: #fff7f0; border-radius: 10px; padding: 10px 12px;
                     font-size: 13px; color: #7a4a12; margin-top: 8px; }
  #xhs-root .flags { list-style: none; margin: 0; padding: 0; }
  #xhs-root .flags li { font-size: 13.5px; color: #444; padding: 7px 0;
                        border-bottom: 1px solid #f4f4f6; display: flex; gap: 8px; }
  #xhs-root .flags li:last-child { border-bottom: none; }
  #xhs-root .flags .m { flex: none; }
  #xhs-root .todo { list-style: none; margin: 0; padding: 0; }
  #xhs-root .todo li { display: flex; gap: 9px; align-items: flex-start; font-size: 13px;
                       color: #444; padding: 5px 0; }
  #xhs-root .todo .box { width: 15px; height: 15px; border: 1.5px solid #ff2442;
                         border-radius: 4px; flex: none; margin-top: 2px; }
  #xhs-root .badge { display: inline-block; font-size: 11.5px; padding: 1px 8px;
                     border-radius: 999px; margin-left: 6px; vertical-align: 2px; }
  #xhs-root .badge.watch { background: #fff0f2; color: #ff2442; }
  #xhs-root .badge.down { background: #f2f2f4; color: #666; }
  #xhs-root .discipline { margin-top: 12px; font-size: 12.5px; color: #666;
                          border-top: 1px dashed #e8e8ec; padding-top: 10px; }
  #xhs-root .actions { display: flex; justify-content: space-around; margin-top: 12px;
                       padding-top: 10px; border-top: 1px solid #f0f0f2; font-size: 12.5px;
                       color: #888; }
  #xhs-root .legal { font-size: 11px; color: #aaa; margin-top: 8px; text-align: center; }
  #xhs-root .nb-head { text-align: center; padding: 6px 0 2px; }
  #xhs-root .nb-head .title { font-size: 18px; font-weight: 700; color: #1f1f1f; }
  #xhs-root .nb-head .sub { font-size: 12.5px; color: #999; margin-top: 2px; }
  #xhs-root .nb-stats { display: flex; justify-content: center; gap: 10px; flex-wrap: wrap;
                        padding: 10px 0; }
  #xhs-root .nb-stats .chip { background: #fff; border-radius: 999px; font-size: 12.5px;
                              color: #444; padding: 4px 12px;
                              box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  #xhs-root .nb-stats .chip b { color: #ff2442; }
  #xhs-root .kv { display: flex; gap: 8px; font-size: 13px; color: #444; padding: 3px 0; }
  #xhs-root .kv .k { color: #999; flex: none; width: 74px; }
  /* —— 视图切换与导出 —— */
  #xhs-root .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
                       padding: 12px 0 4px; }
  #xhs-root .toolbar .seg { display: inline-flex; background: #fff; border-radius: 999px;
                            padding: 3px; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  #xhs-root .toolbar .seg button { border: 0; background: transparent; padding: 6px 16px;
                                   border-radius: 999px; font-size: 13px; color: #666;
                                   cursor: pointer; }
  #xhs-root .toolbar .seg button[aria-pressed="true"] { background: #1f1f1f; color: #fff; }
  #xhs-root .toolbar .export { border: 0; background: #fff; color: #ff2442; font-size: 13px;
                               padding: 6px 16px; border-radius: 999px; cursor: pointer;
                               box-shadow: 0 1px 3px rgba(0,0,0,.05); font-weight: 600; }
  #xhs-root .view-desk, #xhs-root .view-xhs { display: none; }
  #xhs-root[data-view="desk"] .view-desk { display: block; }
  #xhs-root[data-view="xhs"] .view-xhs { display: block; }
  /* —— 研究台（正常渲染）视图：桌面表格，移动端卡片化 —— */
  #xhs-root .desk-title { font-size: 14px; font-weight: 700; color: #1f1f1f;
                          padding: 2px 2px 8px; }
  #xhs-root .desk-card { background: #fff; border-radius: 12px; overflow: hidden;
                         box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  #xhs-root .desk-wrap { overflow-x: auto; }
  #xhs-root table.desk { width: 100%; border-collapse: collapse; font-size: 13px;
                         min-width: 860px; }
  #xhs-root table.desk th { text-align: left; padding: 10px 10px; background: #f5f5f7;
                            color: #666; font-weight: 600; border-bottom: 1px solid #e4e4e8;
                            white-space: nowrap; }
  #xhs-root table.desk td { padding: 9px 10px; border-bottom: 1px solid #f4f4f6;
                            vertical-align: top; }
  #xhs-root table.desk td.w-limit { max-width: 250px; }
  #xhs-root table.desk tr:last-child td { border-bottom: none; }
  #xhs-root .mono { font-variant-numeric: tabular-nums; }
  #xhs-root .muted { color: #999; }
  #xhs-root .pos { color: #c4322f; }
  #xhs-root .neg { color: #1a7f37; }
  @media (max-width: 760px) {
    /* 10 列表格在手机上没法读：每行转成卡片，列名由 data-label 提供 */
    #xhs-root .desk-card { background: transparent; box-shadow: none; overflow: visible; }
    #xhs-root table.desk, #xhs-root table.desk tbody, #xhs-root table.desk tr,
    #xhs-root table.desk td { display: block; width: 100%; min-width: 0; }
    #xhs-root table.desk { overflow: hidden; }   /* collapse 边框在 block 表格上的幽灵宽度 */
    #xhs-root .desk-wrap { overflow-x: visible; }
    #xhs-root table.desk thead { display: none; }
    #xhs-root table.desk td.w-limit { max-width: none; }
    #xhs-root table.desk tr { background: #fff; border-radius: 12px; padding: 6px 2px;
                              margin-bottom: 10px;
                              box-shadow: 0 1px 3px rgba(0,0,0,.05); }
    #xhs-root table.desk td { border-bottom: 1px solid #f4f4f6; padding: 7px 14px; }
    #xhs-root table.desk tr:last-child td { border-bottom: none; }
    #xhs-root table.desk td[data-label]::before { content: attr(data-label) "：";
                                                  color: #999; margin-right: 6px; }
    #xhs-root table.desk td:first-child { padding-bottom: 2px; }
  }
"""


def _status_badge(status: str) -> str:
    cls = "watch" if status in ("观察", "持仓研究") else "down"
    return f'<span class="badge {cls}">{_esc(status)}</span>'


def _watch_line(card: Card) -> str:
    """汇总行用的一句话盯价摘要：品种 最新价(30日±x%)。"""
    items = ((card.commodities or {}).get("items")) or {}
    if not items:
        return "未映射"
    parts = []
    for stats in items.values():
        chg = stats.get("chg_30d")
        chg_text = "—" if chg is None else f"{chg * 100:+.1f}%"
        parts.append(f"{stats.get('symbol', '')} {stats.get('latest', '—')}（30日{chg_text}）")
    return " ｜ ".join(parts)


def _commodity_rows(card: Card) -> str:
    """盯的价格块：最新价、30/250 日变化、一年位置。代理关系已在名称里。"""
    items = ((card.commodities or {}).get("items")) or {}
    if not items:
        return ""
    rows = []
    for stats in items.values():
        def _pct(value, short=False):
            if value is None:
                return "—"
            text = f"{value * 100:+.1f}%"
            return text + ("（短窗口）" if short else "")
        position = stats.get("position_250d")
        position_text = "—" if position is None else f"{position * 100:.0f}%"
        trend = "".join("▁▂▃▄▅▆▇"[min(6, max(0, int((p - stats.get("low_250d", p))
                          / max(1e-9, (stats.get("high_250d", p) - stats.get("low_250d", p)))
                          * 6)))] for p in [row["price"] for row in stats.get("series_tail", [])])
        rows.append(
            f'<div class="kv"><span class="k">{_esc(stats.get("name", ""))}</span>'
            f'<span><b>{_esc(stats.get("latest", "—"))}</b>'
            f'（{_esc(stats.get("day", ""))}）｜30日 {_esc(_pct(stats.get("chg_30d"), stats.get("chg_30d_short")))}'
            f'｜一年 {_esc(_pct(stats.get("chg_250d"), stats.get("chg_250d_short")))}'
            f'｜一年区间位置 {position_text}'
            + (f'｜<span aria-hidden="true">{trend}</span>' if trend else "")
            + "</span></div>")
    return (f'<section><h3>💰 盯的价格（行业给的就要盯行业）</h3>{"".join(rows)}'
            f'<p class="derived">来源：{_esc((card.commodities or {}).get("source", ""))}；'
            f'代理关系见名称标注</p></section>')


def _regime_line(card: Card) -> str:
    regime = (card.market_context or {}).get("market_regime") or {}
    if not regime:
        return ""
    factor = "按 0.5× 折算" if regime.get("state") == "下行" else "不折算"
    index_name = regime.get("index") or ("恒生指数" if card.market == "HK" else "沪深300")
    caveat = f"；{_esc(regime['caveat'])}" if regime.get("caveat") else ""
    return (f"市场状态<b>{_esc(regime.get('state', '—'))}</b>（{_esc(regime.get('day', ''))}，"
            f"{_esc(index_name)} {_esc(regime.get('close', '—'))}）：此类票仓位{factor}{caveat}")


def _tags(card: Card) -> list[str]:
    tags = [card.market, card.code]
    evidence_text = " ".join(e.claim for e in card.evidence[:3])
    for word in ("扭亏", "预增", "预盈", "减亏", "预减", "盈警", "盈喜"):
        if word in evidence_text:
            tags.append(word)
            break
    industry = (card.industry_context or {}).get("industry")
    if industry:
        tags.append(str(industry))
    verdict = (card.industry_context or {}).get("verdict")
    if verdict:
        tags.append(str(verdict))
    tags.append(card.status)
    return tags


def _due(card: Card) -> str:
    today = date.today().isoformat()
    dates = [c.date for c in card.catalysts if c.date]
    dates += [e.next_check for e in card.evidence
              if e.next_check and re.match(r"^\d{4}-\d{2}-\d{2}", str(e.next_check))]
    dates = [d[:10] for d in dates if d]
    if not dates:
        return ""
    nearest = min(dates)
    marker = "（已过期）" if nearest < today else ""
    return f"{nearest}{marker}"


def note_fragment(card: Card) -> str:
    """单张卡片 → 小红书笔记 HTML 片段。"""
    quarters = quarterly(card.financials, "净利润") if card.financials.metrics else []
    momentum = "逐季减少" if len(quarters) >= 2 and quarters[-1][1] < quarters[0][1] else (
        "逐季增加" if len(quarters) >= 2 and quarters[-1][1] > quarters[0][1] else "")
    resonance = card.industry_context or {}
    capital = card.capital or {}
    expectations = card.expectations or {}
    scans = card.scans or {}
    findings = validate(card)
    blocks = blocking(findings)

    parts: list[str] = []
    parts.append(
        f'<div class="author"><div class="avatar" aria-hidden="true">研</div><div>'
        f'<div class="who">{_esc(card.name)}（{_esc(card.market)} {_esc(card.code)}）</div>'
        f'<div class="meta">小资金研究台 · {_esc(card.status)}池 · 更新 {_esc(card.updated or card.created)}</div>'
        f'</div></div>')

    title = f"{card.name} {_esc(card.code)}"
    if momentum:
        title = (f"{'单季利润' + momentum}" if momentum else title)
    hl = f'<span class="hl">{_esc(momentum)}</span>' if momentum else ""
    parts.append(
        f'<div class="cover"><h2>{_esc(card.name)}{_status_badge(card.status)}<br>'
        f'单季利润{hl or "拆解"}，先看再说话</h2>'
        f'<p class="sub">{_esc(card.market)} {_esc(card.code)} ｜ 结论等级 {_esc(card.derived_level())}'
        f'｜ 最近到期 {_esc(_due(card) or "未定")}</p></div>')
    parts.append('<p class="tags">' + " ".join(f"#{_esc(t)}" for t in _tags(card)) + "</p>")

    event = next((e for e in card.evidence if e.claim), None)
    if event:
        parts.append(
            f'<section><h3>1️⃣ 事件</h3>'
            f'<p class="fact">{_esc(event.claim[:160])}</p>'
            f'<p class="fact">出处：{_esc(event.publisher or "")} · {_esc(event.published or "")}'
            f' · <b>{_esc(event.status)}</b></p></section>')

    if quarters:
        rows = _meter_rows(quarters)
        margin_series = card.financials.metrics.get("销售毛利率") or {}
        margins = sorted(margin_series.items(), reverse=True)[:3]
        margin_text = " → ".join(f"{_esc(v)}" for _, v in margins)
        parts.append(
            f'<section><h3>2️⃣ 单季拆解{("：" + _esc(momentum)) if momentum else ""}</h3>'
            f'<div class="meter" role="img" aria-label="单季净利润：'
            + "、".join(f"{label} {value / 1e8:.2f} 亿" for label, value in quarters)
            + f'">{rows}</div>'
            + (f'<p class="fact">销售毛利率（累计口径，近三期）：{margin_text}</p>' if margin_text else "")
            + '<p class="derived">单季值由累计披露相减推算，需回定期报告原文核对</p></section>')

    if resonance:
        industry = resonance.get("industry") or "—"
        if resonance.get("market") == "HK" or resonance.get("level") == "hk":
            core = (f"同行业 {resonance.get('peers_with_forecast', 0)} 家，同向 "
                    f"{resonance.get('peers_same_sign', 0)} 家")
            caveat = resonance.get("caveat", "")
        else:
            core = (f"同行业 {resonance.get('peers_with_forecast', 0)} 家发预告，"
                    f"同向 {resonance.get('peers_same_sign', 0)} 家"
                    f"（同向率 {(resonance.get('same_sign_rate') or 0) * 100:.0f}%）")
            caveat = "共振高=行业给的；行业反转时会一起跌"
        parts.append(
            f'<section><h3>3️⃣ 行业还是公司</h3>'
            f'<p class="fact">行业：<b>{_esc(industry)}</b> ｜ {core}</p>'
            f'<div class="quote">判定：<b>{_esc(resonance.get("verdict", "—"))}</b>。{_esc(caveat)}</div></section>')

    if capital:
        parts.append(
            f'<section><h3>4️⃣ 利润之外的第三只眼</h3>'
            f'<p class="fact">经营现金流 {_esc(capital.get("operating_cf_label", "—"))} ｜ '
            f'自由现金流 {_esc(capital.get("fcf_label", "—"))} ｜ '
            f'资本开支 {_esc(capital.get("capex_label", "—"))}</p>'
            f'<p class="fact">在建工程 {_esc(capital.get("construction_label", "—"))} ｜ '
            f'固定资产 {_esc(capital.get("fixed_assets_label", "—"))}</p></section>')

    commodity_block = _commodity_rows(card)
    if commodity_block:
        parts.append(commodity_block)

    risks = scans.get("risks") or []
    governance = scans.get("governance") or []
    business = scans.get("business") or {}
    if risks or governance or business:
        lines = []
        for item in risks[:5]:
            lines.append(f'<li><span class="m">⚠️</span><span>[{_esc(item.get("category", ""))}] '
                         f'{_esc(item.get("published", ""))} {_esc(item.get("title", ""))}</span></li>')
        for item in [g for g in governance if g.get("severity") == "高"][:3] + \
                    [g for g in governance if g.get("severity") != "高"][:2]:
            lines.append(f'<li><span class="m">{"⚠️" if item.get("severity") == "高" else "·"}</span>'
                         f'<span>{_esc(item.get("category", ""))}：{_esc(item.get("detail", ""))}</span></li>')
        if business.get("diff"):
            diff = business["diff"]
            changed = {k: v for k, v in (diff.get("theme_delta") or {}).items() if v}
            lines.append(f'<li><span class="m">·</span><span>经营回顾 {_esc(diff.get("prior_period", ""))}'
                         f' → {_esc(diff.get("period", ""))}：{_esc(changed or "无明显变化")}</span></li>')
        if lines:
            parts.append(f'<section><h3>🚩 红旗与留痕</h3><ul class="flags">{"".join(lines)}</ul>'
                         f'<p class="derived">扫描截至 {_esc(scans.get("as_of", "—"))}；'
                         f'每条都要回原文确认（关键词会误报）</p></section>')

    coverage = expectations.get("coverage")
    parts.append(
        f'<section><h3>👤 第三方预期</h3>'
        f'<p class="fact">覆盖机构 <b>{_esc(coverage if coverage is not None else "—")}</b> 家 ｜ '
        f'最新评级 {_esc(expectations.get("latest_rating") or "—")} ｜ '
        f'目标价 {_esc(expectations.get("target_price") or "—")}</p>'
        + ('<p class="fact">⚠ 无机构覆盖：缺第三方预期这条证据，需自行补</p>'
           if not coverage else "") + "</section>")

    action = _action_block(card)
    if action:
        parts.append(action)

    if blocks:
        items = "".join(f'<li><span class="box" aria-hidden="true"></span>'
                        f'<span>{_esc(f)}</span></li>' for f in blocks[:6])
        parts.append(f'<section><h3>✍️ 这张卡还欠的判断（工具不代填）</h3>'
                     f'<ul class="todo">{items}</ul></section>')

    regime = _regime_line(card)
    if regime:
        parts.append(f'<p class="discipline">仓位纪律：小仓多注（20 只 × 5%）；{_regime_line(card)}</p>')
    parts.append('<div class="actions" aria-hidden="true"><span>❤️ 点赞</span>'
                 '<span>⭐ 收藏</span><span>💬 评论</span></div>'
                 '<p class="legal">研究工具输出，非投资建议 · 数据：巨潮/同花顺/东财 · '
                 f'截至 {_esc(card.updated or card.created or "")}</p>')
    return (f'<div class="note" id="note-{_esc(card.market)}-{_esc(card.code)}">'
            + "".join(parts) + "</div>")


def _card_summary(card: Card) -> dict:
    """两种视图共用的每卡摘要（数据只提一遍，视图各自渲染）。"""
    resonance = card.industry_context or {}
    quarters = quarterly(card.financials, "净利润") if card.financials.metrics else []
    scans = card.scans or {}
    momentum = ""
    if len(quarters) >= 2:
        momentum = "逐季↓" if quarters[-1][1] < quarters[0][1] else (
            "逐季↑" if quarters[-1][1] > quarters[0][1] else "持平")
    return {
        "card": card,
        "resonance": resonance,
        "quarters": quarters,
        "momentum": momentum,
        "high_flags": len([g for g in (scans.get("governance") or [])
                           if g.get("severity") == "高"]),
        "risk_count": len(scans.get("risks") or []),
        "capital": card.capital or {},
        "expectations": card.expectations or {},
        "blocks": len(blocking(validate(card))),
    }


def _xhs_feed(summaries: list[dict]) -> str:
    """小红书信息流视图（导出格式的来源）。"""
    notes = []
    for item in summaries:
        card: Card = item["card"]
        resonance = item["resonance"]
        quarters = item["quarters"]
        rows = [
            ("共振", f"{resonance.get('verdict', '—')}" + (f"·{resonance.get('industry', '')}"
                                                             if resonance.get("industry") else "")),
            ("单季利润", (f"{item['momentum']}（{quarters[0][0]} {quarters[0][1] / 1e8:.2f} 亿 → "
                          f"{quarters[-1][0]} {quarters[-1][1] / 1e8:.2f} 亿）") if quarters else "—"),
            ("经营现金流", item["capital"].get("operating_cf_label", "—")),
            ("盯价", _watch_line(card)),
            ("相对强度", ((card.execution or {}).get("strength") or {}).get("rs", "—")
             if ((card.execution or {}).get("strength") or {}).get("rs") is not None else "—"),
            ("红旗", f"风险公告 {item['risk_count']} 条 · 高severity治理 {item['high_flags']} 条"
                     if (item["risk_count"] or item["high_flags"]) else "未扫描留痕"),
            ("机构覆盖", str(item["expectations"].get("coverage", "—")) + " 家"),
            ("最近到期", _due(card) or "未定"),
        ]
        kv = "".join(f'<div class="kv"><span class="k">{_esc(k)}</span>'
                     f'<span>{_esc(v)}</span></div>' for k, v in rows)
        head = (_esc(card.name) + _status_badge(card.status)
                + (f' <span class="badge down">{item["blocks"]} 项阻塞</span>'
                   if item["blocks"] else ""))
        notes.append(f'<div class="note"><div class="cover" style="margin:0 0 10px">'
                     f'<h2 style="font-size:16px">{head}</h2>'
                     f'<p class="sub">{_esc(card.market)} {_esc(card.code)} · '
                     f'{_esc(card.derived_level())} · 更新 {_esc(card.updated or card.created or "")}</p></div>'
                     + kv + '<div class="actions" aria-hidden="true"><span>❤️</span>'
                       '<span>⭐</span><span>💬</span></div></div>')
    return f'<div class="feed">{"".join(notes)}</div>'


def _strength_line(card: Card) -> str:
    strength = (card.execution or {}).get("strength") or {}
    if strength.get("rs") is None:
        return "—"
    rs = strength["rs"]
    cls = "pos" if rs > 0 else "neg"
    return (f'<span class="{cls}">{rs:+.1f}pct</span><br>'
            f'<span class="muted">个股 {strength.get("stock_return", "—")}% vs '
            f'指数 {strength.get("index_return", "—")}%</span>')


def _action_block(card: Card) -> str:
    """行动条件区：何时看 / 何时动 / 何时撤——全是触发器，不是买卖点预测。"""
    execution = card.execution or {}
    liquidity = execution.get("liquidity") or {}
    strength = execution.get("strength") or {}
    regime = (card.market_context or {}).get("market_regime") or {}

    watch = []
    for catalyst in card.catalysts[:2]:
        if catalyst.event:
            watch.append(f"{catalyst.event}（{catalyst.date or '日期待定'}）")
    if strength.get("rs") is not None:
        watch.append(f"相对强度转向（当前 {strength['rs']:+.1f}pct vs 大盘）")
    act = []
    blocks = len(blocking(validate(card)))
    act.append(f"质量门{'通过' if not blocks else f'还有 {blocks} 项阻塞'}")
    if regime.get("state"):
        factor = "0.5×" if regime.get("state") == "下行" else "全额"
        act.append(f"市场状态 {regime.get('state')}（仓位按 {factor}）")
    if liquidity.get("level"):
        act.append(f"流动性 {liquidity['level']}")
    quit_lines = list(card.invalidation[:3])
    stop = (card.market_context or {}).get("stop_reference")
    if stop:
        quit_lines.append(f"收盘跌破 2ATR 参考线 {stop}（参考失效，不是保证成交）")
    if not watch and not act and not quit_lines:
        return ""
    rows = ""
    if watch:
        rows += ('<div class="kv"><span class="k">何时看</span><span>'
                 + _esc("；".join(watch)) + "</span></div>")
    if act:
        rows += ('<div class="kv"><span class="k">何时动</span><span>'
                 + _esc("；".join(act)) + "</span></div>")
    if quit_lines:
        rows += ('<div class="kv"><span class="k">何时撤</span><span>'
                 + _esc("；".join(quit_lines)) + "</span></div>")
    return (f'<section><h3>🎯 行动条件（触发器，不是买卖点）</h3>{rows}'
            '<p class="derived">条件出现是行动信号，点位由你与券商确定；本区不预测涨跌</p></section>')


def _hold_candidates(summaries: list[dict]) -> list[dict]:
    """最接近「持仓研究」资格的候选：资格是硬的，排序信号是软的、可解释的。

    资格（缺一不排入）：状态为观察/持仓研究、结论等级=证据充分、无阻塞。
    排序信号：①共振非「行业共振」（公司自己的事才值得持有个股，行业 beta
    不如买 ETF）②盯价 30 日方向顺风 ③无高危治理红旗 ④有机构覆盖。
    每只附 reasons 与 gap——gap 是还差什么，状态推进的最终裁决永远是人。
    """
    out = []
    for item in summaries:
        card: Card = item["card"]
        if card.status not in ("观察", "持仓研究"):
            continue
        if card.derived_level() != "证据充分" or item["blocks"]:
            continue
        reasons = []
        verdict = (item["resonance"] or {}).get("verdict", "")
        if verdict and verdict != "行业共振":
            reasons.append(f"共振「{verdict}」：变化更可能是公司自己的事")
        items = ((card.commodities or {}).get("items")) or {}
        tailwind = [s.get("name", "") for s in items.values()
                    if (s.get("chg_30d") or 0) > 0]
        if tailwind:
            reasons.append("盯价 30 日顺风：" + "、".join(tailwind))
        if not item["high_flags"]:
            reasons.append("无高危治理红旗")
        if item["expectations"].get("coverage"):
            reasons.append(f"机构覆盖 {item['expectations']['coverage']} 家")
        checks = card.account_checks or []
        gap = ("账户准入检查未写（最坏情境/流动性/相关性），状态推进由你裁决"
               if not checks else "账户检查已写，状态推进由你裁决")
        out.append({"card": card, "reasons": reasons, "gap": gap,
                    "score": len(reasons) + (1 if verdict == "公司特有" else 0)})
    out.sort(key=lambda row: (-row["score"], row["card"].code))
    return out[:2]


def _due_html(card: Card) -> str:
    """desk 表格的到期单元格：已过期给警示色（返回 HTML，调用处不再转义）。"""
    due = _due(card)
    if not due:
        return '<span class="muted">未定</span>'
    if "已过期" in due:
        return f'<span class="pos">{_esc(due)}</span>'
    return _esc(due)


def _desk_view(summaries: list[dict], regime_parts: list[str], ranked_count: int,
               active_count: int) -> str:
    """研究台绯红终端视图：状态条 + 持仓候选 + 全卡表。"""
    candidates = _hold_candidates(summaries)
    if candidates:
        rows = []
        for c in candidates:
            card = c["card"]
            context = card.market_context or {}
            quote = []
            if context.get("price"):
                quote.append(f"现价 {_esc(context['price'])}")
            if context.get("market_cap"):
                quote.append(f"市值 {context['market_cap'] / 1e8:.0f} 亿")
            rows.append(
                f'<div class="promo-row"><div class="promo-name"><b>{_esc(card.name)}</b><br>'
                f'<span class="muted mono">{_esc(card.market)} {_esc(card.code)}</span>'
                + (f'<br><span class="mono">{" ｜ ".join(quote)}</span>' if quote else "")
                + '</div>'
                f'<div class="promo-why">{_esc("；".join(c["reasons"]) or "证据齐但信号少")}'
                f'<br><span class="gap">还差：{_esc(c["gap"])}</span></div></div>')
        promo = ('<div class="desk-promo"><h4>持仓候选 · 最接近资格（资格=证据充分+零阻塞）</h4>'
                 + "".join(rows)
                 + '<div class="desk-foot" style="text-align:left;padding-top:8px">'
                 "排序信号：共振非行业 beta ＞ 盯价顺风 ＞ 无高危红旗 ＞ 有机构覆盖；"
                 "这是研究推进顺序，不是买入指令——最终裁决是你</div></div>")
    else:
        promo = ('<div class="desk-promo"><h4>持仓候选</h4>'
                 '<p class="promo-none">当前没有同时满足「证据充分 + 零阻塞」的观察卡——'
                 '这是纪律不是缺口：判断没写完就不该有持仓候选。</p></div>')
    status_line = " ｜ ".join(regime_parts)
    return (
        '<div class="desk-shell">'
        '<div class="desk-topline"><div class="desk-brand">小资金研究台'
        "<small>A / H · RESEARCH TERMINAL</small></div>"
        f'<div class="desk-status">{_esc(status_line)}<br>'
        f"<b>{ranked_count}</b> 张在册 · 活跃 <b>{active_count}</b> · "
        "非投资建议</div></div>"
        f"{promo}{_desk_table(summaries)}"
        '<div class="desk-foot">研究工具输出，非投资建议 · 数据：巨潮 / 同花顺 / 东财 / akshare'
        f" · {_esc(date.today().isoformat())}</div>"
        "</div>")


def _desk_table(summaries: list[dict]) -> str:
    """研究台全卡表：桌面一张表；<760px 每行转卡片（td 的 data-label 提供列名）。"""
    head = ("标的", "状态", "共振", "单季净利润", "经营现金流", "盯的价格",
            "相对强度", "红旗", "覆盖", "最近到期", "阻塞")
    body = []
    for item in summaries:
        card: Card = item["card"]
        resonance = item["resonance"]
        quarters = item["quarters"]
        momentum = item["momentum"]
        arrow = ('<span class="pos">' + momentum + "</span>" if momentum == "逐季↑"
                   else '<span class="neg">' + momentum + "</span>") if momentum else ""
        profit = (f'<span class="mono"><b>{quarters[-1][1] / 1e8:.2f} 亿</b> {arrow}</span>'
                  f'<br><span class="muted mono">季初 {quarters[0][1] / 1e8:.2f} 亿'
                  f'（{quarters[0][0]}）</span>') if quarters else "—"
        red = (f'<span class="pos">风险公告 {item["risk_count"]}</span><br>'
               f'<span class="muted">高危治理 {item["high_flags"]}</span>'
               if (item["risk_count"] or item["high_flags"]) else
               '<span class="muted">未留痕</span>')
        cash = item["capital"].get("operating_cf_label", "—")
        cash_cell = (f'<span class="pos">{_esc(cash)}</span>'
                     if str(cash).strip().startswith("-") else _esc(cash))
        cells = [
            f'<td data-label="{head[0]}"><b>{_esc(card.name)}</b><br>'
            f'<span class="muted mono">{_esc(card.market)} {_esc(card.code)} · '
            f'{_esc(card.derived_level())}</span></td>',
            f'<td data-label="{head[1]}">{_status_badge(card.status)}</td>',
            f'<td data-label="{head[2]}">{_esc(resonance.get("verdict", "—"))}'
            + (f'<br><span class="muted">{_esc(resonance["industry"])}</span>'
               if resonance.get("industry") else "") + "</td>",
            f'<td data-label="{head[3]}">{profit}</td>',
            f'<td data-label="{head[4]}" class="mono">{cash_cell}</td>',
            f'<td data-label="{head[5]}" class="mono w-limit">'
            f'{_esc(_watch_line(card)).replace(" ｜ ", "<br>")}</td>',
            f'<td data-label="{head[6]}">{_strength_line(card)}</td>',
            f'<td data-label="{head[7]}">{red}</td>',
            f'<td data-label="{head[8]}" class="mono">'
            f'{_esc(item["expectations"].get("coverage", "—"))}</td>',
            f'<td data-label="{head[9]}" class="mono">{_due_html(card)}</td>',
            (f'<td data-label="{head[10]}" class="pos">{item["blocks"]}</td>'
             if item["blocks"] else
             f'<td data-label="{head[10]}" class="muted">0</td>'),
        ]
        body.append("<tr>" + "".join(cells) + "</tr>")
    return ('<div class="desk-title">全部研究卡片（观察/持仓在前，降级在后）</div>'
            '<div class="desk-card"><div class="desk-wrap"><table class="desk">'
            + "<thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead>"
            + "<tbody>" + "".join(body) + "</tbody></table></div></div>")


_JS = """
(function () {
  var root = document.getElementById("xhs-root");
  if (!root) return;
  root.querySelectorAll("[data-view-btn]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      root.dataset.view = btn.getAttribute("data-view-btn");
      root.querySelectorAll("[data-view-btn]").forEach(function (b) {
        b.setAttribute("aria-pressed", b === btn ? "true" : "false");
      });
    });
  });
  var exportBtn = root.querySelector("[data-export-xhs]");
  if (exportBtn) exportBtn.addEventListener("click", function () {
    var view = root.querySelector(".view-xhs");
    var style = root.querySelector("style") ? root.querySelector("style").outerHTML : "";
    var doc = "<!doctype html>\\n<html lang=\\"zh-CN\\">\\n<head>\\n<meta charset=\\"utf-8\\">"
      + "<meta name=\\"viewport\\" content=\\"width=device-width, initial-scale=1\\">"
      + "<title>小红书笔记导出</title>"
      + "<style>body{margin:0;padding:16px 10px;background:#f5f5f7;}</style>\\n</head>\\n<body>\\n"
      + "<div id=\\"xhs-root\\" data-view=\\"xhs\\">" + style
      + '<div class="view-xhs" style="display:block">' + view.innerHTML + "</div></div>"
      + "\\n</body>\\n</html>";
    try {
      var blob = new Blob([doc], { type: "text/html" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "xhs-notebook-" + new Date().toISOString().slice(0, 10) + ".html";
      document.body.appendChild(a); a.click(); a.remove();
      exportBtn.textContent = "已导出 ✓";
    } catch (err) {
      exportBtn.textContent = "导出失败：请在浏览器独立打开本页后重试";
    }
  });
})();
"""


def notebook_fragment(cards: list[Card]) -> str:
    """全部卡片 → 汇总页。默认研究台视图（正常渲染），可切小红书笔记视图并导出。"""
    order = {"持仓研究": 0, "观察": 1, "降级": 2, "淘汰": 3}
    ranked = sorted(cards, key=lambda c: (order.get(c.status, 9), c.code))
    active = [c for c in ranked if c.status in ("观察", "持仓研究")]
    counts: dict[str, int] = {}
    for card in ranked:
        counts[card.status] = counts.get(card.status, 0) + 1
    chips = "".join(f'<span class="chip">{_esc(status)} <b>{count}</b></span>'
                    for status, count in counts.items())
    regime_parts = []
    for market, label in (("A", "A 股·沪深300"), ("HK", "港股·恒生指数")):
        for card in ranked:
            if card.market != market:
                continue
            regime = (card.market_context or {}).get("market_regime") or {}
            if regime.get("state"):
                regime_parts.append(f"{label}：{_esc(regime['state'])}"
                                    f"（{_esc(regime.get('day', ''))}）")
                break
    regime_state = " ｜ ".join(regime_parts)
    summaries = [_card_summary(card) for card in ranked]

    return (f'<div id="xhs-root" data-view="desk"><style>{_CSS}</style>'
            f'<div class="nb-head"><div class="title">小资金研究台</div>'
            f'<div class="sub">在册 {len(ranked)} 张 · 活跃 {len(active)} 张 · '
            f'{_esc(regime_state)}</div></div>'
            f'<div class="nb-stats">{chips}</div>'
            '<div class="toolbar">'
            '<div class="seg" role="group" aria-label="视图切换">'
            '<button type="button" data-view-btn="desk" aria-pressed="true">研究台</button>'
            '<button type="button" data-view-btn="xhs" aria-pressed="false">小红书笔记</button>'
            "</div>"
            '<button type="button" class="export" data-export-xhs>导出小红书笔记 HTML</button>'
            "</div>"
            f'<div class="view-desk">{_desk_view(summaries, regime_parts, len(ranked), len(active))}</div>'
            f'<div class="view-xhs">{_xhs_feed(summaries)}</div>'
            f'<p class="legal">研究工具输出，非投资建议 · 数据：巨潮/同花顺/东财 · '
            f'生成于 {date.today().isoformat()}</p>'
            f"<script>{_JS}</script></div>")


def save_document(fragment: str, path: Path, title: str, dark: bool = False) -> Path:
    """片段 → 可独立打开的完整 HTML 文档。dark=True 用于研究台深色页。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not fragment.lstrip().startswith("<div id=\"xhs-root\""):
        fragment = f'<div id="xhs-root"><style>{_CSS}</style>{fragment}</div>'
    background = "#0d0a0b" if dark else "#f5f5f7"
    theme_color = "#150f11" if dark else "#f5f5f7"
    document = (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<meta name=\"color-scheme\" content=\"light\">\n"
        f"<meta name=\"theme-color\" content=\"{theme_color}\">\n"
        f"<title>{_esc(title)}</title>\n"
        f"<style>body{{margin:0;padding:16px 10px;background:{background};}}"
        "@media (max-width:760px){body{padding:10px 8px;}}</style>\n"
        "</head>\n<body>\n" + fragment + "\n</body>\n</html>\n")
    path.write_text(document, encoding="utf-8")
    return path
