# AGENTS.md

A股/港股股票投资分析工具集。包含四个 skill：

| Skill | 用途 |
|-------|------|
| `security-analysis` | 五大分析模块（财报采集/ETF估值/周期股/红利股/行业竞争），共用 `financial_data.db` |
| `financial-report-pdf-extractor` | 从年报/中报 PDF 提取财务报表数据（ColumnPage 位置感知提取） |
| `financial-report-downloader` | 搜索、下载、验证并存储上市公司财报 PDF |
| `buffett-lens` | 巴菲特视角问诊：15 张原则卡 + 8 项量化读数 + 可逐字溯源的语料检索，含引用真实性闸门 |

分析前先加载 `security-analysis` skill（opencode 用 `skill` 工具加载，Claude Code 用 `/security-analysis`），其中含完整的子文档、报告框架与行业知识库。

**各 skill 需要哪些数据、数据源是什么，见根目录 `DATA-SOURCES.md`**（四个 skill 的数据需求、上游接口、数据流链条与覆盖度缺口）。表清单与列数仍以 `CLAUDE.md`「数据库表」一节为唯一维护点。

做巴菲特视角的问诊时加载 `buffett-lens`（Claude Code 用 `/buffett-lens`）；问诊需要哪些数据、每项从哪来见该技能的 `data-sources.md`（A股/港股字段清单、科目代码映射、口径陷阱、覆盖度现状）。它自带的语料索引库 `buffett_corpus.db` 与 `financial_data.db` **分开存放、互不相干**，语料原文**严禁写入 `financial_data.db`**。

## 技能单一数据源与软链接（重要）

- `.claude/skills/` 下每个 skill（`security-analysis`、`financial-report-pdf-extractor`、`financial-report-downloader`、`buffett-lens`）都是唯一权威源，每个 skill 目录根部须有 `SKILL.md`（勿多层嵌套）。
- 以下路径均是指向对应 skill 的**软链接**（改动自动同步，勿重复维护内容）：
  - `.dsh/skills/<name>`
  - `.qoder/skills/<name>`
  - `.opencode/skills/<name>`
- **只编辑 `.claude/` 下的文件；不要删除后重建文件**，否则会破坏所有软链接导致各工具（Claude Code / DSH / Qoder / OpenCode）配置不同步。
- OpenCode 原生支持发现 `.claude/skills/*/SKILL.md` 与 `.opencode/skills/*/SKILL.md`，同名 skill 按真实路径去重，软链接不会产生重复条目。

## 运行环境

- 必须使用项目 venv：`.venv/bin/python`。**`.venv/` 不入 git，也可能尚未创建**——执行任何脚本前先确认它存在，缺失则：
  ```bash
  python3 -m venv .venv && .venv/bin/pip install akshare pandas requests beautifulsoup4 lxml pymupdf
  ```
  系统 `python`/`python3` 未装 `akshare`/`pymupdf`，直接跑脚本会在 import 阶段 ImportError。
- 数据库定位顺序：`FINANCIAL_DATA_DIR` 环境变量 > 当前工作目录 > 向上查找含 `financial_data.db` 的目录（默认命中 `/home/zt/stock/financial_data.db`）。`--db`/`--db-dir` 参数仍然优先。
- 分析前必须先执行 `collect` 命令采集数据，否则查不到数据。
- 估值类接口有 7 天缓存，加 `--refresh` 强制刷新。
- 无测试、无 lint、无 CI。分析报告写入 `report/`。
- `financial_data.db` 已入库且随每次采集变动，`git status` 常显示其修改属正常。
- 当前环境缺少 `rg`（ripgrep），opencode 的 skill 工具加载时会报 "ripgrep execution failed"；此时仍可直接 Read `.claude/skills/security-analysis/SKILL.md` 获取完整框架。

## 提交约定（重要）

- **提交信息不得包含 `Co-Authored-By` 署名行**（含 `Co-Authored-By: Claude <noreply@anthropic.com>`），也不得加任何 AI 工具署名/生成标记。提交信息只写用户本人署名，正文只描述改动本身。此项与 `.claude/settings.json` 无关，属项目约定，**任何工具（Claude Code / DSH / Qoder / OpenCode）提交时都须遵守**。

## 常用命令（项目根目录执行）

```bash
# 采集数据（必须先做）
.venv/bin/python .claude/skills/security-analysis/scripts/collect_financial_data.py collect --code 600519 --market sh --start-year 2016
.venv/bin/python .claude/skills/security-analysis/scripts/collect_financial_data.py collect --code 00700 --market hk

# 周期股分析 / ETF估值 / 红利股分析 / 行业竞争
.venv/bin/python .claude/skills/security-analysis/scripts/cyclical_stock_analysis.py inventory --code 600519 --market sh
.venv/bin/python .claude/skills/security-analysis/scripts/etf_valuation.py valuation --fund-code 510300
.venv/bin/python .claude/skills/security-analysis/scripts/dividend_stock_analysis.py analyze --code 600519 --market sh
.venv/bin/python .claude/skills/security-analysis/scripts/industry_competition_analysis.py analyze --code 600519

# 查询数据库
.venv/bin/python .claude/skills/security-analysis/scripts/collect_financial_data.py tables
.venv/bin/python .claude/skills/security-analysis/scripts/collect_financial_data.py query --code 600519 --table em_financial_indicator --year 2024

# 财报 PDF 提取（从 PDF 提取资产负债表/损益表/现金流量表）
.venv/bin/python .claude/skills/financial-report-pdf-extractor/scripts/extract_financial_statements.py report/年报.pdf
.venv/bin/python .claude/skills/financial-report-pdf-extractor/scripts/extract_balance_sheet.py report/年报.pdf

# 分部收入附注抽取（经营分部资料 → hk_segment_revenue，两道硬校验通过才入库）
.venv/bin/python .claude/skills/financial-report-pdf-extractor/scripts/extract_segment_note.py --pdf report/农夫山泉_09633/农夫山泉_2025_年报.pdf --code 09633 --store

# 财报下载（搜索+下载+验证）
.venv/bin/python .claude/skills/financial-report-downloader/scripts/download_financial_reports.py --company "海尔智家" --code "600690" --year 2024 --report-type "年报"
.venv/bin/python .claude/skills/financial-report-downloader/scripts/verify_pdf.py --file report/年报.pdf --company "腾讯" --report-type "年报"

# 巴菲特视角问诊（量化读数 + 语料检索 + 引用闸门）
.venv/bin/python .claude/skills/buffett-lens/scripts/buffett_analysis.py data-check --code 600519
.venv/bin/python .claude/skills/buffett-lens/scripts/buffett_analysis.py segment --code 09633
.venv/bin/python .claude/skills/buffett-lens/scripts/buffett_analysis.py screen --code 600519
.venv/bin/python .claude/skills/buffett-lens/scripts/search_corpus.py search --query "moat" --source letters --limit 5
.venv/bin/python .claude/skills/buffett-lens/scripts/lint_citations.py --cards .claude/skills/buffett-lens/principles/ --self-test

# 语料维护（首次或需要更新语料时）
.venv/bin/python .claude/skills/buffett-lens/scripts/fetch_letters.py fetch --all --delay 1.0
.venv/bin/python .claude/skills/buffett-lens/scripts/fetch_letters.py fetch-meetings
.venv/bin/python .claude/skills/buffett-lens/scripts/build_index.py build --rebuild
```

## PDF 提取技术说明

财报 PDF 提取采用 **ColumnPage 位置感知提取**（而非正则或 find_tables()）：
- `pdf_helper.py` 封装 PyMuPDF/pypdf/pdfminer.six 三种后端，优先 PyMuPDF
- `ColumnPage` 通过 `get_text('dict')` 获取 span 坐标，按 X 频率聚类列、Y 自适应聚类行
- 精确对齐栏目与金额，解决多列表格中传统正则解析的错位问题
- `pdf_helper.py` **唯一实现在 extractor**（`financial-report-pdf-extractor/scripts/pdf_helper.py`），downloader 目录下那份是指向它的软链接——改一处即可，不存在两份副本需要同步

## 报告规范（解读分析结果时）

- 周期股报告：按 `.claude/skills/security-analysis/cyclical-analysis-guide.md` 的七段式结构，且必须结合 `knowledge/INDEX.md` 路由到的行业知识解读。
- 红利股报告：按四维框架（股息率趋势/回购注销/自由现金流/营收健康度），注意区分回购注销与其他回购用途。
- 行业竞争分析：先 Web Search 确认公司主营业务和所属行业（与东财 API 结果交叉验证），再跑脚本。
- 巴菲特视角问诊：按 `.claude/skills/buffett-lens/diagnostic-guide.md` §五 的问诊单模板，每条判定必须挂 `chunk_id`、脚本输出行或 B 类页码（`[年报<年> p<n>]`），挂不上的一律标【推测】。**数字按 §三 分四级标注来源（A可重算/B年报原文/C外部/D语料），C 类外部来源不得进入判定依据栏，只许进「我的推测」并标【外部·未经年报核实】。** 持仓情绪问诊（`portfolio-check.md`）是纯检索模式，不得出现估值、分位、目标价与买卖结论。** 引用必须逐字且带 `chunk_id`，中文引用署"（CNBC 原文 · 中译：一朵喵）"。周期股须先做 PE 分位反转校正。【NA 不是 PASS】，护城河/盈利质量/杠杆任一 REJECT 即一票否决。
