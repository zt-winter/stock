# 数据来源与字段清单

本文档回答两个问题：**问诊要哪些数据**、**每一项从哪来**。

配套另一条纪律在 [diagnostic-guide.md](diagnostic-guide.md) §三：数字分四级（A 可重算 / B 年报原文 / C 外部来源 / D 语料），**C 类不得进入判定依据栏**。本文档所列全部为 **A 类**——即 `financial_data.db` 里可重算的数据。

## 一、总览：数据 → 读数 → 卡片

```
financial_data.db ──┬── 财报三表（A股宽表 / 港股EAV长表）── 【二】–【七】八项读数
                    ├── stock_valuation_history ──────────── 【八】【九】
                    ├── hk_yield_cache ──────────────────── 【八】交叉验证（不参与计算）
                    └── hk_segment_revenue ──────────────── ⓪能力圈 / ①定价权

buffett_corpus.db ───── 致股东信 + 年会问答 ─────────────── 各卡逐字引用（独立库）
```

**两个库互不相干**：语料原文**严禁写入 `financial_data.db`**（该库已在 git 中）。

## 二、A 股

### 2.1 `em_financial_indicator`（年报口径）

过滤条件 `REPORT_DATE_NAME LIKE '%年报'`——**该表没有 `year`/`quarter` 列**，只有 `REPORT_YEAR` + `REPORT_DATE_NAME`。用季报算 ROE 一致性会得出错误结论。

| 字段 | 供哪项读数 |
|---|---|
| `ROEJQ` | 【三】10 年 ROE 一致性（主线） |
| `ROEKCJQ` | 【三】扣非交叉校验（与 `ROEJQ` 长期背离 >5pct 则降级） |
| `XSMLL` | 【四】毛利率稳定性 |
| `XSJLL` | 【四】净利率同向校验 |
| `ZCFZL` | 【七】杠杆 + 【三】高杠杆年份剔除（>60% 剔出最低值判定） |
| `INTEREST_DEBT_RATIO` | 【七】有息负债率 |
| `PARENTNETPROFIT` | 【七】偿还年数、【八】留存分子 |
| `EPSJB` / `BPS` | 【八】股本反推、市值交叉验证 |
| `KCFJCXSYJLR` | 【三】扣非净利 |
| `TOTALOPERATEREVE` | 备用 |

### 2.2 `em_cash_flow`（`quarter=4`）

**流量是年初至今累计，所以只取 `quarter=4`，绝不 ×4/3 折算**；`FA_IR_DEPR`/`IA_AMORTIZE` 也只有 q4 有值。

| 字段 | 供哪项读数 |
|---|---|
| `NETCASH_OPERATE` | 【二】股东盈余、【五】capex/OCF、【六】盈利质量 |
| `CONSTRUCT_LONG_ASSET` | 【二】【五】capex（购建固定资产、无形资产和其他长期资产支付的现金） |
| `ASSIGN_DIVIDEND_PORFIT` | 【八】股息（第②阶梯，**含利息支付**） |
| `FA_IR_DEPR` + `IA_AMORTIZE` + `LPE_AMORTIZE` | 【五】capex/折旧摊销 |

### 2.3 `em_income_statement`（`quarter=4`）

| 字段 | 供哪项读数 |
|---|---|
| `PARENT_NETPROFIT` | 【二】【六】【八】归母净利 |
| `BASIC_EPS` | 【八】股本反推 |
| `TOTAL_OPERATE_INCOME` | 【五】capex/营收 |

### 2.4 `stock_valuation_history`（A 股）

`date` / `pe_ttm` / `pb` → 【八】市值推算、【九】安全边际分位。

**A 股的 `market_cap` 全部为 NULL（实测 12 只中只有 3 只港股有值），所以 A 股市值必须推导**：`市值 = pe_ttm × 归母净利`，取 `date` 最接近 `Y-12-31` 的行（±10 天）。再用 `pb × BPS × 股本` 交叉验证，两法背离 >20% 时脚本打印告警——**推出来的数不能假装是查出来的**。另外这张表**没有价格列**（`hk_yield_cache.price` 是另一张表的单日快照，不可回填历史序列）。

### 2.5 `dividend_annual_yield`

`year` / `dividend_per_share` → 【八】股息第①阶梯（`每股股息 × 股本`，**口径最干净**）。

## 三、港股

港股三张报表是 **EAV 长表**（`year` / `quarter` / `STD_ITEM_CODE` / `AMOUNT`），靠科目代码取值，与 A 股宽表结构完全不同。取数层的唯一职责是把它们**透视成与 A 股同名的键**——这样八个指标函数一行都不用改，加港股支持的成本锁死在取数层。

### 3.1 科目代码映射

| 科目代码 | 含义 | 供哪项读数 |
|---|---|---|
| `004001999` | 营运收入 | 营收（**与 `004001001` 营业额是同值镜像，切勿相加**） |
| `004007999` | 毛利 | 【四】毛利率 |
| `004025002` | 股东应占溢利 | 归母净利 |
| `004027002` | 每股基本盈利 | 【八】股本 |
| `004030999` | 股东权益（**归母**） | 【三】ROE 分母 |
| `004009999` / `004025999` | 总资产 / 总负债 | 【七】资产负债率 |
| `004011010` `004020001` `004011006` `004020005` | 短贷 + 长贷 + 租赁（流动+非流动） | 【七】有息负债（**港股 BS 无此行，只能合成**） |
| `003999` | 经营业务现金净额 | 【二】【五】【六】 |
| `005005` + `005007` | 购建固定资产 + 购建无形资产及其他 | 【二】【五】capex |
| `007004` | 已付股息（融资） | 【八】股息 |
| `001009` | 加：折旧及摊销 | 【五】capex/折旧 |

### 3.2 辅助表

| 表 | 字段 | 用途 |
|---|---|---|
| `stock_valuation_history` | `market_cap`（港股独有） | 【八】市值**直接取观测值，不推算** |
| `hk_fx_rate` | 年末即期 + 年均 | 【八】市值从港元折人民币 |
| `hk_yield_cache` | `dividend_yield` / `buyback_yield` | 【八】**单日快照**，只对照、不参与计算 |
| `hk_financial_indicator` | 仅取 `SECURITY_NAME_ABBR` | **可选**——指标层从长表合成，不依赖它（00322 仅 2022 起有） |
| `hk_segment_revenue` | `metric` / `segment` / `amount` / `kind` | ⓪能力圈 + ①定价权（见 §五） |

## 四、取数层已编码的口径陷阱

这些都是实测确认、写进代码注释的硬教训。**改动取数层前请逐条复核。**

| 陷阱 | 后果 |
|---|---|
| `hk_financial_indicator.CURRENCY` 标 `'HKD'` | **是源端按上市地硬编码的错标**，实际是人民币（00322 的 790.68 亿与年报逐位吻合，若真是港元应放大约 8%） |
| `004030999` vs `004028999` | 前者**归母**、后者含少数股东权益。ROE 分母误用会低估约 19%（00322 在 2025 年两者差 35.19 亿） |
| 港股 2016 年 | 美元报表按人民币重述的比较数（2017-01-01 起呈报货币由 USD 改 RMB），且三表最早只到 2016 → 无期初权益，平均 ROE 不可算，故 ROE 序列自 2017 起 |
| `hk_cash_flow.AMOUNT` 在 SQLite 里是 INTEGER | 比值前必须先转 float |
| 港股市值折算汇率 | 取的是**年末时点**市值，就必须用**年末即期**汇率；用年均价会引入系统性偏差（2020 年港元年初 0.89→年末 0.84，均价高估约 5.6%，而留存测试的结论完全由幅度承载） |
| 港股 PE 源是混合币种口径 | 市值用 `PE×净利` 反推**比直接取总市值更差**（百度源 rebuild 不出净利润：2018 年 市值/PE=31.2 亿 vs 实际 24.6 亿），故不以它为主线 |
| A 股流量字段 | 年初至今累计，只取 `quarter=4` |

## 五、分部数据（可选增强）

`hk_segment_revenue` 由 `financial-report-pdf-extractor` 的 `extract_segment_note.py` 从年报**分部附注**抽出，过了两道硬闸门（① Σ分部+冲销==总计 ② 对外收入合计==利润表营运收入）才入库。它的性质与上面所有数据都不同：

- 它是 **B 类（年报原文）经脚本对账后升格为 A 类**，不是接口直接给的
- `metric` 列区分口径（`external_revenue` 对外销售 / `segment_revenue` 含分部间），`kind` 列区分 分部/冲销/总计——**跨口径不可比较**
- 缺该股数据时，`segment` 子命令会打印抽取命令；**不要用记忆里的分部数字代替**

## 六、数据来源（采集命令）

| 数据 | 采集方式 | 上游 |
|---|---|---|
| A 股财报三表 + `em_financial_indicator` | `collect_financial_data.py collect --code X --market sh/sz` | 东财 / 新浪 / 同花顺 |
| 港股财报三表 | `collect_financial_data.py collect --code X --market hk` | 同上 |
| 港股估值 + 汇率 | `buffett_analysis.py fetch-valuation --code X` | 百度源（PE/PB/总市值）+ 汇率 |
| A 股估值历史 | `stock_valuation_history`，由 security-analysis 的周期股/红利股模块写入 | 东财 |
| `hk_yield_cache` | 由 `etf_valuation.py` 写入 | 港股 ETF 成份股估值流程 |
| `hk_segment_revenue` | `extract_segment_note.py --pdf <年报.pdf> --code X --store` | **年报 PDF 分部附注** |
| 语料 | `fetch_letters.py` + `build_index.py` → `buffett_corpus.db` | `berkshirehathaway.com` + GitHub 年会仓库 |

## 七、当前覆盖度（截至 2026-09-13 实测）

> 覆盖度随每次采集变动，**引用前请重跑 `data-check` 核对**，不要照抄本节数字。

| 表 | 股票数 | 行数 | 覆盖 |
|---|---|---|---|
| `em_financial_indicator` | **4** | 158 | 000858, 600519, 601600, 600887 |
| `em_cash_flow` | 11 | 431 | 000568, 000596, 000799, 000858, 002304, 600519, 600809, 600887, 601600, 601919, 603369 |
| `em_income_statement` | 11 | 431 | 同上 |
| `hk_balance_sheet` | 4 | 1,649 | 00700, 09987, 00322, 09633 |
| `hk_income_statement` | 4 | 955 | 同上 |
| `hk_cash_flow` | 4 | 1,570 | 同上 |
| `hk_financial_indicator` | 4 | 36 | 同上（可选表） |
| `stock_valuation_history` | 12 | 8,919 | 含 00322, 00700, 09633 |
| `dividend_annual_yield` | **1** | 6 | 仅 601919 |
| `hk_yield_cache` | 62 | 62 | ETF 成份股 |
| `hk_segment_revenue` | **2** | 276 | 00322, 09633 |

### 两个已知缺口

**缺口一：A 股的【三】【四】【七】只覆盖 4 只，而三表覆盖 11 只。**

`ind_roe` / `ind_margin` / `ind_leverage` 只吃 `em_financial_indicator` 的行，不读利润表。所以那多出来的 7 只（`000568` `002304` `603369` `000799` `600809` `000596` `601919`）跑 `screen` 时，【三】ROE、【四】毛利率、【七】杠杆会**全部记 NA**。

**这是采集覆盖缺口，不是数据本身拿不到**——`collect` 时让 `em_financial_indicator` 一起入库即可。遇到这 7 只，先补采集再判读，不要把 NA 读成"没问题"。

**缺口二：`dividend_annual_yield` 只有 1 只。**

所以【八】一美元留存测试的股息几乎全靠 `ASSIGN_DIVIDEND_PORFIT`，而该科目**把利息支付也算作股息** → 股息被高估 → 留存被低估 → **测试偏保守**。这个方向是安全的，但读报告时要记住脚本会打印这句修正。

## 八、明确不进 `financial_data.db` 的

**致股东信与股东大会问答原文**。

理由两条：版权（年会仓库无 LICENSE，要求注明 CNBC）与体积（语料 5.92 MB + 抽文本）。它们只存在于 `buffett_corpus.db`，而该库与 `corpus/` 均在 `.gitignore` 中——因为 `financial_data.db` **已经在版本控制里**。

## 九、数据缺失时怎么办

脚本对每一处 DB 访问都包了异常处理，**缺失时记 `NA` 而不是报错崩掉**。三条纪律：

1. **NA 不是 PASS**。样本不足（【三】【四】【六】需 ≥7 年，【八】需 ≥5 年）一律记 NA，那是"不知道"，不是"没问题"。
2. **先补数据再判读**。`data-check` 会打印补齐命令；能补就补，补不上就在报告里写清缺什么、因此哪几项读不出来。
3. **绝不用记忆里的数字代替**。库里没有就说明库里没有——这是这份工具存在的全部理由。
