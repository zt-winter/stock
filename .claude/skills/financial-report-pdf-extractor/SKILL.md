---
name: financial-report-pdf-extractor
description: 'Comprehensive skill for extracting financial data from corporate annual report PDFs. Use when Claude needs to: (1) Extract financial statements (balance sheet, income statement, cash flow) from company annual reports; (2) Parse Chinese/English bilingual financial reports (especially HKFRS/IFRS compliant); (3) Convert PDF financial tables into structured Markdown or JSON format; (4) Analyze financial ratios and metrics from extracted data. Trigger for tasks involving "financial statements", "annual report", "PDF extraction", "balance sheet", "income statement", or "cash flow statement" from corporate reports.'
---

# Financial Report PDF Extractor

## Overview

This skill provides specialized workflows and tools for extracting, parsing, and structuring financial data from corporate annual report PDFs. It handles the unique challenges of financial PDFs including bilingual content (Chinese/English), complex table structures, accounting formatting (parentheses for negatives, thousands separators), and standardized financial statement layouts according to HKFRS/IFRS standards.

The skill includes Python scripts for reliable extraction, reference materials for financial accounting standards, and templates for standardized output formats.

## Quick Start

### Basic Extraction Workflow

When extracting financial statements from a PDF:

1. **Identify PDF location**: Locate the annual report PDF file (typically named like "2024年报.pdf" or "Annual_Report_2024.pdf")
2. **Find financial statement pages**: Financial statements usually appear after the auditor's report, typically around pages 140-160
3. **Choose extraction method**:
   - For structured extraction: Use `scripts/extract_financial_tables.py` for table-based extraction
   - For text-based extraction: Use `scripts/extract_financial_statements.py` for reliable text parsing
4. **Process the output**: Convert extracted data into standardized Markdown tables with year-over-year comparisons

### Example Commands

```bash
# Extract tables from specific pages
python3 scripts/extract_financial_tables.py /path/to/report.pdf 141 160

# Extract and structure financial statements
python3 scripts/extract_financial_statements.py /path/to/report.pdf

# Extract specific financial statement
python3 scripts/extract_balance_sheet.py /path/to/report.pdf
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

## Workflow Decision Tree

When approaching a financial PDF extraction task:

```
Is the PDF text-searchable?
├── Yes: Use text extraction (scripts/extract_financial_statements.py)
└── No: Try table extraction (scripts/extract_financial_tables.py) or OCR

Which financial statements are needed?
├── All three: Extract pages 140-160 comprehensively
├── Balance sheet only: Search for "STATEMENT OF FINANCIAL POSITION"
├── Income statement only: Search for "STATEMENT OF PROFIT OR LOSS"
└── Cash flow only: Search for "STATEMENT OF CASH FLOWS"

What output format?
├── Markdown tables: Use scripts/generate_markdown_tables.py
├── JSON structured data: Use scripts/convert_to_json.py
└── Excel spreadsheet: Use scripts/export_to_excel.py
```

## Detailed Extraction Methods

### Text-Based Extraction (Recommended)

For most financial PDFs, text extraction is more reliable than table extraction:

1. **Extract all text** from target pages using pdfplumber
2. **Split by lines** and identify statement sections using keywords
3. **Parse line-by-line** matching financial item patterns
4. **Extract amounts** using regex patterns for accounting numbers
5. **Handle bilingual labels** by checking both English and Chinese

### Table-Based Extraction

When tables are well-structured:

1. **Extract tables** using pdfplumber's table detection
2. **Clean table data** by removing empty rows/columns
3. **Identify header rows** containing year columns
4. **Map financial items** to standardized categories
5. **Handle merged cells** and multi-line labels

### Key Challenges and Solutions

| Challenge                                | Solution                                   |
| ---------------------------------------- | ------------------------------------------ |
| Tables split across pages                | Extract from multiple pages and merge      |
| Bilingual labels creating duplicate rows | Use first occurrence, ignore translations  |
| Parentheses for negative numbers         | Convert to negative values during parsing  |
| Missing or inconsistent labels           | Use fuzzy matching with reference mappings |
| Currency unit variations                 | Detect and standardize (all to thousands)  |

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

## Scripts Reference

### extract_financial_statements.py
Main script for comprehensive financial statement extraction.

**Usage**:
```bash
python3 extract_financial_statements.py <pdf_path> [start_page] [end_page]
```

**Features**:
- Automatically detects financial statement sections
- Extracts balance sheet, income statement, and cash flow statement
- Handles bilingual content (English/Chinese)
- Outputs structured JSON and Markdown formats
- Includes year-over-year comparisons

### extract_balance_sheet.py
Specialized script for balance sheet extraction only.

**Usage**:
```bash
python3 extract_balance_sheet.py <pdf_path>
```

### generate_markdown_tables.py
Converts extracted financial data to standardized Markdown tables.

**Usage**:
```bash
python3 generate_markdown_tables.py <json_input> <output_md>
```

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

## Troubleshooting

### Common Issues and Solutions

| Issue                         | Possible Cause                | Solution                                   |
| ----------------------------- | ----------------------------- | ------------------------------------------ |
| No financial statements found | Wrong page range              | Search for "STATEMENT OF" or "財務狀況表"  |
| Amounts extracted incorrectly | Parentheses not handled       | Enable accounting format parsing           |
| Duplicate rows extracted      | Bilingual content duplication | Filter by language or use first occurrence |
| Tables split across pages     | PDF pagination                | Extract from multiple consecutive pages    |
| OCR needed                    | Scanned PDF                   | Use OCR preprocessing before extraction    |

### Validation Checklist

After extraction, verify:
- [ ] Balance sheet balances: Total Assets = Total Liabilities + Equity
- [ ] Cash flow reconciliation: Net cash flow = Cash end - Cash begin
- [ ] Year-over-year comparisons available
- [ ] All major financial items extracted
- [ ] Currency units consistent
- [ ] Notes/references included where applicable

---

**Skill Design**: Based on experience extracting financial statements from Hong Kong-listed company reports with bilingual (Chinese/English) content, complex table structures, and HKFRS accounting standards.