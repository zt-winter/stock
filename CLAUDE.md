# 股票投资分析工具集 (Stock Investment Analysis Toolkit)

A股/港股财报数据采集、ETF估值分析、周期股深度分析、红利股评估工具集。

## 项目架构

```
stock/
├── financial_data.db          # 主数据库（SQLite，所有模块共用）
├── financial_report.py        # 财报数据采集核心模块
├── etf_weight.py              # ETF权重/估值计算
├── etf_redemption.py          # ETF申赎清单解析
├── stock_dividend.py          # A股历史分红采集
├── cash_flow_analysis.py      # 现金流分析
└── .claude/skills/security-analysis/
    ├── SKILL.md               # 安全分析技能入口
    ├── scripts/               # CLI 分析脚本
    │   ├── collect_financial_data.py   # 财报数据采集
    │   ├── etf_valuation.py            # ETF估值查询
    │   ├── cyclical_stock_analysis.py  # 周期股六维分析
    │   ├── dividend_stock_analysis.py  # 红利股四维分析
    │   └── industry_competition_analysis.py  # 行业竞争格局分析
    ├── knowledge/             # 行业知识库
    │   ├── INDEX.md           # 行业路由表
    │   ├── baijiu.md          # 白酒行业
    │   └── aluminum.md        # 铝业
    └── *.md                   # 各模块文档
```

## 五大功能模块

| 模块 | 脚本 | 用途 |
|------|------|------|
| 财报采集 | `.claude/skills/security-analysis/scripts/collect_financial_data.py` | 采集A股/港股财报数据入库 |
| ETF估值 | `.claude/skills/security-analysis/scripts/etf_valuation.py` | ETF成份股PE/PB/股息率查询 |
| 周期股分析 | `.claude/skills/security-analysis/scripts/cyclical_stock_analysis.py` | 周期股六维分析+库存周期识别 |
| 红利股分析 | `.claude/skills/security-analysis/scripts/dividend_stock_analysis.py` | 股息率趋势+回购注销+FCF+衰退判断 |
| 行业竞争分析 | `.claude/skills/security-analysis/scripts/industry_competition_analysis.py` | 行业识别+龙头排名+集中度+竞争格局判断 |

## 环境变量

- `FINANCIAL_DATA_DIR` — 指向包含 `financial_data.db` 的目录（默认项目根目录 `/home/zt/stock`）

## 依赖

```bash
pip install akshare pandas requests
```

## 快速开始

### 1. 采集财报数据

```bash
# A股
python .claude/skills/security-analysis/scripts/collect_financial_data.py collect --code 600519 --market sh --start-year 2016

# 港股
python .claude/skills/security-analysis/scripts/collect_financial_data.py collect --code 00700 --market hk

# 批量采集
python .claude/skills/security-analysis/scripts/collect_financial_data.py batch --file stocks.txt
```

### 2. 分析个股

```bash
# 周期股分析（估值+财务+库存周期四阶段）
python .claude/skills/security-analysis/scripts/cyclical_stock_analysis.py inventory --code 600519 --market sh

# ETF估值查询
python .claude/skills/security-analysis/scripts/etf_valuation.py valuation --fund-code 510300

# 红利股分析
python .claude/skills/security-analysis/scripts/dividend_stock_analysis.py analyze --code 600519 --market sh
```

### 3. 查询数据库

```bash
# 查看所有表
python .claude/skills/security-analysis/scripts/collect_financial_data.py tables

# 查询指定数据
python .claude/skills/security-analysis/scripts/collect_financial_data.py query --code 600519 --table em_financial_indicator --year 2024
```

## 技能 (Skills)

使用 `/security-analysis` 启动证券分析技能，该技能会加载完整的分析框架和行业知识库。

## DeepSeek Harness 适配

本技能同时兼容 Claude Code（`.claude/skills/`）与 DeepSeek Harness（DSH）：

- **`.dsh/skills/security-analysis`** 是指向 `.claude/skills/security-analysis` 的符号链接，DSH 以项目级 skill 根（rank 100）自动发现；`.claude` 目录保持原样，内容单一数据源，改动共享。
- **数据库定位不依赖脚本目录的固定层级**：各脚本按 `FINANCIAL_DATA_DIR` 环境变量 > 当前工作目录 > 向上查找含 `financial_data.db` 的目录依次解析，因此 DSH 会话（cwd 为项目根）无需额外配置即可命中 `/home/zt/stock/financial_data.db`；`--db`/`--db-dir` 参数仍然优先。
- DSH 中通过 `skill` 工具加载（无需 `/security-analysis` 斜杠命令）；Claude Code 中用法不变。

## 数据库表（共20张）

### 财报数据（15张表）
- A股指标: `sina_financial_indicator`, `em_financial_indicator`
- A股报表: `sina/ths/em_balance_sheet`, `sina/ths/em_income_statement`, `sina/ths/em_cash_flow`
- 港股: `hk_financial_indicator`, `hk_balance_sheet`, `hk_income_statement`, `hk_cash_flow`

### ETF/分红数据（2张表）
- `stock_valuation` — 股票估值缓存（PE/PB/股息率/价格）
- `stock_dividend` — A股历史分红汇总

### 周期股/红利股分析缓存（3张表）
- `stock_valuation_history` — PE/PB历史数据缓存（7天有效期）
- `dividend_annual_yield` — 年度股息率缓存
- `stock_repurchase` — 股票回购记录

## 重要说明

- 所有模块共用同一个 `financial_data.db` 数据库
- 分析前必须先采集财报数据（`collect` 命令）
- 估值数据有7天缓存，使用 `--refresh` 强制刷新
- 周期股分析必须结合行业知识库（`.claude/skills/security-analysis/knowledge/`）进行解读
- 报告生成必须遵循七段式结构（周期股）或四维框架（红利股）
