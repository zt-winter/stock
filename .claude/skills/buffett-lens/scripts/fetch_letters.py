"""
fetch_letters.py - buffett-lens 语料采集

功能:
    采集两套语料并写入 corpus/ 与 manifest.jsonl：
    A. 巴菲特致股东信（伯克希尔官网公开，1977-2024，英文原文）
    B. 伯克希尔股东大会问答（GitHub: wuxiaoda/BRK-Annual-Meeting，中文译本）

数据源:
    A. https://www.berkshirehathaway.com/letters/letters.html
       - 1977-1998: 真实 HTML（windows-1252 编码）
       - 1999-2004: 跳转桩页（~1.4KB），正文在桩页内层链接指向的 PDF，且文件名不可预测
         （final1999pdf.pdf / 2000pdf.pdf / 2001pdf.pdf / 2002pdf.pdf / 2003ltr.pdf）
         → 必须解析桩页内层链接，绝不猜文件名
       - 2005-2024: 直接 PDF（YYYYltr.pdf）
    B. https://github.com/wuxiaoda/BRK-Annual-Meeting （无 LICENSE，仅供本地个人研究）

实测坑（已处理，勿简化掉）:
    1. 官网 CDN（Sucuri）强制 Content-Encoding: br 且忽略客户端 Accept-Encoding；
       .venv 未装 brotli，requests 会静默返回原始压缩字节 → fetch_bytes() 带 curl --compressed 回退
    2. raw.githubusercontent.com 超时不可用；GitHub contents API 未认证限速 60 次/小时，
       149 个文件必然触发限流 → 年会语料必须 git clone --depth 1
    3. 各年份 HTML 标记不统一：1998 有 0 个 <B>，1977 正文在 <PRE> 里
       → 标题检测以文本行为主、标记为辅，不可依赖单一标签

使用方法:
    # 查看索引页各年份的格式与桩页标记（不下载）
    python scripts/fetch_letters.py list

    # 采集致股东信（--all 可断点续跑）
    python scripts/fetch_letters.py fetch --all --delay 1.0
    python scripts/fetch_letters.py fetch --year 1996 --refresh

    # 采集年会问答（git clone）
    python scripts/fetch_letters.py fetch-meetings

    # 采集本地自备材料（如用户自己的年会演讲文本）
    python scripts/fetch_letters.py ingest-local --dir corpus/local/annual_meeting \
        --source annual_meeting --doc-type qa

    # 完整性校验
    python scripts/fetch_letters.py verify

依赖:
    pip install requests beautifulsoup4 lxml pymupdf
    需要 curl（Brotli 回退，libcurl 需带 brotli 支持）
"""

from __future__ import annotations

import argparse
import html as H
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffett_common as C  # noqa: E402

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

# Windows 终端 GBK 编码兼容
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

LETTERS_BASE = "https://www.berkshirehathaway.com/letters/"
LETTERS_INDEX = LETTERS_BASE + "letters.html"
MEETINGS_REPO = "https://github.com/wuxiaoda/BRK-Annual-Meeting"
MEETINGS_RAW_DIR = C.CORPUS_RAW_DIR / "BRK-Annual-Meeting"

UA = "Mozilla/5.0 (X11; Linux x86_64) buffett-lens/1.0 (personal research)"
LETTER_YEAR_MIN, LETTER_YEAR_MAX = 1977, 2024
MIN_LETTER_CHARS = 8000

# 标记哨兵：先把标记替换成哨兵字符，再去标签，这样能保留"哪些行是粗体/居中/PRE"
SENT_BOLD_ON, SENT_BOLD_OFF = "\x01", "\x02"
SENT_PRE_ON, SENT_PRE_OFF = "\x03", "\x04"
SENT_CTR_ON, SENT_CTR_OFF = "\x05", "\x06"
SENT_TBL_ON, SENT_TBL_OFF = "\x07", "\x08"
_SENT_RE = re.compile("[\x01-\x08]")


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def fetch_bytes(url: str, timeout: int = 60) -> tuple[bytes, str]:
    """抓取 URL 返回 (bytes, 实际使用的通道)。处理 Sucuri 强制 Brotli 的问题。

    返回的字节已确认解压成功——否则抛异常。绝不把压缩态原始字节当正文交出去。
    """
    err = None
    if requests is not None:
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": UA,
                                                            "Accept-Encoding": "gzip, deflate"})
            r.raise_for_status()
            enc = (r.headers.get("Content-Encoding") or "identity").lower()
            if enc in ("", "identity", "gzip", "deflate"):
                return r.content, "requests"
            err = f"requests 返回 Content-Encoding={enc} 且无法自动解压"
        except Exception as e:  # noqa: BLE001
            err = f"requests 失败: {type(e).__name__}: {e}"

    # 回退：curl --compressed（libcurl 带 brotli 支持时会自动解压 br）
    try:
        p = subprocess.run(
            ["curl", "-sS", "--compressed", "--max-time", str(timeout), "-A", UA, url],
            capture_output=True, check=True,
        )
        return p.stdout, "curl"
    except FileNotFoundError:
        raise RuntimeError(
            f"抓取失败且找不到 curl。{err}\n"
            "请安装 curl（需带 brotli 支持），或 pip install brotli 让 requests 支持 br。"
        ) from None
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"curl 抓取失败 rc={e.returncode}: {e.stderr.decode(errors='replace')[:200]}") from None


def _decode_body(raw: bytes, ext: str) -> str:
    """解码前先做哨兵检查——乱码绝不进入下游。"""
    if ext == "pdf":
        if not raw.startswith(b"%PDF"):
            raise RuntimeError(f"不是 PDF（magic={raw[:8]!r}）——可能被 CDN 返回了错误页或未解压内容")
        return ""
    if not raw:
        raise RuntimeError("响应为空")
    head = raw[:4000].lower()
    # 未解压的 br/gzip 会表现为大量不可打印字节
    printable = sum(1 for b in raw[:4000] if 9 <= b <= 13 or 32 <= b <= 126 or b >= 128)
    if printable / max(1, len(raw[:4000])) < 0.6 and b"<html" not in head:
        raise RuntimeError("响应疑似未解压的压缩字节（Brotli/Gzip 未解码）")
    for enc in ("windows-1252", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 致股东信：索引页与桩页
# ---------------------------------------------------------------------------
def parse_index(index_html: str) -> list[dict]:
    """解析 letters.html 得到 [{year, href}]。href 取自索引页，不硬编码年份分界。"""
    out = []
    for m in re.finditer(r'<a\s+[^>]*href="([^"]+)"[^>]*>\s*((?:19|20)\d{2})', index_html, re.I):
        href, year = m.group(1).strip(), int(m.group(2))
        if not (LETTER_YEAR_MIN <= year <= LETTER_YEAR_MAX):
            continue
        if href.startswith(("http://", "https://", "//")) and "berkshirehathaway.com" not in href:
            continue  # 站外链接（如 Adobe、IngramSpark）
        out.append({"year": year, "href": href})
    # 同年去重，保留首个
    seen, uniq = set(), []
    for r in sorted(out, key=lambda x: x["year"]):
        if r["year"] not in seen:
            seen.add(r["year"])
            uniq.append(r)
    return uniq


_STUB_HINT = re.compile(r"(presented in|being presented in|pdf format|IMPORTANT NOTE)", re.I)


def _find_pdf_link(html_text: str, base: str) -> str | None:
    """从桩页里找出内层 PDF 链接。桩页文件名不可预测，只能读出来。"""
    cands = []
    for m in re.finditer(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.I | re.S):
        href, label = m.group(1).strip(), re.sub(r"<[^>]+>", "", m.group(2))
        if "adobe.com" in href.lower():
            continue
        if href.lower().endswith(".pdf") or "pdf" in label.lower():
            cands.append(href)
    if not cands:
        return None
    pdfs = [h for h in cands if h.lower().endswith(".pdf")]
    href = (pdfs or cands)[0]
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("/"):
        return "https://www.berkshirehathaway.com" + href
    return base + href


def get_letter_source(year: int, href: str) -> tuple[str, bytes | str, str]:
    """取某一年的正文，返回 (kind, payload, final_url)。kind 为 'html'(payload=str) 或 'pdf'(payload=bytes)。

    桩页（1999-2003）会自动跟进到内层 PDF；站点明确推荐 PDF 版，且 2004 起索引页直接给 PDF，
    故对同时提供 HTML/PDF 的桩页也一律取 PDF，保证口径一致。
    """
    url = href if href.startswith("http") else LETTERS_BASE + href.lstrip("/")
    raw, _chan = fetch_bytes(url)

    if url.lower().endswith(".pdf"):
        return "pdf", raw, url

    text = _decode_body(raw, "html")
    body_plain = re.sub(r"<[^>]+>", " ", text)
    body_plain = re.sub(r"\s+", " ", H.unescape(body_plain)).strip()
    looks_stub = bool(_STUB_HINT.search(body_plain)) and len(body_plain) < 3000
    if looks_stub:
        pdf_url = _find_pdf_link(text, url.rsplit("/", 1)[0] + "/")
        if pdf_url:
            praw, _ = fetch_bytes(pdf_url)
            if praw.startswith(b"%PDF"):
                return "pdf", praw, pdf_url
    return "html", text, url


# ---------------------------------------------------------------------------
# HTML / PDF → 行序列
# ---------------------------------------------------------------------------
class Line:
    __slots__ = ("text", "bold", "pre", "centered", "table", "heading")

    def __init__(self, text: str, bold=False, pre=False, centered=False, table=False,
                 heading=False):
        self.text, self.bold, self.pre, self.centered, self.table = text, bold, pre, centered, table
        # heading=True 表示"来源已明确标出这是标题"（年会 markdown 的 ### / **N、** 等）。
        # 这类行不再走启发式打分——格式本身就是证据，不该被长度/标点规则误杀。
        self.heading = heading

    def __repr__(self):
        return f"Line({self.text[:40]!r}, bold={self.bold}, pre={self.pre}, table={self.table})"


def html_to_lines(html_text: str) -> list[Line]:
    """HTML → 行序列，保留粗体/PRE/居中/表格信息供标题检测使用。"""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.S | re.I)
    t = re.sub(r"<table\b[^>]*>", SENT_TBL_ON, t, flags=re.I)
    t = re.sub(r"</table\s*>", SENT_TBL_OFF, t, flags=re.I)
    # 先给标记打哨兵，再去标签，这样哨兵能活到行级别
    t = re.sub(r"<b\b[^>]*>", SENT_BOLD_ON, t, flags=re.I)
    t = re.sub(r"</b\s*>", SENT_BOLD_OFF, t, flags=re.I)
    t = re.sub(r"<strong\b[^>]*>", SENT_BOLD_ON, t, flags=re.I)
    t = re.sub(r"</strong\s*>", SENT_BOLD_OFF, t, flags=re.I)
    t = re.sub(r"<pre\b[^>]*>", SENT_PRE_ON, t, flags=re.I)
    t = re.sub(r"</pre\s*>", SENT_PRE_OFF, t, flags=re.I)
    t = re.sub(r'<p\b[^>]*align\s*=\s*"?center[^>]*>', SENT_CTR_ON, t, flags=re.I)
    t = re.sub(r"</p\s*>", SENT_CTR_OFF + "\n\n", t, flags=re.I)
    # 块级边界
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(div|tr|table|h[1-6]|li|blockquote)>", "\n", t, flags=re.I)
    t = re.sub(r"</t[dh]>", "  ", t, flags=re.I)
    t = re.sub(r"<p\b[^>]*>", "\n\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = H.unescape(t)
    t = t.replace("\r\n", "\n").replace("\r", "\n")

    lines: list[Line] = []
    bold = pre = centered = False
    for raw_ln in t.split("\n"):
        bold = bold or (SENT_BOLD_ON in raw_ln)
        pre = pre or (SENT_PRE_ON in raw_ln)
        centered = centered or (SENT_CTR_ON in raw_ln)
        ln = _SENT_RE.sub("", raw_ln)
        ln = re.sub(r"[ \t ]+", " ", ln).strip()
        if ln:
            lines.append(Line(ln, bold, pre, centered))
        elif lines and lines[-1].text:
            lines.append(Line(""))
        if SENT_BOLD_OFF in raw_ln:
            bold = False
        if SENT_PRE_OFF in raw_ln:
            pre = False
        if SENT_CTR_OFF in raw_ln:
            centered = False
    return _squeeze_blank(lines)


def pdf_to_lines(pdf_bytes: bytes) -> list[Line]:
    """PDF → 行序列。

    两个关键点（都是实测得出，勿想当然）：
    1. PyMuPDF 的 block 就是段落，故按 block 聚行、block 之间留空行——标题检测（依赖前后空行）
       与分块（依赖段落边界）都建立在这个结构上。
    2. 小标题是「粗体 + 正文字号」（实测 2010 年标题字号 = 中位数 10.0），字号不是可靠信号。
       但表格单元格同样短且常为粗体，故再用版面判表格：同一 y 带有多个 block 即为表格行。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError("需要 pymupdf: pip install pymupdf") from None

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages: list[list[dict]] = []
    for page in doc:
        blocks = []
        for block in page.get_text("dict").get("blocks", []):
            blines: list[Line] = []
            x0 = y0 = y1 = None
            for ln in block.get("lines", []):
                parts, bold = [], False
                for sp in ln.get("spans", []):
                    parts.append(sp.get("text", ""))
                    if int(sp.get("flags", 0)) & 2**4:
                        bold = True
                text = re.sub(r"[ \t\u00a0]+", " ", "".join(parts)).strip()
                if text:
                    blines.append(Line(text, bold=bold))
                bb = ln.get("bbox") or (0, 0, 0, 0)
                x0 = bb[0] if x0 is None else min(x0, bb[0])
                y0 = bb[1] if y0 is None else min(y0, bb[1])
                y1 = bb[3] if y1 is None else max(y1, bb[3])
            if blines:
                blocks.append({"lines": blines, "x0": x0 or 0, "y0": y0 or 0, "y1": y1 or 0})
        # 表格判定：同一竖直带上有 ≥2 个 block（并排的单元格）
        for a in range(len(blocks)):
            for b in range(a + 1, len(blocks)):
                lo, hi = max(blocks[a]["y0"], blocks[b]["y0"]), min(blocks[a]["y1"], blocks[b]["y1"])
                if hi - lo > 0.5 * min(blocks[a]["y1"] - blocks[a]["y0"],
                                       blocks[b]["y1"] - blocks[b]["y0"]):
                    blocks[a]["table"] = blocks[b]["table"] = True
        for blk in blocks:
            if blk.get("table"):
                for ln in blk["lines"]:
                    ln.table = True
        pages.append(blocks)
    doc.close()

    lines: list[Line] = []
    for blocks in pages:
        for blk in blocks:
            lines.extend(blk["lines"])
            lines.append(Line(""))
    return _squeeze_blank(lines)


def _squeeze_blank(lines: list[Line]) -> list[Line]:
    """连续空行压成一个，首尾去空。"""
    out: list[Line] = []
    for ln in lines:
        if not ln.text:
            if out and out[-1].text:
                out.append(ln)
        else:
            out.append(ln)
    while out and not out[-1].text:
        out.pop()
    while out and not out[0].text:
        out.pop(0)
    return out


# ---------------------------------------------------------------------------
# 标题检测（文本行为主，标记为辅）
# ---------------------------------------------------------------------------
_SEP_LINE = re.compile(r"^[\s*\-=~#_·•]+$")
_STMT_ITEM = re.compile(r"^[A-Za-z0-9]{1,2}\s*[.)]\s+\S")      # "A. As Reported" / "2. Reduction of..."
_ALLCAPS_WORD = re.compile(r"^[A-Z&\-'\s]{3,40}$")              # "ASSETS" / "LIABILITIES"
# 点线引导符（"Total Current Assets ………"）。必须锚在行尾且连续 ≥4 个——中文正文里的
# 省略号 "……" 只有 2 个字符且多出现在句中，宽松匹配会把它当成复刻财报，
# 进而让 mark_statement_runs 把附近真正的小标题一起标掉（实测 2021 年会丢了 41 节）。
_DOTTED_LEADER = re.compile(r"[.·…]{4,}\s*$")
_UNDERSCORE_ROW = re.compile(r"^_{2,}")                          # "____595"
_TABLEISH = re.compile(r"(\d[\d,\.]*\s){3,}")          # 数字列很多 → 表头/表格行
_NUMERIC_LINE = re.compile(r"^[\d\s,\.%$()\-]+$")


def mark_statement_runs(lines: list[Line]) -> None:
    """把「复刻的财报」整段标为表格，就地修改 lines。

    1986/1990 的 Appendix 里有一张逐行用点线排版的模拟财报（"Total Current Assets ……"），
    其中的 "Assets"、"Company O"、"Liabilities" 这些短行会被当成小标题，一次能多出
    三十多个假小节。点线是这类版式独有的特征：用它定位整段，把夹在点线之间的短行
    一并排除——点线本身是数据行，不可能是标题。
    """
    n = len(lines)
    is_leader = [bool(_DOTTED_LEADER.search(ln.text)) for ln in lines]
    i = 0
    while i < n:
        if not is_leader[i]:
            i += 1
            continue
        j = i
        while j < n and is_leader[j]:
            j += 1
        # 向前后各吞掉被点线夹住的短行（"Assets" / "Company O" / "(000s Omitted)"），
        # 遇到空行即停——真标题周围总有空行，所以不会被误吞。
        s, k = i, j
        for _ in range(4):
            if s > 0 and lines[s - 1].text.strip() and len(lines[s - 1].text.strip()) <= 40:
                s -= 1
            else:
                break
        for _ in range(12):
            if k < n and lines[k].text.strip() and len(lines[k].text.strip()) <= 40:
                k += 1
            else:
                break
        for x in range(s, k):
            lines[x].table = True
        i = max(k, j)


def _heading_score(lines: list[Line], i: int, require_markup: bool = False) -> int:
    """给第 i 行打分，>=4 视为标题。故意保守——检测不到就留空，绝不编造。

    require_markup=True 时必须有粗体/居中标记（PDF 实测可靠）；HTML 各年份标记不统一
    （1998 有 0 个 <B>），故不强制。
    """
    ln = lines[i]
    txt = ln.text
    if ln.table:                                        # 表格单元格一律不是标题
        return 0
    if require_markup and not (ln.bold or ln.centered):
        return 0
    if not txt or len(txt) > 90 or len(txt) < 3:
        return 0
    if _SEP_LINE.match(txt) or _NUMERIC_LINE.match(txt):
        return 0
    # 财报条目与全大写单词（模拟财报里的 ASSETS / LIABILITIES）不是小标题。
    # 但 APPENDIX 例外——那是真标题。
    if _STMT_ITEM.match(txt):
        return 0
    if _ALLCAPS_WORD.match(txt) and len(txt.split()) == 1 and "APPENDIX" not in txt:
        return 0
    if _TABLEISH.search(txt):
        return 0
    if _DOTTED_LEADER.search(txt) or _UNDERSCORE_ROW.match(txt):
        return 0
    if txt.endswith((",", ";", "。")):
        return 0
    if txt.count("  ") >= 2:                            # 表格里用多空格对齐
        return 0
    if require_markup:
        # PDF 的块划分不可靠：2006 年 "Acquisitions" 与后文同属一个 block，前后没有
        # 空行。既已有粗体/居中这个实测可靠的标记，就不该再拿空行当必要条件——
        # 改为只要求"这行处在一个段落的开头"。
        if len(txt) > 80 or txt[:1] in "*\u00b7\u2022-":
            return 0
        if txt[-1] in ".,;:\u3002\uff0c\uff1b\uff1a\u3001":
            return 0
        if len(txt.split()) > 12:
            return 0
        if not (txt[:1].isupper() or txt[:1].isdigit() or C.is_cjk_char(txt[:1])):
            return 0
        prev_txt = lines[i - 1].text if i > 0 else ""
        if prev_txt and prev_txt[-1] not in ".!?\"\u201d\u2019)":
            return 0                                    # 上一行没结束，说明是句中换行
        return 6

    # 上下文：标题前后应有空行边界
    prev_blank = i == 0 or not lines[i - 1].text
    next_blank = i + 1 >= len(lines) or not lines[i + 1].text
    if not (prev_blank and next_blank):
        return 0

    score = 1
    if ln.bold or ln.centered:
        score += 2                                      # 标记线索最强
    if txt.endswith(":"):                               # "To the Shareholders...:" / "Dear ___:" 是抬头
        score -= 1
    elif not txt.endswith((".", "?", "!")):
        score += 1
    words = txt.split()
    if 1 <= len(words) <= 14:
        score += 1
    # 首字母大写的实词比例高 → 像标题
    cap = sum(1 for w in words if w[:1].isupper())
    if words and cap / len(words) >= 0.5:
        score += 1
    if txt.isupper() and len(txt) < 60:
        score += 1
    # 正文段首通常缩进或被自动换行成满行，标题一般短
    if len(txt) <= 60:
        score += 1
    return score


def detect_headings(lines: list[Line], require_markup: bool = False) -> list[int]:
    """返回被判定为小标题的行号列表。"""
    out = []
    for i, ln in enumerate(lines):
        if ln.heading:                      # 来源已明确标注，直接采信
            if not ln.table:
                out.append(i)
            continue
        if _heading_score(lines, i, require_markup=require_markup) >= 4:
            out.append(i)
    return out


def build_doc(lines: list[Line], require_markup: bool = False) -> tuple[str, list[dict]]:
    """行序列 → (full_text, sections)。sections 的 start/end 是 full_text 的字符偏移。"""
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln.text) + 1
    full_text = "\n".join(ln.text for ln in lines)

    mark_statement_runs(lines)                      # 先排除复刻财报，再找标题
    hidx = detect_headings(lines, require_markup=require_markup)
    sections: list[dict] = []
    for n, i in enumerate(hidx):
        start = offsets[i]
        end = offsets[hidx[n + 1]] if n + 1 < len(hidx) else len(full_text)
        sections.append({"title": lines[i].text, "part": None, "start": start, "end": end})
    if not sections:
        sections = [{"title": None, "part": None, "start": 0, "end": len(full_text)}]
    else:
        # 首个标题之前的内容单独成节（通常是抬头）
        if sections[0]["start"] > 0:
            sections.insert(0, {"title": None, "part": None, "start": 0, "end": sections[0]["start"]})
    return full_text, sections


# ---------------------------------------------------------------------------
# 致股东信采集
# ---------------------------------------------------------------------------
def fetch_letter(year: int, href: str) -> dict:
    kind, payload, url = get_letter_source(year, href)
    if kind == "pdf":
        lines = pdf_to_lines(payload)
        (C.CORPUS_RAW_DIR / f"{year}ltr.pdf").write_bytes(payload)
    else:
        lines = html_to_lines(payload)
        (C.CORPUS_RAW_DIR / f"{year}.html").write_text(payload, encoding="utf-8")

    full_text, sections = build_doc(lines, require_markup=(kind == "pdf"))
    n_fffd = full_text.count("�")
    suspect = len(full_text) < MIN_LETTER_CHARS or n_fffd / max(1, len(full_text)) > 0.05
    if re.search(r"presented in (both HTML and )?PDF format", full_text, re.I):
        suspect = True
        raise RuntimeError(f"{year}: 抓到的是跳转桩页而非正文（内层 PDF 链接解析失败）")

    out = C.CORPUS_TEXT_DIR / "letters" / f"{year}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(full_text, encoding="utf-8")

    return {
        "doc_id": f"letters:{year}",
        "source": "letters",
        "year": year,
        "title": f"{year} Letter to Shareholders",
        "doc_type": "letter",
        "url": url,
        "attribution": C.attribution_for("letters", year),
        "fetched_at": C.now_iso(),
        "char_len": len(full_text),
        "path": str(out.relative_to(C.SKILL_DIR)),
        "sections": sections,
        "suspect": suspect,
    }


def cmd_list(_args) -> int:
    raw, chan = fetch_bytes(LETTERS_INDEX)
    idx = parse_index(_decode_body(raw, "html"))
    print(C.sep())
    print("  致股东信索引（来源：berkshirehathaway.com/letters/letters.html）")
    print(C.sep())
    print(f"  {'年份':<6}{'href':<22}{'类型':<12}{'说明'}")
    print(C.line())
    for r in idx:
        is_stub = r["href"].endswith(".html") and r["year"] >= 1999
        kind = "PDF" if r["href"].endswith(".pdf") else ("HTML→PDF" if is_stub else "HTML")
        note = "跳转桩页，需跟进内层 PDF" if is_stub else ""
        print(f"  {r['year']:<6}{r['href']:<22}{kind:<12}{note}")
    print(C.line())
    print(f"  共 {len(idx)} 年（{min(r['year'] for r in idx)}-{max(r['year'] for r in idx)}）  通道={chan}")
    return 0


def cmd_fetch(args) -> int:
    raw, _ = fetch_bytes(LETTERS_INDEX)
    idx = parse_index(_decode_body(raw, "html"))
    if args.year:
        idx = [r for r in idx if r["year"] == args.year]
        if not idx:
            print(f"  索引页没有 {args.year} 年")
            return 1
    else:
        # 默认幂等：已在 manifest 中且非可疑的年份跳过；--refresh 强制重下
        have = {r["year"] for r in C.read_manifest()
                if r.get("source") == "letters" and not r.get("suspect")}
        todo = [r for r in idx if args.refresh or r["year"] not in have]
        skipped = len(idx) - len(todo)
        if skipped:
            print(f"  跳过 {skipped} 个已有年份（--refresh 可强制重下）")
        idx = todo
        if not idx:
            print("  无待采集年份，已是最新")
            return 0

    records, failed = [], []
    for n, r in enumerate(idx, 1):
        try:
            rec = fetch_letter(r["year"], r["href"])
            records.append(rec)
            flag = "  [WARN 文本偏少]" if rec["suspect"] else ""
            print(f"  [{n}/{len(idx)}] {r['year']}  {rec['char_len']:>7,} 字  "
                  f"{len(rec['sections']):>2} 节{flag}")
        except Exception as e:  # noqa: BLE001
            failed.append((r["year"], str(e)))
            print(f"  [{n}/{len(idx)}] {r['year']}  失败: {e}")
        if args.delay and n < len(idx):
            time.sleep(args.delay)

    if records:
        C.upsert_manifest(records)
    print(C.line())
    print(f"  成功 {len(records)} 年，失败 {len(failed)} 年 → {C.MANIFEST_PATH}")
    for y, msg in failed:
        print(f"    失败 {y}: {msg[:160]}")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# 年会问答采集
# ---------------------------------------------------------------------------
_PART_ORDER = {"上午场（上）": 0, "上午场（中）": 1, "上午场（下）": 2,
               "下午场（上）": 3, "下午场（中）": 4, "下午场（下）": 5}


def _part_sort_key(path: Path) -> tuple[int, str]:
    m = re.search(r"(上午场|下午场)?\s*[（(]?(上|中|下)?[）)]?", path.stem)
    label = re.sub(r"^\d{4}年伯克希尔股东大会Q&A\s*", "", path.stem).strip()
    return (_PART_ORDER.get(label, 99), path.stem)


def _split_meeting_file(text: str) -> tuple[dict, str]:
    """拆出文件头部的来源信息与正文。头部含 CNBC 链接、译者、精彩片段，不进正文（避免与正文重复命中）。"""
    meta: dict = {"cnbc_url": None, "translator": None}
    m = re.search(r"https?://buffett\.cnbc\.com/\S+", text)
    if m:
        meta["cnbc_url"] = m.group(0).rstrip("）)。,")
    if "一朵喵" in text:
        meta["translator"] = "一朵喵"
    body = text
    marker = re.search(r"^\s*-{3,}\s*正文\s*-{3,}\s*$", text, re.M)
    if marker:
        body = text[marker.end():]
    else:
        # 无正文标记：砍掉含 CNBC 链接/译注/精彩片段的前置块
        lines = text.splitlines()
        cut = 0
        for i, ln in enumerate(lines[:120]):
            if re.search(r"(英文原文URL|中文翻译及编辑|转载请注明|中文链接|本文精彩片段|所有引用必须注明)", ln):
                cut = i + 1
        body = "\n".join(lines[cut:])
    return meta, body.strip()


# 年会语料跨年份有四种标题写法，必须都认：
#   1994-2007  "### 7、标题"        2008-2016/2022  "**7、标题**"
#   2019       "**Q6：问题文本**"   2021            "#### Q10"
# 只认其中一种会静默丢掉整段语料（实测 2008-2016 全部切出 0 节）。
_MT_Q = [
    re.compile(r"^#{1,6}\s+\**\s*(\d{1,3})\s*[、.．]\s*(.+?)\s*\**\s*$"),
    re.compile(r"^\*\*\s*(\d{1,3})\s*[、.．]\s*([^*]+?)\s*\*\*\s*$"),
    re.compile(r"^\*\*\s*Q\s*(\d{1,3})\s*[:：]\s*(.+?)\s*\*\*\s*$"),
    re.compile(r"^(?:#{1,6}\s+|\*\*)\s*Q\s*(\d{1,3})\s*\**\s*$"),
]
_MT_PART = [
    re.compile(r"^#{1,6}\s+\**\s*((?:上午场|下午场)\s*[（(]?\s*[上中下]?\s*[）)]?)\**\s*$"),
    re.compile(r"^#{1,6}\s+\**\s*(第[一二三四五六七八九十]+部分)\**\s*$"),
]
# 这些是版式分隔，不是内容小节
_MT_SKIP = re.compile(r"^(问答环节|正文|目录|索引)\s*$")


def _mt_classify(ln: str) -> tuple[str, str | None, str | None]:
    """判断一行是不是标题。返回 (kind, label, title)，kind ∈ {q, part, none}。"""
    t = ln.strip()
    if not t:
        return "none", None, None
    for pat in _MT_PART:
        m = pat.match(t)
        if m:
            return "part", m.group(1).strip(), None
    for pat in _MT_Q:
        m = pat.match(t)
        if m:
            return "q", m.group(1), m.group(2).strip().strip("*").strip() if m.lastindex and m.lastindex >= 2 else None
    return "none", None, None


def meeting_to_lines(body: str) -> list[Line]:
    """年会 markdown → 行序列。标题行标 heading=True，供 detect_headings 直接采信。

    2021 这类 "#### Q10" 只有编号没有问题文本，取紧随其后的段落前 40 字作为标题
    ——那是原文里的话，不是编的；纯粹标个 "Q10" 检索时没有信息量。
    """
    lines: list[Line] = []
    raw = body.replace("\r\n", "\n").split("\n")
    for i, raw_ln in enumerate(raw):
        s = raw_ln.rstrip()
        kind, label, title = _mt_classify(s)
        if kind == "q":
            if not title:                       # 2021 式：向后找第一条正文当标题
                for nxt in raw[i + 1: i + 6]:
                    cand = nxt.strip().strip("*").strip()
                    if len(cand) >= 8:
                        title = cand[:40] + ("…" if len(cand) > 40 else "")
                        break
            text = f"Q{label}：{title}" if title else f"Q{label}"
            lines.append(Line(""))
            lines.append(Line(text, bold=True, heading=True))
            lines.append(Line(""))
        elif kind == "part":
            lines.append(Line(""))
            lines.append(Line(label, bold=True, heading=True))
            lines.append(Line(""))
        elif _MT_SKIP.match(s.strip().lstrip("#").strip()):
            continue
        else:
            lines.append(Line(s.strip()))
    return _squeeze_blank(lines)


_MEETING_Q = re.compile(r"^(?:Q\s*)?(\d{1,3})\s*[、.．:：]?\s*(.*)$")


def meeting_sections(lines: list[Line], base_sections: list[dict], full_text: str) -> list[dict]:
    """把标题拆成 (part, question)：part 形如「上午场（上）」「第三部分」。"""
    out = []
    for sec in base_sections:
        title = sec.get("title")
        part = None
        if title:
            t = title.strip()
            if re.match(r"^(上午场|下午场|第[一二三四五六七八九十]+部分)", t) and len(t) <= 12:
                part, title = t, None          # 纯场次标题 → 降级为 part，不单独成节
            else:
                q = _MEETING_Q.match(t)
                if q and q.group(2):
                    title = f"{q.group(1)}、{q.group(2)}"
        out.append({"title": title, "part": part, "start": sec["start"], "end": sec["end"]})
    cur = None
    for sec in out:                            # part 向后继承
        if sec["part"]:
            cur = sec["part"]
        elif cur:
            sec["part"] = cur
    merged: list[dict] = []
    for sec in out:                            # part-only 的空节并入相邻节，避免产生空 chunk
        if sec["title"] is None and merged and merged[-1]["title"] is None:
            merged[-1]["end"] = sec["end"]
            merged[-1]["part"] = merged[-1]["part"] or sec["part"]
        else:
            merged.append(sec)
    return merged


def cmd_fetch_meetings(args) -> int:
    MEETINGS_RAW_DIR.parent.mkdir(parents=True, exist_ok=True)
    if (MEETINGS_RAW_DIR / ".git").is_dir():
        print(f"  已存在，执行 git pull: {MEETINGS_RAW_DIR}")
        p = subprocess.run(["git", "-C", str(MEETINGS_RAW_DIR), "pull", "--ff-only"],
                           capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  git pull 失败（继续使用现有副本）: {p.stderr.strip()[:200]}")
    else:
        print(f"  git clone --depth 1 {MEETINGS_REPO}")
        p = subprocess.run(["git", "clone", "--depth", "1", MEETINGS_REPO, str(MEETINGS_RAW_DIR)],
                           capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  git clone 失败: {p.stderr.strip()[:400]}")
            return 1

    md_files = [p for p in MEETINGS_RAW_DIR.rglob("*.md") if p.name.lower() != "readme.md"]
    by_year: dict[int, list[Path]] = {}
    for p in md_files:
        m = re.match(r"^(\d{4})$", p.parent.name)
        if m:
            by_year.setdefault(int(m.group(1)), []).append(p)
    if not by_year:
        print("  未找到形如 <年>/xxx.md 的文件")
        return 1

    records, failed = [], []
    for year in sorted(by_year):
        files = sorted(by_year[year], key=_part_sort_key)
        parts, cnbc, translator = [], None, None
        for f in files:
            try:
                meta, body = _split_meeting_file(f.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:  # noqa: BLE001
                failed.append((f"{year}/{f.name}", str(e)))
                continue
            if not body.strip():
                failed.append((f"{year}/{f.name}", "正文为空"))
                continue
            label = re.sub(r"^\d{4}年伯克希尔股东大会Q&A\s*", "", f.stem).strip() or f.stem
            parts.append((label, body))
            cnbc = cnbc or meta.get("cnbc_url")
            translator = translator or meta.get("translator")

        if not parts:
            continue
        # 整年拼成一个 document，各文件作为一个 part
        lines: list[Line] = []
        part_bounds: list[tuple[str, int, int]] = []   # (part, start_line, end_line)
        for label, body in parts:
            if lines:
                lines.append(Line(""))
            start = len(lines)
            lines.append(Line(label, bold=True))
            lines.extend(meeting_to_lines(body))
            part_bounds.append((label, start, len(lines)))
        lines = _squeeze_blank(lines)

        full_text, base_sections = build_doc(lines, require_markup=True)
        sections = meeting_sections(lines, base_sections, full_text)
        # 依据 part 的行边界把 part 归属校准到字符级
        offsets, pos = [], 0
        for ln in lines:
            offsets.append(pos)
            pos += len(ln.text) + 1
        for label, s_line, e_line in part_bounds:
            c_start = offsets[min(s_line, len(offsets) - 1)]
            c_end = offsets[e_line] if e_line < len(offsets) else len(full_text)
            for sec in sections:
                if sec["start"] >= c_start and sec["start"] < c_end:
                    sec["part"] = label

        out = C.CORPUS_TEXT_DIR / "annual_meeting" / f"{year}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(full_text, encoding="utf-8")
        n_q = sum(1 for s in sections if s.get("title"))
        records.append({
            "doc_id": f"annual_meeting:{year}",
            "source": "annual_meeting",
            "year": year,
            "title": f"{year}年伯克希尔股东大会问答",
            "doc_type": "qa",
            "url": cnbc or C.CNBC_ORIGIN,
            "attribution": C.attribution_for("annual_meeting", year),
            "fetched_at": C.now_iso(),
            "char_len": len(full_text),
            "path": str(out.relative_to(C.SKILL_DIR)),
            "sections": sections,
            "n_files": len(parts),
            "suspect": len(full_text) < 5000,
        })
        print(f"  {year}  {len(parts)} 文件  {len(full_text):>8,} 字  {n_q:>3} 问答节")

    if records:
        C.upsert_manifest(records)
    print(C.line())
    print(f"  成功 {len(records)} 年 → {C.MANIFEST_PATH}")
    for name, msg in failed:
        print(f"    跳过 {name}: {msg[:140]}")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# 本地材料接入（用户自备的年会演讲/其他文本）
# ---------------------------------------------------------------------------
def cmd_ingest_local(args) -> int:
    src_dir = Path(args.dir)
    if not src_dir.is_absolute():
        src_dir = (Path.cwd() / src_dir).resolve()
    if not src_dir.is_dir():
        print(f"  目录不存在: {src_dir}")
        return 1
    files = [p for p in sorted(src_dir.rglob("*")) if p.is_file()
             and p.suffix.lower() in (".txt", ".md", ".html", ".htm", ".pdf")]
    if not files:
        print(f"  {src_dir} 下没有 .txt/.md/.html/.pdf 文件")
        return 1

    year_re = re.compile(args.year_regex)
    records, failed = [], []
    for f in files:
        try:
            m = year_re.search(f.stem) or year_re.search(f.parent.name)
            year = int(m.group(1)) if m else None
            raw = f.read_bytes()
            if f.suffix.lower() == ".pdf":
                lines = pdf_to_lines(raw)
            elif f.suffix.lower() in (".html", ".htm"):
                lines = html_to_lines(_decode_body(raw, "html"))
            else:
                lines = meeting_to_lines(_decode_body(raw, "html"))
            full_text, base_sections = build_doc(lines)
            if len(full_text) < 1000:
                failed.append((str(f), f"正文仅 {len(full_text)} 字"))
                continue
            slug = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", f.stem).strip("-")[:40]
            doc_id = f"{args.source}:{year or 'na'}:{slug}"
            out = C.CORPUS_TEXT_DIR / args.source / f"{year or 'na'}-{slug}.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(full_text, encoding="utf-8")
            records.append({
                "doc_id": doc_id, "source": args.source, "year": year,
                "title": f.stem, "doc_type": args.doc_type, "url": None,
                "attribution": C.attribution_for(args.source, year),
                "fetched_at": C.now_iso(), "char_len": len(full_text),
                "path": str(out.relative_to(C.SKILL_DIR)), "sections": base_sections,
                "suspect": False,
            })
            print(f"  {f.name}  → {doc_id}  {len(full_text):,} 字  {len(base_sections)} 节")
        except Exception as e:  # noqa: BLE001
            failed.append((str(f), str(e)))
    if records:
        C.upsert_manifest(records)
    print(C.line())
    print(f"  接入 {len(records)} 个文档（source={args.source}, doc_type={args.doc_type}）")
    for name, msg in failed:
        print(f"    失败 {Path(name).name}: {msg[:140]}")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def cmd_verify(_args) -> int:
    recs = C.read_manifest()
    if not recs:
        print("  manifest 为空，先运行 fetch --all")
        return 1
    problems = []

    letters = sorted([r for r in recs if r.get("source") == "letters"], key=lambda r: r["year"])
    print(C.sep())
    print("  致股东信完整性校验")
    print(C.sep())
    print(f"  {'年份':<6}{'字符数':>10}{'节数':>6}  {'状态'}")
    print(C.line())
    got = {r["year"] for r in letters}
    for y in range(LETTER_YEAR_MIN, LETTER_YEAR_MAX + 1):
        r = next((x for x in letters if x["year"] == y), None)
        if r is None:
            print(f"  {y:<6}{'—':>10}{'—':>6}  [缺失]")
            problems.append(f"{y} 年缺失")
            continue
        st = []
        if r.get("suspect"):
            st.append("文本偏少")
            problems.append(f"{y} 年文本仅 {r.get('char_len')} 字")
        if (r.get("char_len") or 0) < MIN_LETTER_CHARS:
            st.append("<8000字")
            problems.append(f"{y} 年 char_len={r.get('char_len')} 低于 {MIN_LETTER_CHARS}")
        p = C.SKILL_DIR / r.get("path", "")
        if not p.is_file():
            st.append("文件丢失")
            problems.append(f"{y} 年文本文件不存在: {r.get('path')}")
        else:
            t = p.read_text(encoding="utf-8", errors="replace")
            if t.count("�") / max(1, len(t)) > 0.01:
                st.append("乱码")
                problems.append(f"{y} 年含大量替换字符")
            if re.search(r"presented in (both HTML and )?PDF format", t, re.I):
                st.append("是桩页")
                problems.append(f"{y} 年抓到的是跳转桩页")
            # manifest 里的 char_len 是抓取当时记下的；磁盘上的文件若与之对不上，
            # 说明缓存被截断或改写——这类损坏不一定留下替换字符，只有比对长度才看得见。
            if (r.get("char_len") or 0) and abs(len(t) - r["char_len"]) > max(50, r["char_len"] * 0.01):
                st.append("与manifest不符")
                problems.append(
                    f"{y} 年磁盘文本 {len(t)} 字与 manifest 记录的 {r['char_len']} 字不符")
        print(f"  {y:<6}{r.get('char_len', 0):>10,}{len(r.get('sections') or []):>6}  "
              f"{'OK' if not st else ' / '.join(st)}")
    print(C.line())
    print(f"  致股东信: {len(got)}/{LETTER_YEAR_MAX - LETTER_YEAR_MIN + 1} 年")

    meetings = sorted([r for r in recs if r.get("source") == "annual_meeting"], key=lambda r: r["year"])
    if meetings:
        print()
        print(C.sep())
        print("  股东大会问答完整性校验")
        print(C.sep())
        print(f"  {'年份':<6}{'字符数':>10}{'问答节':>8}  {'状态'}")
        print(C.line())
        for r in meetings:
            n_q = sum(1 for s in (r.get("sections") or []) if s.get("title"))
            st = []
            if r.get("suspect"):
                st.append("文本偏少")
                problems.append(f"年会 {r['year']} 文本偏少 ({r.get('char_len')})")
            # 与致股东信一支同等对待：manifest 记录不能替代对磁盘文件的复查，
            # 否则年会语料被截断或写坏时 verify 会照样报 OK。
            p = C.SKILL_DIR / r.get("path", "")
            if not p.is_file():
                st.append("文件丢失")
                problems.append(f"年会 {r['year']} 文本文件不存在: {r.get('path')}")
            else:
                t = p.read_text(encoding="utf-8", errors="replace")
                if t.count("�") / max(1, len(t)) > 0.01:
                    st.append("乱码")
                    problems.append(f"年会 {r['year']} 含大量替换字符")
                if (r.get("char_len") or 0) and abs(len(t) - r["char_len"]) > max(50, r["char_len"] * 0.01):
                    st.append("与manifest不符")
                    problems.append(
                        f"年会 {r['year']} 磁盘文本 {len(t)} 字与 manifest 记录的 {r['char_len']} 字不符")
            print(f"  {r['year']:<6}{r.get('char_len', 0):>10,}{n_q:>8}  {'OK' if not st else ' / '.join(st)}")
        yrs = [r["year"] for r in meetings]
        missing = [y for y in range(min(yrs), max(yrs) + 1) if y not in yrs]
        print(C.line())
        print(f"  年会: {len(meetings)} 年（{min(yrs)}-{max(yrs)}）")
        if missing:
            print(f"  区间内缺失年份（上游仓库本身没有）: {missing}")

    print()
    if problems:
        print(f"  校验未通过，{len(problems)} 个问题:")
        for p in problems[:40]:
            print(f"    - {p}")
        return 1
    print("  校验通过 ✓")
    return 0


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="buffett-lens 语料采集（致股东信 + 股东大会问答）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出索引页各年份格式与桩页标记（不下载）")

    p_fetch = sub.add_parser("fetch", help="采集致股东信")
    p_fetch.add_argument("--year", type=int, help="指定年份")
    p_fetch.add_argument("--all", action="store_true", help="全部年份（默认行为）")
    p_fetch.add_argument("--refresh", action="store_true", help="连已有年份也重新下载")
    p_fetch.add_argument("--delay", type=float, default=1.0, help="请求间隔秒（默认 1.0）")

    sub.add_parser("fetch-meetings", help="git clone 采集股东大会问答")

    p_local = sub.add_parser("ingest-local", help="接入本地自备材料")
    p_local.add_argument("--dir", required=True, help="材料目录")
    p_local.add_argument("--source", default="annual_meeting", help="来源标识（默认 annual_meeting）")
    p_local.add_argument("--doc-type", default="qa", help="文档类型（默认 qa）")
    p_local.add_argument("--year-regex", default=r"(19|20)\d{2}", help="从文件名提取年份的正则")

    sub.add_parser("verify", help="完整性校验")

    args = parser.parse_args()
    handlers = {
        "list": cmd_list, "fetch": cmd_fetch, "fetch-meetings": cmd_fetch_meetings,
        "ingest-local": cmd_ingest_local, "verify": cmd_verify,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
