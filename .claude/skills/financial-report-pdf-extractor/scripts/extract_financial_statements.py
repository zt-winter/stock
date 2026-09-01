#!/usr/bin/env python3
"""
Financial Statement Extractor from PDF Annual Reports

基于位置感知提取（ColumnPage），精确对齐栏目与金额：
  1. 用 extract_text() 定位报表所在页
  2. 用 ColumnPage（get_text('dict') + X/Y 聚类）提取结构化行列数据
  3. 支持多行标签合并、跨页表格合并

支持PDF后端（按优先级）：PyMuPDF > pypdf > pdfminer.six

Usage:
    python3 extract_financial_statements.py <pdf_path> [start_page] [end_page]

Example:
    python3 extract_financial_statements.py 2024年报.pdf 141 160
"""

import sys
import os
import re
import json
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict, field

# 导入兼容层
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from scripts.pdf_helper import open_pdf, get_pdf_backend, ColumnPage
except ImportError:
    from pdf_helper import open_pdf, get_pdf_backend, ColumnPage


@dataclass
class FinancialItem:
    """Represents a single financial statement item."""
    label: str
    note: str = ""
    amount_current: str = ""
    amount_previous: str = ""
    is_header: bool = False
    is_total: bool = False


@dataclass
class FinancialStatement:
    """Represents a complete financial statement."""
    name: str
    period: str
    items: List[FinancialItem]
    currency_unit: str = "RMB'000"
    page_range: str = ""


# ── 关键词定义 ──────────────────────────────────────────────

STATEMENT_KEYWORDS = {
    "balance_sheet": {
        "keywords": [
            "CONSOLIDATED STATEMENT OF FINANCIAL POSITION",
            "STATEMENT OF FINANCIAL POSITION", "BALANCE SHEET",
            "綜合財務狀況表", "财务状况表", "资产负债表",
            "簡明綜合財務狀況表",
        ],
        "stop_keywords": [
            "STATEMENT OF PROFIT", "STATEMENT OF CASH",
            "損益表", "現金流量表", "利潤表", "權益變動表",
        ],
    },
    "income_statement": {
        "keywords": [
            "CONSOLIDATED STATEMENT OF PROFIT OR LOSS",
            "STATEMENT OF PROFIT OR LOSS", "INCOME STATEMENT",
            "綜合損益表", "综合损益表", "利润表",
            "簡明綜合損益表",
        ],
        "stop_keywords": [
            "STATEMENT OF FINANCIAL", "STATEMENT OF CASH",
            "財務狀況表", "現金流量表", "權益變動表",
        ],
    },
    "cash_flow": {
        "keywords": [
            "CONSOLIDATED STATEMENT OF CASH FLOWS",
            "STATEMENT OF CASH FLOWS", "CASH FLOW STATEMENT",
            "綜合現金流量表", "现金流量表", "現金流量表",
            "簡明綜合現金流量表",
        ],
        "stop_keywords": [
            "STATEMENT OF FINANCIAL", "STATEMENT OF PROFIT",
            "財務狀況表", "損益表", "權益變動表",
        ],
    },
}

HEADER_RE = re.compile(
    r'^(非流動資產|NON.CURRENT.ASSETS|流動資產|CURRENT.ASSETS'
    r'|資產$|ASSETS$|非流動負債|NON.CURRENT.LIAB'
    r'|流動負債|CURRENT.LIAB|負債$|LIABILITIES$'
    r'|權益$|EQUITY$)',
    re.IGNORECASE,
)

TOTAL_KEYWORDS = [
    "total", "總額", "總計", "合計", "净额", "淨額",
    "權益及負債總額", "資產總額", "負債總額",
]


class FinancialPDFExtractor:
    """Main class for extracting financial statements from PDFs."""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.pdf = open_pdf(pdf_path)

    # ── 页面定位 ────────────────────────────────────────────

    def find_statement_pages(self) -> Dict[str, int]:
        """Find the first page number for each statement type."""
        result: Dict[str, int] = {}
        for i, page in enumerate(self.pdf.pages):
            text = (page.extract_text() or "").upper()
            for stmt_type, cfg in STATEMENT_KEYWORDS.items():
                if stmt_type in result:
                    continue
                for kw in cfg["keywords"]:
                    if kw.upper() in text:
                        result[stmt_type] = i + 1
                        break
        return result

    def find_statement_end(self, stmt_type: str, start: int) -> int:
        """Find where a statement ends (next statement starts or notes begin)."""
        cfg = STATEMENT_KEYWORDS[stmt_type]
        for i in range(start, min(start + 20, len(self.pdf.pages))):
            text = (self.pdf.pages[i].extract_text() or "").upper()
            for stop_kw in cfg["stop_keywords"]:
                if stop_kw.upper() in text and i + 1 > start:
                    return i  # stop keywords found on this page → previous page was end
            # 检查是否进入附注
            if "中期財務資料附註" in text or "NOTES TO" in text:
                return i
        return min(start + 10, len(self.pdf.pages))

    # ── 位置感知行提取 ───────────────────────────────────────

    def extract_column_rows(self, start: int, end: int) -> List[Tuple[int, 'ColumnRow']]:
        """
        从 start 到 end 页提取 ColumnRow 数据。
        返回 [(page_num, ColumnRow), ...]
        """
        all_rows = []
        for pnum in range(start, end + 1):
            idx = pnum - 1
            if idx < 0 or idx >= len(self.pdf.pages):
                continue
            page = self.pdf.pages[idx]
            cp = ColumnPage(page)
            cp.detect_columns()
            if len(cp.col_boundaries) < 2:
                continue  # 不够列
            rows = cp.extract_rows(y_tolerance=5.0)
            for r in rows:
                all_rows.append((pnum, r))
        return all_rows

    @staticmethod
    def merge_continuation_labels(rows: List[Tuple[int, 'ColumnRow']]) -> List[Tuple[int, 'ColumnRow']]:
        """
        合并多行标签：若一行有标签但无金额，下一行无标签但有金额，
        则将金额合并到上一行。不合并各自独立的标签行。
        """
        merged = []
        i = 0
        while i < len(rows):
            pnum, row = rows[i]
            label = row.label.strip()
            has_amount = any(c.strip() for c in row.cols)

            if label and not has_amount:
                new_cols = list(row.cols)
                j = i + 1
                while j < len(rows):
                    _, next_row = rows[j]
                    next_label = next_row.label.strip()
                    next_has = any(c.strip() for c in next_row.cols)
                    if not next_label and next_has:
                        new_cols = list(next_row.cols)
                        j += 1
                        break
                    elif not next_label and not next_has:
                        j += 1
                        continue
                    else:
                        # 有标签的行 → 不合并
                        break
                from pdf_helper import ColumnRow as CR
                merged.append((pnum, CR(label=label, cols=new_cols, y=row.y)))
                i = j
            else:
                merged.append(rows[i])
                i += 1
        return merged

    # ── 解析为 FinancialItem ────────────────────────────────

    @staticmethod
    def detect_column_mapping(ncols: int, sample_rows) -> Dict[str, int]:
        """根据样本行推断列映射。"""
        mapping = {"label": 0, "note": -1, "current": -1, "previous": -1}
        # 默认布局：[label, (note), current, previous]
        if ncols >= 4:
            mapping["note"] = 1
            mapping["current"] = ncols - 2
            mapping["previous"] = ncols - 1
        elif ncols == 3:
            mapping["current"] = 1
            mapping["previous"] = 2
        elif ncols == 2:
            mapping["current"] = 1

        # 用样本行验证（检查附注列等）
        for _, row in sample_rows[:10]:
            for ci, c in enumerate(row.cols):
                cs = c.strip()
                if "附註" in cs or "附注" in cs or "note" in cs.lower():
                    mapping["note"] = ci + 1  # +1 因为 cols 不含 label 列
                    break
                if "百萬元" in cs or "千元" in cs or "million" in cs.lower():
                    # 货币单位行，不改变映射
                    pass
        return mapping

    @staticmethod
    def row_to_item(label: str, cols: List[str], col_map: Dict[str, int]) -> Optional[FinancialItem]:
        """将标签和列数据转换为 FinancialItem。"""
        if not label:
            return None

        is_header = bool(HEADER_RE.match(label))
        is_total = any(k in label.lower() for k in TOTAL_KEYWORDS)

        note_idx = col_map.get("note", -1) - 1  # cols 索引从0开始
        cur_idx = col_map.get("current", -1) - 1
        prev_idx = col_map.get("previous", -1) - 1

        note = cols[note_idx].strip() if 0 <= note_idx < len(cols) else ""
        amt_cur = cols[cur_idx].strip() if 0 <= cur_idx < len(cols) else ""
        amt_prev = cols[prev_idx].strip() if 0 <= prev_idx < len(cols) else ""

        # 清洗金额
        amt_cur = amt_cur.replace("–", "-").replace("—", "-").replace(" ", "")
        amt_prev = amt_prev.replace("–", "-").replace("—", "-").replace(" ", "")

        # 跳过纯说明行（无金额且非 header）
        if not amt_cur and not amt_prev and not is_header:
            skip_patterns = [
                r'^\d{1,3}$', r'未經審核', r'經審核', r'百萬元', r'千元',
                r'million', r'thousand', r'附註', r'附注',
            ]
            if any(re.search(p, label, re.IGNORECASE) for p in skip_patterns):
                return None
            if re.match(r'.*(年|月|日|三十|三十一|六月|十二月)$', label):
                return None
            return None

        # 跳过表头行（金额列包含非数字文本，如“未經審核”）
        if amt_cur or amt_prev:
            def _is_numeric_or_empty(s: str) -> bool:
                if not s:
                    return True
                # 允许: 数字、千位逗号、括号负数、负号、小数点、破折号(零值)
                return bool(re.match(
                    r'^[\d,\.\(\)\-\s]*$', s
                ))
            if not _is_numeric_or_empty(amt_cur) or not _is_numeric_or_empty(amt_prev):
                return None

        return FinancialItem(
            label=label, note=note,
            amount_current=amt_cur,
            amount_previous=amt_prev,
            is_header=is_header, is_total=is_total,
        )

    # ── 主提取流程 ─────────────────────────────────────────

    def extract_statement(self, start_page: int = 1, end_page: int = 999) -> Dict[str, FinancialStatement]:
        """Extract financial statements from specified page range."""
        stmt_starts = self.find_statement_pages()

        statements: Dict[str, FinancialStatement] = {}
        names = {
            "balance_sheet": "Consolidated Balance Sheet",
            "income_statement": "Consolidated Income Statement",
            "cash_flow": "Consolidated Cash Flow Statement",
        }
        periods = {
            "balance_sheet": "As at period end",
            "income_statement": "For the period",
            "cash_flow": "For the period",
        }

        for stmt_type, start in stmt_starts.items():
            if start < start_page or start > end_page:
                continue

            # 确定结束页
            actual_end = self.find_statement_end(stmt_type, start)
            actual_end = min(actual_end, end_page)

            # 提取位置感知行
            raw_rows = self.extract_column_rows(start, actual_end)
            if not raw_rows:
                continue

            # 合并多行标签
            merged = self.merge_continuation_labels(raw_rows)

            # 检测列映射
            ncols = max(len(r.cols) + 1 for _, r in merged) if merged else 4
            col_map = self.detect_column_mapping(ncols, merged[:10])

            # 解析为 FinancialItem
            items: List[FinancialItem] = []
            for pnum, row in merged:
                item = self.row_to_item(row.label.strip(), row.cols, col_map)
                if item:
                    items.append(item)

            if not items:
                continue

            statements[stmt_type] = FinancialStatement(
                name=names.get(stmt_type, stmt_type),
                period=periods.get(stmt_type, ""),
                items=items,
                page_range=f"{start}-{actual_end}",
            )

        return statements

    # ── 输出 ───────────────────────────────────────────────

    def export_to_json(self, statements: Dict[str, FinancialStatement], output_path: str):
        output_data = {}
        for stmt_type, stmt in statements.items():
            stmt_dict = asdict(stmt)
            stmt_dict['items'] = [asdict(item) for item in stmt.items]
            output_data[stmt_type] = stmt_dict
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"Exported to {output_path}")

    def generate_markdown(self, statements: Dict[str, FinancialStatement]) -> str:
        md = "# Financial Statements Extracted from PDF\n\n"
        for stmt_type, stmt in statements.items():
            md += f"## {stmt.name}\n"
            md += f"**{stmt.period}**  \n"
            md += f"**Pages**: {stmt.page_range}  \n"
            md += f"**Currency Unit**: {stmt.currency_unit}  \n\n"
            md += "| Item | Note | Current | Previous |\n"
            md += "|------|------|---------|----------|\n"
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
        self.pdf.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 extract_financial_statements.py <pdf_path> [start_page] [end_page]")
        print(f"\nCurrent PDF backend: {get_pdf_backend()}")
        sys.exit(1)

    pdf_path = sys.argv[1]
    start_page = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    end_page = int(sys.argv[3]) if len(sys.argv) > 3 else 999

    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found: {pdf_path}")
        sys.exit(1)

    print(f"Extracting from {pdf_path} (pages {start_page}-{end_page})")
    print(f"PDF backend: {get_pdf_backend()}")

    extractor = FinancialPDFExtractor(pdf_path)
    try:
        # 定位报表页面
        stmt_pages = extractor.find_statement_pages()
        print("\nStatement pages found:")
        for st, pg in stmt_pages.items():
            end = extractor.find_statement_end(st, pg)
            print(f"  {st}: pages {pg}-{end}")

        # 提取
        statements = extractor.extract_statement(start_page, end_page)

        if not statements:
            print("\nNo financial statements found.")
            return

        print(f"\nExtracted {len(statements)} statement(s):")
        for st, stmt in statements.items():
            n_items = len([i for i in stmt.items if not i.is_header])
            print(f"  - {st}: {n_items} items (pages {stmt.page_range})")

        # 输出
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        json_path = f"{base_name}_financial_statements.json"
        extractor.export_to_json(statements, json_path)

        md_path = f"{base_name}_financial_statements.md"
        md_content = extractor.generate_markdown(statements)
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
