# ETF 估值查询 + 历史分红采集

基于上交所/深交所申赎清单，批量获取成份股行情（PE/PB/TTM股息率），计算ETF加权估值指标。
支持A股ETF和港股跨境ETF（如恒生消费、恒生科技等）。

**CLI 工具**: `scripts/etf_valuation.py`（独立版，无需依赖外部模块）
**核心模块**: `etf_weight.py`、`etf_redemption.py`、`stock_dividend.py`（项目根目录）

## CLI 用法

### 查询ETF估值（PE/PB/分红率）

```bash
# 上交所ETF（自动识别市场）
python scripts/etf_valuation.py valuation --fund-code 510300

# 深交所ETF
python scripts/etf_valuation.py valuation --fund-code 159008 --market sz

# 港股跨境ETF（如159699恒生消费ETF、513180恒生科技ETF）
python scripts/etf_valuation.py valuation --fund-code 159699 --market sz
python scripts/etf_valuation.py valuation --fund-code 513180

# 指定市场并保存CSV
python scripts/etf_valuation.py valuation --fund-code 510300 --save

# 限制显示行数
python scripts/etf_valuation.py valuation --fund-code 510300 --max-rows 50
```

**输出内容：**
- ETF基本信息（基金名称、管理公司、净值、申赎状态等）
- ETF估值指标（整体PE、整体PB、加权股息率、各指标覆盖率）
- 成份股占比明细（代码、名称、收盘价、PE、PB、股息率、市值、占比）
- 前10大权重股摘要

### 采集A股历史分红数据

```bash
# 采集所有A股历史分红（新浪数据源，全市场）
python scripts/etf_valuation.py dividend

# 指定数据库路径
python scripts/etf_valuation.py dividend --db-dir /path/to/db
```

### 查询分红数据

```bash
# 查询单只股票
python scripts/etf_valuation.py query --code 600519

# 累计股息TOP 50
python scripts/etf_valuation.py query --top 50
```

### 查看数据库表

```bash
python scripts/etf_valuation.py tables
```

## 数据来源

| 数据 | 来源 | 说明 |
|------|------|------|
| ETF申赎清单 | 上交所(SSE) / 深交所(SZSE) | 当日成份股明细、数量、现金替代标志 |
| 股票价格/PE/PB | 腾讯财经(qt.gtimg.cn) | 实时行情，PE为动态市盈率，支持A股+港股 |
| 股票价格(备用) | 东方财富 | 腾讯失败时兑底，仅含价格，仅支持A股 |
| TTM股息率(A股) | 东方财富datacenter-web | 基于近12个月分红记录计算 |
| TTM股息率(港股) | 东方财富 EM CoreReading | 港股分红派息记录，365天内除净日求和 |
| 历史分红汇总 | 新浪财经 | 累计股息、年均股息、分红次数 |

## 估值计算方法

ETF整体估值采用**调和平均**（与官方口径一致）：

| 指标 | 计算公式 | 说明 |
|------|----------|------|
| 整体PE | Σ(占比) / Σ(占比/PE) | 等价于总市值÷总净利润，排除亏损股 |
| 整体PB | Σ(占比) / Σ(占比/PB) | 等价于总市值÷总净资产，排除负净资产股 |
| 加权股息率 | Σ(占比×股息率) / Σ(占比) | 算术加权平均 |

**TTM股息率计算：**
1. 查询每只股票最近365天的分红记录（ASSIGN_PROGRESS="实施分配"）
2. TTM每股分红 = Σ(每10股税前分红) / 10
3. 股息率(%) = TTM每股分红 / 当前股价 × 100

## 港股跨境ETF支持

跨境ETF（如恒生消费ETF、恒生科技ETF）的成份股为港股，与A股ETF有以下差异：

### 代码识别

| 市场 | 代码位数 | 腾讯接口前缀 | 示例 |
|------|----------|--------------|------|
| 上交所 | 6位 | `sh` | sh600519（贵州茅台） |
| 深交所 | 6位 | `sz` | sz000333（美的集团） |
| 港股 | 3-5位 | `hk`+5位零填充 | hk00700（腾讯控股） |

判断规则：**代码≤5位一定是港股**（A股固定6位），自动转为`hk`前缀查询。

### 实现注意事项

- **market_map 双key策略**：PCF解析出的原始代码（如`669`）和腾讯标准化代码（`00669`）必须同时作为 key 存入 market_map，否则行情数据与股息率数据无法匹配
- **数量字段去千位逗号**：PCF中数量可能含逗号（如`1,727`），需先 `str.replace(",", "")` 再 `pd.to_numeric`
- **亏损股排除**：PE<0 的股票不参与整体PE调和平均计算
- **新股PB缺失**：新上市港股可能无PB数据，PB调和平均会自动排除

### PB字段位置差异

腾讯接口返回的字段索引因市场不同：

| 字段 | A股索引 | 港股索引 | 说明 |
|------|---------|----------|------|
| 现价 | `parts[3]` | `parts[3]` | 相同 |
| 动态PE | `parts[39]` | `parts[39]` | 相同 |
| PB | `parts[46]` | **`parts[47]`** | 港股`parts[46]`为英文ticker（如"TENCENT"） |

脚本通过响应变量名前缀（`v_hk` vs `v_sh`/`v_sz`）自动切换字段索引。

### 股息率数据源

| 功能 | A股 | 港股 |
|------|------|------|
| 腾讯行情（价格/PE/PB） | ✅ | ✅ |
| 东方财富备用价格 | ✅ | ❌（不支持港股） |
| TTM股息率（datacenter-web） | ✅ | ❌（API仅支持A股） |
| TTM股息率（EM CoreReading） | ❌ | ✅（港股分红接口） |
| 估值缓存入库 | ✅ | ❌（避免与A股代码冲突） |

港股TTM股息率通过东方财富 EM CoreReading 接口获取：
1. 查询每只港股最近的分红派息记录（最近3条）
2. 按除净日过滤365天内记录
3. 解析分红方案文本（支持港币、美元、人民币等多种格式）
4. 美元/人民币分红优先取港币等价值，无港币时按近似汇率转换
5. 股息率(%) = TTM每股分红(HKD) / 当前股价(HKD) × 100

### 申赎清单特殊项

跨境ETF的PCF文件中包含一个`申赎现金`条目（如`159900`），这是现金替代占位项，不是真实成份股：
- 数量 = 0，市值 = 0，占比 = 0%
- 不影响其他成份股的权重计算
- 成份股权重之和 < 100%，差额即为现金部分

### PCF字段兼容

深交所港股ETF的PCF文件格式与A股ETF略有不同：
- A股PCF为 8 字段格式（含申购/赎回替代金额）
- 跨境ETF PCF为 7 字段格式（无赎回替代金额，申购替代金额后直接是市场）
- 脚本按字段数量自动选择对应的解析规则

脚本已兼容两种PCF格式（A股 8 字段 / 跨境 7 字段），自动识别并正确解析。

## 数据库表

| 表名 | 说明 |
|------|------|
| `stock_valuation` | 股票估值缓存（PE/PB/股息率/价格），按(stock_code, date)主键 |
| `stock_dividend` | A股历史分红汇总（累计股息、年均股息、分红次数等） |

### stock_valuation 表结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `stock_code` | TEXT | 股票代码 |
| `date` | TEXT | 日期 YYYY-MM-DD |
| `pe` | REAL | 动态市盈率 |
| `pb` | REAL | 市净率 |
| `dividend_yield` | REAL | TTM股息率(%) |
| `price` | REAL | 收盘价 |
| `updated_at` | TEXT | 更新时间 |

### stock_dividend 表结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `stock_code` | TEXT | 股票代码 |
| `market` | TEXT | 市场 sh/sz（小写） |
| `名称` | TEXT | 股票简称 |
| `上市日期` | TEXT | 上市日期 |
| `累计股息` | REAL | 累计股息率(%) |
| `年均股息` | REAL | 年均股息率(%) |
| `分红次数` | REAL | 历史分红总次数 |
| `融资总额` | REAL | 累计融资额(亿元) |
| `融资次数` | REAL | 融资总次数 |

## 查询技巧

```sql
-- 查询高股息股票
SELECT stock_code, market, 名称, 累计股息, 年均股息, 分红次数
FROM stock_dividend
WHERE 分红次数 > 10
ORDER BY 累计股息 DESC
LIMIT 50;

-- 查询今日估值
SELECT stock_code, pe, pb, dividend_yield, price
FROM stock_valuation
WHERE date = '2025-07-12'
  AND dividend_yield IS NOT NULL
ORDER BY dividend_yield DESC;
```
