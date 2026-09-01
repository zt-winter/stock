#!/usr/bin/env python3
"""
Balance Sheet Extractor from PDF Annual Reports

基于位置感知提取（ColumnPage），精确对齐栏目与金额：
  1. 用 extract_text() 定位资产负债表所在页
  2. 用 ColumnPage（get_text('dict') + X/Y 聚类）提取结构化行列数据
  3. 自动分类为资产/负债/权益三大板块

支持PDF后端（按优先级）：PyMuPDF > pypdf > pdfminer.six

Usage:
    python3 extract_balance_sheet.py <pdf_path> [start_page] [end_page]

Example:
    python3 extract_balance_sheet.py 2024年报.pdf
    python3 extract_balance_sheet.py 2024年报.pdf 140 160
"""

import sys
import os
import re
import json
from typing import Dict, List, Optional, Tuple

# 导入兼容层
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from scripts.pdf_helper import open_pdf, get_pdf_backend, ColumnPage, ColumnRow
except ImportError:
    from pdf_helper import open_pdf, get_pdf_backend, ColumnPage, ColumnRow

# ── 关键词定义 ──────────────────────────────────────────────

BALANCE_SHEET_KEYWORDS = [
    "CONSOLIDATED STATEMENT OF FINANCIAL POSITION",
    "STATEMENT OF FINANCIAL POSITION", "BALANCE SHEET",
    "綜合財務狀況表", "财务状况表", "资产负债表",
    "簡明綜合財務狀況表",
]

# section 检测（标签 + 是否进入新 section）
SECTION_PATTERNS = [
    (r'非流動資產', "assets", "non_current"),
    (r'NON.CURRENT.ASSETS', "assets", "non_current"),
    (r'^流動資產', "assets", "current"),
    (r'^CURRENT.ASSETS', "assets", "current"),
    (r'^資產$', "assets", None),
    (r'^ASSETS$', "assets", None),
    (r'非流動負債', "liabilities", "non_current"),
    (r'NON.CURRENT.LIAB', "liabilities", "non_current"),
    (r'^流動負債', "liabilities", "current"),
    (r'^CURRENT.LIAB', "liabilities", "current"),
    (r'^負債$', "liabilities", None),
    (r'^LIABILITIES$', "liabilities", None),
    (r'^權益$', "equity", None),
    (r'^EQUITY$', "equity", None),
]

TOTAL_KEYWORDS = [
    "total", "總額", "總計", "合計",
    "權益及負債總額", "資產總額", "負債總額", "權益總額",
]

SKIP_LABELS = re.compile(
    r'^\d{1,3}$|未經審核|經審核|百萬元|千元|million|thousand|附註|附注',
    re.IGNORECASE,
)


class BalanceSheetExtractor:
    """Specialized extractor for balance sheets using position-aware extraction."""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.pdf = open_pdf(pdf_path)

    def find_balance_sheet_pages(self) -> List[int]:
        """Find pages containing balance sheet keywords."""
        pages = []
        for i, page in enumerate(self.pdf.pages):
            text = (page.extract_text() or "").upper()
            for kw in BALANCE_SHEET_KEYWORDS:
                if kw.upper() in text:
                    pages.append(i + 1)
                    break
        return pages

    def find_balance_sheet_end(self, start: int) -> int:
        """Find where balance sheet ends."""
        stop_keywords = [
            "STATEMENT OF PROFIT", "STATEMENT OF CASH",
            "STATEMENT OF CHANGES IN EQUITY",
            "損益表", "現金流量表", "利潤表", "權益變動表",
        ]
        for i in range(start, min(start + 15, len(self.pdf.pages))):
            text = (self.pdf.pages[i].extract_text() or "").upper()
            for stop_kw in stop_keywords:
                if stop_kw.upper() in text and i + 1 > start:
                    return i
        return min(start + 5, len(self.pdf.pages))

    def extract_column_rows(self, start: int, end: int) -> List[ColumnRow]:
        """Extract ColumnRow data from pages."""
        all_rows = []
        for pnum in range(start, end + 1):
            idx = pnum - 1
            if idx < 0 or idx >= len(self.pdf.pages):
                continue
            page = self.pdf.pages[idx]
            cp = ColumnPage(page)
            cp.detect_columns()
            if len(cp.col_boundaries) < 2:
                continue
            rows = cp.extract_rows(y_tolerance=5.0)
            all_rows.extend(rows)
        return all_rows

    @staticmethod
    def merge_continuation_labels(rows: List[ColumnRow]) -> List[ColumnRow]:
        """Merge multi-line labels and continuation rows.
        
        规则：
        - 标签 + 无标签有金额 → 合并金额
        - 标签 + 无标签无金额 → 合并文本（多行标签续行）
        - 标签 + 有标签 → 不合并（各自独立）
        """
        merged = []
        i = 0
        while i < len(rows):
            row = rows[i]
            label = row.label.strip()
            has_amount = any(c.strip() for c in row.cols)

            if label and not has_amount:
                new_cols = list(row.cols)
                new_label = label
                j = i + 1
                while j < len(rows):
                    next_row = rows[j]
                    next_label = next_row.label.strip()
                    next_has = any(c.strip() for c in next_row.cols)
                    if not next_label and next_has:
                        # 无标签有金额 → 合并金额，结束
                        new_cols = list(next_row.cols)
                        j += 1
                        break
                    elif not next_label and not next_has:
                        # 无标签无金额 → 跳过空行
                        j += 1
                        continue
                    else:
                        # 有标签 → 不合并，各自独立
                        break
                merged.append(ColumnRow(label=new_label, cols=new_cols, y=row.y))
                i = j
            else:
                merged.append(row)
                i += 1
        return merged

    def parse_balance_sheet(self, rows: List[ColumnRow]) -> Dict:
        """Parse ColumnRow list into structured balance sheet data."""
        bs = {
            "name": "Consolidated Balance Sheet",
            "as_at": "",
            "currency_unit": "RMB'000",
            "assets": {"non_current": [], "current": [], "total": {}},
            "liabilities": {"non_current": [], "current": [], "total": {}},
            "equity": [],
            "totals": {},
        }

        # 检测列映射
        ncols = max(len(r.cols) for r in rows) if rows else 3
        if ncols >= 3:
            note_idx = 0  # cols[0] = note
            cur_idx = ncols - 2  # cols[-2] = current
            prev_idx = ncols - 1  # cols[-1] = previous
        else:
            note_idx = -1
            cur_idx = 0
            prev_idx = 1

        # 检查样本行的附注列
        for r in rows[:8]:
            for ci, c in enumerate(r.cols):
                if "附註" in c or "附注" in c or "note" in c.lower():
                    note_idx = ci
                    break

        current_section = None
        current_sub = None

        for row in rows:
            label = row.label.strip()
            if not label:
                continue

            # 检查 section header
            matched_section = None
            for pattern, section, sub in SECTION_PATTERNS:
                if re.search(pattern, label, re.IGNORECASE):
                    matched_section = (section, sub)
                    break

            if matched_section:
                section, sub = matched_section
                current_section = section
                current_sub = sub
                # 如果是 "TOTAL" 类的 header（如"資產總額"），记录 total
                continue

            # 跳过非数据行
            if SKIP_LABELS.search(label):
                continue
            if re.match(r'.*(年|月|日|三十|三十一|六月|十二月)$', label):
                continue
            if any(kw in label for kw in ["簡明綜合", "CONSOLIDATED STATEMENT"]):
                continue

            # 提取金额
            note = row.cols[note_idx].strip() if 0 <= note_idx < len(row.cols) else ""
            amt_cur = row.cols[cur_idx].strip() if 0 <= cur_idx < len(row.cols) else ""
            amt_prev = row.cols[prev_idx].strip() if 0 <= prev_idx < len(row.cols) else ""
            amt_cur = amt_cur.replace("–", "-").replace("—", "-").replace(" ", "")
            amt_prev = amt_prev.replace("–", "-").replace("—", "-").replace(" ", "")

            if not amt_cur and not amt_prev:
                continue  # 无金额的行跳过

            # 跳过表头行（金额列包含非数字文本）
            def _is_numeric_or_empty(s: str) -> bool:
                if not s:
                    return True
                return bool(re.match(r'^[\d,\.\(\)\-\s]*$', s))
            if not _is_numeric_or_empty(amt_cur) or not _is_numeric_or_empty(amt_prev):
                continue

            is_total = any(k in label.lower() for k in TOTAL_KEYWORDS)
            item_data = {
                "label": label,
                "note": note,
                "amount_current": amt_cur,
                "amount_previous": amt_prev,
                "is_total": is_total,
            }

            # 分类
            if current_section == "assets":
                sub_key = current_sub or "non_current"
                bs["assets"][sub_key].append(item_data)
            elif current_section == "liabilities":
                sub_key = current_sub or "non_current"
                bs["liabilities"][sub_key].append(item_data)
            elif current_section == "equity":
                bs["equity"].append(item_data)

            # 记录总计
            if is_total:
                ll = label.lower()
                # “權益及負債總額” 不应覆盖 total_liabilities
                if ("資產" in label or "asset" in ll) and "負債" not in label:
                    bs["totals"]["total_assets"] = amt_cur
                elif ("負債" in label or "liabilit" in ll) and "權益" not in label:
                    bs["totals"]["total_liabilities"] = amt_cur
                elif ("權益" in label or "equity" in ll) and "負債" not in label:
                    bs["totals"]["total_equity"] = amt_cur

        return bs

    # ── 输出 ───────────────────────────────────────────────

    def export_to_json(self, data: Dict, output_path: str):
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Exported to {output_path}")

    def generate_markdown(self, data: Dict) -> str:
        if not data or not any(
            data.get("assets", {}).get(k) for k in ("non_current", "current")
        ):
            return "# No Balance Sheet Data Extracted\n"

        md = f"# {data['name']}\n\n"
        if data.get("as_at"):
            md += f"**As at**: {data['as_at']}  \n"
        md += f"**Currency Unit**: {data.get('currency_unit', 'RMB 000')}  \n\n"

        def _table_rows(items):
            lines = []
            for item in items:
                if item.get("is_total"):
                    lines.append(
                        f"| **{item['label']}** | {item.get('note','')} | "
                        f"**{item['amount_current']}** | **{item['amount_previous']}** |"
                    )
                else:
                    lines.append(
                        f"| {item['label']} | {item.get('note','')} | "
                        f"{item['amount_current']} | {item['amount_previous']} |"
                    )
            return '\n'.join(lines)

        header = "| Item | Note | Current | Previous |\n|------|------|---------|----------|\n"

        md += "## Assets\n\n### Non-current Assets\n" + header
        md += _table_rows(data["assets"].get("non_current", [])) + "\n\n"
        md += "### Current Assets\n" + header
        md += _table_rows(data["assets"].get("current", [])) + "\n\n"

        md += "## Liabilities\n\n### Non-current Liabilities\n" + header
        md += _table_rows(data["liabilities"].get("non_current", [])) + "\n\n"
        md += "### Current Liabilities\n" + header
        md += _table_rows(data["liabilities"].get("current", [])) + "\n\n"

        md += "## Equity\n" + header
        md += _table_rows(data.get("equity", [])) + "\n\n"

        # Summary
        totals = data.get("totals", {})
        if totals:
            md += "## Summary\n| Item | Amount |\n|------|--------|\n"
            for k, v in totals.items():
                md += f"| **{k.replace('_', ' ').title()}** | **{v}** |\n"
            md += "\n"

            ta = self._parse_amount(totals.get("total_assets", ""))
            tl = self._parse_amount(totals.get("total_liabilities", ""))
            if ta and tl and ta > 0:
                md += f"**Debt Ratio**: {tl/ta:.1%}\n"

        return md

    @staticmethod
    def _parse_amount(s: str) -> float:
        if not s:
            return 0.0
        clean = s.replace('(', '').replace(')', '').replace(',', '').replace(' ', '')
        try:
            val = float(clean)
            if '(' in s:
                val = -val
            return val
        except ValueError:
            return 0.0

    def close(self):
        self.pdf.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 extract_balance_sheet.py <pdf_path> [start_page] [end_page]")
        print(f"\nCurrent PDF backend: {get_pdf_backend()}")
        sys.exit(1)

    pdf_path = sys.argv[1]
    start_page = int(sys.argv[2]) if len(sys.argv) > 2 else None
    end_page = int(sys.argv[3]) if len(sys.argv) > 3 else None

    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found: {pdf_path}")
        sys.exit(1)

    print(f"Extracting balance sheet from {pdf_path}")
    print(f"PDF backend: {get_pdf_backend()}")

    extractor = BalanceSheetExtractor(pdf_path)
    try:
        # 定位资产负债表页面
        pages = extractor.find_balance_sheet_pages()
        if not pages:
            print("No balance sheet pages found. Trying default range 140-160...")
            start, end = 140, 160
        else:
            start = pages[0]
            end = extractor.find_balance_sheet_end(start)
            print(f"Balance sheet pages: {start}-{end}")

        # 提取
        raw_rows = extractor.extract_column_rows(start, end)
        if not raw_rows:
            print("No table data found on balance sheet pages.")
            sys.exit(1)

        # 合并多行标签
        merged = extractor.merge_continuation_labels(raw_rows)
        print(f"Extracted {len(merged)} data rows")

        # 解析
        bs = extractor.parse_balance_sheet(merged)

        n_assets = len(bs["assets"]["non_current"]) + len(bs["assets"]["current"])
        n_liab = len(bs["liabilities"]["non_current"]) + len(bs["liabilities"]["current"])
        n_equity = len(bs["equity"])
        print(f"  Non-current assets: {len(bs['assets']['non_current'])}")
        print(f"  Current assets: {len(bs['assets']['current'])}")
        print(f"  Non-current liabilities: {len(bs['liabilities']['non_current'])}")
        print(f"  Current liabilities: {len(bs['liabilities']['current'])}")
        print(f"  Equity: {n_equity}")

        # 输出
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        json_path = f"{base_name}_balance_sheet.json"
        extractor.export_to_json(bs, json_path)

        md_path = f"{base_name}_balance_sheet.md"
        md_content = extractor.generate_markdown(bs)
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        print(f"Generated Markdown: {md_path}")

        print("\n" + "=" * 70)
        print(md_content[:3000])
        if len(md_content) > 3000:
            print("... (truncated, see full file)")
        print("=" * 70)

    finally:
        extractor.close()


if __name__ == "__main__":
    main()
