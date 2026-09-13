---
name: buffett-lens
description: 巴菲特视角投资问诊工具，含数据来源与字段清单（A股/港股）。三层架构：1)15张原则卡(能力圈/护城河/管理层与资本配置/安全边际/市场先生/卖出与错误六组，每张以PASS-QUESTION-REJECT收尾)；2)量化筛查脚本(股东盈余、10年ROE一致性、毛利率稳定性、资本开支强度、盈利质量、杠杆、一美元留存测试、安全边际共8项读数，含周期股PE分位反转与高杠杆剔除修正)；3)可逐字溯源的语料检索(巴菲特致股东信1977-2024英文原文+股东大会问答1994-2022中译，引用必须带chunk_id并经三键校验)。适用于个股基本面问诊、投资决策清单式复核、持仓情绪问诊(纯检索不产出买卖结论)、巴菲特原话溯源等场景。依赖 financial_data.db 的财报与估值数据。
---

> **⚠️ 软链接共享提示**
> 本 skill 目录通过软链接被 `.dsh/skills/buffett-lens`、`.qoder/skills/buffett-lens`、`.opencode/skills/buffett-lens` 引用，指向同一份物理文件。
> 修改时请直接在当前路径编辑，**不要删除后重建文件**，以免破坏软链接导致各工具间配置不同步。

# 巴菲特视角投资问诊

**这套工具不预测股价，也不给买卖建议。** 它把"我觉得这家公司不错"拆成若干条可以被推翻的判定，每条判定要么挂着巴菲特的原话（可逐字复核），要么挂着脚本算出的数字（可重算）。挂不上来源的，必须标成【推测】。

## 三层架构

| 层 | 承担什么 | 载体 | 防的是 |
|---|---|---|---|
| 原则卡 | **准则**：判定标准与阈值 | `principles/*.md`（15 张，6 组） | 想当然、"我觉得" |
| 量化筛查 | **数字**：可重算的读数 | `scripts/buffett_analysis.py` | 记忆里的财务印象 |
| 语料检索 | **原话**：逐字可溯的出处 | `scripts/search_corpus.py` | 编造"巴菲特说过" |

贯穿三层的是**一道质量闸门**：`scripts/lint_citations.py` 对每张卡里的引用做三键校验（① 引文在该年全文中 ② 年份与文档一致 ③ 所标 chunk_id 确实包含它）。这是防止幻觉引用的唯一硬约束——只查字符串是不够的，一句真实存在但标错年份的引用照样会变成"巴菲特在 2008 年就预言了……"。

## 功能模块

| 模块 | 子文档 | 脚本 | 用途 |
|------|--------|------|------|
| 问诊方法论 | [diagnostic-guide.md](diagnostic-guide.md) | — | 三层运行顺序、读数规则、报告模板、常见陷阱 |
| 数据来源 | [data-sources.md](data-sources.md) | — | **问诊要哪些数据、每项从哪来**：A股/港股字段清单、科目代码映射、口径陷阱、采集命令、覆盖度现状 |
| 持仓情绪问诊 | [portfolio-check.md](portfolio-check.md) | `scripts/search_corpus.py` | **纯检索模式**：命名情绪+原话+不行动清单，不产出买卖结论 |
| 原则卡 | [principles/INDEX.md](principles/INDEX.md) | — | 15 张卡的挑卡表与一票否决规则 |
| 来源准入 | [diagnostic-guide.md](diagnostic-guide.md) §三 | — | 数字四级分层（A可重算/B年报原文/C外部/D语料）+ 三条硬约束 |
| 量化筛查 | [diagnostic-guide.md](diagnostic-guide.md) §四 | `scripts/buffett_analysis.py` | 8 项读数【二】–【九】+ 汇总 + 一票否决（【一】能力圈固定 N/A） |
| 语料检索 | — | `scripts/search_corpus.py` | 中英文检索、取 chunk 全文、引用校验 |
| 引用闸门 | — | `scripts/lint_citations.py` | 卡片引用的三键校验 + 结构 lint |

## 典型工作流

**完整问诊**（分析一只个股）：

```bash
V=.venv/bin/python; S=.claude/skills/buffett-lens/scripts

$V $S/buffett_analysis.py data-check --code 600519        # 先摸清缺哪些数据
$V $S/buffett_analysis.py segment --code 09633            # ⓪能力圈/①定价权的分部证据
$V $S/buffett_analysis.py screen --code 600519            # 8 项读数 + 汇总
$V $S/search_corpus.py search --query "moat" --source letters --limit 5
$V $S/search_corpus.py get --chunk-id letters:2007:0020   # 取原话，逐字复制
```

`segment` 读的是 `hk_segment_revenue`（由 `financial-report-pdf-extractor` 的 `extract_segment_note.py` 从年报分部附注抽出入库），给出各分部的收入与利润率逐年走势——这是**能力圈**（钱从哪来）与**定价权**（提价能否落地）的原始证据。读数纪律：**分部收入增长本身不是定价权**，收入涨可能只是铺货或降价换量；要看到「收入增长的同时分部利润率没有塌」。另外 `external_revenue`（对外销售）与 `segment_revenue`（含分部间）口径不可混用。库内无该股数据时脚本会打印抽取命令。

**持仓情绪问诊**（大跌/大涨后想动手时）：

```bash
$V $S/search_corpus.py "恐慌" --limit 5
$V $S/search_corpus.py search --query "Mr. Market" --source letters --limit 5
```

**维护语料与卡片**：

```bash
$V $S/fetch_letters.py list                       # 看致股东信索引与桩页识别
$V $S/fetch_letters.py fetch --all --delay 1.0    # 采集致股东信
$V $S/fetch_letters.py fetch-meetings             # 采集股东大会问答（git clone）
$V $S/build_index.py build --rebuild              # 建双 FTS5 索引
$V $S/build_index.py verify && $V $S/build_index.py stats
$V $S/lint_citations.py --cards principles/ --self-test   # 闸门自检 + 卡引用校验
```

## 运行纪律

1. **能力圈前置**：先一句话说清"这家公司靠什么赚钱"+ 3 个竞争对手。说不清就是能力圈外，到此为止——后面的数字再漂亮也不该看。
2. **挑卡按「适用」不按「有利」**，每次只加载 4–6 张，绝不加载全部 15 张。
3. **NA 不是 PASS**。样本不足时脚本记 `NA`，那是"不知道"，不是"没问题"。
4. **一票否决不可讨价还价**：护城河、盈利质量、杠杆任一项 REJECT → 无论估值多低都不通过。"但是很便宜"救不了一家烂生意。
5. **数字要标来源级别**（详见 [diagnostic-guide.md](diagnostic-guide.md) §三）：A 可重算 / B 年报原文（带页码）/ C 外部来源 / D 语料（带 `chunk_id`）。**C 类（媒体、研报、搜索摘要）不得进入第一至四节的依据栏**，只许进「我的推测」并标【外部·未经年报核实】。需要 C 类才能成立的判定，就是证据不足的判定——降级为 QUESTION。**不标级的数字一律按 C 类处理。**
6. **原话必须逐字**，且署名要分层（见下）。检索不到就明说检索不到，**不得转述冒充引用**。
7. **周期股要校正**：`601600`、`601919` 这类强周期股 PE 分位语义反转，改以 PB 为主信号，并对照 `security-analysis` 技能的 `cyclical-analysis-guide.md` §一。

## 版权与署名

语料来源与署名要求（引用时必须照此标注）：

- **致股东信**（1977–2024）：`berkshirehathaway.com` 公开原文 → 署 `巴菲特致股东信 <年>`（英文原文，一手）
- **股东大会问答**（1994–2022，中译，**缺 2017、2018**，2019/2020 残缺）：来自 `github.com/wuxiaoda/BRK-Annual-Meeting`，该仓库**无 LICENSE**，README 明确要求**所有引用必须注明来自 CNBC**，中文译者为「一朵喵」（雪球），部分内容引自张志雄《投资大家：巴菲特系列》 → 署 `巴菲特股东大会问答 <年>（CNBC 原文 · 中译：一朵喵）`，并附 `buffett.cnbc.com`

**语料原文与索引库不入 git**（见 `.gitignore`），且**严禁写入 `financial_data.db`**——该库已在版本控制中。

## 依赖

```bash
pip install requests beautifulsoup4 lxml pymupdf
```

- 采集致股东信时 CDN 强制 `Content-Encoding: br`，若环境未装 `brotli`，脚本会回退调用 `curl --compressed`（需系统有 `curl`）。
- 索引库用 SQLite 的 FTS5，需要 `trigram` 与 `porter unicode61` 分词器（SQLite ≥ 3.34，本项目实测 3.53.4 可用）。

## 目录与数据库定位

```
.claude/skills/buffett-lens/
├── SKILL.md
├── diagnostic-guide.md        # 问诊方法论
├── data-sources.md            # 数据来源与字段清单
├── portfolio-check.md         # 持仓情绪问诊
├── principles/                # 15 张原则卡 + INDEX.md + keywords.tsv
├── scripts/                   # 6 个脚本
│   ├── buffett_common.py      # 共享底座：DB 定位、normalize_for_match、终端格式
│   ├── buffett_analysis.py    # 8 项量化读数 + 分部证据
│   ├── search_corpus.py       # 语料检索 + 引用校验
│   ├── lint_citations.py      # 引用真实性闸门（三键校验）
│   ├── fetch_letters.py       # 采集致股东信 / 股东大会问答
│   └── build_index.py         # 建双 FTS5 索引
├── corpus/                    # 原始与抽文本语料（不入 git）
└── buffett_corpus.db          # 语料索引库（不入 git）
```

- **语料索引库**固定在 skill 目录下，由 `buffett_common.py` 定位，与 `financial_data.db` 分开存放。
- **财报数据库** `financial_data.db` 定位顺序：`FINANCIAL_DATA_DIR` 环境变量 > 当前工作目录 > 向上查找含该文件的目录；`--db`/`--db-dir` 参数优先。数据缺失时先跑 `security-analysis` 技能的 `collect_financial_data.py collect`。
- 估值数据有 7 天缓存，用 `--refresh` 强制刷新。
