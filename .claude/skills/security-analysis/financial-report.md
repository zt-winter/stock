# 财报数据采集

基于 akshare 从多数据源采集 A 股 + 港股财报数据，存入 `financial_data.db`。

**CLI 工具**: `scripts/collect_financial_data.py`（独立版，无需依赖外部模块）
**核心模块**: `financial_report.py`（项目根目录，支持 Python 导入）

## CLI 用法

数据库默认在项目根目录。可用 `--db-dir` 指定路径。

### 采集单只股票

```bash
# A 股（采集新浪/同花顺/东方财富 5 个数据源）
python scripts/collect_financial_data.py collect --code 600519 --market sh --start-year 2016

# 港股（仅东方财富 2 个数据源）
python scripts/collect_financial_data.py collect --code 00700 --market hk
```

### 批量采集

创建 `stocks.txt`，每行格式：`股票代码 市场 [起始年]`

```
600519 sh 2016
000858 sz 2016
00700  hk
```

```bash
python scripts/collect_financial_data.py batch --file stocks.txt
```

### 查询数据

```bash
# 查东财指标
python scripts/collect_financial_data.py query --code 600519 --table em_financial_indicator --year 2024 --columns REPORT_DATE,EPSJB,BPS

# 查利润表年报
python scripts/collect_financial_data.py query --code 600519 --table em_income_statement --year 2024 --quarter 4

# 查港股利润表
python scripts/collect_financial_data.py query --code 00700 --table hk_income_statement --year 2024 --quarter 4
```

### 查看所有表

```bash
python scripts/collect_financial_data.py tables
```

## 数据源

| 数据源 | 市场 | 格式 | 说明 |
|--------|------|------|------|
| 新浪 | A股 | 宽表 86项 | 中文列名，财务指标 |
| 新浪 | A股 | 宽表 | 三大报表 |
| 同花顺 | A股 | 长表 | 每行一个指标（metric_name + value） |
| 东方财富 | A股 | 宽表 140项 | 英文缩写列名 |
| 东方财富 | A股 | 宽表 300+列 | 三大报表 |
| 东方财富 | 港股 | 宽表 36项 | 主要指标 |
| 东方财富 | 港股 | 长表 | 三大报表（STD_ITEM_NAME + AMOUNT） |

## 数据库表（共 15 张）

### A 股（11 张）

| 表名 | 数据源 |
|------|--------|
| `sina_financial_indicator` | 新浪-财务指标（86项） |
| `em_financial_indicator` | 东财-主要指标（140项） |
| `sina_balance_sheet` / `sina_income_statement` / `sina_cash_flow` | 新浪-三大报表 |
| `ths_balance_sheet` / `ths_income_statement` / `ths_cash_flow` | 同花顺-三大报表（长表） |
| `em_balance_sheet` / `em_income_statement` / `em_cash_flow` | 东财-三大报表（宽表） |

### 港股（4 张）

| 表名 | 说明 |
|------|------|
| `hk_financial_indicator` | 主要指标（36项，含 CURRENCY） |
| `hk_balance_sheet` / `hk_income_statement` / `hk_cash_flow` | 三大报表（长表） |

## 统一字段

所有表均包含：

| 字段 | 说明 |
|------|------|
| `stock_code` | 纯数字代码：A股 `600519` / 港股 `00700` |
| `market` | 小写：`sh` / `sz` / `hk` |
| `year` | 整数年份（三大报表+港股指标表有） |
| `quarter` | 1=Q1 2=半年报 3=Q3 4=年报 |

> `em_financial_indicator` 和 `sina_financial_indicator` 无 year/quarter 列，查询时用 REPORT_DATE 或 日期 字段替代。

## Python 模块导入

```python
from financial_report import (
    get_financial_report,          # 新浪-财务指标（A股）
    get_financial_report_em,       # 东财-主要指标（A股）
    get_financial_statements_sina, # 新浪-三大报表（A股）
    get_financial_statements_ths,  # 同花顺-三大报表（A股）
    get_financial_statements_em,   # 东财-三大报表（A股）
    get_financial_report_hk,       # 东财-主要指标（港股）
    get_financial_statements_hk,   # 东财-三大报表（港股）
    get_conn, save_to_db, DB_PATH
)
```

## 查询技巧

```sql
-- market 不区分大小写
WHERE market = 'SH' COLLATE NOCASE

-- 同花顺长表按指标名筛选
WHERE metric_name = '营业总收入'

-- 港股长表按科目筛选
WHERE STD_ITEM_NAME = '营业额'

-- 东财指标关键字段
-- EPSJB=每股收益, BPS=每股净资产, ROEJQ=ROE
-- XSJLL=净利率, XSMLL=毛利率, ZCFZL=资产负债率
```
