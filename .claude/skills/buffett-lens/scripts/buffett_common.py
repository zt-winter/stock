"""
buffett_common.py - buffett-lens 共享底座

功能:
    被本技能全部脚本复用的公共能力，集中在一处以免出现"两份实现"：
    1. 财务库定位：FINANCIAL_DATA_DIR > 当前工作目录 > 向上查找含 financial_data.db 的目录
    2. 语料库定位：buffett_corpus.db / corpus/ / manifest.jsonl（固定在本技能目录下）
    3. 文本归一化：normalize_variants() —— 引用真实性校验的唯一实现，build_index 与
       lint_citations 必须共用；两侧都跑同一套变体才能可靠比对
    4. 终端格式：CJK 宽度感知的 pad()、sep/line/header 等（中文字符按 2 列宽计算）
    5. 检索辅助：CJK/ASCII 分段、署名生成

重要:
    normalize_variants() 是引用闸门的地基。任何脚本都不得自行实现文本归一化，
    否则"引用必须逐字命中语料"的保证会退化成两份不一致的实现中较弱的那份。

依赖:
    pip install requests beautifulsoup4 lxml pymupdf
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
SKILL_DIR = Path(__file__).resolve().parent.parent
CORPUS_DIR = SKILL_DIR / "corpus"
CORPUS_RAW_DIR = CORPUS_DIR / "raw"
CORPUS_TEXT_DIR = CORPUS_DIR / "text"
CORPUS_LOCAL_DIR = CORPUS_DIR / "local"
MANIFEST_PATH = CORPUS_DIR / "manifest.jsonl"
CORPUS_DB_PATH = SKILL_DIR / "buffett_corpus.db"

SCHEMA_VERSION = "1"


def _resolve_data_dir() -> str:
    """定位财务数据目录：FINANCIAL_DATA_DIR 环境变量 > 当前工作目录 > 向上查找含 financial_data.db 的目录。"""
    env_dir = os.environ.get("FINANCIAL_DATA_DIR")
    if env_dir:
        return env_dir
    cwd = Path.cwd()
    if (cwd / "financial_data.db").is_file():
        return str(cwd)
    for parent in Path(__file__).resolve().parents:
        if (parent / "financial_data.db").is_file():
            return str(parent)
    return str(cwd)


DB_PATH = str(Path(_resolve_data_dir()) / "financial_data.db")


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    """打开财务库连接（WAL）。"""
    conn = sqlite3.connect(db_path or DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    conn.row_factory = sqlite3.Row
    return conn


def get_corpus_conn(db_path: str | None = None) -> sqlite3.Connection:
    """打开语料库连接（WAL）。语料库独立于 financial_data.db——后者已在 git 中。"""
    conn = sqlite3.connect(db_path or str(CORPUS_DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    conn.row_factory = sqlite3.Row
    return conn


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 文本归一化 —— 引用真实性校验的唯一实现
# ---------------------------------------------------------------------------
VARIANT_NAMES = ("plain", "dehyphen", "nopunct")

_CURLY = {
    "’": "'", "‘": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "—": "-", "–": "-", "‒": "-", "―": "-",
    " ": " ", "　": " ",
}


def _fold(text: str) -> str:
    """NFKC + 弯引号拉直 + 破折号统一 + 空白折叠。"""
    t = unicodedata.normalize("NFKC", text or "")
    t = "".join(_CURLY.get(ch, ch) for ch in t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def normalize_variants(text: str, lower: bool = False) -> dict[str, str]:
    """把文本编号成三种可比对变体。

    plain     —— 归一分隔符与空白后的原样
    dehyphen  —— 去掉全部连字符。PDF/两端对齐会把词断成 "acquir-\\ning"，
                 或 "well-known" 两侧写法不一致，需要这一变体兜底
    nopunct   —— 去掉全部标点。中英标点混用、译者加逗号等情况靠这一变体兜底

    任一变体命中即视为命中，并报告是哪个变体命中的（便于人工复核）。
    """
    base = _fold(text)
    t = base.lower() if lower else base
    return {
        "plain": t,
        "dehyphen": re.sub(r"-\s*", "", t),
        "nopunct": re.sub(r"[\W_]+", "", t),
    }


def match_quote(quote: str, haystack: str) -> str | None:
    """在 haystack 中查找 quote，返回命中的变体名（含 "+ci" 表示大小写不敏感命中），未命中返回 None。

    先按原样大小写比，再退回全小写比——先紧后松，命中后能把"是否是逐字原文"这件事说清楚。
    """
    if not quote or not quote.strip() or not haystack:
        return None
    for lower, suffix in ((False, ""), (True, "+ci")):
        qv = normalize_variants(quote, lower=lower)
        hv = normalize_variants(haystack, lower=lower)
        for name in VARIANT_NAMES:
            if qv[name] and qv[name] in hv[name]:
                return name + suffix
    return None


def norm_simple(text: str) -> str:
    """入库用的单一归一化形式（plain 小写），供 LIKE 回退与粗筛使用。"""
    return normalize_variants(text, lower=True)["plain"]


# ---------------------------------------------------------------------------
# CJK / 检索分段
# ---------------------------------------------------------------------------
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def is_cjk_char(ch: str) -> bool:
    return bool(_CJK_RE.match(ch))


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def segment_query(query: str) -> list[tuple[str, str]]:
    """把查询切成 (类型, 片段) 列表，类型为 'cjk' 或 'ascii'。用于决定走哪个索引。"""
    segs: list[tuple[str, str]] = []
    cur, cur_kind = [], None
    for ch in query:
        kind = "cjk" if is_cjk_char(ch) else "ascii"
        if cur_kind is None or kind == cur_kind:
            cur.append(ch)
            cur_kind = kind
        else:
            segs.append((cur_kind, "".join(cur).strip()))
            cur, cur_kind = [ch], kind
    if cur:
        segs.append((cur_kind, "".join(cur).strip()))
    return [(k, s) for k, s in segs if s]


# ---------------------------------------------------------------------------
# 终端格式（CJK 宽度感知）
# ---------------------------------------------------------------------------
def disp_width(s: str) -> int:
    """显示宽度：全角/宽字符按 2 列计。中英混排的表格对齐靠它。"""
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s: str, width: int, align: str = "left") -> str:
    """按显示宽度补空格。align: left|right|center。"""
    s = str(s)
    gap = max(0, width - disp_width(s))
    if align == "right":
        return " " * gap + s
    if align == "center":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def wrap(text: str, width: int = 84) -> list[str]:
    """按显示宽度折行。优先在空白处断，中文长串与长数字再按字符硬切。

    之前的 ``re.findall(r".{1,84}")`` 会把 "823.2 亿" 切成 "82" + "3.2 亿"，
    看着像数据错了——数字不能被拆开。
    """
    out: list[str] = []
    cur, curw = "", 0
    for token in re.split(r"(\s+)", text or ""):
        if not token:
            continue
        tw = disp_width(token)
        if curw + tw <= width:
            cur += token
            curw += tw
            continue
        if token.strip() and tw > width:          # 单个超宽 token：按字符硬切
            for ch in token:
                cw = disp_width(ch)
                if curw + cw > width and cur:
                    out.append(cur.rstrip())
                    cur, curw = "", 0
                cur += ch
                curw += cw
            continue
        if cur.strip():
            out.append(cur.rstrip())
        cur, curw = token.lstrip(), disp_width(token.lstrip())
    if cur.strip():
        out.append(cur.rstrip())
    return out or [""]


def sep() -> str:
    return "=" * 70


def line() -> str:
    return "-" * 70


def _cn_num(n: int) -> str:
    """1→一 … 10→十, 11→十一 … 20→二十, 21→二十一 …"""
    d = "一二三四五六七八九"
    if n <= 0:
        return str(n)
    if n < 10:
        return d[n - 1]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + d[n - 11]
    tens, ones = divmod(n, 10)
    return d[tens - 1] + "十" + (d[ones - 1] if ones else "")


def heading(n: int, title: str) -> str:
    """输出【一、标题】样式的段落头。"""
    return f"【{_cn_num(n)}、{title}】"


def fmt(v, spec: str = "{:.2f}", na: str = "N/A") -> str:
    """安全格式化：None/非数字返回 na。"""
    if v is None:
        return na
    try:
        return spec.format(v)
    except (TypeError, ValueError):
        return str(v)


def pct(v, digits: int = 2, na: str = "N/A") -> str:
    """把已是百分数的数值格式化为带 % 的字符串。"""
    if v is None:
        return na
    try:
        return f"{float(v):.{digits}f}%"
    except (TypeError, ValueError):
        return na


# ---------------------------------------------------------------------------
# 署名 —— 语料来源不同，引用时必须标注不同出处
# ---------------------------------------------------------------------------
SOURCE_LABELS = {
    "letters": "巴菲特致股东信",
    "annual_meeting": "巴菲特股东大会问答",
}

# 语料仓库 README 明确要求"所有引用必须注明来自 CNBC"；中译为「一朵喵」。
CNBC_ORIGIN = "https://buffett.cnbc.com/annual-meetings/"
MEETING_TRANSLATOR = "一朵喵（雪球）"


def attribution_for(source: str, year: int | None, doc_type: str | None = None) -> str:
    """生成一行出处署名。问答语料是中译，不能与致股东信（英文一手）混为一谈。"""
    label = SOURCE_LABELS.get(source, source)
    head = f"{label} {year}".strip() if year else label
    if source == "annual_meeting":
        return f"{head}（CNBC 原文 · 中译：{MEETING_TRANSLATOR}）"
    return head


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
def read_manifest(path: Path | None = None) -> list[dict]:
    """读取 manifest.jsonl；容忍尾部写坏的行。"""
    p = path or MANIFEST_PATH
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def write_manifest(records: list[dict], path: Path | None = None) -> None:
    """整体重写 manifest.jsonl，按 (source, year, doc_id) 排序。"""
    p = path or MANIFEST_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        records,
        key=lambda r: (r.get("source", ""), r.get("year") or 0, r.get("doc_id", "")),
    )
    with p.open("w", encoding="utf-8") as f:
        for r in ordered:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def upsert_manifest(records: list[dict]) -> None:
    """按 doc_id 覆盖写入 manifest。"""
    existing = {r["doc_id"]: r for r in read_manifest() if "doc_id" in r}
    for r in records:
        existing[r["doc_id"]] = r
    write_manifest(list(existing.values()))
