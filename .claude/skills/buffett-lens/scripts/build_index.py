#!/usr/bin/env python3
"""
build_index.py - 把 corpus/ 里的文本建成可检索的 FTS5 索引

功能:
    只读 manifest.jsonl 与 corpus/text/ 下的文本文件，写出 buffett_corpus.db。
    不联网、不含任何"致股东信专属"逻辑——这是语料可扩展的契约：任何新语料
    只要写进 manifest + 文本文件，就能被同一套索引与检索流程接住。

子命令:
    build [--rebuild] [--doc-id ID]   建索引（默认增量 upsert；--rebuild 全量重建）
    verify                            校验三表一致、chunk 偏移与原文对齐
    stats                             语料规模概览
    show-chunk --chunk-id ID [--width] 打印单个 chunk 全文

表结构:
    documents   一篇文档一行，存全文与归一化全文（引用闸门对着 full_text_norm 查）
    chunks      语义边界切出的块，chunk_id 形如 letters:1996:0012
    corpus_meta 构建元信息
    fts_zh      trigram 分词（主索引：中文语料占多数，且能精确子串匹配英文）
    fts_en      porter unicode61（辅索引：英文词形归并与 IDF）

为什么是两个 FTS 表:
    trigram 需要查询 ≥3 字符，2 字中文查询（如"分红"）走不了；中文 query 直接命中
    fts_zh 效率最好。而英文查询在 trigram 下退化成"精确子串"，丢掉词形变化
    （acquire/acquiring），所以英文交给 porter。两表结果各自归一化后合并。

依赖:
    无第三方库（sqlite3 需编译进 FTS5；Python 3.11+ 自带 SQLite 通常已含）
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffett_common as C  # noqa: E402

# 分块参数。目标 1200 字是"一次检索结果能读得完、又足够自包含"的折中。
TARGET_CHARS = 1200
MAX_CHARS = 1600
MIN_CHARS = 400
OVERLAP_PARA_MAX = 500  # 相邻 chunk 重叠时，上一块末尾段落超过这个长度就只取句尾


# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------
DDL = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id         TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    year           INTEGER,
    title          TEXT,
    doc_type       TEXT,
    url            TEXT,
    attribution    TEXT,
    fetched_at     TEXT,
    char_len       INTEGER,
    full_text      TEXT NOT NULL,
    full_text_norm TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id   TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id),
    source     TEXT NOT NULL,
    year       INTEGER,
    doc_type   TEXT,
    section    TEXT,
    seq        INTEGER,
    char_start INTEGER,
    char_end   INTEGER,
    lang       TEXT,
    n_chars    INTEGER,
    text       TEXT NOT NULL,
    text_norm  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc     ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source  ON chunks(source, year);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_zh USING fts5(
    chunk_id UNINDEXED, doc_id UNINDEXED, source UNINDEXED, year UNINDEXED,
    section, title, body, tokenize="trigram");

CREATE VIRTUAL TABLE IF NOT EXISTS fts_en USING fts5(
    chunk_id UNINDEXED, doc_id UNINDEXED, source UNINDEXED, year UNINDEXED,
    section, title, body, tokenize="porter unicode61 remove_diacritics 2");

CREATE TABLE IF NOT EXISTS corpus_meta (k TEXT PRIMARY KEY, v TEXT);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# 分块
# ---------------------------------------------------------------------------
class Para:
    __slots__ = ("start", "end", "text")

    def __init__(self, start: int, end: int, text: str):
        self.start, self.end, self.text = start, end, text


def split_paragraphs(text: str, base: int) -> list[Para]:
    """按空行切段，记录每段在全文中的字符偏移。"""
    out, pos = [], 0
    for m in re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", text):
        seg = m.group(0)
        if seg.strip():
            out.append(Para(base + m.start(), base + m.end(), seg.strip()))
        pos = m.end()
    return out


_SENT_END = re.compile(r"(?<=[。！？；!?;])\s*|(?<=[.])\s+(?=[A-Z0-9“\"'(])")


def split_long_para(p: Para) -> list[Para]:
    """把超长段落按句末切开；切不动就硬切。保证每块 ≤ MAX_CHARS 附近。"""
    if len(p.text) <= MAX_CHARS:
        return [p]
    bounds, cur = [], 0
    for m in _SENT_END.finditer(p.text):
        if m.end() - cur >= TARGET_CHARS:
            bounds.append(m.end())
            cur = m.end()
    bounds.append(len(p.text))
    out, prev = [], 0
    for b in bounds:
        seg = p.text[prev:b]
        if not seg.strip():
            prev = b
            continue
        lead = len(seg) - len(seg.lstrip())
        out.append(Para(p.start + prev + lead, p.start + b, seg.strip()))
        prev = b
    return out or [p]


def pack_section(text: str, base: int, *, overlap: bool) -> list[tuple[int, int]]:
    """把一段文本贪心聚合成若干 (char_start, char_end)。

    overlap=True 时，每个块会向前吃掉上一块的末尾段落作为重叠——只在段落接缝处
    重叠，不做字符级切分，读起来不会从半句话开始。
    """
    paras: list[Para] = []
    for p in split_paragraphs(text, base):
        paras.extend(split_long_para(p))
    if not paras:
        return []

    groups: list[list[int]] = [[0]]
    for i in range(1, len(paras)):
        head = paras[groups[-1][0]].start
        if paras[i].end - head <= TARGET_CHARS:
            groups[-1].append(i)
        else:
            groups.append([i])

    spans: list[tuple[int, int]] = []
    for gi, g in enumerate(groups):
        start = paras[g[0]].start
        if overlap and gi > 0:
            prev_last = paras[groups[gi - 1][-1]]
            if len(prev_last.text) <= OVERLAP_PARA_MAX:
                start = prev_last.start
            else:
                # 上一段太长，只回退到它句尾附近，避免重叠体量失控
                cut = max(prev_last.start, prev_last.end - MAX_CHARS // 2)
                m = re.search(r"[。！？；.!?;]\s*", prev_last.text[cut - prev_last.start:])
                start = cut + (m.end() if m else 0)
        spans.append((start, paras[g[-1]].end))

    # 小块向后合并（只在同一段落序列内，不跨文档/不跨小节调用方已保证）
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and (e - s) < MIN_CHARS and (e - merged[-1][0]) <= MAX_CHARS:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def chunk_spans(full_text: str, sections: list[dict], source: str) -> list[dict]:
    """按小节边界切块。返回 [{section, char_start, char_end}]。

    问答语料：每条 `### N、标题` 自包含，独立成块，不跨条合并。
    致股东信：小节内贪心聚合 + 段落接缝重叠；小节之间不越界。
    """
    secs = [s for s in (sections or []) if s.get("start") is not None]
    secs = [s for s in secs if 0 <= s["start"] < len(full_text)]
    if not secs:
        secs = [{"title": None, "start": 0, "end": len(full_text)}]
    else:
        secs = sorted(secs, key=lambda s: s["start"])
        if secs[0]["start"] > 0:  # 第一个小标题之前的前言也要能检索到
            secs.insert(0, {"title": None, "start": 0, "end": secs[0]["start"]})

    out: list[dict] = []
    for i, s in enumerate(secs):
        end = s.get("end")
        if not end or end > len(full_text) or end <= s["start"]:
            end = secs[i + 1]["start"] if i + 1 < len(secs) else len(full_text)
        body = full_text[s["start"]:end]
        if not body.strip():
            continue
        if source == "annual_meeting":
            # 一条问答一个块；超长才再切，且切块之间不重叠（避免问题标题重复出现在
            # 多条检索结果里造成"同一段话被当成多处依据"）
            for a, b in pack_section(body, s["start"], overlap=False):
                out.append({"section": s.get("title"), "char_start": a, "char_end": b})
        else:
            for a, b in pack_section(body, s["start"], overlap=True):
                out.append({"section": s.get("title"), "char_start": a, "char_end": b})
    return out


def detect_lang(text: str) -> str:
    if not text:
        return "en"
    n_cjk = sum(1 for ch in text if C.is_cjk_char(ch))
    return "zh" if n_cjk / max(1, len(text)) > 0.05 else "en"


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def text_path_for(rec: dict) -> Path:
    p = rec.get("path")
    if p:
        cand = C.SKILL_DIR / p
        if cand.is_file():
            return cand
    return C.CORPUS_TEXT_DIR / rec["source"] / f"{rec['year']}.txt"


def build_doc(conn: sqlite3.Connection, rec: dict) -> int:
    """写入/覆盖一篇文档及其 chunks，返回 chunk 数。"""
    doc_id = rec["doc_id"]
    tp = text_path_for(rec)
    if not tp.is_file():
        print(f"  [跳过] {doc_id}: 文本文件不存在 {tp}", file=sys.stderr)
        return 0
    full_text = tp.read_text(encoding="utf-8")
    full_norm = C.norm_simple(full_text)

    conn.execute("DELETE FROM fts_zh WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM fts_en WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
    conn.execute(
        "INSERT INTO documents (doc_id, source, year, title, doc_type, url, attribution,"
        " fetched_at, char_len, full_text, full_text_norm) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, rec.get("source"), rec.get("year"), rec.get("title"), rec.get("doc_type"),
         rec.get("url"), rec.get("attribution"), rec.get("fetched_at"),
         len(full_text), full_text, full_norm),
    )

    spans = chunk_spans(full_text, rec.get("sections") or [], rec.get("source") or "")
    n = 0
    for seq, sp in enumerate(spans, 1):
        text = full_text[sp["char_start"]:sp["char_end"]]
        if not text.strip():
            continue
        n += 1
        cid = f"{rec.get('source')}:{rec.get('year')}:{n:04d}"
        section = sp.get("section") or ""
        title = rec.get("title") or ""
        lang = detect_lang(text)
        conn.execute(
            "INSERT INTO chunks (chunk_id, doc_id, source, year, doc_type, section, seq,"
            " char_start, char_end, lang, n_chars, text, text_norm)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, doc_id, rec.get("source"), rec.get("year"), rec.get("doc_type"),
             section, n, sp["char_start"], sp["char_end"], lang, len(text),
             text, C.norm_simple(text)),
        )
        for tbl in ("fts_zh", "fts_en"):
            conn.execute(
                f"INSERT INTO {tbl} (chunk_id, doc_id, source, year, section, title, body)"
                " VALUES (?,?,?,?,?,?,?)",
                (cid, doc_id, rec.get("source"), rec.get("year"), section, title, text),
            )
    return n


def cmd_build(args) -> int:
    if not C.MANIFEST_PATH.is_file():
        print(f"manifest 不存在: {C.MANIFEST_PATH}", file=sys.stderr)
        print("请先运行 fetch_letters.py fetch --all / fetch-meetings", file=sys.stderr)
        return 1

    conn = C.get_corpus_conn()
    if args.rebuild:
        for t in ("fts_zh", "fts_en", "chunks", "documents", "corpus_meta"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
    init_db(conn)

    recs = C.read_manifest()
    if args.doc_id:
        want = set(args.doc_id)
        recs = [r for r in recs if r["doc_id"] in want]
    if not recs:
        print("manifest 为空，无可建索引的文档。", file=sys.stderr)
        return 1

    total = 0
    for rec in recs:
        n = build_doc(conn, rec)
        total += n
        flag = "  [suspect]" if rec.get("suspect") else ""
        print(f"  {rec['doc_id']:<28} {rec.get('char_len', 0):>8,} 字  {n:>4} 块{flag}")
    conn.commit()

    _refresh_meta(conn)
    print(C.line())
    print(f"索引完成：{len(recs)} 篇文档 / {total} 块 → {C.CORPUS_DB_PATH}")
    if total == 0:
        print("警告：没有产出任何 chunk，检索将不可用。", file=sys.stderr)
        return 1
    return 0


def _refresh_meta(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT COUNT(*), MIN(year), MAX(year) FROM documents"
    ).fetchone()
    n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    meta = {
        "schema_version": C.SCHEMA_VERSION,
        "build_at": C.now_iso(),
        "n_docs": str(row[0]),
        "n_chunks": str(n_chunks),
        "years_min": str(row[1] if row[1] is not None else ""),
        "years_max": str(row[2] if row[2] is not None else ""),
    }
    for src, cnt, ymin, ymax in conn.execute(
        "SELECT source, COUNT(*), MIN(year), MAX(year) FROM documents GROUP BY source"
    ):
        meta[f"src.{src}.docs"] = str(cnt)
        meta[f"src.{src}.years"] = f"{ymin}-{ymax}"
    for k, v in meta.items():
        conn.execute("INSERT OR REPLACE INTO corpus_meta (k, v) VALUES (?,?)", (k, v))
    conn.commit()


# ---------------------------------------------------------------------------
# verify / stats / show-chunk
# ---------------------------------------------------------------------------
def cmd_verify(_args) -> int:
    if not C.CORPUS_DB_PATH.is_file():
        print(f"索引库不存在: {C.CORPUS_DB_PATH}", file=sys.stderr)
        return 1
    conn = C.get_corpus_conn()
    problems: list[str] = []

    manifest_ids = {r["doc_id"] for r in C.read_manifest() if not r.get("suspect")}
    doc_ids = {r[0] for r in conn.execute("SELECT doc_id FROM documents")}
    missing, extra = manifest_ids - doc_ids, doc_ids - manifest_ids
    if missing:
        problems.append(f"manifest 有但索引缺失 {len(missing)} 篇: {sorted(missing)[:5]}")
    if extra:
        problems.append(f"索引有但 manifest 无 {len(extra)} 篇: {sorted(extra)[:5]}")

    n_doc = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    n_chunk = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    n_zh = conn.execute("SELECT COUNT(*) FROM fts_zh").fetchone()[0]
    n_en = conn.execute("SELECT COUNT(*) FROM fts_en").fetchone()[0]
    if not (n_chunk == n_zh == n_en):
        problems.append(f"三表计数不等: chunks={n_chunk} fts_zh={n_zh} fts_en={n_en}")

    # chunk 文本必须与全文偏移严格对齐——偏移错位会让"引用是真的"这个结论失效
    bad_off = bad_norm = 0
    for r in conn.execute(
        "SELECT c.chunk_id, c.char_start, c.char_end, c.text, c.text_norm,"
        " d.full_text, d.full_text_norm FROM chunks c JOIN documents d USING (doc_id)"
    ):
        if r["full_text"][r["char_start"]:r["char_end"]] != r["text"]:
            bad_off += 1
        if r["text_norm"] != C.norm_simple(r["text"]):
            bad_norm += 1
        if r["text_norm"] not in r["full_text_norm"]:
            bad_norm += 1
    if bad_off:
        problems.append(f"{bad_off} 个 chunk 的文本与 char_start/char_end 不匹配")
    if bad_norm:
        problems.append(f"{bad_norm} 个 chunk 的 text_norm 与全文对不上")

    empty = conn.execute("SELECT COUNT(*) FROM documents WHERE full_text_norm = ''").fetchone()[0]
    if empty:
        problems.append(f"{empty} 篇文档归一化后为空")

    print(C.sep())
    print(f"索引校验：{n_doc} 篇文档 / {n_chunk} 块 / fts_zh {n_zh} / fts_en {n_en}")
    print(C.sep())
    for src, cnt, ymin, ymax in conn.execute(
        "SELECT source, COUNT(DISTINCT year), MIN(year), MAX(year) FROM documents GROUP BY source"
    ):
        n_ch = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE source = ?", (src,)
        ).fetchone()[0]
        print(f"  {src:<16} {cnt:>3} 年  {ymin}-{ymax}  {n_ch:>6} 块")
    if problems:
        print(C.line())
        for p in problems:
            print(f"  ✗ {p}")
        print("校验未通过。")
        return 1
    print(C.line())
    print("校验通过：三表一致，chunk 偏移与全文严格对齐。")
    return 0


def cmd_stats(_args) -> int:
    if not C.CORPUS_DB_PATH.is_file():
        print("索引库不存在，请先运行 build。", file=sys.stderr)
        return 1
    conn = C.get_corpus_conn()
    print(C.sep())
    print("buffett-lens 语料规模")
    print(C.sep())
    tot_ch = tot_len = 0
    for src in ("letters", "annual_meeting"):
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT year), MIN(year), MAX(year),"
            " COALESCE(SUM(char_len),0) FROM documents WHERE source = ?", (src,)
        ).fetchone()
        n_ch = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(n_chars),0) FROM chunks WHERE source = ?", (src,)
        ).fetchone()
        if not row[0]:
            print(f"{C.SOURCE_LABELS.get(src, src):<18} 未收录")
            continue
        tot_ch += n_ch[0]
        tot_len += row[4]
        print(f"{C.SOURCE_LABELS.get(src, src):<18} {row[0]:>3} 篇  {row[1]:>2} 年  "
              f"{row[2]}-{row[3]}  {row[4]:>9,} 字  {n_ch[0]:>5} 块")
        yrs = [r[0] for r in conn.execute(
            "SELECT DISTINCT year FROM documents WHERE source = ? ORDER BY year", (src,))]
        gaps = [y for y in range(min(yrs), max(yrs) + 1) if y not in set(yrs)]
        if gaps:
            print(f"{'':<18} 缺年：{', '.join(str(y) for y in gaps)}")
    print(C.line())
    print(f"{'合计':<18} {tot_len:>9,} 字  {tot_ch:>5} 块")
    row = conn.execute(
        "SELECT v FROM corpus_meta WHERE k = 'build_at'").fetchone()
    if row:
        print(f"{'构建时间':<18} {row[0]}")
    print(f"{'索引库':<18} {C.CORPUS_DB_PATH}")
    return 0


def cmd_show_chunk(args) -> int:
    conn = C.get_corpus_conn()
    r = conn.execute(
        "SELECT c.*, d.title, d.attribution, d.url FROM chunks c"
        " JOIN documents d USING (doc_id) WHERE c.chunk_id = ?", (args.chunk_id,)
    ).fetchone()
    if not r:
        print(f"找不到 chunk: {args.chunk_id}", file=sys.stderr)
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if not n:
            print("索引为空，请先运行 build。", file=sys.stderr)
        return 1
    print(C.sep())
    print(f"{r['chunk_id']}  ·  {r['attribution'] or r['title']}")
    if r["section"]:
        print(f"小节: {r['section']}")
    print(f"偏移: {r['char_start']}-{r['char_end']}  ·  {r['n_chars']} 字  ·  {r['lang']}")
    if r["url"]:
        print(f"来源: {r['url']}")
    print(C.sep())
    txt = r["text"]
    if args.width and len(txt) > args.width:
        txt = txt[:args.width] + f"\n…（已截断，共 {r['n_chars']} 字，去掉 --width 看全文）"
    print(txt)
    return 0


# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(
        description="把 buffett-lens 语料建成 FTS5 索引",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  build_index.py build --rebuild      全量重建索引
  build_index.py build --doc-id letters:1996
  build_index.py verify               校验三表一致与偏移对齐
  build_index.py stats                语料规模概览
  build_index.py show-chunk --chunk-id letters:1996:0012
""")
    sub = p.add_subparsers(dest="cmd")

    b = sub.add_parser("build", help="建索引")
    b.add_argument("--rebuild", action="store_true", help="先删表再全量重建")
    b.add_argument("--doc-id", action="append", help="只重建指定 doc_id（可重复）")
    b.set_defaults(func=cmd_build)

    sub.add_parser("verify", help="校验索引一致性").set_defaults(func=cmd_verify)
    sub.add_parser("stats", help="语料规模概览").set_defaults(func=cmd_stats)

    s = sub.add_parser("show-chunk", help="打印单个 chunk")
    s.add_argument("--chunk-id", required=True)
    s.add_argument("--width", type=int, default=0, help="截断到 N 字，0 = 全文")
    s.set_defaults(func=cmd_show_chunk)

    args = p.parse_args()
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    try:
        return args.func(args)
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            print("索引库尚未初始化，请先运行 build。", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
