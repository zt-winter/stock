#!/usr/bin/env python3
"""
Markdown Table Generator for Financial Statements

This script converts extracted financial statement data (JSON format) 
into standardized, well-formatted Markdown tables with proper sectioning,
headers, and formatting for all three financial statements.

Usage:
    python3 generate_markdown_tables.py <input_json> <output_md> [--company "Company Name"] [--year YYYY]

Example:
    python3 generate_markdown_tables.py financial_statements.json output.md --company "光大永年" --year 2024
    python3 generate_markdown_tables.py balance_sheet.json balance_sheet.md
"""

import json
import sys
import os
import argparse
from typing import Dict, List, Any
from datetime import datetime


class MarkdownFinancialTableGenerator:
    """Generates standardized Markdown tables from financial statement data."""
    
    def __init__(self, company_name: str = "", year: int = None):
        """Initialize generator with company name and year."""
        self.company_name = company_name
        self.year = year if year else datetime.now().year
        self.previous_year = self.year - 1
        
    def load_financial_data(self, json_path: str) -> Dict:
        """Load financial data from JSON file."""
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data
        except Exception as e:
            print(f"Error loading JSON file {json_path}: {e}")
            sys.exit(1)
    
    def detect_data_type(self, data: Dict) -> str:
        """Detect the type of financial data (full statements, balance sheet only, etc.)."""
        if "balance_sheet" in data and "income_statement" in data and "cash_flow" in data:
            return "full_statements"
        elif "balance_sheet" in data:
            return "balance_sheet_only"
        elif "income_statement" in data:
            return "income_statement_only"
        elif "cash_flow" in data:
            return "cash_flow_only"
        elif "assets" in data and "liabilities" in data:
            # This is likely a balance_sheet.json from extract_balance_sheet.py
            return "structured_balance_sheet"
        else:
            # Try to infer from keys
            keys = list(data.keys())
            if any("balance" in k.lower() for k in keys):
                return "balance_sheet_only"
            elif any("income" in k.lower() or "profit" in k.lower() for k in keys):
                return "income_statement_only"
            elif any("cash" in k.lower() for k in keys):
                return "cash_flow_only"
            else:
                return "unknown"
    
    def generate_full_statements_markdown(self, data: Dict) -> str:
        """Generate Markdown for all three financial statements."""
        md = f"# {self.company_name} Financial Statements - {self.year}\n\n"
        
        # Header information
        md += "**Report Period**: Year ended 31 December\n"
        md += f"**Currency Unit**: RMB'000 (unless otherwise stated)\n"
        md += f"**Data Source**: {self.year} Annual Report\n"
        md += f"**Generated**: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        
        md += "---\n\n"
        
        # Balance Sheet
        if "balance_sheet" in data:
            balance_sheet = data["balance_sheet"]
            md += self._generate_statement_markdown(balance_sheet, "Balance Sheet", "As at 31 December")
            md += "\n\n---\n\n"
        
        # Income Statement
        if "income_statement" in data:
            income_statement = data["income_statement"]
            md += self._generate_statement_markdown(income_statement, "Income Statement", "Year ended 31 December")
            md += "\n\n---\n\n"
        
        # Cash Flow Statement
        if "cash_flow" in data:
            cash_flow = data["cash_flow"]
            md += self._generate_statement_markdown(cash_flow, "Cash Flow Statement", "Year ended 31 December")
            md += "\n\n"
        
        # Financial Ratios and Analysis
        md += self._generate_financial_analysis(data)
        
        return md
    
    def generate_balance_sheet_markdown(self, data: Dict) -> str:
        """Generate Markdown specifically for balance sheet data."""
        if self._is_structured_balance_sheet(data):
            return self._generate_structured_balance_sheet_markdown(data)
        else:
            # Assume it's a statement object
            return self._generate_statement_markdown(data, "Balance Sheet", "As at 31 December")
    
    def _is_structured_balance_sheet(self, data: Dict) -> bool:
        """Check if data is in structured balance sheet format."""
        return "assets" in data and "liabilities" in data and "equity" in data
    
    def _generate_structured_balance_sheet_markdown(self, data: Dict) -> str:
        """Generate Markdown for structured balance sheet format."""
        md = f"# {self.company_name} - Consolidated Balance Sheet\n\n"
        
        # Header information
        if "name" in data:
            md += f"**{data['name']}**  \n"
        if "as_at" in data:
            md += f"**As at**: {data['as_at']} {self.year}  \n"
        else:
            md += f"**As at**: 31 December {self.year}  \n"
        
        if "currency_unit" in data:
            md += f"**Currency Unit**: {data['currency_unit']}  \n"
        else:
            md += "**Currency Unit**: RMB'000  \n"
        
        md += f"**Data Source**: {self.year} Annual Report  \n"
        md += f"**Generated**: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        
        md += "---\n\n"
        
        # Assets section
        md += "## Assets\n\n"
        
        # Non-current assets
        if "non_current" in data.get("assets", {}):
            md += "### Non-current Assets\n"
            md += "| Item | Note | Current Year | Previous Year |\n"
            md += "|------|------|--------------|---------------|\n"
            
            for item in data["assets"]["non_current"]:
                md += self._format_table_row(item)
            
            md += "\n"
        
        # Current assets
        if "current" in data.get("assets", {}):
            md += "### Current Assets\n"
            md += "| Item | Note | Current Year | Previous Year |\n"
            md += "|------|------|--------------|---------------|\n"
            
            for item in data["assets"]["current"]:
                md += self._format_table_row(item)
            
            md += "\n"
        
        # Liabilities section
        md += "## Liabilities\n\n"
        
        # Non-current liabilities
        if "non_current" in data.get("liabilities", {}):
            md += "### Non-current Liabilities\n"
            md += "| Item | Note | Current Year | Previous Year |\n"
            md += "|------|------|--------------|---------------|\n"
            
            for item in data["liabilities"]["non_current"]:
                md += self._format_table_row(item)
            
            md += "\n"
        
        # Current liabilities
        if "current" in data.get("liabilities", {}):
            md += "### Current Liabilities\n"
            md += "| Item | Note | Current Year | Previous Year |\n"
            md += "|------|------|--------------|---------------|\n"
            
            for item in data["liabilities"]["current"]:
                md += self._format_table_row(item)
            
            md += "\n"
        
        # Equity section
        md += "## Equity\n\n"
        md += "| Item | Note | Current Year | Previous Year |\n"
        md += "|------|------|--------------|---------------|\n"
        
        for item in data.get("equity", []):
            md += self._format_table_row(item)
        
        md += "\n"
        
        # Summary totals
        if "totals" in data and data["totals"]:
            md += "## Summary\n\n"
            md += "| Item | Current Year |\n"
            md += "|------|--------------|\n"
            
            totals = data["totals"]
            if "total_assets" in totals:
                md += f"| **Total Assets** | **{totals['total_assets']}** |\n"
            if "total_liabilities" in totals:
                md += f"| **Total Liabilities** | **{totals['total_liabilities']}** |\n"
            if "total_equity" in totals:
                md += f"| **Total Equity** | **{totals['total_equity']}** |\n"
            
            md += "\n"
        
        # Financial ratios
        md += self._generate_balance_sheet_ratios(data)
        
        return md
    
    def _generate_statement_markdown(self, statement: Dict, title: str, period: str) -> str:
        """Generate Markdown for a single financial statement."""
        md = f"## {title}\n\n"
        md += f"**{period}**  \n"
        
        if "currency_unit" in statement:
            md += f"**Currency Unit**: {statement['currency_unit']}  \n"
        
        md += "\n"
        
        # Create table
        md += "| Item | Note | Current Year | Previous Year |\n"
        md += "|------|------|--------------|---------------|\n"
        
        if "items" in statement:
            for item in statement["items"]:
                md += self._format_table_row(item)
        else:
            # Try to find items in other keys
            for key, value in statement.items():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and "label" in item:
                            md += self._format_table_row(item)
        
        return md
    
    def _format_table_row(self, item: Dict) -> str:
        """Format a single table row from item data."""
        label = item.get("label", "")
        note = item.get("note", "")
        amount_current = item.get("amount_current", "")
        amount_previous = item.get("amount_previous", "")
        
        # Check if this is a header or total
        is_header = item.get("is_header", False)
        is_total = item.get("is_total", False)
        
        if is_header:
            return f"| **{label}** | | | |\n"
        elif is_total:
            return f"| **{label}** | {note} | **{amount_current}** | **{amount_previous}** |\n"
        else:
            return f"| {label} | {note} | {amount_current} | {amount_previous} |\n"
    
    def _generate_financial_analysis(self, data: Dict) -> str:
        """Generate financial ratios and analysis section."""
        md = "## Financial Analysis\n\n"
        
        # Try to extract key numbers for ratios
        ratios = []
        
        # Extract from balance sheet
        balance_sheet = data.get("balance_sheet", {})
        if "items" in balance_sheet:
            for item in balance_sheet["items"]:
                label = item.get("label", "").lower()
                amount = item.get("amount_current", "")
                
                if "total assets" in label or "資產總值" in label:
                    ratios.append(("Total Assets", amount))
                elif "total liabilities" in label or "負債總額" in label:
                    ratios.append(("Total Liabilities", amount))
                elif "total equity" in label or "權益總額" in label:
                    ratios.append(("Total Equity", amount))
                elif "cash" in label and "bank" in label:
                    ratios.append(("Cash & Bank Balances", amount))
        
        # Extract from income statement
        income_statement = data.get("income_statement", {})
        if "items" in income_statement:
            for item in income_statement["items"]:
                label = item.get("label", "").lower()
                amount = item.get("amount_current", "")
                
                if "revenue" in label or "收益" in label:
                    ratios.append(("Revenue", amount))
                elif "net profit" in label or "淨利潤" in label or "年內利潤" in label:
                    ratios.append(("Net Profit", amount))
                elif "profit before tax" in label or "稅前利潤" in label:
                    ratios.append(("Profit Before Tax", amount))
        
        if ratios:
            md += "### Key Financial Metrics\n\n"
            md += "| Metric | Amount |\n"
            md += "|--------|--------|\n"
            
            for name, amount in ratios:
                if amount:
                    md += f"| {name} | {amount} |\n"
            
            md += "\n"
        
        md += "### Key Ratios\n\n"
        md += "- **Debt Ratio**: Total Liabilities / Total Assets\n"
        md += "- **Return on Equity (ROE)**: Net Profit / Total Equity\n"
        md += "- **Profit Margin**: Net Profit / Revenue\n"
        md += "- **Current Ratio**: Current Assets / Current Liabilities\n"
        md += "- **Cash Ratio**: Cash & Bank Balances / Current Liabilities\n\n"
        
        md += "*Note: Calculate specific ratios using extracted amounts above.*\n"
        
        return md
    
    def _generate_balance_sheet_ratios(self, data: Dict) -> str:
        """Generate ratios specifically for balance sheet data."""
        md = "## Financial Ratios\n\n"
        
        # Extract totals
        totals = data.get("totals", {})
        total_assets = totals.get("total_assets", "")
        total_liabilities = totals.get("total_liabilities", "")
        total_equity = totals.get("total_equity", "")
        
        # Extract cash from current assets
        cash_amount = ""
        for item in data.get("assets", {}).get("current", []):
            label = item.get("label", "").lower()
            if "cash" in label or "現金" in label:
                cash_amount = item.get("amount_current", "")
                break
        
        # Extract current assets and liabilities totals
        current_assets_total = ""
        current_liabilities_total = ""
        
        # Look for totals in current sections
        for item in data.get("assets", {}).get("current", []):
            if item.get("is_total", False):
                current_assets_total = item.get("amount_current", "")
                break
        
        for item in data.get("liabilities", {}).get("current", []):
            if item.get("is_total", False):
                current_liabilities_total = item.get("amount_current", "")
                break
        
        md += "| Ratio | Formula | Calculation |\n"
        md += "|-------|---------|-------------|\n"
        
        # Debt Ratio
        if total_assets and total_liabilities:
            try:
                assets_val = self._parse_amount(total_assets)
                liabilities_val = self._parse_amount(total_liabilities)
                if assets_val > 0:
                    debt_ratio = liabilities_val / assets_val
                    md += f"| Debt Ratio | Total Liabilities / Total Assets | {debt_ratio:.1%} |\n"
            except:
                md += f"| Debt Ratio | Total Liabilities / Total Assets | Requires numeric values |\n"
        else:
            md += f"| Debt Ratio | Total Liabilities / Total Assets | Data missing |\n"
        
        # Current Ratio
        if current_assets_total and current_liabilities_total:
            try:
                ca_val = self._parse_amount(current_assets_total)
                cl_val = self._parse_amount(current_liabilities_total)
                if cl_val > 0:
                    current_ratio = ca_val / cl_val
                    md += f"| Current Ratio | Current Assets / Current Liabilities | {current_ratio:.2f} |\n"
            except:
                md += f"| Current Ratio | Current Assets / Current Liabilities | Requires numeric values |\n"
        else:
            md += f"| Current Ratio | Current Assets / Current Liabilities | Data missing |\n"
        
        # Cash Ratio
        if cash_amount and current_liabilities_total:
            try:
                cash_val = self._parse_amount(cash_amount)
                cl_val = self._parse_amount(current_liabilities_total)
                if cl_val > 0:
                    cash_ratio = cash_val / cl_val
                    md += f"| Cash Ratio | Cash / Current Liabilities | {cash_ratio:.2f} |\n"
            except:
                md += f"| Cash Ratio | Cash / Current Liabilities | Requires numeric values |\n"
        elif cash_amount:
            md += f"| Cash Ratio | Cash / Current Liabilities | Current liabilities data missing |\n"
        else:
            md += f"| Cash Ratio | Cash / Current Liabilities | Cash data missing |\n"
        
        md += "\n"
        
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


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Generate Markdown tables from financial JSON data')
    parser.add_argument('input_json', help='Input JSON file with financial data')
    parser.add_argument('output_md', help='Output Markdown file')
    parser.add_argument('--company', '-c', default='', help='Company name for header')
    parser.add_argument('--year', '-y', type=int, default=datetime.now().year, help='Financial year')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input_json):
        print(f"Error: Input file not found: {args.input_json}")
        sys.exit(1)
    
    print(f"Generating Markdown tables from {args.input_json}")
    
    # Initialize generator
    generator = MarkdownFinancialTableGenerator(company_name=args.company, year=args.year)
    
    # Load data
    data = generator.load_financial_data(args.input_json)
    
    # Detect data type and generate appropriate Markdown
    data_type = generator.detect_data_type(data)
    
    print(f"Detected data type: {data_type}")
    
    if data_type == "full_statements":
        markdown = generator.generate_full_statements_markdown(data)
    elif data_type == "balance_sheet_only" or data_type == "structured_balance_sheet":
        markdown = generator.generate_balance_sheet_markdown(data)
    elif data_type == "income_statement_only":
        # For now, use generic statement generation
        if isinstance(data, dict) and "items" in data:
            markdown = generator._generate_statement_markdown(data, "Income Statement", "Year ended 31 December")
        else:
            markdown = "# Income Statement\n\nData format not recognized.\n"
    elif data_type == "cash_flow_only":
        if isinstance(data, dict) and "items" in data:
            markdown = generator._generate_statement_markdown(data, "Cash Flow Statement", "Year ended 31 December")
        else:
            markdown = "# Cash Flow Statement\n\nData format not recognized.\n"
    else:
        # Try generic approach
        markdown = "# Financial Statements\n\n"
        markdown += "**Note**: Data format not fully recognized. Displaying raw structure:\n\n"
        markdown += "```json\n"
        markdown += json.dumps(data, indent=2, ensure_ascii=False)[:1000]
        if len(json.dumps(data, indent=2, ensure_ascii=False)) > 1000:
            markdown += "\n... (truncated)"
        markdown += "\n```\n"
    
    # Write output
    with open(args.output_md, 'w', encoding='utf-8') as f:
        f.write(markdown)
    
    print(f"Successfully generated Markdown: {args.output_md}")
    
    # Print preview
    print("\n" + "="*60)
    print("Preview (first 1000 characters):")
    print("="*60)
    print(markdown[:1000])
    if len(markdown) > 1000:
        print("... (truncated, see full file)")
    print("="*60)


if __name__ == "__main__":
    main()