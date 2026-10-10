"""Run with: streamlit run app.py"""

from __future__ import annotations

import json
import hashlib
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from retail_agent.core import Profile, rank, shortlist
from retail_agent.data import DataError, fetch_history, fetch_quotes, import_csv
from retail_agent.llm import critique


st.set_page_config(page_title="A/H 小资金研究台", page_icon="📈", layout="wide")
st.markdown("""
<style>
html, body, [class*="css"] { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; }
.block-container { max-width: 1320px; padding-top: 1.4rem; }
h1 { font-size: 1.75rem !important; letter-spacing: 0 !important; }
h2, h3 { letter-spacing: 0 !important; }
[data-testid="stMetricValue"] { font-size: 1.25rem; }
</style>
""", unsafe_allow_html=True)

st.title("A/H 小资金研究台")
st.caption("量价候选 · 反证核查 · 交易单位约束  |  研究工具，不构成投资建议")


def parse_lots(text: str) -> dict[str, int]:
    lots = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            code, size = [part.strip() for part in line.split("=", 1)]
            lot = int(size)
            if not code.isdigit() or len(code) > 5 or lot <= 0:
                raise ValueError()
            lots[code.zfill(5)] = lot
        except ValueError:
            raise ValueError(f"每手股数格式错误：{line}；应为 00001=500") from None
    return lots


with st.sidebar:
    st.header("资金与风险")
    capital_cny = st.number_input("A 股资金 · CNY", min_value=1000, value=30000, step=1000)
    capital_hkd = st.number_input("港股资金 · HKD", min_value=1000, value=30000, step=1000)
    risk_pct = st.slider("单笔参考风险上限", min_value=0.1, max_value=2.0, value=0.5, step=0.1, format="%.1f%%")
    max_alloc = st.slider("单只仓位上限", min_value=5, max_value=30, value=20, step=1, format="%d%%")
    lot_text = st.text_area("港股每手股数（逐行）", placeholder="00001=500\n00700=100", height=100)
    st.caption("请按交易所/券商资料核实每只港股的每手股数。两地资金不自动换汇。")
    st.divider()
    st.header("AI 反证（可选）")
    model = st.text_input("模型名称", value=os.getenv("STOCK_LLM_MODEL", ""), placeholder="你的模型 ID")
    base_url = st.text_input("兼容 API 的 v1 地址", value=os.getenv("STOCK_LLM_BASE_URL", "https://api.openai.com/v1"))
    api_key = st.text_input("API key", type="password", value=os.getenv("STOCK_LLM_API_KEY", ""))
    st.caption("仅在点击分析时，将该候选的行情指标发送至配置的模型服务；密钥不写入导出文件。")

try:
    profile = Profile(capital_cny, capital_hkd, risk_pct / 100, max_alloc / 100, hk_lots=parse_lots(lot_text))
except ValueError as exc:
    st.sidebar.error(str(exc))
    st.stop()


@st.cache_data(ttl=1800, show_spinner=False)
def quotes_cached(market: str):
    return fetch_quotes(market)


@st.cache_data(ttl=1800, show_spinner=False)
def history_cached(market: str, code: str, name: str, price: float, turnover: float, market_cap):
    from retail_agent.core import Quote
    return fetch_history(Quote(market, code, name, price, turnover, market_cap))


def run_screen(market: str, quote_file=None, bar_file=None):
    st.session_state[f"attempted_{market}"] = True
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    source = "本地 CSV" if quote_file and bar_file else "AKShare 东方财富接口"
    try:
        if quote_file and bar_file:
            quotes, imported = import_csv(quote_file, bar_file, market)
        else:
            quotes, imported = quotes_cached(market), {}
    except DataError as exc:
        st.error(str(exc))
        return
    picked = shortlist(quotes, market, limit=35)
    if not picked:
        st.warning("快照中没有符合初筛条件的证券。请核查数据字段和行情时段。")
        return
    histories, errors = {}, []
    progress = st.progress(0, text="读取日线中")
    for i, quote in enumerate(picked):
        try:
            if quote_file and bar_file:
                histories[quote.code] = imported.get(quote.code, [])
            else:
                histories[quote.code] = history_cached(quote.market, quote.code, quote.name,
                                                       quote.price, quote.turnover, quote.market_cap)
        except DataError as exc:
            errors.append(str(exc))
        progress.progress((i + 1) / len(picked), text=f"日线 {i + 1}/{len(picked)}")
    progress.empty()
    st.session_state[f"result_{market}"] = {
        "quotes": picked, "histories": histories,
        "sample": len(picked), "universe": len(quotes), "errors": errors,
        "source": source, "fetched_at": now.isoformat(timespec="seconds"),
    }


def display_market(market: str):
    label = "A 股 · 主板" if market == "A" else "港股"
    st.subheader(label)
    if market == "A":
        st.caption("A 股主板；快照市值 20–300 亿元，股价 ≥ 2 元。创业板、科创板、北交所和 ST 不在本轮。")
    else:
        st.caption("港股成交额 500 万–3 亿港元的候选样本；未核实市值和每手股数，不能据此断言是中小市值。")
    with st.expander("本地 CSV 数据（在线接口不可用时）"):
        quote_file = st.file_uploader("快照 CSV", type="csv", key=f"quote_csv_{market}")
        bar_file = st.file_uploader("日线 CSV", type="csv", key=f"bar_csv_{market}")
        st.caption("上传后改用本地数据，不调用行情接口；两份文件必须同时提供。列名和单位见 README。")
    if st.button("读取并筛选" if quote_file and bar_file else "刷新并筛选", key=f"run_{market}", type="primary"):
        if bool(quote_file) != bool(bar_file):
            st.error("请同时提供快照 CSV 和日线 CSV。")
        else:
            run_screen(market, quote_file, bar_file)
    result = st.session_state.get(f"result_{market}")
    if not result:
        if not st.session_state.get(f"attempted_{market}"):
            st.info("点击刷新并筛选，获取行情快照与最多 35 只证券的日线。数据接口可能需要数分钟。")
        return
    candidates = rank(result["quotes"], result["histories"], profile)
    st.caption(f"来源：{result['source']} · 读取时间：{result['fetched_at']}（北京时间） · 快照记录 {result['universe']} · 日线抽样 {result['sample']} · 候选 {len(candidates)} · 失败 {len(result['errors'])}")
    if result["errors"]:
        with st.expander("接口失败详情"):
            st.code("\n".join(result["errors"][:35]), language=None)
    if not candidates:
        st.warning("抽样中没有满足量价结构且与最新日线日期一致的候选。这不是全市场无机会的结论。")
        return
    latest = max(c.day for c in candidates)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    stale = latest > today or latest < today - timedelta(days=14)
    if stale:
        st.error(f"日线日期 {latest} 与当前日期相距过远或在未来；停止展示仓位和 AI 分析，请核查数据源。")
        return
    st.info(f"最新日线：{latest}。快照可能是盘中值，日线可能未收盘；信号请在收盘后及下一交易日复核。排名只覆盖抽样证券。")
    rows = [{"代码": c.quote.code, "名称": c.quote.name, "形态": c.signal,
             "分数": c.score, "日线收盘": c.close, "量比(20日)": c.volume_ratio,
             "波动 ATR%": c.atr_pct, "20日成交额(百万)": round(c.median_turnover / 1e6, 1),
             "快照市值(亿)": round(c.quote.market_cap / 1e8, 1) if c.quote.market_cap else None,
             "参考股数": c.shares, "状态": c.status} for c in candidates]
    st.dataframe(rows, hide_index=True, use_container_width=True)
    for c in candidates[:10]:
        with st.expander(f"{c.quote.code}  {c.quote.name}  ·  {c.signal}  ·  {c.score}"):
            left, middle, right = st.columns(3)
            left.metric("日线收盘", f"{c.close:.3f} {'元' if market == 'A' else '港元'}")
            middle.metric("近 20 日前高", f"{c.prior_high:.3f}")
            right.metric("参考失效线 · 2 ATR", f"{c.stop_reference:.3f}")
            st.write(f"MA20 {c.ma20:.3f} · MA60 {c.ma60:.3f} · 量比 {c.volume_ratio:.2f} · ATR {c.atr_pct:.2f}%")
            st.write(f"{c.status}。每手 {c.lot or '未提供'} 股；参考 {c.lots} 手 / {c.shares} 股；名义金额 {c.notional:,.2f}，止损价差参考风险 {c.risk_amount:,.2f} {'CNY' if market == 'A' else 'HKD'}。")
            st.caption("按日线收盘估算，不含佣金、印花税、平台费、点差、滑点及汇率；跳空、停牌、跌停和 A 股 T+1 可能使止损无法执行。不是委托或收益预测。")
            fingerprint = hashlib.sha256(json.dumps({"candidate": asdict(c), "model": model, "base_url": base_url},
                                                  ensure_ascii=False, default=str, sort_keys=True).encode()).hexdigest()[:16]
            analysis_key = f"analysis_{market}_{c.quote.code}_{fingerprint}"
            if st.button("生成 AI 机会与反证", key=f"ai_{market}_{c.quote.code}", disabled=not (api_key and model)):
                with st.spinner("分析中"):
                    try:
                        st.session_state[analysis_key] = critique(c, api_key, base_url, model)
                    except ValueError as exc:
                        st.error(str(exc))
            analysis = st.session_state.get(analysis_key)
            if analysis:
                for title, key in (("机会假设", "opportunity"), ("反证", "countercase"),
                                   ("复核触发", "trigger"), ("失效条件", "invalidation"),
                                   ("待核实", "questions")):
                    st.write(title)
                    st.text(analysis[key])
                st.caption("模型文本未核实，不能替代公告、财报与交易规则。")
    export = {"source": result["source"] + "；未复权日线（请核实上传数据口径）", "market": market,
              "fetched_at": result["fetched_at"], "sample": result["sample"],
              "universe": result["universe"], "failures": result["errors"],
              "profile": {"capital": profile.capital_cny if market == "A" else profile.capital_hkd,
                          "currency": "CNY" if market == "A" else "HKD", "risk_fraction": profile.risk_fraction,
                          "max_allocation": profile.max_allocation},
              "candidates": [{**asdict(c), "day": c.day.isoformat()} for c in candidates]}
    st.download_button("下载本次研究记录 JSON", json.dumps(export, ensure_ascii=False, indent=2),
                       file_name=f"research_{market}_{latest}.json", mime="application/json",
                       key=f"download_{market}")


tab_a, tab_hk = st.tabs(["A 股", "港股"])
with tab_a:
    display_market("A")
with tab_hk:
    display_market("HK")
