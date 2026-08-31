# Accounting Format Handling for Financial PDF Extraction

## Overview

Financial statements use specific formatting conventions for numbers, negative values, and presentation. This reference covers how to handle these formats when extracting data from PDFs, particularly for bilingual (English/Chinese) Hong Kong financial reports.

## Number Formatting

### Thousands and Millions Separators

Financial reports typically present numbers in thousands or millions:

| Format | Example | Meaning | Conversion Factor |
|--------|---------|---------|-------------------|
| RMB'000 | 1,234 | 1,234,000 | × 1,000 |
| HKD'000 | 5,678 | 5,678,000 | × 1,000 |
| RMB million | 1.234 | 1,234,000 | × 1,000,000 |
| HKD million | 5.678 | 5,678,000 | × 1,000,000 |
| 人民幣千元 | 1,234 | 1,234,000 | × 1,000 |
| 港幣千元 | 5,678 | 5,678,000 | × 1,000 |

**Common currency units in Hong Kong reports:**
- `RMB'000` - Renminbi in thousands
- `HKD'000` - Hong Kong dollars in thousands  
- `USD'000` - US dollars in thousands
- `人民幣千元` - Renminbi in thousands (Chinese)
- `港幣千元` - Hong Kong dollars in thousands (Chinese)

### Decimal Separators and Rounding

- **Commas as thousand separators**: `1,234,567`
- **Periods as decimal points**: `1,234.56` (when not in thousands)
- **No decimal places in thousands**: `1,234` (means 1,234,000)
- **Rounded to nearest thousand**: `1,234` could be `1,234,123` rounded down

### Negative Numbers

Financial statements use parentheses to indicate negative amounts:

| Format | Meaning | Examples |
|--------|---------|----------|
| Parentheses `(1,234)` | Negative | `(1,234)`, `(5,678)` |
| Minus sign `-1,234` | Negative | `-1,234`, `-5,678` |
| Red text/color | Negative | Often in colored PDFs |
| 括号 `(1,234)` | 负数 | 中文报表中使用 |

**Note**: Some reports may use a minus sign instead of parentheses. Always check the consistent pattern throughout the document.

### Zero Values

| Format | Meaning |
|--------|---------|
| `-` | Zero or not applicable |
| `—` | Zero or not applicable |
| `0` | Zero |
| `nil` | Zero |
| `无` | Zero (Chinese) |
| `不適用` | Not applicable (Chinese) |

## Text Extraction Challenges

### Bilingual Content Handling

Hong Kong financial reports typically include both English and Chinese:

**Common patterns:**
1. **Parallel columns**: English left, Chinese right
   ```
   Property, plant and equipment      物業、廠房及設備
   1,234                               1,234
   ```

2. **Mixed in same line**: 
   ```
   Property, plant and equipment 物業、廠房及設備 1,234
   ```

3. **Separate sections**: English section followed by Chinese section

**Extraction strategy:**
- Extract all text first
- Identify language patterns
- Prefer English labels for standardization
- Keep Chinese for reference if needed

### Line Continuation and Wrapping

Financial items may wrap across lines:
```
Property, plant and
equipment                       1,234
```

**Handling strategy:**
- Merge lines that don't contain amounts
- Look for indentation patterns
- Check if next line starts with lowercase (continuation)

### Table Structure Variations

PDF tables may have:
- **Merged cells**: For section headers
- **Empty cells**: For alignment
- **Multi-level headers**: Year columns with sub-columns
- **Notes in separate columns**: Reference numbers in first column

## Regular Expressions for Financial Data Extraction

### Amount Patterns

```python
# Basic accounting amount (with optional parentheses and commas)
amount_pattern = r'\(?\d{1,3}(?:,\d{3})*\)?'

# With optional decimal places
amount_with_decimal = r'\(?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?'

# Strict accounting format (parentheses for negative)
accounting_amount = r'(?:\(\d{1,3}(?:,\d{3})*\)|\d{1,3}(?:,\d{3})*)'

# Currency symbols (optional)
currency_amount = r'[A-Z]{3}\s?' + amount_pattern
```

### Financial Statement Headers

```python
# Balance sheet headers
balance_sheet_pattern = r'(?:CONSOLIDATED\s+)?STATEMENT\s+OF\s+FINANCIAL\s+POSITION|綜合財務狀況表'

# Income statement headers  
income_statement_pattern = r'(?:CONSOLIDATED\s+)?STATEMENT\s+OF\s+PROFIT\s+OR\s+LOSS|綜合損益表'

# Cash flow headers
cash_flow_pattern = r'(?:CONSOLIDATED\s+)?STATEMENT\s+OF\s+CASH\s+FLOWS|綜合現金流量表'
```

### Section Headers

```python
# Asset sections
assets_pattern = r'NON-CURRENT\s+ASSETS|CURRENT\s+ASSETS|非流動資產|流動資產'

# Liability sections
liabilities_pattern = r'NON-CURRENT\s+LIABILITIES|CURRENT\s+LIABILITIES|非流動負債|流動負債'

# Equity sections
equity_pattern = r'EQUITY|權益|股本|儲備'
```

## Data Cleaning and Normalization

### Step 1: Extract Raw Text
```python
import pdfplumber

with pdfplumber.open('report.pdf') as pdf:
    text = ''
    for page in pdf.pages[140:160]:  # Typical financial statement pages
        text += page.extract_text() + '\n'
```

### Step 2: Clean and Split
```python
lines = text.split('\n')
cleaned_lines = []

for line in lines:
    line = line.strip()
    # Remove page headers/footers
    if not line or line.startswith('Page') or line.isdigit():
        continue
    cleaned_lines.append(line)
```

### Step 3: Parse Accounting Amounts
```python
import re

def parse_accounting_amount(amount_str):
    """Convert accounting format string to numeric value."""
    if not amount_str or amount_str in ['-', '—', 'nil', '无', '不適用']:
        return 0.0
    
    # Remove parentheses and commas
    clean = amount_str.replace('(', '').replace(')', '').replace(',', '')
    
    try:
        value = float(clean)
        # Check if original had parentheses (negative)
        if '(' in amount_str:
            value = -value
        return value
    except:
        return 0.0
```

### Step 4: Handle Bilingual Labels
```python
def extract_english_label(line):
    """Extract English portion from bilingual line."""
    # Common pattern: English then Chinese then amounts
    # Try to find where Chinese characters start
    import re
    
    # Pattern for Chinese characters
    chinese_pattern = r'[\u4e00-\u9fff]+'
    
    match = re.search(chinese_pattern, line)
    if match:
        # Return text before Chinese characters
        return line[:match.start()].strip()
    else:
        return line.strip()
```

## Common PDF Extraction Issues and Solutions

### Issue 1: Scanned PDFs (No Selectable Text)
**Solution**: Use OCR
```python
# Requires: pip install pytesseract pdf2image
from pdf2image import convert_from_path
import pytesseract

images = convert_from_path('scanned.pdf', first_page=140, last_page=160)
text = ''
for image in images:
    text += pytesseract.image_to_string(image) + '\n'
```

### Issue 2: Complex Table Structures
**Solution**: Use pdfplumber's table extraction
```python
with pdfplumber.open('report.pdf') as pdf:
    for page in pdf.pages[140:160]:
        tables = page.extract_tables()
        for table in tables:
            # Process table data
            for row in table:
                print(row)
```

### Issue 3: Inconsistent Formatting
**Solution**: Flexible parsing with multiple patterns
```python
def flexible_amount_parse(text):
    """Try multiple patterns to extract amount."""
    patterns = [
        r'\(?\d{1,3}(?:,\d{3})*\)?',  # Standard accounting
        r'\d{1,3}(?:,\d{3})*',        # No parentheses
        r'\(\d+\)',                   # Simple parentheses
        r'-?\d+'                      # Simple with minus
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None
```

## Standardized Output Format

### JSON Structure Example
```json
{
  "balance_sheet": {
    "name": "Consolidated Balance Sheet",
    "as_at": "31 December 2024",
    "currency_unit": "RMB'000",
    "assets": {
      "non_current": [
        {
          "label": "Property, plant and equipment",
          "label_chinese": "物業、廠房及設備",
          "note": "11",
          "amount_2024": "1,491",
          "amount_2023": "1,541",
          "amount_2024_numeric": 1491000,
          "amount_2023_numeric": 1541000
        }
      ],
      "current": [...]
    },
    "liabilities": {...},
    "equity": [...]
  }
}
```

### Markdown Table Format
```markdown
| Item | Note | 2024 | 2023 |
|------|------|------|------|
| **Non-current assets** | | | |
| Property, plant and equipment | 11 | 1,491 | 1,541 |
| Investment properties | 12 | 967,100 | 959,500 |
| ... | ... | ... | ... |
```

## Validation Rules

### Balance Sheet Validation
- Total Assets = Total Liabilities + Total Equity
- Non-current assets + Current assets = Total assets
- Non-current liabilities + Current liabilities = Total liabilities

### Cash Flow Validation
- Net cash flow = Cash end - Cash begin
- Net cash flow = Operating + Investing + Financing activities

### Cross-statement Validation
- Net profit (Income statement) appears in cash flow operating activities
- Property, plant and equipment changes reflected in cash flow investing activities

## Best Practices

1. **Always preserve original text**: Keep extracted text for verification
2. **Handle errors gracefully**: Continue extraction even if some lines fail
3. **Provide clear warnings**: Log formatting issues for manual review
4. **Support multiple formats**: Accommodate variations in presentation
5. **Include metadata**: Document extraction parameters and assumptions
6. **Validate results**: Check accounting equations where possible
7. **Support reprocessing**: Allow easy re-extraction with adjusted parameters

## Troubleshooting Checklist

- [ ] Are amounts in thousands or millions?
- [ ] Are negative numbers in parentheses or with minus sign?
- [ ] Is the PDF text-searchable or scanned?
- [ ] Are tables spanning multiple pages?
- [ ] Are there merged cells affecting column alignment?
- [ ] Is bilingual content causing duplicate entries?
- [ ] Are note references being extracted correctly?
- [ ] Do totals match the sum of components?