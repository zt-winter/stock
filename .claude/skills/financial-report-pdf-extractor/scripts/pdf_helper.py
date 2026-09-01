#!/usr/bin/env python3
"""
PDF库兼容层 - 替代 pdfplumber，支持多种后端：
  1. PyMuPDF (pymupdf/fitz) - 首选，表格提取能力强
  2. pypdf - 轻量级纯 Python 实现
  3. pdfminer.six - 精细布局分析

用法：
    from scripts.pdf_helper import open_pdf, get_pdf_backend

    with open_pdf("report.pdf") as pdf:
        print(f"后端: {pdf.backend}")
        print(f"页数: {len(pdf.pages)}")
        text = pdf.pages[0].extract_text()
"""

import sys
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# 检测可用后端，优先级：PyMuPDF > pypdf > pdfminer.six
_BACKEND = None
_BACKEND_LIB = None

try:
    import pymupdf as _pymupdf
    _BACKEND = "pymupdf"
    _BACKEND_LIB = _pymupdf
except ImportError:
    try:
        import fitz as _fitz
        _BACKEND = "fitz"
        _BACKEND_LIB = _fitz
    except ImportError:
        pass

if _BACKEND is None:
    try:
        import pypdf as _pypdf
        _BACKEND = "pypdf"
        _BACKEND_LIB = _pypdf
    except ImportError:
        pass

if _BACKEND is None:
    try:
        from pdfminer.high_level import extract_text as _pdfminer_extract
        from pdfminer.high_level import extract_pages as _pdfminer_extract_pages
        from pdfminer.layout import LTTextContainer, LTChar, LAParams
        import pdfminer as _pdfminer
        _BACKEND = "pdfminer"
        _BACKEND_LIB = _pdfminer
    except ImportError:
        pass

if _BACKEND is None:
    print("错误：需要安装 PDF 处理库，请运行以下任一命令：")
    print("  pip install pymupdf   # 推荐，表格提取能力强")
    print("  pip install pypdf     # 轻量级纯 Python")
    print("  pip install pdfminer.six  # 精细布局分析")
    sys.exit(1)


def get_pdf_backend() -> str:
    """返回当前使用的 PDF 后端名称"""
    return _BACKEND


class PDFPageWrapper:
    """统一的 PDF 页面接口"""

    def __init__(self, page, backend: str):
        self._page = page
        self._backend = backend

    def extract_text(self) -> str:
        """提取页面文本文本"""
        if self._backend in ("pymupdf", "fitz"):
            return self._page.get_text() or ""
        elif self._backend == "pypdf":
            return self._page.extract_text() or ""
        elif self._backend == "pdfminer":
            return self._extract_text_pdfminer()
        return ""

    def _extract_text_pdfminer(self) -> str:
        """使用 pdfminer 提取页面文本"""
        try:
            from pdfminer.high_level import extract_text
            from pdfminer.layout import LAParams
            from io import BytesIO
            # pdfminer 的 extract_text 针对整个文档，这里用页面级提取
            # 回退到用底层 API 逐页提取
            from pdfminer.converter import PDFPageAggregator
            from pdfminer.layout import LTTextContainer
            from pdfminer.pdfpage import PDFPage
            from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter

            # 简化实现：直接返回已缓存的文本
            if hasattr(self, '_cached_text'):
                return self._cached_text
            return ""
        except Exception:
            return ""

    def extract_tables(self, strategy: str = "text") -> List[List[List[str]]]:
        """
        提取页面中的表格（仅 PyMuPDF 后端支持）
        
        Args:
            strategy: 表格检测策略
                - "text"（默认）：基于文本位置推断表格，适合无线框的财报表格
                - "lines"：基于线条检测，适合有明确边框的表格
                - "lines_strict"：严格线条检测
        
        Returns:
            表格列表，每个表格是行列二维数组
            非 PyMuPDF 后端返回空列表
        """
        if self._backend not in ("pymupdf", "fitz"):
            return []
        try:
            tabs = self._page.find_tables(strategy=strategy)
            return [tab.extract() for tab in tabs]
        except Exception:
            return []

    def get_spans(self) -> List[dict]:
        """获取页面中所有带坐标的文本 span（仅 PyMuPDF 后端）。
        返回: [{'text': str, 'x0': float, 'y0': float, 'x1': float}, ...]
        """
        if self._backend not in ("pymupdf", "fitz"):
            return []
        result = []
        try:
            blocks = self._page.get_text('dict')['blocks']
            for b in blocks:
                if 'lines' not in b:
                    continue
                for line in b['lines']:
                    for s in line['spans']:
                        text = s['text'].strip()
                        if text:
                            bbox = s['bbox']
                            result.append({
                                'text': text,
                                'x0': bbox[0], 'y0': bbox[1],
                                'x1': bbox[2],
                            })
        except Exception:
            pass
        return result


class ColumnRow:
    """位置感知提取的一行数据。"""
    def __init__(self, label: str, cols: List[str], y: float):
        self.label = label       # 第一列（标签）
        self.cols = cols         # 其余列
        self.y = y              # 行 Y 坐标


class ColumnPage:
    """
    基于文本位置的分列提取器。
    
    将页面 span 按 X 坐标聚类为列，按 Y 坐标聚类为行，
    避免 find_tables() 对中文标签的截断问题。
    
    用法:
        cp = ColumnPage(page)
        cp.detect_columns()           # 自动检测列边界
        rows = cp.extract_rows()      # 返回 ColumnRow 列表
    """

    def __init__(self, page: 'PDFPageWrapper'):
        self.page = page
        self.spans = page.get_spans()
        self.col_boundaries: List[float] = []  # 列左边界列表

    def detect_columns(self, min_gap: float = 25.0, min_freq: int = 2) -> List[float]:
        """
        自动检测列边界。
        基于 span 的 X 坐标分布，用频率加权聚类，
        过滤低频干扰（如表头、特殊符号）。
        返回列左边界列表（升序）。
        """
        if not self.spans:
            self.col_boundaries = []
            return []

        from collections import Counter
        # 统计每个 X0 的出现次数
        x_freq: Counter = Counter(round(s['x0']) for s in self.spans)

        # 只保留频率 >= min_freq 的 X 值（过滤干扰）
        common_x = sorted(x for x, cnt in x_freq.items() if cnt >= min_freq)
        if not common_x:
            common_x = sorted(x_freq.keys())  # fallback

        # 按间隙聚类
        clusters: List[List[int]] = [[common_x[0]]]
        for x in common_x[1:]:
            if x - clusters[-1][-1] > min_gap:
                clusters.append([x])
            else:
                clusters[-1].append(x)

        # 每个 cluster 的最小值作为列左边界
        self.col_boundaries = [min(c) for c in clusters]
        return self.col_boundaries

    def _assign_column(self, x0: float) -> int:
        """将 X 坐标映射到列索引（使用最近边界法）。"""
        if not self.col_boundaries:
            return 0
        # 找到最近的列：x0 落在 [boundary_i, boundary_{i+1}) 区间内
        best = 0
        for i in range(len(self.col_boundaries) - 1, -1, -1):
            if x0 >= self.col_boundaries[i] - 5:
                best = i
                break
        return best

    def extract_rows(self, y_tolerance: float = 4.0) -> List[ColumnRow]:
        """
        提取所有行数据。
        
        Args:
            y_tolerance: Y 坐标容差，在此范围内视为同一行
        
        Returns:
            ColumnRow 列表
        """
        if not self.spans:
            return []
        if not self.col_boundaries:
            self.detect_columns()

        ncols = len(self.col_boundaries)

        # 按 Y 坐标聚类行（自适应：排序后相邻 Y 差值 > tolerance 则分行）
        sorted_spans = sorted(self.spans, key=lambda s: s['y0'])
        row_groups: List[List[dict]] = []
        current_group: List[dict] = [sorted_spans[0]]
        current_y_center = sorted_spans[0]['y0']

        for s in sorted_spans[1:]:
            if abs(s['y0'] - current_y_center) <= y_tolerance:
                current_group.append(s)
                # 更新中心值（移动平均）
                current_y_center = sum(sp['y0'] for sp in current_group) / len(current_group)
            else:
                row_groups.append(current_group)
                current_group = [s]
                current_y_center = s['y0']
        row_groups.append(current_group)

        rows = []
        for group in row_groups:
            cell_map = {c: [] for c in range(ncols)}
            avg_y = sum(s['y0'] for s in group) / len(group)
            for s in group:
                col = self._assign_column(s['x0'])
                cell_map[col].append(s)

            # 每列内按 X 排序并拼接
            cells = []
            for c in range(ncols):
                col_spans = sorted(cell_map[c], key=lambda s: s['x0'])
                cells.append(' '.join(s['text'] for s in col_spans))

            # 第一列作为 label，其余列作为 cols
            label = cells[0] if cells else ""
            rest = cells[1:] if len(cells) > 1 else []

            # 跳过全空行
            if not any(c.strip() for c in cells):
                continue

            rows.append(ColumnRow(label=label, cols=rest, y=avg_y))

        return rows


class PDFWrapper:
    """统一的 PDF 文档接口，模拟 pdfplumber 的 API"""

    def __init__(self, path: str):
        self.path = path
        self.backend = _BACKEND
        self._doc = None
        self._pages: List[PDFPageWrapper] = []
        self._open()

    def _open(self):
        if self.backend in ("pymupdf", "fitz"):
            self._doc = _BACKEND_LIB.open(self.path)
            self._pages = [PDFPageWrapper(p, self.backend) for p in self._doc]
        elif self.backend == "pypdf":
            self._doc = _BACKEND_LIB.PdfReader(self.path)
            self._pages = [PDFPageWrapper(p, self.backend) for p in self._doc.pages]
        elif self.backend == "pdfminer":
            # pdfminer 不支持随机页面访问，一次性提取所有页面文本
            self._doc = None
            self._load_pdfminer_pages()

    def _load_pdfminer_pages(self):
        """pdfminer 需要特殊处理：先获取页数，再按需提取文本"""
        try:
            from pdfminer.pdfpage import PDFPage
            from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
            from pdfminer.converter import PDFPageAggregator
            from pdfminer.layout import LTTextContainer, LAParams

            # 获取所有页面
            with open(self.path, 'rb') as f:
                pages = list(PDFPage.get_pages(f))

            # 为每个页面创建包装器，预提取文本
            rsrcmgr = PDFResourceManager()
            laparams = LAParams()
            device = PDFPageAggregator(rsrcmgr, laparams=laparams)
            interpreter = PDFPageInterpreter(rsrcmgr, device)

            for page in pages:
                interpreter.process_page(page)
                layout = device.get_result()
                text_parts = []
                for element in layout:
                    if isinstance(element, LTTextContainer):
                        text_parts.append(element.get_text())
                device.close()
                wrapper = PDFPageWrapper(None, self.backend)
                wrapper._cached_text = '\n'.join(text_parts)
                self._pages.append(wrapper)

        except Exception as e:
            logger.warning(f"pdfminer 页面加载失败: {e}")
            self._pages = []

    @property
    def pages(self) -> List[PDFPageWrapper]:
        return self._pages

    def close(self):
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __len__(self):
        return len(self._pages)


def open_pdf(path: str) -> PDFWrapper:
    """
    打开 PDF 文件，返回统一接口的包装器对象。
    支持 with 语句（上下文管理器）。

    用法：
        with open_pdf("report.pdf") as pdf:
            text = pdf.pages[0].extract_text()
            page_count = len(pdf.pages)
    """
    return PDFWrapper(path)
