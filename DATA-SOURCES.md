# DATA-SOURCES.md — 各 skill 的数据需求与数据源总表

本文档回答三个问题：**项目里有哪些 skill**、**每个 skill 工作时需要哪些数据**、**这些数据的数据源是什么**。

> **维护边界**
> - **表清单与列数只在项目根 [CLAUDE.md](CLAUDE.md)「数据库表」一节维护**，本文不重复列 26 张表的元数据，只写"哪个 skill 读/写哪些表"。
> - **字段级清单（A股/港股逐字段、科目代码映射、口径陷阱）**在 [`.claude/skills/buffett-lens/data-sources.md`](.claude/skills/buffett-lens/data-sources.md)——那份是问诊取数层的详表，本文是**项目级总览**，两者视角不同：那份回答"这个字段喂给哪项读数"，本文回答"这个 skill 靠哪些数据活着、从哪来"。

## 一、四个 skill 与数据流

| Skill | 干什么 | 数据落点 |
|---|---|---|
| [`security-analysis`](.claude/skills/security-analysis/SKILL.md) | 财报采集 / ETF估值 / 周期股 / 红利股 / 行业竞争（5 模块） | `financial_data.db` + 实时 API |
| [`buffett-lens`](.claude/skills/buffett-lens/SKILL.md) | 巴菲特视角问诊（原则卡 + 量化读数 + 语料检索） | 读 `financial_data.db` + 自建 `buffett_corpus.db` |
| [`financial-report-downloader`](.claude/skills/financial-report-downloader/SKILL.md) | 搜 / 下 / 验 / 存年报与中报 PDF | `report/{公司}_{代码}/*.pdf` |
| [`financial-report-pdf-extractor`](.claude/skills/financial-report-pdf-extractor/SKILL.md) | 从年报 PDF 抽三表 + 分部附注 | JSON/Markdown + 回写 `hk_segment_revenue` |

四者构成一条链，**不是并列的四个独立工具**：

```
financial-report-downloader        security-analysis（collect）
        │ 年报 PDF 原文                    │ akshare 三源财报
        ▼                                  ▼
financial-report-pdf-extractor ──►  financial_data.db  ◄── security-analysis（估值/分红/回购）
        │ 分部附注 → hk_segment_revenue        │
        └──────────────────────────────────────┤
                                               ▼
                                        buffett-lens（问诊）
                                               │ 原话
                                               ▼
                                        buffett_corpus.db（独立库）
```

**两个库严格分离**：`buffett_corpus.db` 与 `corpus/` 存巴菲特语料原文，**严禁写入 `financial_data.db`**（该库已在 git 中，涉及版权与体积）；两者均在 `.gitignore` 内。

## 二、各 skill 需要的数据与数据源

### 2.1 security-analysis

#### 模块一：财报采集（`scripts/collect_financial_data.py`）

- **需要**：股票代码 + 市场（`sh`/`sz`/`hk`）+ 起始年
- **数据源**：akshare 聚合三源，**A股 5 个源、港股 2 个源**

| 上游 | akshare 接口 | 覆盖 |
|---|---|---|
| 新浪 | `stock_financial_analysis_indicator` | A股财务指标 |
| 新浪 | `stock_financial_report_sina` | A股三大报表 |
| 同花顺 | `stock_financial_{debt,benefit,cash}_new_ths` | A股三大报表（长表） |
| 东方财富 | `stock_financial_analysis_indicator_em` | A股主要指标 |
| 东方财富 | `stock_{balance_sheet,profit_sheet,cash_flow_sheet}_by_report_em` | A股三大报表 |
| 东方财富 | `stock_financial_hk_analysis_indicator_em` | 港股主要指标 |
| 东方财富 | `stock_financial_hk_report_em` | 港股三大报表（EAV 长表） |

- **写入**：15 张表 —— `{sina,ths,em}_` × `{balance_sheet,income_statement,cash_flow}`（9 张）+ `sina_financial_indicator` + `em_financial_indicator` + 港股 4 张
- **注意**：`sina_*` 与 `ths_*` 共 8 张为 **`只写不读`**，其中 `ths_*` 三张占库体积约 44%

#### 模块二：ETF 估值（`scripts/etf_valuation.py`）

- **需要**：基金代码（可选 `--market`）
- **数据源**：**本模块不走 akshare**（除分红汇总外），直接打 HTTP 接口

| 数据 | 接口 | 说明 |
|---|---|---|
| ETF 申赎清单 PCF | 上交所 `query.sse.com.cn/commonQuery.do`；深交所 `szse.cn/api/report/ShowReport/data` + `reportdocs.static.szse.cn` | 当日成份股、数量、现金替代标志 |
| 价格 / PE / PB | 腾讯 `qt.gtimg.cn` | **港股 PB 在 `parts[58]`，A股在 `parts[46]`**，脚本按 `v_hk`/`v_sh` 前缀切换 |
| A股备用价格 | 东方财富 `82.push2.eastmoney.com` | 腾讯失败兜底，**仅 A股** |
| A股 TTM 股息率 | 东财 `datacenter-web.eastmoney.com` | 近 12 个月分红 |
| 港股 TTM 股息率 | 东财 `emweb.securities.eastmoney.com`（EM CoreReading） | 主源，365 天内除净日求和 |
| 港股 TTM 股息率（备用） | 东财 `RPT_HKF10_MAIN_DIVBASIC` | **只在主源无该股记录时整体替换，绝不相加** |
| 港股回购收益率 | 东财 `RPT_HK_BUYBACK` | 场内回购，**不区分注销/库存** |
| 历史分红汇总 | 新浪 `ak.stock_history_dividend` | 累计股息、年均股息、分红次数 |

- **写入**：`stock_valuation`（A股）、`hk_yield_cache`（港股，含回购与总回报率）、`stock_dividend`（**惰性表**，`dividend` 子命令首跑才建）

#### 模块三：周期股分析（`scripts/cyclical_stock_analysis.py`）

- **需要**：`em_income_statement` / `em_cash_flow`（季度：存货、毛利率、预收/合同负债、应付账款）+ 估值历史序列
- **数据源**：akshare `stock_zh_valuation_baidu`（百度源，近 10 年 PE/PB 序列）+ 东财行情
- **写入**：`stock_valuation_history`（7 天有效期，`--refresh` 强刷）
- **前置**：必须结合 [`knowledge/INDEX.md`](.claude/skills/security-analysis/knowledge/INDEX.md) 路由到的行业知识解读（现有 `baijiu.md`、`aluminum.md`）

#### 模块四：红利股分析（`scripts/dividend_stock_analysis.py`）

- **需要**：`em_cash_flow` + `em_income_statement`（**前置条件：须先 `collect`**）
- **数据源**

| 数据 | 接口 | 说明 |
|---|---|---|
| A股分红明细 | akshare `stock_history_dividend_detail` | 新浪源，逐笔分红 |
| 年度股息率 / 支付率 | akshare `stock_fhps_em` | 东财分红送配 |
| 回购记录 | akshare `stock_repurchase_em` | 东财，**须区分注销用途 vs 激励用途** |
| 股价（算股息率） | 腾讯 `qt.gtimg.cn` | 新浪分红明细不含现价 |

- **写入**：`dividend_annual_yield`、`stock_dividend_detail`、`stock_repurchase`

#### 模块五：行业竞争分析（`scripts/industry_competition_analysis.py`）

- **需要**：**无前置采集**，全走实时 API（这一点与其他四个模块不同）
- **数据源**：akshare `stock_individual_info_em`（行业识别）、`stock_board_industry_name_em` + `stock_board_industry_cons_em`（行业板块与成份股）、`stock_zh_a_spot_em`（实时总市值）、`stock_yjbb_em`（最近一期业绩报表）
- **写入**：不写库，纯输出
- **硬性前置**：**必须先 Web Search 交叉验证东财行业分类**，不一致时用 `--industry` 覆盖（东财会把"贵州茅台"分到"饮料制造"而非"白酒"）
- **限制**：仅 A股，港股不支持

### 2.2 buffett-lens

问诊的八项量化读数全部吃 `financial_data.db`，原话吃 `buffett_corpus.db`。

| 所需数据 | 表 / 来源 | 提供哪项读数 |
|---|---|---|
| A股 ROE / 毛利率 / 负债率 / 净利 | `em_financial_indicator`（过滤 `REPORT_DATE_NAME LIKE '%年报'`） | 【三】ROE 一致性、【四】毛利率、【七】杠杆 |
| A股 现金流 / capex / 折旧 | `em_cash_flow`（**只取 `quarter=4`**，流量为年初至今累计） | 【二】股东盈余、【五】capex/OCF、【六】盈利质量 |
| A股 归母净利 / EPS / 营收 | `em_income_statement`（`quarter=4`） | 【二】【六】【八】 |
| A股 PE/PB 分位、市值 | `stock_valuation_history` | 【八】市值推算、【九】安全边际 |
| A股 每股股息 | `dividend_annual_yield` | 【八】一美元留存测试第①阶梯 |
| 港股三表 | `hk_balance_sheet` / `hk_income_statement` / `hk_cash_flow`（EAV 长表，按 `STD_ITEM_CODE` 取） | 八项读数（港股口径） |
| 港股估值 + 汇率 | `buffett_analysis.py fetch-valuation` ← akshare `stock_hk_valuation_baidu`（百度源）+ `currency_boc_sina`（中行港币汇率） | 【八】市值、【九】分位 |
| 港股股息/回购对照 | `hk_yield_cache`（由 `etf_valuation.py` 写） | 【八】交叉验证，**只对照、不参与计算** |
| 分部收入/业绩/折旧/capex | `hk_segment_revenue` ← **年报 PDF 分部附注**（由 pdf-extractor 抽） | ⓪能力圈 + ①定价权 |
| 巴菲特原话 | `berkshirehathaway.com`（致股东信 1977–2024）+ GitHub `wuxiaoda/BRK-Annual-Meeting`（年会问答 1994–2022） | 各原则卡的逐字引用 |

- **写入**：`stock_valuation_history`（港股估值）、`hk_fx_rate`（汇率）
- **语料落点**：`buffett_corpus.db` + `corpus/`（实测 117M + 27M），**均不入 git**
- **字段级细节与口径陷阱**：见 [该技能的 data-sources.md](.claude/skills/buffett-lens/data-sources.md) §二–§五（`004030999` 是归母而非 `004028999`、港股汇率必须用年末即期、A股市值必须推导等）

### 2.3 financial-report-downloader

- **需要**：公司名 + 股票代码 + 报告类型（年报/中报）+ 年份
- **数据源**：**WebSearch 网上检索 PDF**，来源优先级
  1. 公司官网投资者关系页面
  2. 香港交易所披露易（港股）
  3. 财经网站（雪球、腾讯财经等）
- **落盘规范**：`report/{公司名称}_{股票代码}/{公司名称}_{年份}_{年报,中报}.pdf`
- **PDF 原文不入 git**（`.gitignore` 中 `report/*_*/`）——单份年报可达 46MB，抽出的数据落在库中，原文可随时重抽
- **验证**：用 `pdf_helper` 抽第一页文本，校验公司名（支持中文/英文/繁体变体）+ 报告类型关键词，排除补充公告与业绩公告

### 2.4 financial-report-pdf-extractor

- **需要**：**本地年报 PDF 路径**（纯本地解析，无网络）
- **数据源**：PDF 文件本身；后端按 PyMuPDF > pypdf > pdfminer.six 探测，**只有 PyMuPDF 支持 `ColumnPage` 位置感知提取**
- **产出**：三表 JSON + Markdown；`extract_segment_note.py` 另将分部附注写入 `hk_segment_revenue`
- **两道硬闸门**（任一不过即拒绝入库）：① 同口径内 Σ分部 + 内部冲销 == 总计 ② 对外收入合计 == 利润表营运收入
- **已覆盖分部数据**：`00322`（康师傅，2020-2025）、`09633`（农夫山泉，2019-2025）

## 三、数据源汇总（按上游归类）

| 上游 | 提供什么 | 谁在用 |
|---|---|---|
| **akshare**（聚合层） | A股/港股财报三表与指标、行业板块、业绩报表、分红回购明细、百度估值序列、中行汇率 | security-analysis 全部模块 + buffett-lens 取数 |
| **东方财富**（直连 HTTP） | ETF 之外的 TTM 股息率、港股分红（双源）、港股回购 | `etf_valuation.py` |
| **腾讯 `qt.gtimg.cn`** | 实时价格 / PE / PB（A股 + 港股） | `etf_valuation.py` |
| **上交所 / 深交所** | ETF 申赎清单 PCF 文本与 JSON 接口 | `etf_valuation.py` |
| **新浪财经** | 历史分红汇总、中行港币汇率 | `etf_valuation.py`、`buffett_analysis.py` |
| **百度股市通** | 近 10 年 PE/PB 序列、港股估值 | `cyclical_stock_analysis.py`、`buffett_analysis.py` |
| **公司官网 / 港交所披露易** | 年报与中报 PDF 原文 | `financial-report-downloader` |
| **berkshirehathaway.com + GitHub 年会仓库** | 巴菲特致股东信与股东大会问答 | `buffett-lens` 语料 |

### 数据落点分布

| 落点 | 内容 | 入 git？ |
|---|---|---|
| `financial_data.db` | 全部采集与分析数据的共享总线（26 张表） | **是**（随采集变动） |
| `buffett_corpus.db` + `corpus/` | 巴菲特语料原文与 FTS5 索引（117M + 27M） | 否 |
| `report/{公司}_{代码}/` | 年报 PDF 原文 | 否 |
| `report/*.md` | 分析报告（问诊单等） | 是 |

## 四、两个必须留意的点

**一、表在库里 ≠ 表有数据。** 26 张表中 8 张 `只写不读`、3 张中文名表（`资产负债表`/`利润表`/`现金流量表`）是**遗留表、无任何代码引用**。查数前先确认目标表是否被实际写入过。

**二、覆盖度有已知缺口（截至 2026-09-13 实测，引用前请重跑 `buffett_analysis.py data-check` 核对）。**

| 表 | 股票数 / 行数 | 影响 |
|---|---|---|
| `em_financial_indicator` | **4** / 158 | 而 `em_cash_flow`/`em_income_statement` 覆盖 11 只 / 各 431 行 → 多出的 7 只跑 `screen` 时【三】ROE、【四】毛利率、【七】杠杆**全记 NA**（`ind_roe`/`ind_margin`/`ind_leverage` 只读指标表，不读利润表）。**这是采集覆盖缺口，补采集即可**，不要把 NA 读成"没问题" |
| `dividend_annual_yield` | **1**（仅 `601919`）/ 6 | 【八】一美元留存测试的股息几乎全靠 `ASSIGN_DIVIDEND_PORFIT`，而该科目**含利息支付** → 股息高估 → 留存低估 → **测试偏保守**（方向安全，但读报告时要知道） |
| `hk_segment_revenue` | 2 / 276 | 缺该股数据时 `segment` 子命令会打印抽取命令，**不要用记忆里的分部数字代替** |
| `stock_dividend_detail` / `stock_repurchase` | 各 1（`601919`）/ 5 行回购 | 红利股四维分析目前**只对 `601919` 完整可跑** |
| `stock_dividend` | **表不存在**（惰性） | `etf_valuation.py dividend` 子命令从未跑过；首次采集后表数变 27，勿据此判断库损坏 |

> 上表为 2026-09-14 实测值。其余表实测：`stock_valuation_history` 12 只 / 8,919 行、`hk_yield_cache` 62 只 / 62 行、`stock_valuation` 58 只 / 58 行、`hk_fx_rate` 按日期而非股票代码为主键。

## 五、数据缺失时的三条纪律

1. **NA 不是 PASS**：样本不足（【三】【四】【六】需 ≥7 年，【八】需 ≥5 年）一律记 NA，那是"不知道"，不是"没问题"。
2. **先补数据再判读**：`data-check` 会打印补齐命令；补不上就在报告里写清缺什么、因此哪几项读不出来。
3. **绝不用记忆里的数字代替**：库里没有就说明库里没有——这是这套工具存在的全部理由。
