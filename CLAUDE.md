# 股票投资分析工具集 (Stock Investment Analysis Toolkit)

A股/港股财报数据采集、ETF估值分析、周期股深度分析、红利股评估工具集。

## 项目架构

```
stock/
├── financial_data.db          # 主数据库（SQLite，所有模块共用）
├── etf_redemption.py          # ETF申赎清单解析（保留：独有 4 个函数，见下）
├── cash_flow_analysis.py      # 现金流分析（保留：非经常性损益剔除，skill 内无替代）
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

.claude/skills/buffett-lens/     # 巴菲特视角问诊（另一套独立技能）
├── SKILL.md                     # 技能入口
├── diagnostic-guide.md          # 问诊方法论（三层架构+来源分层+读数规则+报告模板）
├── data-sources.md              # 数据来源与字段清单（A股/港股字段、口径陷阱、覆盖度）
├── portfolio-check.md           # 持仓情绪问诊（纯检索，不产出买卖结论）
├── principles/                  # 15张原则卡 + INDEX.md 挑卡表 + keywords.tsv
├── scripts/                     # CLI 脚本
│   ├── buffett_common.py        # 共享底座（DB定位、normalize_for_match、终端格式）
│   ├── fetch_letters.py         # 采集致股东信/股东大会问答
│   ├── build_index.py           # 建双 FTS5 索引（trigram + porter）
│   ├── search_corpus.py         # 语料检索 + 引用校验
│   ├── lint_citations.py        # 引用真实性闸门（三键校验）
│   └── buffett_analysis.py      # 8项量化读数
├── corpus/                      # 语料原文与抽文本（不入 git）
└── buffett_corpus.db            # 语料索引库（不入 git，与 financial_data.db 分开）
```

### 根目录保留脚本的职责

根目录只剩两个 py 脚本，都是 **skill 内无替代**的独有能力，其余已全部迁入 `.claude/skills/*/scripts/`：

| 脚本 | 独有能力 | 说明 |
|------|----------|------|
| `etf_redemption.py` | `download_sse_pcf`（上交所 PCF 文本下载落盘）、`query_etf_list`（沪深合并列表）、`save_to_csv`、`_clean_html` | 主体能力已由 `scripts/etf_valuation.py` 取代（SSE 走 JSON API、SZSE 走 `download_szse_pcf`），仅上述 4 个函数无对应 |
| `cash_flow_analysis.py` | `analyze_cash_flow` 的**非经常性损益剔除** | `scripts/` 下的现金流逻辑散在 `dividend_stock_analysis.py` 与 `cyclical_stock_analysis.py`，均无此剔除 |

原先的 `financial_report.py`、`etf_weight.py`、`stock_dividend.py` 已删除——三者的全部函数分别由 `scripts/collect_financial_data.py`、`scripts/etf_valuation.py`、`scripts/dividend_stock_analysis.py` 覆盖（函数级比对确认）。**不要再按旧文档从根目录 import 这三个模块。**

## 五大功能模块

| 模块 | 脚本 | 用途 |
|------|------|------|
| 财报采集 | `.claude/skills/security-analysis/scripts/collect_financial_data.py` | 采集A股/港股财报数据入库 |
| ETF估值 | `.claude/skills/security-analysis/scripts/etf_valuation.py` | ETF成份股PE/PB/股息率查询 |
| 周期股分析 | `.claude/skills/security-analysis/scripts/cyclical_stock_analysis.py` | 周期股六维分析+库存周期识别 |
| 红利股分析 | `.claude/skills/security-analysis/scripts/dividend_stock_analysis.py` | 股息率趋势+回购注销+FCF+衰退判断 |
| 行业竞争分析 | `.claude/skills/security-analysis/scripts/industry_competition_analysis.py` | 行业识别+龙头排名+集中度+竞争格局判断 |

## 巴菲特视角分析技能（buffett-lens）

与 security-analysis 并列的独立技能，做的是**清单式问诊**：不预测股价、不给买卖建议，只把"我觉得这家公司不错"拆成若干条可被推翻的判定，每条判定要么挂着巴菲特原话（可逐字复核），要么挂着脚本数字（可重算）。

| 模块 | 脚本 | 用途 |
|------|------|------|
| 语料采集 | `.claude/skills/buffett-lens/scripts/fetch_letters.py` | 采集致股东信（1977-2024）+ 股东大会问答（1994-2022） |
| 索引构建 | `.claude/skills/buffett-lens/scripts/build_index.py` | 建双 FTS5 索引（中文 trigram + 英文 porter） |
| 语料检索 | `.claude/skills/buffett-lens/scripts/search_corpus.py` | 中英文检索、取 chunk 全文、引用逐字校验 |
| 引用闸门 | `.claude/skills/buffett-lens/scripts/lint_citations.py` | 原则卡引用的三键校验 + 结构 lint |
| 量化筛查 | `.claude/skills/buffett-lens/scripts/buffett_analysis.py` | 股东盈余/ROE/毛利率/capex/盈利质量/杠杆/一美元留存/安全边际 共8项读数 |

**三层架构**：原则卡给准则（`principles/`，15张6组）、脚本给数字、语料给原话；`lint_citations.py` 是唯一的硬约束——每句引用必须三键对齐（① 引文在该年全文中 ② 年份与文档一致 ③ 所标 chunk_id 确实包含它）。

**运行时序**：能力圈前置（说不清"靠什么赚钱"+3个竞争对手就停止）→ 按「适用」挑 4-6 张卡 → `data-check` → `segment`（港股；能力圈与定价权的分部证据）→ `screen` → 每卡检索 1-2 条逐字原话 → 拼问诊单，挂不上出处的标【推测】。

```bash
V=.venv/bin/python; S=.claude/skills/buffett-lens/scripts
$V $S/buffett_analysis.py data-check --code 600519    # 先摸清缺哪些数据
$V $S/buffett_analysis.py segment --code 09633        # ⓪能力圈/①定价权的分部证据（港股）
$V $S/buffett_analysis.py screen --code 600519        # 8项读数 + 汇总 + 一票否决
$V $S/search_corpus.py search --query "moat" --source letters --limit 5
$V $S/lint_citations.py --cards principles/ --self-test   # 闸门自检 + 卡引用校验
```

**关键判读规则**：**数字按四级标注来源——A 可重算（`financial_data.db`）/ B 年报原文（带页码）/ C 外部来源（媒体、研报、搜索摘要）/ D 语料（带 `chunk_id`）；C 类不得进入判定依据栏，只许进「我的推测」并标【外部·未经年报核实】，不标级的数字一律按 C 类处理**（`diagnostic-guide.md` §三）；NA 不是 PASS（样本不足记 NA）；护城河/盈利质量/杠杆任一 REJECT 即一票否决且不可用"便宜"赎回；**周期股（`601600`/`601919` 等）PE 分位语义反转，改以 PB 为主信号**并对照 `security-analysis/cyclical-analysis-guide.md` §一；一美元留存测试的股息取自 `ASSIGN_DIVIDEND_PORFIT`（含利息支付→股息高估→测试偏保守）；**分部收入增长本身不是定价权**——收入涨可能只是铺货或降价换量，要看到「收入增长的同时分部利润率没有塌」，且 `external_revenue` 与 `segment_revenue` 不可跨口径比较。

**版权与署名**：致股东信来自 berkshirehathaway.com（英文原文，署"巴菲特致股东信 <年>"）；股东大会问答来自 `github.com/wuxiaoda/BRK-Annual-Meeting`（**无 LICENSE**，README 要求所有引用注明来自 CNBC，中译：一朵喵），署"巴菲特股东大会问答 <年>（CNBC 原文 · 中译：一朵喵）"并附 `buffett.cnbc.com`。**语料原文与索引库不入 git，且严禁写入 `financial_data.db`。**

## 环境变量

- `FINANCIAL_DATA_DIR` — 指向包含 `financial_data.db` 的目录（默认项目根目录 `/home/zt/stock`）

## 依赖

各文档与 SKILL.md 中的 `.venv/bin/python` 指的是**项目虚拟环境**（`.venv/`，不入 git）。首次使用先建环境：

```bash
python3 -m venv .venv
.venv/bin/pip install akshare pandas requests
# buffett-lens 语料采集 + 两个 PDF skill 额外需要（PDF/HTML 抽取）
.venv/bin/pip install beautifulsoup4 lxml pymupdf
```

- **不要用系统 `python3` 跑这些脚本**：`akshare` 与 `pymupdf` 通常只装在 venv 里，缺任一个都会在 import 阶段直接失败。
- buffett-lens 采集致股东信时 CDN 强制 `Content-Encoding: br`，若环境未装 `brotli`，脚本会回退调用 `curl --compressed`，**需系统有 `curl`**。
- buffett-lens 索引库用 SQLite FTS5 的 `trigram` 与 `porter unicode61` 分词器，需 SQLite ≥ 3.34。
- PDF skill 的 `pdf_helper.py` 按 PyMuPDF > pypdf > pdfminer.six 顺序探测后端，**只有 PyMuPDF 支持 `ColumnPage` 位置感知提取**；退到 pypdf/pdfminer 时财报主表提取会失效。

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

- `/security-analysis` — 证券分析技能，加载完整的分析框架和行业知识库。
- `/buffett-lens` — 巴菲特视角问诊技能，加载三层架构（原则卡 + 量化筛查 + 语料检索）与引用闸门。

## DeepSeek Harness 适配

本技能同时兼容 Claude Code（`.claude/skills/`）与 DeepSeek Harness（DSH）：

- **`.dsh/skills/security-analysis`** 是指向 `.claude/skills/security-analysis` 的符号链接，DSH 以项目级 skill 根（rank 100）自动发现；`.claude` 目录保持原样，内容单一数据源，改动共享。
- **数据库定位不依赖脚本目录的固定层级**：各脚本按 `FINANCIAL_DATA_DIR` 环境变量 > 当前工作目录 > 向上查找含 `financial_data.db` 的目录依次解析，因此 DSH 会话（cwd 为项目根）无需额外配置即可命中 `/home/zt/stock/financial_data.db`；`--db`/`--db-dir` 参数仍然优先。
- DSH 中通过 `skill` 工具加载（无需 `/security-analysis` 斜杠命令）；Claude Code 中用法不变。

## 数据库表（现库中26张）

> 表数已与库核对一致。标注 **`只写不读`** 的表每次 `collect` 仍会写入，但全项目无任何 SELECT 读取（其中 `ths_*` 三张占库体积约 44%）；标注 **`遗留`** 的表无任何代码路径产出或读取。
>
> 另有 1 张**惰性表** `stock_dividend`（分红汇总），由 `etf_valuation.py` 的 `dividend` 子命令在建表时写入——该子命令尚未跑过，故当前库中不存在，首跑后表数变 27。

### A股指标（2张）

- `em_financial_indicator` — 东财主要指标（宽表 143 列，**buffett-lens 的 ROE/毛利率/杠杆读数依赖它**）
- `sina_financial_indicator` — 新浪财务指标（宽表 88 列）**`只写不读`**

### A股三大报表（9张）

- `em_balance_sheet` / `em_income_statement` / `em_cash_flow` — 东财（宽表 323/207/258 列，**分析脚本的主力数据源**）
- `sina_balance_sheet` / `sina_income_statement` / `sina_cash_flow` — 新浪（中文列名）**`只写不读`**
- `ths_balance_sheet` / `ths_income_statement` / `ths_cash_flow` — 同花顺（`metric_name`/`value` 长表）**`只写不读`**

### 港股（4张）

- `hk_financial_indicator` — 东财港股主要指标（**可选表**，指标层主要由下面三张长表合成）
- `hk_balance_sheet` / `hk_income_statement` / `hk_cash_flow` — EAV 长表（`year`/`quarter`/`STD_ITEM_CODE`/`AMOUNT`），靠科目代码取值

### 估值与汇率（4张）

- `stock_valuation` — 股票估值缓存（PE/PB/股息率/价格，单日快照）
- `stock_valuation_history` — PE/PB 历史序列（7天有效期）；`market_cap` **仅港股有值**，A 股为 NULL 需推算
- `hk_yield_cache` — 港股股息率/回购收益率**单日快照**（由 `etf_valuation.py` 写入，buffett-lens 只对照、不参与计算）
- `hk_fx_rate` — 港元兑人民币汇率（含 `spot_year_end` 年末即期，港股折人民币必须用年末即期而非年均）

### 分红与回购（3张）

- `dividend_annual_yield` — 年度股息率缓存（**当前仅覆盖 `601919`**）
- `stock_dividend_detail` — A股历史分红**明细**（中文列名 `代码`/`派息`/`送股`/`进度`…），由 `dividend_stock_analysis.py` 写入。**与 `stock_dividend` 是两张用途不同的表，不是改名关系**——后者是每只股票一行的**汇总**（`名称`/`累计股息`/`年均股息`/`分红次数`/`融资总额`），由 `etf_valuation.py` 的 `dividend` 子命令写入。`buffett_analysis.py` 两个都不读，它的股息取自 `dividend_annual_yield`
- `stock_repurchase` — 股票回购记录（**当前仅覆盖 `601919`**）

### 分部数据（1张）
- `hk_segment_revenue` — 港股分部收入/业绩/折旧/资本开支，由 `extract_segment_note.py` 从年报分部附注抽出入库，含 `metric`（`external_revenue` 对外销售 / `segment_revenue` 含分部间 / `segment_result` / `depreciation` / `capex`）与 `kind`（segment/elimination/total）两列口径标注——**跨口径不可比较**。已覆盖 `00322`（2020-2025）、`09633`（2019-2025）

### 遗留表（3张）—— 无代码引用

- `资产负债表` / `利润表` / `现金流量表` — 各 234 行，schema 与 `em_*` 三张**逐列相同**，仅含 6 只白酒股（`000568` `000596` `000799` `002304` `600809` `603369`）。**自首个 git 提交即在库中，当前无任何代码路径产出或读取**（采集脚本写表时一律加 `sina_`/`ths_`/`em_`/`hk_` 前缀，不会写这三张中文名表）。保留不删，仅作登记。

## 重要说明

- 所有模块共用同一个 `financial_data.db` 数据库
- 分析前必须先采集财报数据（`collect` 命令）
- 估值数据有7天缓存，使用 `--refresh` 强制刷新
- 周期股分析必须结合行业知识库（`.claude/skills/security-analysis/knowledge/`）进行解读
- 报告生成必须遵循七段式结构（周期股）或四维框架（红利股）
- **致股东信与股东大会问答原文严禁写入 `financial_data.db`**（该库已在 git 中）；buffett-lens 的语料与索引库存放在自己的 `corpus/` 与 `buffett_corpus.db`，两者均不入 git
- buffett-lens 的原则卡引用必须过 `lint_citations.py` 三键校验，报告里的引用必须带 `chunk_id`，**不得转述冒充引用**
