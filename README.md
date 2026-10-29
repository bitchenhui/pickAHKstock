# A/H 小资金研究台

**注意：当前应用是早期量价原型，不实现已讨论的账户增长策略，不能依据其排名作投资判断。** 策略边界、证据门槛与验证要求见 [策略 v1 讨论稿](docs/strategy-v1.md)。

面向资金规模较小的个人投资者的 **候选研究工具**，不是自动交易或收益承诺。先由确定性规则限制样本与风险，再可选调用兼容 Chat Completions 的模型做机会假设、反证与待核实事项。模型无法改写排名，也不接触交易账户。

## 运行

要求 Python 3.9+。在仓库根目录：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

打开 Streamlit 在终端显示的本地地址。分别点击 A 股/港股的“刷新并筛选”。不使用模型也能查看规则候选；使用 AI 时在侧栏填入模型名称、兼容 API 的 `/v1` 地址和密钥，逐只点击分析。密钥只在应用会话中使用，不写入导出 JSON。不要将实例直接暴露到公网。

东方财富接口不可用时，可展开“本地 CSV 数据”同时上传快照和日线（UTF-8，首行为以下中文列名）。这是**用户自备的真实行情**，程序不会附带虚构股票作为推荐。两份文件的代码都请保留前导零，金额为原始元/港元而非万元；一行快照对应一只证券，一只证券的日线至少 80 根：

```text
快照 A: 代码,名称,最新价,成交额,总市值
快照 HK: 代码,名称,最新价,成交额
日线 A/H: 代码,日期,开盘,最高,最低,收盘,成交量,成交额
```

日期使用 `YYYY-MM-DD`，不传复权日线；快照和日线应来自同一交易时段的可靠来源。导入后仍只抽样最多 35 只并按最新日线日期筛选。上传文件只在当前本地 Streamlit 会话中处理。

## 方法与边界

- 数据：AKShare 的东方财富 A 股/港股主板快照和未复权日线，或用户自备的 CSV。读取时间、日线日期、接口失败记录可见；在线接口缓存 30 分钟。未复权日线可受拆合股/除权影响，明显跳变会剔除。接口字段变化会报错，不静默补造数据。CSV 的来源/复权口径需用户自行核实。
- 漏斗：A 股只看主板，排除 ST/退市标记，快照总市值 20–300 亿元、股价至少 2 元；港股只以股价及快照成交额 500 万–3 亿港元抽样。每市场最多抓取 35 只日线，按快照成交额接近预设中心值选样，**不是全市场遍历，也不是港股小市值证明**。盘口、股东集中度、财务增长、公告、概念催化未接入，必须另行核查。
- 结构：至少 80 根日线、20 日成交额中位数、MA20/MA60 趋势、前 20 日高点、放量突破或缩量趋势回踩、ATR 波动上限。只保留同市场抽样中最新日线日期的候选；超过 14 个自然日的日线停止显示仓位。分数是内部排序，**不是上涨概率**。
- 仓位：A 股按 100 股的常见交易单位估算；港股必须手工核实并输入个股每手股数，否则仓位为零。分别使用 CNY/HKD 资金，按单笔参考风险及单只资金占用的较小者取整到整手；基于收盘与 2 ATR 参考线估算，不含税费、汇率、买卖价差、滑点、停牌/涨跌停、跳空等。A 股 T+1 导致买入当日不能卖出，参考线不保证止损成交。港股每手股数和实际可交易性应在交易所和券商复核。
- AI：仅发送当前候选的结构化行情数据，不应推断未提供的财务、新闻或公司事实。模型回答仍可能出错，输出是核查清单，不是下单指令。读取用户提供的服务地址前请确认模型服务方的数据与隐私政策。

参考思路：[TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN) 的多视角辩论、[TradingAgents-Astock](https://github.com/simonlin1212/TradingAgents-astock) 的 A 股适配、[go-stock](https://github.com/ArvinLovegood/go-stock) 的本地化行情工具、[aiagents-stock](https://github.com/oficcejo/aiagents-stock) 的 Agent 流程、[WyckoffTradingAgent](https://github.com/YoungCan-Wang/WyckoffTradingAgent) 的价量结构。这里只借鉴研究分工，不复制任何项目代码，也不宣称复现 Wyckoff 完整判读。

数据接口字段参考 [AKShare A/H 股票接口](https://akshare.akfamily.xyz/data/stock/stock.html)；交易单位以 [上交所交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/) 和 [港交所证券市场交易机制](https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism?sc_lang=zh-HK) 为准。依赖外部免费数据服务，可能限流或中断；不要把缺失结果解释为没有机会。

## 测试

```bash
PYTHONPYCACHEPREFIX=/tmp/pickahk-pycache .venv/bin/python -m unittest discover -s tests -v
```

## 研究工作台（`research/`）

上面的 Streamlit 应用是**量价原型**；`research/` 是另一条线：按策略 v1 的研究漏斗做**单股研究**，产出可追溯的研究卡片。两者定位不同，���不互相替代。

- `research/events.py` 巨潮公告检索 + 东方财富业绩预告结构化数据（均可免登录）
- `research/quotes.py` 腾讯行情快照（批量、免登录、字段映射显式）
- `research/discover.py` L1 发现层：按**经营变化**（不是量价形态）提出研究对象，输出带公告日期与原文链接的候选
- `research/pdftext.py` 公告 PDF 下载缓存与取文
- `research/ths.py` 同花顺 F10 财务主表（报告期/年度/单季度三套序列与同比）、分年日线、ATR 与参考失效线、分析师预测、管理层变动，均免账号
- `research/risk.py` 风险公告扫描：按公司名取全部公告再按标题分类，覆盖仲裁诉讼、账户偿债、股权质押、监管问询、审计意见、重组变更、资产质量、治理变动、停复牌九类
- `research/governance.py` 治理与筹码结构：巨潮的股东户数、股权质押、诉讼、担保结构化数据——比标题扫描多出「几次、多少钱、谁在质押、筹码在集中还是分散」
- `research/hk.py` 港股发现层，三条事件源：`alerts` 盈警/盈喜/业绩预告（最像 A 股业绩预告，60 天实测 80 个，多为流动性好的中大盘）、`results` 业绩公告（强制披露、覆盖全市场）、`financials` 财务同比变化（结构化接口，覆盖全市场且给出变化量级）。**每手股数从腾讯行情取到，不必手填**
- `research/account.py` L3 账户层：账户配置、回撤状态、按整手与风险预算的仓位估算、账户级动作（加仓/不加仓/减仓/清仓）
- `research/card.py` 研究卡片 schema 与校验器：**结论等级由证据推导**，证据不足只能停在「观察」；财务路径（§30 已盈利 / §34 未盈利）没写完也不算数
- `research/workflow.py` 卡片草稿生成与 Markdown 渲染
- `cards/` 每只标的一张卡片（JSON，可入版本控制，作为研究留痕）
- `account.example.json` 账户参数模板；复制为 `account.json`（已 gitignore）后填入真实资金、持仓与费率

```bash
.venv/bin/python -m research.cli discover --period 2026-09-30 --limit 10   # 发现候选
.venv/bin/python -m research.cli new --codes 002132,002044                  # 生成卡片草稿（A 股）
.venv/bin/python -m research.cli new --market HK --codes 09696,03323        # 生成卡片草稿（港股）
.venv/bin/python -m research.cli financials A 002132                        # 取财务序列（只取数，不下判断）
.venv/bin/python -m research.cli financials HK 09696                        # 港股财务（东方财富，单只查询较慢）
.venv/bin/python -m research.cli reference A 002132                         # 用日线算 ATR 与 2ATR 参考失效线
.venv/bin/python -m research.cli risks A 002132                             # 风险公告扫描
.venv/bin/python -m research.cli governance A 002132                       # 治理与筹码（户数/质押/诉讼/担保）
.venv/bin/python -m research.cli hk-discover --source results --days 30               # 港股：业绩公告
.venv/bin/python -m research.cli hk-discover --source financials --date 2026-06-30  # 港股：财务同比变化
.venv/bin/python -m research.cli show A 002132                              # 渲染卡片
.venv/bin/python -m research.cli check A 002132                             # 校验（阻塞项会挡住状态推进）
.venv/bin/python -m research.cli list                                       # 所有卡片状态、等级与质量门
.venv/bin/python -m research.cli account show                               # 账户权益、现金、持仓与回撤状态
.venv/bin/python -m research.cli account plan                               # 账户级动作（不是股票名单）
```

工具只负责把数据取来并摆到人面前：路径归类、经常性判断、周期性判断这些必须由人写，写不出来就说明这一关没过。账户层也一样——资金、持仓、费率与峰值权益由使用者填，工具不替你猜；缺 `account.json` 就拒绝给仓位建议。

**单季度口径必须看。** 累计数据会骗人：2026 年前三季度「扭亏」的恒星科技，Q3 单季扣非实际是 -4991 万元。`financials` 命令同时给出单季度序列，就是为了让这种差异无处可藏。

**账户层只给账户动作，不给股票名单。** 已持仓的卡片一旦被降级或淘汰，账户层会给出减仓/清仓动作；卡片还在「观察」时不出手是纪律，不是没找到机会。回撤状态只影响是否新增仓位，不改写研究判断。

**港股公告 PDF 不能用 A 股那套抽文。** 港股公告的中文是 CID 字体，pypdf 抽出的中文字符数为 0（数字完整但对应不到科目），所以港股的数字必须走结构化财务接口，PDF 只用于人工复核。港股财务报表多为人民币口径、股价为港元，比较估值前要自己换算汇率。

**港股的预告标题不能当结论。** 首两张港股卡片就是反例：盈利预喜的天齐锂业，Q3 单季归母环比 −42%（预喜讲的是前三季累计）；盈利预警的中国建材，Q3 单季已扭亏且现金流单季回正 44 亿（预警讲的也是累计）。两个方向的标题都只覆盖累计口径，**必须做单季拆解**。这也是 A 股那边同一个教训在港股的复现。

**风险公告要单独扫。** 业绩预告挑出来的候选，身上可能还挂着仲裁、账户冻结、监管问询、商誉减值、重组终止。`risks` 命令按标题关键词扫九类风险；关键词会误报（例：立昂微「终止信用评级」实为可转债摘牌而非信用恶化），所以每条都要回原文确认。

**治理数据比标题扫描有用。** `governance` 命令给出的是硬数字：股东户数与增减（筹码集中还是分散）、对外担保笔数与**占归母净资产比例**、诉讼次数与金额、质押占股本比例。首轮就发现五只候选的对外担保占净资产从 30.88% 到 885.49% 不等——这类数字比「有担保公告」重要得多。

**卡片字段的设计参照了同类项目**（TradingAgents-CN / astock / aiagents-stock / WyckoffTradingAgent / ai-hedge-fund / easy-stock 等的源码）：催化剂必带日期、风险必带作用机制、必须有「我可能错在哪」、每条证据带出处与新鲜度、整卡有质量门。不同点是我们不输出买卖建议、目标价与上涨概率——评分与概率在这套框架里没有位置。

发现层目前只覆盖 A 股（业绩预告口径）；港股需要另按交易所公告口径实现。卡片中的每条证据都带出处、发布日期与原文链接，缺出处的证据一律标「待核实」，不会因为结论好看而被放行。

