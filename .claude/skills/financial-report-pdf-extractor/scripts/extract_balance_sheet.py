#!/usr/bin/env python3
"""
Balance Sheet Extractor from PDF Annual Reports

Specialized script for extracting balance sheet (statement of financial position) 
from corporate annual report PDFs. This script focuses specifically on balance sheet
extraction with enhanced parsing for assets, liabilities, and equity sections.

Usage:
    python3 extract_balance_sheet.py <pdf_path> [start_page] [end_page]

Example:
    python3 extract_balance_sheet.py 2024年报.pdf
    python3 extract_balance_sheet.py 2024年报.pdf 140 160
"""

import pdfplumber
import re
import sys
import json
from typing import Dict, List, Optional
import os


class BalanceSheetExtractor:
    """Specialized extractor for balance sheets from PDFs."""
    
    # Balance sheet keywords (English and Chinese)
    BALANCE_SHEET_KEYWORDS = [
        "CONSOLIDATED STATEMENT OF FINANCIAL POSITION",
        "綜合財務狀況表",
        "STATEMENT OF FINANCIAL POSITION",
        "BALANCE SHEET",
        "財務狀況表"
    ]
    
    # Common balance sheet section headers
    ASSET_SECTIONS = [
        "NON-CURRENT ASSETS",
        "CURRENT ASSETS",
        "非流動資產",
        "流動資產",
        "ASSETS"
    ]
    
    LIABILITY_SECTIONS = [
        "NON-CURRENT LIABILITIES",
        "CURRENT LIABILITIES",
        "EQUITY",
        "非流動負債",
        "流動負債",
        "權益",
        "LIABILITIES"
    ]
    
    # Common balance sheet items
    ASSET_ITEMS = [
        "INVESTMENT PROPERTIES",
        "PROPERTY, PLANT AND EQUIPMENT",
        "RIGHT-OF-USE ASSETS",
        "TRADE AND OTHER RECEIVABLES",
        "CASH AND BANK BALANCES",
        "投資物業",
        "物業、廠房及設備",
        "使用權資產",
        "貿易及其他應收款項",
        "現金及銀行結餘"
    ]
    
    LIABILITY_ITEMS = [
        "TRADE AND OTHER PAYABLES",
        "CONTRACT LIABILITIES",
        "LEASE LIABILITIES",
        "CURRENT TAX",
        "DEFERRED TAX LIABILITIES",
        "貿易及其他應付款項",
        "合約負債",
        "租賃負債",
        "即期稅項",
        "遞延稅項負債"
    ]
    
    EQUITY_ITEMS = [
        "SHARE CAPITAL",
        "RESERVES",
        "TOTAL EQUITY",
        "股本",
        "儲備",
        "權益總額"
    ]
    
    def __init__(self, pdf_path: str):
        """Initialize with PDF path."""
        self.pdf_path = pdf_path
        self.pdf = pdfplumber.open(pdf_path)
        
    def find_balance_sheet_pages(self) -> List[int]:
        """Find page numbers containing balance sheet."""
        balance_sheet_pages = []
        
        for i, page in enumerate(self.pdf.pages):
            try:
                page_text = page.extract_text()
                if not page_text:
                    continue
                
                # Check for balance sheet keywords
                for keyword in self.BALANCE_SHEET_KEYWORDS:
                    if keyword in page_text:
                        balance_sheet_pages.append(i + 1)
                        break
                        
            except Exception as e:
                print(f"Error processing page {i+1}: {e}")
                
        return balance_sheet_pages
    
    def extract_balance_sheet_text(self, start_page: int = None, end_page: int = None) -> str:
        """Extract balance sheet text from PDF."""
        if start_page is None or end_page is None:
            # Auto-detect pages if not specified
            pages = self.find_balance_sheet_pages()
            if not pages:
                print("No balance sheet found in PDF")
                return ""
            
            start_page = min(pages)
            end_page = max(pages) + 5  # Include a few extra pages
        
        text = ""
        for i in range(start_page - 1, min(end_page, len(self.pdf.pages))):
            try:
                page_text = self.pdf.pages[i].extract_text()
                text += f"\n=== Page {i+1} ===\n{page_text}\n"
            except Exception as e:
                print(f"Error extracting page {i+1}: {e}")
        
        return text
    
    def parse_balance_sheet(self, text: str) -> Dict:
        """Parse balance sheet text into structured data."""
        lines = text.split('\n')
        cleaned_lines = []
        
        # Clean lines
        for line in lines:
            line = line.strip()
            if line and not line.startswith('==='):
                cleaned_lines.append(line)
        
        # Find balance sheet section
        balance_sheet_start = -1
        for i, line in enumerate(cleaned_lines):
            for keyword in self.BALANCE_SHEET_KEYWORDS:
                if keyword in line:
                    balance_sheet_start = i
                    break
            if balance_sheet_start != -1:
                break
        
        if balance_sheet_start == -1:
            print("Balance sheet not found in extracted text")
            return {}
        
        # Extract balance sheet lines
        balance_sheet_lines = cleaned_lines[balance_sheet_start:]
        
        # Parse structured data
        balance_sheet = {
            "name": "Consolidated Balance Sheet",
            "as_at": "31 December",
            "currency_unit": "RMB'000",
            "assets": {"non_current": [], "current": [], "total": {}},
            "liabilities": {"non_current": [], "current": [], "total": {}},
            "equity": [],
            "totals": {}
        }
        
        current_section = None
        current_subsection = None
        
        for line in balance_sheet_lines:
            # Skip if it's just a page marker or header
            if not line or line.startswith('Page'):
                continue
            
            # Check for section headers
            if any(section in line for section in self.ASSET_SECTIONS):
                if "NON-CURRENT" in line or "非流動" in line:
                    current_section = "assets"
                    current_subsection = "non_current"
                elif "CURRENT" in line or "流動" in line:
                    current_section = "assets"
                    current_subsection = "current"
                else:
                    current_section = "assets"
                    current_subsection = None
                continue
            
            if any(section in line for section in self.LIABILITY_SECTIONS):
                if "NON-CURRENT" in line or "非流動" in line:
                    current_section = "liabilities"
                    current_subsection = "non_current"
                elif "CURRENT" in line or "流動" in line:
                    current_section = "liabilities"
                    current_subsection = "current"
                elif "EQUITY" in line or "權益" in line:
                    current_section = "equity"
                    current_subsection = None
                else:
                    current_section = "liabilities"
                    current_subsection = None
                continue
            
            # Parse financial line
            parsed_item = self._parse_financial_line(line)
            if not parsed_item:
                continue
            
            item_data = {
                "label": parsed_item["label"],
                "note": parsed_item["note"],
                "amount_current": parsed_item["amount_current"],
                "amount_previous": parsed_item["amount_previous"]
            }
            
            # Categorize item
            if current_section == "assets":
                if current_subsection == "non_current":
                    balance_sheet["assets"]["non_current"].append(item_data)
                elif current_subsection == "current":
                    balance_sheet["assets"]["current"].append(item_data)
                else:
                    balance_sheet["assets"]["non_current"].append(item_data)
            
            elif current_section == "liabilities":
                if current_subsection == "non_current":
                    balance_sheet["liabilities"]["non_current"].append(item_data)
                elif current_subsection == "current":
                    balance_sheet["liabilities"]["current"].append(item_data)
                else:
                    balance_sheet["liabilities"]["non_current"].append(item_data)
            
            elif current_section == "equity":
                balance_sheet["equity"].append(item_data)
            
            # Check for totals
            if parsed_item["is_total"]:
                total_label = parsed_item["label"].lower()
                if "total assets" in total_label or "資產總值" in total_label:
                    balance_sheet["totals"]["total_assets"] = parsed_item["amount_current"]
                elif "total liabilities" in total_label or "負債總額" in total_label:
                    balance_sheet["totals"]["total_liabilities"] = parsed_item["amount_current"]
                elif "total equity" in total_label or "權益總額" in total_label:
                    balance_sheet["totals"]["total_equity"] = parsed_item["amount_current"]
        
        return balance_sheet
    
    def _parse_financial_line(self, line: str) -> Optional[Dict]:
        """Parse a single line of financial text."""
        # Skip empty lines
        if not line:
            return None
        
        # Check if this is a header
        is_header = line.isupper() or line.startswith('**')
        is_total = 'total' in line.lower() or 'TOTAL' in line or '合计' in line or '總計' in line
        
        # Extract note reference
        note = ""
        note_match = re.match(r'^\s*(\d+[a-z]?)\s+', line)
        if note_match:
            note = note_match.group(1)
            line = line[note_match.end():].strip()
        
        # Extract amounts (accounting format)
        amount_pattern = r'\(?\d{1,3}(?:,\d{3})*\)?'
        amounts = re.findall(amount_pattern, line)
        
        if len(amounts) >= 2:
            amount_current = amounts[0]
            amount_previous = amounts[1]
            # Remove amounts from label
            label = line
            for amount in amounts:
                label = label.replace(amount, '', 1)
            label = label.strip()
            label = re.sub(r'[:\-\.\s]+$', '', label)
        
        elif len(amounts) == 1:
            amount_current = amounts[0]
            amount_previous = ""
            label = line.replace(amount_current, '').strip()
            label = re.sub(r'[:\-\.\s]+$', '', label)
        
        else:
            # No amounts, could be descriptive
            amount_current = ""
            amount_previous = ""
            label = line.strip()
        
        # Skip very short labels unless they're headers or totals
        if len(label) < 2 and not is_header and not is_total:
            return None
        
        return {
            "label": label,
            "note": note,
            "amount_current": amount_current,
            "amount_previous": amount_previous,
            "is_header": is_header,
            "is_total": is_total
        }
    
    def export_to_json(self, balance_sheet: Dict, output_path: str):
        """Export balance sheet to JSON."""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(balance_sheet, f, ensure_ascii=False, indent=2)
        print(f"Exported balance sheet to {output_path}")
    
    def generate_markdown(self, balance_sheet: Dict) -> str:
        """Generate Markdown representation of balance sheet."""
        if not balance_sheet:
            return "# No Balance Sheet Data Extracted\n"
        
        md = f"# {balance_sheet['name']}\n\n"
        md += f"**As at**: {balance_sheet['as_at']}  \n"
        md += f"**Currency Unit**: {balance_sheet['currency_unit']}  \n\n"
        
        # Assets section
        md += "## Assets\n\n"
        
        md += "### Non-current Assets\n"
        md += "| Item | Note | Current Year | Previous Year |\n"
        md += "|------|------|--------------|---------------|\n"
        for item in balance_sheet["assets"]["non_current"]:
            if item.get("is_header", False):
                md += f"| **{item['label']}** | | | |\n"
            elif item.get("is_total", False):
                md += f"| **{item['label']}** | {item['note']} | **{item['amount_current']}** | **{item['amount_previous']}** |\n"
            else:
                md += f"| {item['label']} | {item['note']} | {item['amount_current']} | {item['amount_previous']} |\n"
        md += "\n"
        
        md += "### Current Assets\n"
        md += "| Item | Note | Current Year | Previous Year |\n"
        md += "|------|------|--------------|---------------|\n"
        for item in balance_sheet["assets"]["current"]:
            if item.get("is_header", False):
                md += f"| **{item['label']}** | | | |\n"
            elif item.get("is_total", False):
                md += f"| **{item['label']}** | {item['note']} | **{item['amount_current']}** | **{item['amount_previous']}** |\n"
            else:
                md += f"| {item['label']} | {item['note']} | {item['amount_current']} | {item['amount_previous']} |\n"
        md += "\n"
        
        # Liabilities section
        md += "## Liabilities\n\n"
        
        md += "### Non-current Liabilities\n"
        md += "| Item | Note | Current Year | Previous Year |\n"
        md += "|------|------|--------------|---------------|\n"
        for item in balance_sheet["liabilities"]["non_current"]:
            if item.get("is_header", False):
                md += f"| **{item['label']}** | | | |\n"
            elif item.get("is_total", False):
                md += f"| **{item['label']}** | {item['note']} | **{item['amount_current']}** | **{item['amount_previous']}** |\n"
            else:
                md += f"| {item['label']} | {item['note']} | {item['amount_current']} | {item['amount_previous']} |\n"
        md += "\n"
        
        md += "### Current Liabilities\n"
        md += "| Item | Note | Current Year | Previous Year |\n"
        md += "|------|------|--------------|---------------|\n"
        for item in balance_sheet["liabilities"]["current"]:
            if item.get("is_header", False):
                md += f"| **{item['label']}** | | | |\n"
            elif item.get("is_total", False):
                md += f"| **{item['label']}** | {item['note']} | **{item['amount_current']}** | **{item['amount_previous']}** |\n"
            else:
                md += f"| {item['label']} | {item['note']} | {item['amount_current']} | {item['amount_previous']} |\n"
        md += "\n"
        
        # Equity section
        md += "## Equity\n\n"
        md += "| Item | Note | Current Year | Previous Year |\n"
        md += "|------|------|--------------|---------------|\n"
        for item in balance_sheet["equity"]:
            if item.get("is_header", False):
                md += f"| **{item['label']}** | | | |\n"
            elif item.get("is_total", False):
                md += f"| **{item['label']}** | {item['note']} | **{item['amount_current']}** | **{item['amount_previous']}** |\n"
            else:
                md += f"| {item['label']} | {item['note']} | {item['amount_current']} | {item['amount_previous']} |\n"
        md += "\n"
        
        # Totals section
        md += "## Summary Totals\n\n"
        md += "| Item | Current Year | Previous Year |\n"
        md += "|------|--------------|---------------|\n"
        if "total_assets" in balance_sheet["totals"]:
            md += f"| **Total Assets** | **{balance_sheet['totals']['total_assets']}** | |\n"
        if "total_liabilities" in balance_sheet["totals"]:
            md += f"| **Total Liabilities** | **{balance_sheet['totals']['total_liabilities']}** | |\n"
        if "total_equity" in balance_sheet["totals"]:
            md += f"| **Total Equity** | **{balance_sheet['totals']['total_equity']}** | |\n"
        
        # Add financial ratios if we have totals
        if "total_assets" in balance_sheet["totals"] and "total_liabilities" in balance_sheet["totals"]:
            try:
                total_assets = self._parse_amount(balance_sheet["totals"]["total_assets"])
                total_liabilities = self._parse_amount(balance_sheet["totals"]["total_liabilities"])
                if total_assets > 0:
                    debt_ratio = total_liabilities / total_assets
                    md += f"\n**Debt Ratio**: {debt_ratio:.1%}\n"
            except:
                pass
        
        return md
    
    def _parse_amount(self, amount_str: str) -> float:
        """Parse accounting amount string to float."""
        if not amount_str:
            return 0.0
        
        # Remove parentheses (negative) and commas
        clean_str = amount_str.replace('(', '').replace(')', '').replace(',', '')
        
        try:
            return float(clean_str)
        except:
            return 0.0
    
    def close(self):
        """Close the PDF file."""
        self.pdf.close()


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python3 extract_balance_sheet.py <pdf_path> [start_page] [end_page]")
        print("Example: python3 extract_balance_sheet.py 2024年报.pdf")
        print("         python3 extract_balance_sheet.py 2024年报.pdf 140 160")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    start_page = int(sys.argv[2]) if len(sys.argv) > 2 else None
    end_page = int(sys.argv[3]) if len(sys.argv) > 3 else None
    
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found: {pdf_path}")
        sys.exit(1)
    
    print(f"Extracting balance sheet from {pdf_path}")
    
    extractor = BalanceSheetExtractor(pdf_path)
    
    try:
        # Auto-detect pages if not specified
        if start_page is None or end_page is None:
            pages = extractor.find_balance_sheet_pages()
            if not pages:
                print("No balance sheet found in PDF. Trying default range 140-160...")
                start_page = 140
                end_page = 160
            else:
                start_page = min(pages)
                end_page = max(pages) + 3  # Include a few extra pages
                print(f"Auto-detected balance sheet pages: {start_page}-{end_page}")
        
        # Extract text
        text = extractor.extract_balance_sheet_text(start_page, end_page)
        
        if not text:
            print("No text extracted from PDF")
            sys.exit(1)
        
        # Parse balance sheet
        balance_sheet = extractor.parse_balance_sheet(text)
        
        if not balance_sheet:
            print("Failed to parse balance sheet from extracted text")
            sys.exit(1)
        
        print(f"\nSuccessfully extracted balance sheet data")
        
        # Export to JSON
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        json_path = f"{base_name}_balance_sheet.json"
        extractor.export_to_json(balance_sheet, json_path)
        
        # Generate Markdown
        md_path = f"{base_name}_balance_sheet.md"
        md_content = extractor.generate_markdown(balance_sheet)
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        print(f"Generated Markdown: {md_path}")
        
        # Print summary
        print("\n" + "="*60)
        print(md_content[:1500])  # Print first 1500 chars as preview
        if len(md_content) > 1500:
            print("... (truncated, see full file)")
        print("="*60)
        
    finally:
        extractor.close()


if __name__ == "__main__":
    main()