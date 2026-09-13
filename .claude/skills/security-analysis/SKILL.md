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

## 数据库表

**表清单只在项目根 [CLAUDE.md](../../../CLAUDE.md) 的「数据库表」一节维护**——全部 26 张表的用途、列数、"只写不读"与遗留表标注都在那里。本文件不重复列出，以免两处漂移。

本 skill 各模块**写入**的表：

| 模块 | 写入的表 |
|------|----------|
| 财报采集 | 15 张财报表：`{sina,ths,em}_` × `{balance_sheet,income_statement,cash_flow}`，加 `sina_financial_indicator`、`em_financial_indicator`，以及港股 `hk_financial_indicator` 与 `hk_balance_sheet/_income_statement/_cash_flow` |
| ETF 估值 | `stock_valuation`、`hk_yield_cache`；`dividend` 子命令另建分红汇总表 `stock_dividend`（惰性，首跑才出现） |
| 周期股 | `stock_valuation_history` |
| 红利股 | `dividend_annual_yield`、`stock_dividend_detail`、`stock_repurchase` |

`hk_fx_rate` 与 `hk_segment_revenue` **不由本 skill 写入**（分别归 buffett-lens 与 financial-report-pdf-extractor）；三张中文名遗留表无任何代码引用。

> 唯一容易踩的坑：`stock_dividend`（每只股票一行的**汇总**，ETF 模块惰性建）与 `stock_dividend_detail`（每笔分红一行的**明细**，红利股模块写）是**两张用途不同的表，不是改名关系**。

**估值缓存说明**：
- 周期股分析脚本会自动缓存估值数据到 `stock_valuation_history` 表
- 7天内重复运行会从缓存加载，加快批量分析速度
- 使用 `--refresh` 参数强制刷新缓存

## 依赖

```bash
pip install akshare pandas requests
```
