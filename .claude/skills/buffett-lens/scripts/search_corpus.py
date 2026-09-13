#!/usr/bin/env python3
"""
search_corpus.py - buffett-lens 语料检索

功能:
    在致股东信（英文一手）与股东大会问答（中译）里做全文检索，返回可溯源的
    原文片段。所有结果都带 chunk_id / 年份 / 来源署名——报告里引用的每一句话
    都必须能追到这里，再由 lint_citations.py 复核。

子命令:
    search --query Q [...]              检索
    get --chunk-id ID [--context N]     取单个 chunk（可带上下文）
    verify-quote --year Y --text "..."  校验一句话是否真在 Y 年语料中（exit 0/1）
    years                               各来源覆盖年份
    sections --year Y                   列出某年的小节标题
    stats                               语料规模

索引路由:
    查询按 CJK / ASCII 切段分别走两个 FTS 表——中文语料占多数，trigram 对中文
    效果最好且能精确子串匹配；英文交给 porter 以保留词形归并。
    CJK 段 <3 字时 trigram 用不了，回退到 LIKE 扫描（实现，不是假设）。

重要:
    0 结果一律附可执行的提示，绝不呈现为"巴菲特没说过"——语料缺 2017/2018 年会，
    检索不到可能是覆盖问题而非事实问题。

依赖:
    无第三方库
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffett_common as C  # noqa: E402

KNOWN_VERBS = {"search", "get", "verify-quote", "years", "sections", "stats"}
MIN_TRIGRAM = 3
SNIPPET_PAD = 220

_RESET = "\033[0m"
_HL_ON = "\033[1;33m"


def _hl(text: str, terms: list[str]) -> str:
    """把命中词加粗。非 tty 时不加 ANSI，避免污染管道输出。"""
    if not sys.stdout.isatty():
        return text
    for t in sorted((t for t in terms if len(t) >= 2), key=len, reverse=True):
        text = re.sub(re.escape(t), lambda m: f"{_HL_ON}{m.group(0)}{_RESET}", text,
                      flags=re.IGNORECASE)
    return text


def _fts_escape(term: str) -> str:
    """FTS5 字符串字面量：内部双引号翻倍。"""
    return '"' + term.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# 查询规划
# ---------------------------------------------------------------------------
def plan_query(query: str) -> dict:
    """把查询拆成可路由的部分。

    返回 {cjk_terms, short_cjk, ascii_terms}。CJK 段按连续中文切；<3 字的段进
    short_cjk（走 LIKE），其余进 cjk_terms（走 trigram）。
    """
    segs = C.segment_query(query)
    cjk_terms, short_cjk, ascii_terms = [], [], []
    for kind, seg in segs:
        if kind == "cjk":
            if len(seg) >= MIN_TRIGRAM:
                cjk_terms.append(seg)
            else:
                short_cjk.append(seg)
        else:
            for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-_]*", seg):
                if len(w) >= 2:
                    ascii_terms.append(w)
    if not (cjk_terms or short_cjk or ascii_terms) and query.strip():
        short_cjk.append(query.strip())
    return {"cjk": cjk_terms, "short": short_cjk, "ascii": ascii_terms}


def build_where(args) -> tuple[str, list]:
    """拼 chunks 表的过滤条件（对两个 FTS 表用别名 c）。"""
    sql, params = [], []
    if args.source:
        sql.append("c.source = ?")
        params.append(args.source)
    if args.doc_type:
        sql.append("c.doc_type = ?")
        params.append(args.doc_type)
    if getattr(args, "year_from", None):
        sql.append("c.year >= ?")
        params.append(args.year_from)
    if getattr(args, "year_to", None):
        sql.append("c.year <= ?")
        params.append(args.year_to)
    if getattr(args, "section_like", None):
        sql.append("c.section LIKE ?")
        params.append(f"%{args.section_like}%")
    return (" AND " + " AND ".join(sql) if sql else ""), params


def run_fts(conn, table: str, match_expr: str, args, extra_filter) -> dict[str, dict]:
    """跑一个 FTS 表，返回 {chunk_id: {rel, raw, via}}。bm25 归一化后再合并。"""
    sql = (f"SELECT c.chunk_id, c.doc_id, c.source, c.year, c.doc_type, c.section,"
           f" c.n_chars, c.text, c.lang, bm25({table}) AS score"
           f" FROM {table} JOIN chunks c ON c.chunk_id = {table}.chunk_id"
           f" WHERE {table} MATCH ?{extra_filter.sql}")
    try:
        rows = conn.execute(sql, [match_expr, *extra_filter.extra_params]).fetchall()
    except sqlite3.OperationalError as e:
        print(f"  [检索警告] {table} 查询失败: {e}", file=sys.stderr)
        return {}
    if not rows:
        return {}
    rows = [r for r in rows if _pass_filters(r, args)]
    if not rows:
        return {}
    raw = [-r["score"] for r in rows]  # bm25 是负值，取反后越大越相关
    best = max(raw) or 1.0
    out = {}
    for r, s in zip(rows, raw):
        out[r["chunk_id"]] = {"row": r, "rel": s / best, "raw": s, "via": table}
    return out


def _pass_filters(row, args) -> bool:
    """FTS 表里存了 source/year/section 的副本，但过滤统一走 chunks 表更可靠。"""
    if args.source and row["source"] != args.source:
        return False
    if args.doc_type and row["doc_type"] != args.doc_type:
        return False
    if getattr(args, "year_from", None) and (row["year"] or 0) < args.year_from:
        return False
    if getattr(args, "year_to", None) and (row["year"] or 0) > args.year_to:
        return False
    if getattr(args, "section_like", None):
        if args.section_like.lower() not in (row["section"] or "").lower():
            return False
    return True


def run_like(conn, terms: list[str], args) -> dict[str, dict]:
    """短中文查询（<3 字）的 LIKE 回退。trigram 需要 ≥3 字符，这条路必须存在。"""
    if not terms:
        return {}
    where, params = build_where(args)
    out: dict[str, dict] = {}
    for term in terms:
        pat = f"%{C.norm_simple(term)}%"
        sql = (f"SELECT c.chunk_id, c.doc_id, c.source, c.year, c.doc_type, c.section,"
               f" c.n_chars, c.text, c.text_norm, c.lang FROM chunks c"
               f" WHERE c.text_norm LIKE ?{where} LIMIT 400")
        rows = conn.execute(sql, [pat, *params]).fetchall()
        if not rows:
            continue
        counts = [(r, r["text_norm"].count(C.norm_simple(term))) for r in rows]
        best = max(c for _, c in counts) or 1
        for r, cnt in counts:
            prev = out.get(r["chunk_id"])
            rel = 0.95 * (cnt / best)  # 略低于 FTS 满分，避免压过真正的相关命中
            if not prev or rel > prev["rel"]:
                out[r["chunk_id"]] = {"row": r, "rel": rel, "raw": float(cnt),
                                      "via": "like"}
    return out


def do_search(conn, args) -> list[dict]:
    plan = plan_query(args.query)
    wanted = args.index
    results: dict[str, dict] = {}

    def merge(d):
        for cid, hit in d.items():
            cur = results.get(cid)
            if not cur:
                results[cid] = dict(hit)
            else:
                cur["rel"] = max(cur["rel"], hit["rel"])
                cur["raw"] = max(cur["raw"], hit["raw"])
                if hit["via"] not in cur["via"]:
                    cur["via"] = f"{cur['via']}+{hit['via']}"

    where, params = build_where(args)
    if plan["cjk"] and wanted in ("auto", "zh", "both"):
        expr = " AND ".join(_fts_escape(t) for t in plan["cjk"])
        merge(run_fts(conn, "fts_zh", expr, args, _F(where, params)))
    if plan["ascii"] and wanted in ("auto", "en", "both"):
        expr = " OR ".join(_fts_escape(t) for t in plan["ascii"])
        merge(run_fts(conn, "fts_en", expr, args, _F(where, params)))
    if plan["short"]:
        merge(run_like(conn, plan["short"], args))
    # 显式 --index both 时，把另一条路也跑上
    if wanted == "both":
        if plan["ascii"]:
            merge(run_fts(conn, "fts_zh", " AND ".join(_fts_escape(t) for t in plan["ascii"]),
                          args, _F(where, params)))
        if plan["cjk"]:
            merge(run_fts(conn, "fts_en", " OR ".join(_fts_escape(t) for t in plan["cjk"]),
                          args, _F(where, params)))

    rows = list(results.values())
    if args.sort == "year":
        rows.sort(key=lambda h: ((h["row"]["year"] or 0), -h["rel"]), reverse=True)
    else:
        # 先按相关度，同分时新证据在前（不做事前的时间衰减——1977/1986 的信正是经典依据）
        rows.sort(key=lambda h: (-h["rel"], -(h["row"]["year"] or 0)))
    return rows[: args.limit]


class _F:
    """把 (where_sql, params) 打包传给 run_fts，避免参数散落。"""

    __slots__ = ("sql", "extra_params")

    def __init__(self, sql: str, params: list):
        self.sql, self.extra_params = sql, params


# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------
def make_snippet(text: str, plan: dict, pad: int = SNIPPET_PAD) -> tuple[str, list[str]]:
    """在命中位置附近取一段，而不是永远从 chunk 开头截。"""
    terms = plan["cjk"] + plan["short"] + plan["ascii"]
    low = text.lower()
    pos, hit_term = -1, None
    for t in terms:
        p = low.find(t.lower())
        if p >= 0 and (pos < 0 or p < pos):
            pos, hit_term = p, t
    if pos < 0:
        head = text[: pad * 2]
        return (head + ("…" if len(text) > len(head) else "")), []
    start = max(0, pos - pad)
    end = min(len(text), pos + len(hit_term) + pad)
    # 贴到空白/换行边界，避免从半个词开始
    while start > 0 and text[start - 1] not in " \n\t，。；：、":
        start -= 1
    while end < len(text) and text[end] not in " \n\t，。；：、":
        end += 1
    snip = text[start:end].strip().replace("\n", " ")
    snip = re.sub(r"\s{2,}", " ", snip)
    return (("…" if start > 0 else "") + snip + ("…" if end < len(text) else "")), terms


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
def cmd_search(args) -> int:
    if not C.CORPUS_DB_PATH.is_file():
        print("索引库不存在，请先运行 build_index.py build。", file=sys.stderr)
        return 1
    conn = C.get_corpus_conn()
    plan = plan_query(args.query)
    rows = do_search(conn, args)

    if args.json:
        print(json.dumps([{
            "chunk_id": h["row"]["chunk_id"], "doc_id": h["row"]["doc_id"],
            "source": h["row"]["source"], "year": h["row"]["year"],
            "section": h["row"]["section"], "doc_type": h["row"]["doc_type"],
            "rel": round(h["rel"], 4), "via": h["via"], "n_chars": h["row"]["n_chars"],
            "attribution": C.attribution_for(h["row"]["source"], h["row"]["year"]),
            "text": h["row"]["text"],
        } for h in rows], ensure_ascii=False, indent=2))
        return 0 if rows else 1

    if not rows:
        _no_result_hint(conn, args, plan)
        return 1

    print(C.sep())
    print(f"检索「{args.query}」  命中 {len(rows)} 条"
          f"{'  ·  来源 ' + args.source if args.source else ''}")
    print(C.sep())
    for i, h in enumerate(rows, 1):
        r = h["row"]
        attr = C.attribution_for(r["source"], r["year"])
        snip, terms = make_snippet(r["text"], plan)
        print(f"【{i}】 rel={h['rel']:.2f}  {attr}")
        line = f"     {r['chunk_id']}"
        if r["section"]:
            line += f"  小节: {r['section']}"
        print(C.pad(line, 100))
        print(f"     来源: {h['via']}  ·  {r['n_chars']} 字")
        for ln in re.findall(r".{1,86}", snip):
            print("     " + _hl(ln, terms))
        print()
    print(C.line())
    print("取全文: search_corpus.py get --chunk-id <ID>")
    print("引用前请用 lint_citations.py 复核，勿凭记忆转述。")
    return 0


def _no_result_hint(conn, args, plan) -> None:
    cov = _coverage_text(conn)
    print(C.sep())
    print(f"检索「{args.query}」：0 结果。")
    print(C.sep())
    print("这不是「巴菲特没说过」的证据——只说明在当前语料与查询写法下没匹配到。")
    print(f"当前语料：{cov}")
    print(C.line())
    print("可尝试：")
    if plan["cjk"] and not plan["ascii"]:
        print("  · 换成英文原词（致股东信是英文原文），如「护城河」→「moat」")
    if plan["ascii"] and not plan["cjk"]:
        print("  · 换成中文词（股东大会问答是中译），如「moat」→「护城河」")
    if len(args.query.strip()) < 3:
        print("  · 查询过短，换个更具体的词组")
    if args.source or args.doc_type or getattr(args, "year_from", None) or getattr(args, "year_to", None):
        print("  · 去掉 --source / --doc-type / 年份 限制后重试（当前有过滤条件）")
    print("  · 试试同义表达：护城河/竞争优势/定价权、安全边际/折价、留存收益/一美元测试")
    print("  · 运行 years 查看实际覆盖年份（年会语料缺 2017、2018）")


def _coverage_text(conn) -> str:
    parts = []
    for src in ("letters", "annual_meeting"):
        row = conn.execute(
            "SELECT COUNT(*), MIN(year), MAX(year) FROM documents WHERE source = ?",
            (src,)).fetchone()
        if row and row[0]:
            lang = "英文原文" if src == "letters" else "中文"
            parts.append(f"{C.SOURCE_LABELS.get(src, src)} {row[1]}-{row[2]}（{lang}）")
    return " + ".join(parts) if parts else "（空）"


def cmd_get(args) -> int:
    if not C.CORPUS_DB_PATH.is_file():
        print("索引库不存在，请先运行 build_index.py build。", file=sys.stderr)
        return 1
    conn = C.get_corpus_conn()
    r = conn.execute(
        "SELECT c.*, d.title, d.attribution, d.url FROM chunks c"
        " JOIN documents d USING (doc_id) WHERE c.chunk_id = ?", (args.chunk_id,)
    ).fetchone()
    if not r:
        print(f"找不到 chunk: {args.chunk_id}", file=sys.stderr)
        return 1
    print(C.sep())
    print(f"{r['chunk_id']}  ·  {C.attribution_for(r['source'], r['year'])}")
    if r["section"]:
        print(f"小节: {r['section']}")
    print(f"偏移: {r['char_start']}-{r['char_end']}  ·  {r['n_chars']} 字")
    if r["url"]:
        print(f"来源: {r['url']}")
    if r["source"] == "annual_meeting":
        print(f"英文原声: {C.CNBC_ORIGIN}")
    print(C.sep())
    print(r["text"])
    if args.context:
        for label, lo, hi in (("上文", max(0, r["char_start"] - args.context), r["char_start"]),
                              ("下文", r["char_end"], r["char_end"] + args.context)):
            if lo >= hi:
                continue
            ctx = conn.execute(
                "SELECT SUBSTR(full_text, ?, ?) AS s FROM documents WHERE doc_id = ?",
                (lo + 1, hi - lo, r["doc_id"])).fetchone()
            if ctx and ctx["s"].strip():
                print(C.line())
                print(f"…{label}…")
                print(ctx["s"].strip())
    return 0


def cmd_verify_quote(args) -> int:
    """校验一句话是否真的出现在指定年份的语料里。命中即 exit 0，并报出变体与 chunk。"""
    if not C.CORPUS_DB_PATH.is_file():
        print("索引库不存在，请先运行 build_index.py build。", file=sys.stderr)
        return 1
    conn = C.get_corpus_conn()
    rows = conn.execute(
        "SELECT doc_id, source, year, full_text FROM documents WHERE year = ?", (args.year,)
    ).fetchall()
    if not rows:
        print(f"✗ {args.year} 年语料不存在。", file=sys.stderr)
        print(f"  该年份未收录；运行 years 查看覆盖范围。", file=sys.stderr)
        return 1

    for r in rows:
        variant = C.match_quote(args.text, r["full_text"])
        if not variant:
            continue
        chunk = conn.execute(
            "SELECT chunk_id FROM chunks WHERE doc_id = ? AND text_norm LIKE ? LIMIT 1",
            (r["doc_id"], f"%{C.norm_simple(args.text)}%")).fetchone()
        print(f"✓ 命中  {C.attribution_for(r['source'], r['year'])}")
        print(f"  变体: {variant}"
              f"{'（大小写不敏感）' if variant.endswith('+ci') else ''}"
              f"{'（去标点后命中，原文标点与引用不同）' if variant.startswith('nopunct') else ''}"
              f"{'（去连字符后命中，原文可能跨行断词）' if variant.startswith('dehyphen') else ''}")
        print(f"  chunk_id: {chunk['chunk_id'] if chunk else '（跨 chunk 边界，无法定位单个 chunk）'}")
        return 0

    print(f"✗ 未在 {args.year} 年语料中找到该句。", file=sys.stderr)
    for r in rows:
        print(f"  （已检索 {r['source']} {r['year']}，共 {len(r['full_text']):,} 字）",
              file=sys.stderr)
    print("  引用必须逐字可查，请勿凭记忆转述。", file=sys.stderr)
    return 1


def cmd_years(args) -> int:
    if not C.CORPUS_DB_PATH.is_file():
        print("索引库不存在，请先运行 build_index.py build。", file=sys.stderr)
        return 1
    conn = C.get_corpus_conn()
    out = {}
    print(C.sep())
    print("语料覆盖年份")
    print(C.sep())
    for src in ("letters", "annual_meeting"):
        yrs = [r[0] for r in conn.execute(
            "SELECT DISTINCT year FROM documents WHERE source = ? ORDER BY year", (src,))]
        out[src] = yrs
        print(f"{C.SOURCE_LABELS.get(src, src)}（{len(yrs)} 年）")
        if not yrs:
            print("    （未收录）")
            continue
        for i in range(0, len(yrs), 12):
            print("    " + "  ".join(str(y) for y in yrs[i:i + 12]))
        gaps = [y for y in range(min(yrs), max(yrs) + 1) if y not in set(yrs)]
        if gaps:
            print(f"    缺: {', '.join(str(y) for y in gaps)}")
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_sections(args) -> int:
    if not C.CORPUS_DB_PATH.is_file():
        print("索引库不存在，请先运行 build_index.py build。", file=sys.stderr)
        return 1
    conn = C.get_corpus_conn()
    rows = conn.execute(
        "SELECT doc_id, source, title, section, COUNT(*) AS n, SUM(n_chars) AS chars"
        " FROM chunks WHERE year = ? GROUP BY doc_id, section ORDER BY doc_id, MIN(seq)",
        (args.year,)).fetchall()
    if not rows:
        print(f"{args.year} 年无 chunk。运行 years 查看覆盖范围。", file=sys.stderr)
        return 1
    cur = None
    for r in rows:
        if r["doc_id"] != cur:
            cur = r["doc_id"]
            print(C.sep())
            print(f"{C.attribution_for(r['source'], args.year)}  ·  {r['doc_id']}")
            print(C.sep())
        name = r["section"] or "（无小节标题）"
        print(f"  {C.pad(name, 60)} {r['n']:>3} 块 {r['chars']:>7,} 字")
    return 0


def cmd_stats(args) -> int:
    """直接委托 build_index.py，避免两份统计口径。"""
    sys.argv = [sys.argv[0], "stats"]
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_index
    return build_index.cmd_stats(args)


# ---------------------------------------------------------------------------
def main() -> int:
    argv = sys.argv[1:]
    # 便利性：search_corpus.py "护城河" 直接当 search 用
    if argv and argv[0] not in KNOWN_VERBS and not argv[0].startswith("-"):
        argv = ["search", "--query", argv[0], *argv[1:]]
    elif not argv:
        argv = ["-h"]

    p = argparse.ArgumentParser(
        description="buffett-lens 语料检索",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  search_corpus.py "护城河" --limit 5
  search_corpus.py search --query "circle of competence" --source letters
  search_corpus.py search --query "安全边际" --year-from 1990 --year-to 2010
  search_corpus.py get --chunk-id letters:1996:0012 --context 500
  search_corpus.py verify-quote --year 1996 --text "护城河"
  search_corpus.py sections --year 1996
""")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("search", help="检索语料")
    s.add_argument("--query", "-q", required=True)
    s.add_argument("--source", choices=["letters", "annual_meeting"])
    s.add_argument("--doc-type", dest="doc_type",
                   choices=["letter", "qa", "highlights"])
    s.add_argument("--year-from", dest="year_from", type=int)
    s.add_argument("--year-to", dest="year_to", type=int)
    s.add_argument("--section-like", dest="section_like", help="小节标题包含此串")
    s.add_argument("--index", choices=["auto", "zh", "en", "both"], default="auto")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--sort", choices=["relevance", "year"], default="relevance")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

    g = sub.add_parser("get", help="取 chunk 全文")
    g.add_argument("--chunk-id", required=True)
    g.add_argument("--context", type=int, default=0, help="额外打印上下文字数")
    g.set_defaults(func=cmd_get)

    v = sub.add_parser("verify-quote", help="校验引用是否逐字存在（exit 0/1）")
    v.add_argument("--year", type=int, required=True)
    v.add_argument("--text", required=True)
    v.set_defaults(func=cmd_verify_quote)

    y = sub.add_parser("years", help="各来源覆盖年份")
    y.add_argument("--json", action="store_true")
    y.set_defaults(func=cmd_years)

    sc = sub.add_parser("sections", help="列出某年的小节")
    sc.add_argument("--year", type=int, required=True)
    sc.set_defaults(func=cmd_sections)

    sub.add_parser("stats", help="语料规模").set_defaults(func=cmd_stats)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    try:
        return args.func(args)
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            print("索引库尚未初始化，请先运行 build_index.py build。", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
