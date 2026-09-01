---
name: financial-report-pdf-extractor
description: '从上市公司年报/中报 PDF 中提取财务报表数据（资产负债表、损益表、现金流量表）。采用 ColumnPage 位置感知提取技术，通过 X/Y 坐标聚类精确对齐栏目与金额，支持中英文双语财报（HKFRS/IFRS）。输出结构化 JSON 和 Markdown 表格。适用于：提取财报PDF中的财务数据、解析中英文双语报表、将PDF表格转为结构化数据。'
---

# Financial Report PDF Extractor

## 概述

从上市公司年报/中报 PDF 中提取财务报表数据。核心技术是 **ColumnPage 位置感知提取**：通过 `get_text('dict')` 获取每个文本 span 的精确 (x, y) 坐标，按 X 频率聚类列边界、按 Y 自适应聚类行，精确对齐栏目名称与金额数字。底层使用 pdf_helper 兼容层，支持 PyMuPDF（首选）、pypdf、pdfminer.six 三种后端。

支持中英文双语财报（HKFRS/IFRS）、会计格式（括号负数、千位逗号）、跨页表格合并。

## 快速开始

```bash
# 提取全部财务报表（自动定位资产负债表、损益表、现金流量表）
.venv/bin/python .claude/skills/financial-report-pdf-extractor/scripts/extract_financial_statements.py report/年报.pdf

# 指定页码范围
.venv/bin/python .claude/skills/financial-report-pdf-extractor/scripts/extract_financial_statements.py report/年报.pdf 140 160

# 仅提取资产负债表（自动分类资产/负债/权益）
.venv/bin/python .claude/skills/financial-report-pdf-extractor/scripts/extract_balance_sheet.py report/年报.pdf

# JSON → Markdown 转换
.venv/bin/python .claude/skills/financial-report-pdf-extractor/scripts/generate_markdown_tables.py data.json output.md
```

## Core Capabilities

### 1. Financial Statement Extraction

#### Balance Sheet Extraction
- **Key identifiers**: "CONSOLIDATED STATEMENT OF FINANCIAL POSITION", "綜合財務狀況表"
- **Common structure**: Non-current assets, Current assets, Non-current liabilities, Current liabilities, Equity
- **Critical items**: Investment properties, Cash and bank balances, Trade receivables, Total equity

#### Income Statement Extraction
- **Key identifiers**: "CONSOLIDATED STATEMENT OF PROFIT OR LOSS", "綜合損益表"
- **Common structure**: Revenue, Cost of services, Gross profit, Operating expenses, Finance costs, Tax, Net profit
- **Critical items**: Revenue, Gross profit, Profit before tax, Net profit for the year

#### Cash Flow Statement Extraction
- **Key identifiers**: "CONSOLIDATED STATEMENT OF CASH FLOWS", "綜合現金流量表"
- **Common structure**: Operating activities, Investing activities, Financing activities, Net cash flow
- **Critical items**: Cash generated from operations, Purchase of property/equipment, Dividend paid

### 2. Bilingual Text Processing

Financial reports in Hong Kong/China markets typically include both English and Chinese:

- **Parallel columns**: English labels followed by Chinese translations
- **Mixed content**: Lines may contain both languages separated by spaces
- **Currency units**: "RMB'000" (English) and "人民幣千元" (Chinese) for thousands of RMB

### 3. Accounting Format Handling

- **Negative amounts**: Parentheses "(1,234)" represent negative values
- **Thousands separators**: Commas in numbers "1,234,567"
- **Currency units**: Typically in thousands ("RMB'000") or millions
- **Rounding**: Numbers often rounded to nearest thousand

## 提取架构

### ColumnPage 位置感知提取（核心）

1. **定位报表页面**：`extract_text()` 关键词匹配，定位资产负债表/损益表/现金流量表所在页
2. **列边界检测**：`detect_columns()` 基于 span 的 X 坐标频率聚类（min_gap=25, min_freq=2），过滤低频干扰（表头、特殊符号）
3. **行聚类**：`extract_rows(y_tolerance=5.0)` 自适应 Y 聚类，处理标签与金额的微小 Y 偏差
4. **列分配**：每个 span 映射到最近的列，返回 `ColumnRow(label, cols, y)`
5. **合并续行**：多行标签自动合并，跨页表格自动拼接

### 输出格式

- **JSON**：结构化数据，含 label/note/amount_current/amount_previous/is_header/is_total
- **Markdown**：可直接阅读的表格，header 行加粗，total 行加粗
- 可用 `generate_markdown_tables.py` 从 JSON 转 Markdown

## 已知挑战与解决方案

| 挑战 | 解决方案 |
|------|----------|
| 多列表格中标签与金额分行显示 | ColumnPage 位置感知提取，按坐标自动对齐 |
| PyMuPDF find_tables() 截断中文标签 | 改用 ColumnPage（get_text('dict')）避免截断 |
| 跨页表格 | 多页提取后自动合并续行 |
| 表头干扰行（未經審核、百萬元等） | 非数字金额过滤 + 正则跳过 |
| 括号负数如 (1,234) | 自动转换为负值 |
| 货币单位不统一 | 检测并标注单位（千元/百万元） |

## Financial Statement Templates

### Standardized Markdown Output Format

```markdown
# Company Name (Stock Code) Year Financial Statements

**Reporting Period**: Year ended 31 December YYYY  
**Currency Unit**: Currency in thousands  
**Data Source**: Year Annual Report PDF (pages X-Y)  
**Preparation Date**: YYYY-MM-DD  

## 1. Consolidated Balance Sheet
**As at**: 31 December YYYY

| Item                          | Note | YYYY    | YYYY-1  |
| ----------------------------- | ---- | ------- | ------- |
| **Non-current assets**        |      |         |         |
| Investment properties         | 12   | 967,100 | 959,500 |
| Property, plant and equipment | 11   | 1,491   | 1,541   |
| ...                           | ...  | ...     | ...     |

## 2. Consolidated Income Statement
**Period**: Year ended 31 December YYYY

| Item             | Note | YYYY       | YYYY-1     |
| ---------------- | ---- | ---------- | ---------- |
| Revenue          | 3    | 45,910     | 46,779     |
| Cost of services |      | (12,900)   | (12,162)   |
| **Gross profit** |      | **33,010** | **34,617** |
| ...              | ...  | ...        | ...        |

## 3. Consolidated Cash Flow Statement
**Period**: Year ended 31 December YYYY

| Item                                   | Note  | YYYY      | YYYY-1     |
| -------------------------------------- | ----- | --------- | ---------- |
| **Operating activities**               |       |           |            |
| Cash generated from operations         | 16(b) | 10,799    | 17,987     |
| Income tax paid                        | 20(a) | (2,174)   | (4,078)    |
| **Net cash from operating activities** |       | **8,625** | **13,909** |
| ...                                    | ...   | ...       | ...        |
```

## 脚本参考

### extract_financial_statements.py
主提取脚本，自动定位并提取资产负债表、损益表、现金流量表。

```bash
.venv/bin/python extract_financial_statements.py <pdf_path> [start_page] [end_page]
```

**输出**：`{文件名}_financial_statements.json` + `{文件名}_financial_statements.md`

### extract_balance_sheet.py
专项资产负债表提取，自动分类资产/负债/权益三大板块，计算负债率。

```bash
.venv/bin/python extract_balance_sheet.py <pdf_path> [start_page] [end_page]
```

**输出**：`{文件名}_balance_sheet.json` + `{文件名}_balance_sheet.md`

### generate_markdown_tables.py
JSON → Markdown 转换工具。

```bash
.venv/bin/python generate_markdown_tables.py <json_input> <output_md>
```

### pdf_helper.py
PDF 兼容层，封装 PyMuPDF/pypdf/pdfminer.six，提供 `open_pdf()`、`ColumnPage`、`ColumnRow` 等统一 API。两个 skill（extractor 和 downloader）各有一份相同副本。

## Financial Accounting References

For detailed financial accounting standards and item mappings:

- **HKFRS Standards**: See [references/hkfrs_standards.md](references/hkfrs_standards.md) for Hong Kong Financial Reporting Standards
- **Financial Item Mapping**: See [references/financial_item_mapping.md](references/financial_item_mapping.md) for standardized financial statement item names
- **Accounting Formats**: See [references/accounting_formats.md](references/accounting_formats.md) for handling accounting conventions

## Common Financial Report Patterns

### Hong Kong-Listed Companies
- **Pages**: Financial statements typically pages 140-160
- **Auditor**: KPMG, PwC, Deloitte, EY (Big 4)
- **Currency**: RMB'000 (Chinese companies) or HKD'000 (Hong Kong companies)
- **Standards**: HKFRS (Hong Kong Financial Reporting Standards)

### Chinese A-Share Companies
- **Pages**: Financial statements earlier, often pages 80-120
- **Standards**: CAS (Chinese Accounting Standards)
- **Language**: Primarily Chinese with some English summaries
- **Currency**: RMB元 or RMB thousand元

### US-Listed Companies
- **Pages**: Financial statements after "Item 8" (typically ~80-120)
- **Standards**: US GAAP or IFRS
- **Currency**: USD in thousands or millions
- **Sections**: Balance Sheets, Statements of Operations, Statements of Cash Flows

## 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 找不到报表 | 页码范围不对 | 指定页码范围或让脚本自动扫描全文件 |
| 栏目与金额不对齐 | 未使用 ColumnPage | 确保使用 pdf_helper 的 ColumnPage 而非 find_tables() |
| 合并标签错误 | 两个独立 header 被拼接 | merge_continuation_labels 不合并有独立标签的行 |
| 负债总计错误 | "权益及负债总额"覆盖 | parse_balance_sheet 已加互斥过滤 |
| 扫描 PDF | 无文本层 | 需先 OCR 再提取 |

### 验证清单

提取后应验证：
- [ ] 资产负债表平衡：资产总额 = 负债总额 + 权益总额
- [ ] 现金流量表核对：期末现金 = 期初现金 + 净增加额
- [ ] 附注编号正确捕获
- [ ] 货币单位一致

---

**技能设计**：基于港股/ A 股上市公司财报提取经验，采用 ColumnPage 位置感知提取技术解决多列表格中标签与金额不对齐的核心问题。