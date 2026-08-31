# AGENTS.md

A股/港股股票投资分析工具集。五大模块（财报采集/ETF估值/周期股/红利股/行业竞争）共用同一个 SQLite 数据库 `financial_data.db`。分析前先加载 `security-analysis` skill（opencode 用 `skill` 工具加载，Claude Code 用 `/security-analysis`），其中含完整的子文档、报告框架与行业知识库。

## 技能单一数据源与软链接（重要）

- `.claude/skills/` 下每个 skill（`security-analysis`、`financial-report-pdf-extractor`、`financial-report-downloader`）都是唯一权威源，每个 skill 目录根部须有 `SKILL.md`（勿多层嵌套）。
- 以下路径均是指向对应 skill 的**软链接**（改动自动同步，勿重复维护内容）：
  - `.dsh/skills/<name>`
  - `.qoder/skills/<name>`
  - `.opencode/skills/<name>`
- **只编辑 `.claude/` 下的文件；不要删除后重建文件**，否则会破坏所有软链接导致各工具（Claude Code / DSH / Qoder / OpenCode）配置不同步。
- OpenCode 原生支持发现 `.claude/skills/*/SKILL.md` 与 `.opencode/skills/*/SKILL.md`，同名 skill 按真实路径去重，软链接不会产生重复条目。

## 运行环境

- 必须使用项目 venv：`.venv/bin/python`（已装 akshare 1.18.x、pandas、requests）。系统 `python`/`python3` 未装 akshare，直接跑脚本会 ImportError。
- 数据库定位顺序：`FINANCIAL_DATA_DIR` 环境变量 > 当前工作目录 > 向上查找含 `financial_data.db` 的目录（默认命中 `/home/zt/stock/financial_data.db`）。`--db`/`--db-dir` 参数仍然优先。
- 分析前必须先执行 `collect` 命令采集数据，否则查不到数据。
- 估值类接口有 7 天缓存，加 `--refresh` 强制刷新。
- 无测试、无 lint、无 CI。分析报告写入 `report/`。
- `financial_data.db` 已入库且随每次采集变动，`git status` 常显示其修改属正常。
- 当前环境缺少 `rg`（ripgrep），opencode 的 skill 工具加载时会报 "ripgrep execution failed"；此时仍可直接 Read `.claude/skills/security-analysis/SKILL.md` 获取完整框架。

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
```

## 报告规范（解读分析结果时）

- 周期股报告：按 `.claude/skills/security-analysis/cyclical-analysis-guide.md` 的七段式结构，且必须结合 `knowledge/INDEX.md` 路由到的行业知识解读。
- 红利股报告：按四维框架（股息率趋势/回购注销/自由现金流/营收健康度），注意区分回购注销与其他回购用途。
- 行业竞争分析：先 Web Search 确认公司主营业务和所属行业（与东财 API 结果交叉验证），再跑脚本。
