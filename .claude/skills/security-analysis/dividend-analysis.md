# 红利股分析

从四个维度评估一只A股是否为优质红利股：股息率趋势、回购注销、自由现金流可持续性、企业是否衰退。

**CLI 工具**: `scripts/dividend_stock_analysis.py`
**前置条件**: 需先通过 `scripts/collect_financial_data.py` 采集目标股票的财报数据（em_cash_flow / em_income_statement）

## 分析维度

| 维度 | 数据来源 | 核心指标 |
|------|----------|----------|
| 股息率+支付率 | stock_fhps_em (东财) + stock_history_dividend_detail (新浪) | 近10年股息率均值/标准差/趋势 + 股息支付率(每股派息/EPS) |
| 回购注销 | stock_repurchase_em (东财) | 回购注销金额计入股息（仅注销用途） |
| 自由现金流 | em_cash_flow | FCF = 经营现金流 - 资本支出 |
| 营收健康度 | em_income_statement + em_cash_flow | 营收增速、扣非利润、扣非经营现金流 |

## CLI 用法

### Step 1: 采集财报数据（如尚未采集）

检查数据库中是否已有该股票的财报数据：

```bash
python -c "import sqlite3; conn=sqlite3.connect('financial_data.db'); cur=conn.cursor(); cur.execute(\"SELECT COUNT(*) FROM em_income_statement WHERE stock_code='XXXXXX'\"); print(cur.fetchone()[0])"
```

如果数据为空，用 `scripts/collect_financial_data.py` 采集：

```bash
python scripts/collect_financial_data.py collect --code XXXXXX --market sh --start-year 2016
```

### Step 2: 采集红利分析数据（分红+回购）

```bash
# 一键采集（分红明细 + 年度股息率 + 回购）
python scripts/dividend_stock_analysis.py collect --code 600519 --market sh

# 也可以分步采集:
python scripts/dividend_stock_analysis.py collect-yield --code 600519        # 仅年度股息率
python scripts/dividend_stock_analysis.py collect-repurchase --code 600519   # 仅回购数据
```

**数据缓存说明**：
- 分红明细存入 `stock_dividend_detail` 表（幂等更新，按 stock_code 先删后插）
- 年度股息率存入 `dividend_annual_yield` 表（按 year 幂等，INSERT OR REPLACE）
- 回购数据存入 `stock_repurchase` 表（按 stock_code 先删后插）
- 下次分析同一股票时，已采集的数据直接从数据库读取，无需重复采集

### Step 3: 运行分析

```bash
python scripts/dividend_stock_analysis.py analyze --code 600519 --market sh
```

输出内容:
- **维度一**: 近10年股息率年度数据（股息率、每股派息、EPS、股息支付率）+ 统计摘要（均值/最低/最高/标准差）+ 支付率评价
- **维度二**: 股票回购记录汇总（进度、已回购金额、计划金额）+ 注销用途提示
- **维度三**: 近10年自由现金流（经营现金流 - 资本支出）+ FCF正负统计
- **维度四**: 近10年营收/扣非净利润/经营现金流 + 营收增速 + 衰退判断

### Step 4: AI 解读

基于脚本输出数据，按以下框架进行解读：

#### 红利股评估框架

1. **股息率+支付率质量评估**
   - 近10年股息率均值是否 > 3%（优秀红利股标准）
   - 股息率标准差是否 < 1.5%（稳定性）
   - 股息率趋势：上升（好）/ 下降（警惕）/ 波动（一般）
   - **股息支付率** = 每股派息 / EPS × 100%（必须与股息率同时展示）
   - 支付率 > 80%：分红比例过高，利润稍有下滑即可能削减分红，不可持续
   - 支付率 50-80%：偏高，需关注利润波动对分红的冲击
   - 支付率 20-50%：合理区间，兼顾股东回报与留存再投资
   - 支付率 < 20%：偏低，分红不够慷慨，或公司处于资本开支密集期
   - **关键**: 高股息率 + 高支付率 = 危险组合（分红可能随时缩减）
   - **关键**: 高股息率 + 低支付率 = 优质组合（分红有足够安全垫）

2. **回购注销评估**
   - 是否有回购注销记录（回购注销金额可等效为股息）
   - 股东总回报率 = 股息率 + 回购注销率
   - 注意区分：仅「注销」用途的回购计入股东回报；员工持股计划/股权激励/可转债回购不计入
   - 需根据回购公告确认具体用途

3. **自由现金流可持续性**
   - FCF = 经营现金流净额 - 购建固定资产等支付的现金
   - FCF 是否连续多年为正（≥80%年份为正为佳）
   - FCF 是否能覆盖当年现金分红总额
   - 若 FCF 长期为负但仍在分红，说明在消耗现金储备或举债分红，不可持续

4. **企业衰退判断**
   - 营收是否持续增长或至少保持稳定
   - 扣非归母净利润是否健康（连续为正、无明显萎缩）
   - 经营现金流与扣非利润的比值（>1.0 说明盈利质量高）
   - 营收连续3年下降 + 扣非利润萎缩 = 衰退信号
   - 衰退企业的分红可能是"清算式分红"，不可持续

#### 常见陷阱

- **高股息率陷阱**: 股价大跌导致股息率被动升高，不代表分红能力强
- **一次性分红**: 特殊分红（如资产出售后的特别派息）不代表常态
- **借钱分红**: 经营现金流差但靠借债维持高分红，不可持续
- **回购不注销**: 回购用于股权激励/员工持股，实际上稀释了股东权益
- **高支付率陷阱**: 股息支付率 > 80% 意味着公司把绝大部分利润都分掉了，利润稍有下滑分红就会大幅缩减
- **低支付率高股息假象**: 支付率很低但股息率很高（如>5%），需验证是否因股价暴跌被动推高股息率

#### 报告输出模板

```markdown
## [股票名称] 红利股分析报告

### 一、股息率分析
- 近10年股息率区间: X% ~ Y%
- 均值: Z%，标准差: W%
- 稳定性评价: 优秀/良好/波动较大
- 股息支付率区间: X% ~ Y%（均值: Z%）
- 支付率评价: 过高/偏高/合理/偏低
- 分红比例趋势: ...
- 股息率+支付率组合判断: ...

### 二、回购注销分析
- 是否有回购: 是/否
- 回购总金额: X 亿元
- 回购用途: 注销/员工持股/股权激励/...
- 股东总回报率（含注销回购）: X%

### 三、自由现金流分析
- FCF为正年份: X/Y
- 平均FCF: X 亿元
- FCF覆盖分红能力: 强/中/弱
- 可持续性评价: ...

### 四、企业健康度
- 营收趋势: 增长/稳定/下降
- 扣非利润趋势: 增长/稳定/下降/亏损
- 现金流/利润比: X（>1.0为优）
- 衰退风险: 低/中/高

### 五、综合评价
- 红利股评级: 优秀/良好/一般/不推荐
- 核心优势: ...
- 主要风险: ...
- 建议: ...
```

## 数据库表

### 新增表（2张）

| 表名 | 说明 |
|------|------|
| `dividend_annual_yield` | 年度股息率缓存（stock_code + year 为主键） |
| `stock_repurchase` | 股票回购记录（按 stock_code 筛选） |

### dividend_annual_yield 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | 股票代码 |
| year | INTEGER | 年度 |
| dividend_per_share | REAL | 每股派息（元） |
| dividend_yield | REAL | 股息率（%） |
| cash_dividend_ratio | REAL | 现金分红比例（%） |
| eps | REAL | 每股收益 |
| bps | REAL | 每股净资产 |
| updated_at | TEXT | 更新时间 |

### stock_repurchase 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | 股票代码 |
| stock_name | TEXT | 股票简称 |
| repurchase_amount_lower | REAL | 计划回购金额下限（元） |
| repurchase_amount_upper | REAL | 计划回购金额上限（元） |
| repurchased_amount | REAL | 已回购金额（元） |
| repurchased_qty | REAL | 已回购股份数量 |
| progress | TEXT | 实施进度 |
| start_date | TEXT | 回购起始时间 |
| latest_announce_date | TEXT | 最新公告日期 |
| price_lower/price_upper | REAL | 计划回购价格区间 |
| repurchased_price_lower/repurchased_price_upper | REAL | 已回购价格区间 |
| total_shares_ratio_lower/total_shares_ratio_upper | REAL | 占总股本比例区间 |
| updated_at | TEXT | 更新时间 |
