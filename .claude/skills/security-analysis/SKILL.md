---
name: security-analysis
description: A股/港股投资分析工具集。五大功能模块共用一个 SQLite 数据库(financial_data.db)：1)财报数据采集(新浪/同花顺/东方财富三源,支持A股+港股)；2)ETF估值分析(A股+港股跨境ETF,申赎清单+成份股PE/PB/TTM股息率)；3)周期股深度分析(PE反转+库存周期四阶段+六维框架)；4)红利股分析(股息率趋势+回购注销+自由现金流+营收健康度)；5)行业竞争格局分析(行业识别+龙头排名+集中度+竞争态势判断)。适用于财报数据采集、基本面分析、ETF估值研究、周期股拐点判断、红利股评估、行业竞争分析等场景。
---

> **⚠️ 软链接共享提示**
> 本 skill 目录通过软链接被 `.dsh/skills/security-analysis`、`.qoder/skills/security-analysis`、`.opencode/skills/security-analysis` 引用，四者指向同一份物理文件。
> 修改时请直接在当前路径编辑，**不要删除后重建文件**，以免破坏软链接导致各工具间配置不同步。

# 股票投资分析工具集

五大功能模块共用同一个 `financial_data.db` 数据库。执行具体功能前，先阅读对应的子文档。

## 功能模块

| 模块 | 子文档 | 脚本 | 用途 |
|------|--------|------|------|
| 财报采集 | [financial-report.md](financial-report.md) | `scripts/collect_financial_data.py` | 采集A股/港股财报数据入库 |
| ETF估值 | [etf-valuation.md](etf-valuation.md) | `scripts/etf_valuation.py` | ETF成份股PE/PB/股息率查询 |
| 周期股分析 | [cyclical-analysis.md](cyclical-analysis.md) | `scripts/cyclical_stock_analysis.py` | 周期股六维分析+库存周期识别 |
| 红利股分析 | [dividend-analysis.md](dividend-analysis.md) | `scripts/dividend_stock_analysis.py` | 股息率趋势+回购注销+FCF+衰退判断 |
| 行业竞争分析 | [industry-competition.md](industry-competition.md) | `scripts/industry_competition_analysis.py` | 行业识别+龙头排名+集中度+竞争格局判断 |

分析方法论:
- 周期股方法论: [cyclical-analysis-guide.md](cyclical-analysis-guide.md)（PE/PB反转逻辑、库存周期四阶段、报告模板）
- 行业知识库: [knowledge/INDEX.md](knowledge/INDEX.md)（行业特定分析知识，分析前查阅对应行业文件）

## 共用数据库

五大模块共用 `financial_data.db`，按以下顺序定位（不依赖脚本目录的固定层级，故 Claude Code 与 DSH 会话均无需额外配置）：

1. **CLI 参数**: `--db-dir` 或 `--db`（不同脚本参数名略有差异），优先级最高
2. **环境变量**: `FINANCIAL_DATA_DIR` 指向含 `financial_data.db` 的目录
3. **当前工作目录**
4. **向上查找**含 `financial_data.db` 的目录

## 典型工作流

### 1. 采集数据（必须先执行）

```bash
# 采集单只A股（5个数据源）
python scripts/collect_financial_data.py collect --code 600519 --market sh --start-year 2016

# 采集港股
python scripts/collect_financial_data.py collect --code 00700 --market hk
```

详见 [financial-report.md](financial-report.md)

### 2. 分析个股

```bash
# 周期股分析（估值+财务+库存周期）
python scripts/cyclical_stock_analysis.py inventory --code 600519 --market sh

# ETF估值查询
python scripts/etf_valuation.py valuation --fund-code 510300
```

### 3. AI 解读

- **周期股**: 先查 [knowledge/INDEX.md](knowledge/INDEX.md) 加载行业知识，再阅读 [cyclical-analysis-guide.md](cyclical-analysis-guide.md) 按六维框架生成报告
- **ETF**: 分析加权 PE/PB/股息率，对比同类 ETF
- **红利股**: 按 [dividend-analysis.md](dividend-analysis.md) 的四维评估框架生成报告，注意区分回购注销与其他回购用途
- **财报查询**: 按需查询特定财务指标
- **行业竞争**: 按 [industry-competition.md](industry-competition.md) 流程执行。必须先通过 Web Search 确认公司主营业务和所属行业（与东财 API 结果交叉验证），再运行脚本分析，重点关注龙头优势倍数和集中度指标

## 数据库表总览

`financial_data.db` 现含 **26 张表**（另有 1 张惰性表 `stock_dividend`，见下）。下按用途分组；**权威口径（含"只写不读"与遗留表的完整说明）见项目根 [CLAUDE.md](../../../CLAUDE.md) 的「数据库表」一节**。

### 财报数据（15张）

| 类别 | 表名 | 数据源 |
|------|------|--------|
| A股指标 | `em_financial_indicator` | 东财主要指标（宽表 143 列，**分析脚本 + buffett-lens 的 ROE/毛利率/杠杆读数依赖它**） |
| | `sina_financial_indicator` | 新浪财务指标（宽表 88 列）**`只写不读`** |
| A股三大报表 | `em_balance_sheet` / `em_income_statement` / `em_cash_flow` | 东财（宽表 323/207/258 列，**分析脚本的主力数据源**） |
| | `sina_balance_sheet` / `sina_income_statement` / `sina_cash_flow` | 新浪（中文列名）**`只写不读`** |
| | `ths_balance_sheet` / `ths_income_statement` / `ths_cash_flow` | 同花顺（`metric_name`/`value` 长表）**`只写不读`**，三张约占库体积 44% |
| 港股 | `hk_financial_indicator` | 东财港股主要指标（**可选表**，指标层主要靠下面三张长表合成） |
| | `hk_balance_sheet` / `hk_income_statement` / `hk_cash_flow` | EAV 长表（`year`/`quarter`/`STD_ITEM_CODE`/`AMOUNT`），靠科目代码取值 |

### 估值与汇率（4张）

| 表名 | 说明 |
|------|------|
| `stock_valuation` | 股票估值缓存（PE/PB/股息率/价格，单日快照） |
| `stock_valuation_history` | PE/PB 历史序列（7天有效期）；`market_cap` **仅港股有值**，A 股为 NULL 需推算 |
| `hk_yield_cache` | 港股股息率/回购收益率**单日快照**（由 `etf_valuation.py` 写入） |
| `hk_fx_rate` | 港元兑人民币汇率（含 `spot_year_end` 年末即期，港股折人民币必须用年末即期而非年均） |

### 分红与回购（3张 + 1张惰性表）

| 表名 | 说明 |
|------|------|
| `dividend_annual_yield` | 年度股息率缓存（**当前仅覆盖 `601919`**） |
| `stock_dividend_detail` | A股分红**明细**（中文列名 `代码`/`派息`/`送股`/`进度`…），由 `dividend_stock_analysis.py` 按代码先删后插 |
| `stock_repurchase` | 股票回购记录（**当前仅覆盖 `601919`**） |
| `stock_dividend`（惰性） | A股分红**汇总**（`stock_code`/`名称`/`累计股息`/`年均股息`/`分红次数`/`融资总额`…），由 `etf_valuation.py` 的 `dividend` 子命令写入。**当前库中尚未创建**，首跑后表数变 27。详见 [etf-valuation.md](etf-valuation.md) |

> `stock_dividend` 与 `stock_dividend_detail` 是**两张用途不同的表**，不是同一张表的改名：前者每只股票一行（汇总股息率与分红次数），后者每笔分红一行（公告日、派息、除权日）。`buffett_analysis.py` 两个都不读——它的股息取自 `dividend_annual_yield`。

### 分部数据（1张）

| 表名 | 说明 |
|------|------|
| `hk_segment_revenue` | 港股分部收入/业绩/折旧/资本开支，由 `financial-report-pdf-extractor` 的 `extract_segment_note.py` 从年报分部附注抽出入库。含 `metric`（`external_revenue` 对外销售 / `segment_revenue` 含分部间 / `segment_result` / `depreciation` / `capex`）与 `kind`（segment/elimination/total）两列口径标注——**跨口径不可比较**。已覆盖 `00322`（2020-2025）、`09633`（2019-2025） |

### 遗留表（3张）—— 无代码引用

`资产负债表` / `利润表` / `现金流量表` — 各 234 行，schema 与 `em_*` 三张**逐列相同**，仅含 6 只白酒股（`000568` `000596` `000799` `002304` `600809` `603369`）。自首个 git 提交即在库中，当前无任何代码路径产出或读取。保留不删，仅作登记。

**估值缓存说明**：
- 周期股分析脚本会自动缓存估值数据到 `stock_valuation_history` 表
- 7天内重复运行会从缓存加载，加快批量分析速度
- 使用 `--refresh` 参数强制刷新缓存

## 依赖

```bash
pip install akshare pandas requests
```
