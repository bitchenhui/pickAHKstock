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
  :host, #xhs-root { max-width: 440px; margin: 0 auto; }
  #xhs-root { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI",
              "Microsoft YaHei", sans-serif; color: #333; line-height: 1.55;
              -webkit-font-smoothing: antialiased; }
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
"""


def _status_badge(status: str) -> str:
    cls = "watch" if status in ("观察", "持仓研究") else "down"
    return f'<span class="badge {cls}">{_esc(status)}</span>'


def _regime_line(card: Card) -> str:
    regime = (card.market_context or {}).get("market_regime") or {}
    if not regime:
        return ""
    factor = "按 0.5× 折算" if regime.get("state") == "下行" else "不折算"
    return (f"市场状态<b>{_esc(regime.get('state', '—'))}</b>（{_esc(regime.get('day', ''))}，"
            f"沪深300 {_esc(regime.get('close', '—'))}）：此类票仓位{factor}")


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


def notebook_fragment(cards: list[Card]) -> str:
    """全部卡片 → 一页信息流汇总。观察/持仓在前，降级/淘汰在后。"""
    order = {"持仓研究": 0, "观察": 1, "降级": 2, "淘汰": 3}
    ranked = sorted(cards, key=lambda c: (order.get(c.status, 9), c.code))
    active = [c for c in ranked if c.status in ("观察", "持仓研究")]
    counts: dict[str, int] = {}
    for card in ranked:
        counts[card.status] = counts.get(card.status, 0) + 1
    chips = "".join(f'<span class="chip">{_esc(status)} <b>{count}</b></span>'
                    for status, count in counts.items())
    regime_state = ""
    for card in ranked:
        regime = (card.market_context or {}).get("market_regime") or {}
        if regime.get("state"):
            regime_state = f'A股市场状态：{_esc(regime["state"])}（{_esc(regime.get("day", ""))}）'
            break

    notes = []
    for card in ranked:
        resonance = card.industry_context or {}
        quarters = quarterly(card.financials, "净利润") if card.financials.metrics else []
        scans = card.scans or {}
        high_flags = len([g for g in (scans.get("governance") or [])
                          if g.get("severity") == "高"])
        risk_count = len(scans.get("risks") or [])
        capital = card.capital or {}
        expectations = card.expectations or {}
        momentum = ""
        if len(quarters) >= 2:
            momentum = "逐季↓" if quarters[-1][1] < quarters[0][1] else (
                "逐季↑" if quarters[-1][1] > quarters[0][1] else "持平")
        rows = [
            ("共振", f"{resonance.get('verdict', '—')}" + (f"·{resonance.get('industry', '')}"
                                                             if resonance.get("industry") else "")),
            ("单季利润", (f"{momentum}（{quarters[0][0]} {quarters[0][1] / 1e8:.2f} 亿 → "
                          f"{quarters[-1][0]} {quarters[-1][1] / 1e8:.2f} 亿）") if quarters else "—"),
            ("经营现金流", capital.get("operating_cf_label", "—")),
            ("红旗", f"风险公告 {risk_count} 条 · 高severity治理 {high_flags} 条"
                     if (risk_count or high_flags) else "未扫描留痕"),
            ("机构覆盖", str(expectations.get("coverage", "—")) + " 家"),
            ("最近到期", _due(card) or "未定"),
        ]
        kv = "".join(f'<div class="kv"><span class="k">{_esc(k)}</span>'
                     f'<span>{_esc(v)}</span></div>' for k, v in rows)
        blocks = len(blocking(validate(card)))
        head = (_esc(card.name) + _status_badge(card.status)
                + (f' <span class="badge down">{blocks} 项阻塞</span>' if blocks else ""))
        notes.append(f'<div class="note"><div class="cover" style="margin:0 0 10px">'
                     f'<h2 style="font-size:16px">{head}</h2>'
                     f'<p class="sub">{_esc(card.market)} {_esc(card.code)} · '
                     f'{_esc(card.derived_level())} · 更新 {_esc(card.updated or card.created or "")}</p></div>'
                     + kv + '<div class="actions" aria-hidden="true"><span>❤️</span>'
                       '<span>⭐</span><span>💬</span></div></div>')
    return (f'<div id="xhs-root"><style>{_CSS}</style>'
            f'<div class="nb-head"><div class="title">小资金研究台 · 观察池信息流</div>'
            f'<div class="sub">在册 {len(ranked)} 张 · 活跃 {len(active)} 张 · '
            f'{_esc(regime_state)}</div></div>'
            f'<div class="nb-stats">{chips}</div>'
            f'<div class="feed">{"".join(notes)}</div>'
            f'<p class="legal">研究工具输出，非投资建议 · 数据：巨潮/同花顺/东财 · '
            f'生成于 {date.today().isoformat()}</p></div>')


def save_document(fragment: str, path: Path, title: str) -> Path:
    """片段 → 可独立打开的完整 HTML 文档。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not fragment.lstrip().startswith("<div id=\"xhs-root\""):
        fragment = f'<div id="xhs-root"><style>{_CSS}</style>{fragment}</div>'
    document = (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{_esc(title)}</title>\n"
        "<style>body{margin:0;padding:16px 10px;background:#f5f5f7;}</style>\n"
        "</head>\n<body>\n" + fragment + "\n</body>\n</html>\n")
    path.write_text(document, encoding="utf-8")
    return path
