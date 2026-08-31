#!/usr/bin/env python3
"""
Financial Statement Extractor from PDF Annual Reports

This script extracts financial statements (balance sheet, income statement, cash flow)
from corporate annual report PDFs. It uses text-based extraction which is more reliable
for complex financial tables with bilingual content and accounting formatting.

Usage:
    python3 extract_financial_statements.py <pdf_path> [start_page] [end_page]

Example:
    python3 extract_financial_statements.py 2024年报.pdf 141 160
"""

import pdfplumber
import re
import sys
import json
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
import os


@dataclass
class FinancialItem:
    """Represents a single financial statement item with amounts for current and previous year."""
    label: str
    note: str = ""
    amount_current: str = ""
    amount_previous: str = ""
    is_header: bool = False
    is_total: bool = False


@dataclass
class FinancialStatement:
    """Represents a complete financial statement (balance sheet, income statement, or cash flow)."""
    name: str
    period: str
    items: List[FinancialItem]
    currency_unit: str = "RMB'000"
    page_range: str = ""
    

class FinancialPDFExtractor:
    """Main class for extracting financial statements from PDFs."""
    
    # Keywords to identify financial statements
    BALANCE_SHEET_KEYWORDS = [
        "CONSOLIDATED STATEMENT OF FINANCIAL POSITION",
        "綜合財務狀況表",
        "STATEMENT OF FINANCIAL POSITION",
        "BALANCE SHEET"
    ]
    
    INCOME_STATEMENT_KEYWORDS = [
        "CONSOLIDATED STATEMENT OF PROFIT OR LOSS", 
        "綜合損益表",
        "STATEMENT OF PROFIT OR LOSS",
        "INCOME STATEMENT",
        "PROFIT AND LOSS"
    ]
    
    CASH_FLOW_KEYWORDS = [
        "CONSOLIDATED STATEMENT OF CASH FLOWS",
        "綜合現金流量表",
        "STATEMENT OF CASH FLOWS",
        "CASH FLOW STATEMENT"
    ]
    
    def __init__(self, pdf_path: str):
        """Initialize with PDF path."""
        self.pdf_path = pdf_path
        self.pdf = pdfplumber.open(pdf_path)
        self.statements = []
        
    def extract_text_from_pages(self, start_page: int = 140, end_page: int = 160) -> str:
        """Extract text from specified page range."""
        text = ""
        for i in range(start_page - 1, min(end_page, len(self.pdf.pages))):
            try:
                page_text = self.pdf.pages[i].extract_text()
                text += f"\n=== Page {i+1} ===\n{page_text}\n"
            except Exception as e:
                print(f"Error extracting page {i+1}: {e}")
        return text
    
    def find_statement_pages(self, statement_type: str = "all") -> Dict[str, List[int]]:
        """Find page numbers containing financial statements."""
        statement_pages = {
            "balance_sheet": [],
            "income_statement": [],
            "cash_flow": []
        }
        
        for i, page in enumerate(self.pdf.pages):
            try:
                page_text = page.extract_text()
                if not page_text:
                    continue
                    
                page_num = i + 1
                
                # Check for balance sheet
                for keyword in self.BALANCE_SHEET_KEYWORDS:
                    if keyword in page_text:
                        statement_pages["balance_sheet"].append(page_num)
                        break
                
                # Check for income statement
                for keyword in self.INCOME_STATEMENT_KEYWORDS:
                    if keyword in page_text:
                        statement_pages["income_statement"].append(page_num)
                        break
                
                # Check for cash flow
                for keyword in self.CASH_FLOW_KEYWORDS:
                    if keyword in page_text:
                        statement_pages["cash_flow"].append(page_num)
                        break
                        
            except Exception as e:
                print(f"Error processing page {i+1}: {e}")
                
        return statement_pages
    
    def extract_statement(self, start_page: int, end_page: int = None) -> Dict[str, FinancialStatement]:
        """Extract financial statements from specified page range."""
        if end_page is None:
            end_page = start_page + 20  # Default to 20 pages after start
            
        text = self.extract_text_from_pages(start_page, end_page)
        
        # Split into lines and clean
        lines = text.split('\n')
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('==='):
                cleaned_lines.append(line)
        
        # Parse financial statements
        statements = {}
        
        # Identify statement sections
        current_section = None
        statement_lines = []
        
        for i, line in enumerate(cleaned_lines):
            # Check for balance sheet
            for keyword in self.BALANCE_SHEET_KEYWORDS:
                if keyword in line:
                    if current_section and statement_lines:
                        statements[current_section] = self._parse_statement_lines(current_section, statement_lines)
                    current_section = "balance_sheet"
                    statement_lines = []
                    break
            
            # Check for income statement
            for keyword in self.INCOME_STATEMENT_KEYWORDS:
                if keyword in line:
                    if current_section and statement_lines:
                        statements[current_section] = self._parse_statement_lines(current_section, statement_lines)
                    current_section = "income_statement"
                    statement_lines = []
                    break
            
            # Check for cash flow
            for keyword in self.CASH_FLOW_KEYWORDS:
                if keyword in line:
                    if current_section and statement_lines:
                        statements[current_section] = self._parse_statement_lines(current_section, statement_lines)
                    current_section = "cash_flow"
                    statement_lines = []
                    break
            
            if current_section:
                statement_lines.append(line)
        
        # Parse the last section
        if current_section and statement_lines:
            statements[current_section] = self._parse_statement_lines(current_section, statement_lines)
        
        return statements
    
    def _parse_statement_lines(self, statement_type: str, lines: List[str]) -> FinancialStatement:
        """Parse lines of text into a structured financial statement."""
        items = []
        
        # Determine statement name and period
        statement_name = ""
        period = ""
        
        if statement_type == "balance_sheet":
            statement_name = "Consolidated Balance Sheet"
            period = "As at 31 December"
        elif statement_type == "income_statement":
            statement_name = "Consolidated Income Statement"
            period = "Year ended 31 December"
        elif statement_type == "cash_flow":
            statement_name = "Consolidated Cash Flow Statement"
            period = "Year ended 31 December"
        
        # Parse financial items
        for line in lines:
            # Skip header lines and section markers
            if any(keyword in line for keyword in self.BALANCE_SHEET_KEYWORDS + 
                   self.INCOME_STATEMENT_KEYWORDS + self.CASH_FLOW_KEYWORDS):
                continue
            
            # Extract financial item
            item = self._parse_financial_line(line)
            if item:
                items.append(item)
        
        return FinancialStatement(
            name=statement_name,
            period=period,
            items=items,
            currency_unit="RMB'000",
            page_range=""
        )
    
    def _parse_financial_line(self, line: str) -> Optional[FinancialItem]:
        """Parse a single line of financial text into a FinancialItem."""
        # Skip empty lines and page markers
        if not line or line.startswith('Page'):
            return None
        
        # Check if this is a header line (bold or all caps)
        is_header = line.isupper() or line.startswith('**')
        is_total = 'total' in line.lower() or 'TOTAL' in line or '合计' in line
        
        # Extract note reference (numbers in parentheses at start)
        note = ""
        note_match = re.match(r'^\s*(\d+[a-z]?)\s+', line)
        if note_match:
            note = note_match.group(1)
            line = line[note_match.end():].strip()
        
        # Try to extract amounts (accounting format with parentheses and commas)
        # Pattern for accounting numbers: optional parentheses, digits with commas
        amount_pattern = r'\(?\d{1,3}(?:,\d{3})*\)?'
        amounts = re.findall(amount_pattern, line)
        
        if len(amounts) >= 2:
            # Assume first amount is current year, second is previous year
            amount_current = amounts[0]
            amount_previous = amounts[1]
            
            # Remove amounts from label
            label = line
            for amount in amounts:
                label = label.replace(amount, '', 1)
            label = label.strip()
            
            # Clean up label (remove trailing punctuation, extra spaces)
            label = re.sub(r'[:\-\.\s]+$', '', label)
            
        elif len(amounts) == 1:
            # Only one amount found
            amount_current = amounts[0]
            amount_previous = ""
            label = line.replace(amount_current, '').strip()
            label = re.sub(r'[:\-\.\s]+$', '', label)
        else:
            # No amounts found, could be a header or descriptive line
            amount_current = ""
            amount_previous = ""
            label = line.strip()
        
        # Skip lines that are just numbers or very short
        if len(label) < 2 and not is_header and not is_total:
            return None
        
        return FinancialItem(
            label=label,
            note=note,
            amount_current=amount_current,
            amount_previous=amount_previous,
            is_header=is_header,
            is_total=is_total
        )
    
    def export_to_json(self, statements: Dict[str, FinancialStatement], output_path: str):
        """Export extracted statements to JSON format."""
        output_data = {}
        
        for stmt_type, stmt in statements.items():
            stmt_dict = asdict(stmt)
            # Convert FinancialItem objects to dictionaries
            stmt_dict['items'] = [asdict(item) for item in stmt.items]
            output_data[stmt_type] = stmt_dict
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        print(f"Exported to {output_path}")
    
    def generate_markdown(self, statements: Dict[str, FinancialStatement]) -> str:
        """Generate Markdown representation of financial statements."""
        md = "# Financial Statements Extracted from PDF\n\n"
        
        for stmt_type, stmt in statements.items():
            md += f"## {stmt.name}\n"
            md += f"**{stmt.period}**  \n"
            md += f"**Currency Unit**: {stmt.currency_unit}  \n\n"
            
            md += "| Item | Note | Current Year | Previous Year |\n"
            md += "|------|------|--------------|---------------|\n"
            
            for item in stmt.items:
                if item.is_header:
                    md += f"| **{item.label}** | | | |\n"
                elif item.is_total:
                    md += f"| **{item.label}** | {item.note} | **{item.amount_current}** | **{item.amount_previous}** |\n"
                else:
                    md += f"| {item.label} | {item.note} | {item.amount_current} | {item.amount_previous} |\n"
            
            md += "\n\n"
        
        return md
    
    def close(self):
        """Close the PDF file."""
        self.pdf.close()


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python3 extract_financial_statements.py <pdf_path> [start_page] [end_page]")
        print("Example: python3 extract_financial_statements.py 2024年报.pdf 141 160")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    start_page = int(sys.argv[2]) if len(sys.argv) > 2 else 140
    end_page = int(sys.argv[3]) if len(sys.argv) > 3 else 160
    
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found: {pdf_path}")
        sys.exit(1)
    
    print(f"Extracting financial statements from {pdf_path} (pages {start_page}-{end_page})")
    
    extractor = FinancialPDFExtractor(pdf_path)
    
    try:
        # Find statement pages
        statement_pages = extractor.find_statement_pages()
        print("\nFound financial statements at pages:")
        for stmt_type, pages in statement_pages.items():
            if pages:
                print(f"  {stmt_type}: {pages}")
        
        # Extract statements
        statements = extractor.extract_statement(start_page, end_page)
        
        if not statements:
            print("\nNo financial statements found in specified page range.")
            print("Trying to extract from entire PDF...")
            statements = extractor.extract_statement(1, min(200, len(extractor.pdf.pages)))
        
        if statements:
            print(f"\nSuccessfully extracted {len(statements)} financial statement(s):")
            for stmt_type in statements:
                print(f"  - {stmt_type}")
            
            # Export to JSON
            base_name = os.path.splitext(os.path.basename(pdf_path))[0]
            json_path = f"{base_name}_financial_statements.json"
            extractor.export_to_json(statements, json_path)
            
            # Generate Markdown
            md_path = f"{base_name}_financial_statements.md"
            md_content = extractor.generate_markdown(statements)
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(md_content)
            print(f"Generated Markdown: {md_path}")
            
            # Print summary
            print("\n" + "="*60)
            print(md_content[:2000])  # Print first 2000 chars as preview
            if len(md_content) > 2000:
                print("... (truncated, see full file)")
            print("="*60)
            
        else:
            print("\nNo financial statements could be extracted.")
            print("Consider:")
            print("1. Adjusting page range (default is 140-160)")
            print("2. Checking if PDF is text-searchable (not scanned)")
            print("3. Using OCR if PDF is scanned")
            
    finally:
        extractor.close()


if __name__ == "__main__":
    main()