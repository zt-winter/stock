---
name: security-analysis
description: A股/港股投资分析工具集。五大功能模块共用一个 SQLite 数据库(financial_data.db)：1)财报数据采集(新浪/同花顺/东方财富三源,支持A股+港股)；2)ETF估值分析(A股+港股跨境ETF,申赎清单+成份股PE/PB/TTM股息率)；3)周期股深度分析(PE反转+库存周期四阶段+六维框架)；4)红利股分析(股息率趋势+回购注销+自由现金流+营收健康度)；5)行业竞争格局分析(行业识别+龙头排名+集中度+竞争态势判断)。适用于财报数据采集、基本面分析、ETF估值研究、周期股拐点判断、红利股评估、行业竞争分析等场景。
---

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

三个模块共享 `financial_data.db`，通过以下方式定位：

- **环境变量**: `FINANCIAL_DATA_DIR` 指向项目根目录（含 `financial_data.db` 的目录）
- **CLI 参数**: `--db-dir` 或 `--db` 临时指定（不同脚本参数名略有差异）
- **默认路径**: 项目根目录 `/home/zt/stock/financial_data.db`

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

### 财报数据（15张表）

| 类别 | 表名 | 数据源 |
|------|------|--------|
| A股指标 | `sina_financial_indicator` | 新浪-86项 |
| | `em_financial_indicator` | 东财-140项 |
| A股报表 | `sina/ths/em_balance_sheet` | 新浪/同花顺/东财 |
| | `sina/ths/em_income_statement` | |
| | `sina/ths/em_cash_flow` | |
| 港股 | `hk_financial_indicator` | 东财-36项 |
| | `hk_balance_sheet/income_statement/cash_flow` | 东财-长表 |

### ETF/分红数据（2张表）

| 表名 | 说明 |
|------|------|
| `stock_valuation` | 股票估值缓存（PE/PB/股息率/价格） |
| `stock_dividend` | A股历史分红汇总 |

### 周期股分析缓存（1张表）

| 表名 | 说明 |
|------|------|
| `stock_valuation_history` | PE/PB历史数据缓存（7天有效期），避免重复调用API |

### 红利股分析数据（2张表）

| 表名 | 说明 |
|------|------|
| `dividend_annual_yield` | 年度股息率缓存（stock_code+year主键） |
| `stock_repurchase` | 股票回购记录（含已回购金额、进度等） |

**估值缓存说明**：
- 周期股分析脚本会自动缓存估值数据到 `stock_valuation_history` 表
- 7天内重复运行会从缓存加载，加快批量分析速度
- 使用 `--refresh` 参数强制刷新缓存

## 依赖

```bash
pip install akshare pandas requests
```
