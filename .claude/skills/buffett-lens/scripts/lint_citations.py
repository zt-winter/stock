#!/usr/bin/env python3
"""
lint_citations.py - 引用真实性闸门

功能:
    这是整个 buffett-lens 唯一的硬约束。原则卡里的每一句「巴菲特原话」都必须
    通过三键校验，缺一不可：

        ① 引用文本（归一化后）出现在 documents.full_text_norm
        ② 引用所标年份 == 该文档的年份
        ③ 所标的 chunk_id 确实包含该引用

    只做 ① 是不够的——一句真实存在、但标错年份的引用照样能通过字符串检查，
    却会在报告里变成"巴菲特在 2008 年就预言了…"这类看似有据的幻觉。②③ 才是
    把"有出处"和"出处正确"区分开的东西。

    三键管的是引用块。卡片正文（适用/反面案例等）里还常以行内方式标注旁证出处，
    那部分不受三键约束，一个编造或写错的 chunk_id 会以"可追溯"的样子留下来。
    所以另有第 ④ 条：文中任何位置的 chunk_id 都必须真实存在、且与自身年份自洽。

    归一化由 buffett_common.normalize_variants() 提供，与 build_index.py 共用
    同一份实现——两份实现会让保证退化到较弱的那份。

子命令:
    lint --cards DIR [--export FILE] [--quiet]   校验原则卡目录
    lint --self-test                             自检：闸门必须真的会响

退出码:
    0 全部通过；1 存在失败（含 self-test 未能拒绝伪造引用）

依赖:
    无第三方库
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffett_common as C  # noqa: E402

DEFAULT_CARDS_DIR = C.SKILL_DIR / "principles"

# 每张卡必须具备的结构要素。缺任何一项这张卡就是"读了但没法用"。
REQUIRED_FIELDS = ["一句话", "判定标准", "巴菲特原话", "适用", "不适用", "反面案例"]

# 「判定标准」里必须至少有一个可执行的东西：数值阈值，或一个真实字段名。
NUMERIC_HINT = re.compile(r"\d")
FIELD_HINT = re.compile(r"\b[A-Z][A-Z_]{3,}\b")
# 出处里声明的来源必须和 chunk_id 的前缀一致
CHUNK_RE = re.compile(r"^(letters|annual_meeting):(\d{4}):(\d+)$")


class Citation:
    __slots__ = ("card", "line_no", "year", "quote", "chunk_id", "declared_source",
                 "keywords", "variant")

    def __init__(self, card: str, line_no: int):
        self.card = card
        self.line_no = line_no
        self.year: int | None = None
        self.quote = ""
        self.chunk_id: str | None = None
        self.declared_source: str | None = None
        self.keywords = ""
        self.variant = ""

    def __repr__(self) -> str:
        return f"<Citation {self.card}:{self.line_no} {self.year} {self.chunk_id}>"


# ---------------------------------------------------------------------------
# 卡片解析
# ---------------------------------------------------------------------------
_QUOTE_HEAD = re.compile(r"\*\*原文\*\*\s*[（(]\s*(\d{4})\s*[）)]\s*[:：]\s*(.*)$")
_SOURCE_HEAD = re.compile(r"\*\*出处\*\*\s*[:：]\s*(.*)$")
_DECLARED_SRC = re.compile(r"\*\*来源\*\*\s*[:：]\s*([A-Za-z_]+)")
_KEYWORDS = re.compile(r"\*\*检索关键词\*\*\s*[:：]\s*(.*)$")
_QUOTE_END = re.compile(r"[\"'”’]\s*$")


def _strip_quote(s: str) -> str:
    s = s.strip()
    s = re.sub(r'^[\"“‘\']+', "", s)
    s = re.sub(r'[\"”’\']+$', "", s)
    return s.strip()


def parse_card(path: Path) -> tuple[list[Citation], list[str]]:
    """解析一张卡，返回 (引用列表, 结构缺失项)。

    引用块形如：
        > **原文**（1996）: "……"
        > **出处**: `letters:1996:0012` · **来源**: letters · **检索关键词**: moat
    """
    text = path.read_text(encoding="utf-8")
    card = path.stem
    lines = text.splitlines()

    citations: list[Citation] = []
    cur: Citation | None = None
    parts: list[str] = []

    def flush():
        nonlocal cur
        if cur is not None:
            cur.quote = _strip_quote(" ".join(parts))
            citations.append(cur)
            cur = None
        parts.clear()

    for i, raw in enumerate(lines, 1):
        ln = re.sub(r"^\s*>\s?", "", raw).rstrip()
        m = _QUOTE_HEAD.search(ln)
        if m:
            flush()
            cur = Citation(card, i)
            cur.year = int(m.group(1))
            parts.append(m.group(2))
            if _QUOTE_END.search(m.group(2)):
                # 同行就闭合了，仍要等出处行，但引用文本已完整
                cur.quote = _strip_quote(m.group(2))
            continue
        if cur is not None:
            sm = _SOURCE_HEAD.search(ln)
            if sm:
                body = sm.group(1)
                cm = re.search(r"`([^`]+)`", body)
                if cm:
                    cur.chunk_id = cm.group(1).strip()
                ds = _DECLARED_SRC.search(body)
                if ds:
                    cur.declared_source = ds.group(1)
                km = _KEYWORDS.search(body)
                if km:
                    cur.keywords = km.group(1).strip()
                cur.quote = _strip_quote(" ".join(parts)) if parts else cur.quote
                flush()
                continue
            if _QUOTE_END.search(ln) and not parts[-1:] == [""]:
                parts.append(ln)
                cur.quote = _strip_quote(" ".join(parts))
                continue
            if not _QUOTE_END.search(" ".join(parts)):
                parts.append(ln)
                continue
    flush()

    missing = []
    for f in REQUIRED_FIELDS:
        if not re.search(rf"\*\*{re.escape(f)}\*\*\s*[:：]", text):
            missing.append(f)

    # 「判定标准」必须落到可执行的东西上
    m = re.search(r"\*\*判定标准\*\*\s*[:：]\s*(.+)", text)
    if m:
        crit = m.group(1)
        if not (NUMERIC_HINT.search(crit) or FIELD_HINT.search(crit)):
            missing.append("判定标准(无数值阈值也无字段名)")
    else:
        missing.append("判定标准(空)")

    for f in ("适用", "不适用", "反面案例"):
        m = re.search(rf"\*\*{re.escape(f)}\*\*\s*[:：]\s*(.*)", text)
        if m and not m.group(1).strip():
            missing.append(f"{f}(空)")

    return citations, missing


# ---------------------------------------------------------------------------
# 第 ④ 条：文中任意位置的 chunk_id 都必须真实存在
# ---------------------------------------------------------------------------
REF_RE = re.compile(r"\b(letters|annual_meeting):(\d{4}):(\d+)\b")


def find_dangling_refs(conn: sqlite3.Connection, text: str) -> list[tuple[int, str]]:
    """扫描卡片全文（含正文与引用块）里出现的每个 chunk_id，返回 [(行号, 错误)]。

    三键校验只覆盖引用块。卡片正文里的旁证出处（"反面案例见 `letters:2014:0068`"）
    同样是在声称可追溯，却没有任何东西检查过它——一个编造或手误的 chunk_id 会
    安安静静地留在那里。

    这里补的是**机械可查的那一半**：存在性 + 年份自洽。它证明不了"该 chunk 是否
    真的支持那句话"——那仍然要人读。但不该让一个查不到的编号冒充出处。
    """
    errs: list[tuple[int, str]] = []
    checked: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in REF_RE.finditer(line):
            cid = m.group(0)
            if cid in checked:
                continue
            checked.add(cid)
            row = conn.execute(
                "SELECT year FROM chunks WHERE chunk_id = ?", (cid,)).fetchone()
            if not row:
                errs.append((lineno, f"文中引用的 chunk_id 不存在于索引：{cid}"))
            elif int(m.group(2)) != row["year"]:
                errs.append((lineno,
                             f"文中引用的 chunk_id 年份自相矛盾：{cid} 属于 {row['year']} 年"))
    return errs


# ---------------------------------------------------------------------------
# 三键校验
# ---------------------------------------------------------------------------
def verify_citation(conn: sqlite3.Connection, cit: Citation) -> list[str]:
    """返回失败原因列表；空列表 = 通过。"""
    errs: list[str] = []

    if not cit.quote or len(cit.quote) < 8:
        errs.append(f"引用过短或为空（{len(cit.quote)} 字），不足以构成逐字引用")
        return errs

    if not cit.chunk_id:
        errs.append("缺 chunk_id —— 无法定位出处；请用 search_corpus.py 检索后填真实 chunk_id")
        return errs

    m = CHUNK_RE.match(cit.chunk_id)
    if not m:
        errs.append(f"chunk_id 格式非法: {cit.chunk_id!r}（应形如 letters:1996:0012）")
        return errs
    cid_source, cid_year, _ = m.group(1), int(m.group(2)), m.group(3)

    # 键 ②（前半）：声明年份 vs chunk_id 里的年份
    if cit.year != cid_year:
        errs.append(f"年份不匹配：引用标 {cit.year} 年，但 chunk_id 是 {cid_year} 年")

    # 取保留大小写的原文而非 *_norm 列：后者已小写化，拿它比对会让每一条引用都只能
    # 报 "+ci"（大小写不敏感），而"大小写也逐字一致"正是本闸门想区分的第一档。
    row = conn.execute(
        "SELECT c.doc_id, c.text, c.source, c.year, d.full_text, d.year AS doc_year"
        " FROM chunks c JOIN documents d USING (doc_id) WHERE c.chunk_id = ?",
        (cit.chunk_id,)).fetchone()
    if not row:
        errs.append(f"chunk_id 不存在于索引: {cit.chunk_id}")
        return errs

    # 键 ②（后半）：chunk_id 年份 vs 文档年份
    if row["doc_year"] != cid_year:
        errs.append(f"chunk_id 年份({cid_year}) 与文档年份({row['doc_year']}) 不一致")
    if cit.year != row["doc_year"]:
        errs.append(f"年份不匹配：引用标 {cit.year} 年，而 {cit.chunk_id} 属于 {row['doc_year']} 年")

    # 声明来源 vs 实际来源
    if cit.declared_source and cit.declared_source != row["source"]:
        errs.append(f"来源标注不符：卡上写 {cit.declared_source}，实际是 {row['source']}")

    # 键 ①：对全文查，避免引用跨 chunk 边界时被误判
    v_doc = C.match_quote(cit.quote, row["full_text"])
    if not v_doc:
        errs.append(f"引文在该年全文里找不到（{row['doc_id']}）—— 疑似转述或改写，必须逐字")
        return errs

    # 键 ③：对 chunk 查，确保出处指向的是真正包含这句话的那块
    v_chunk = C.match_quote(cit.quote, row["text"])
    if not v_chunk:
        errs.append(f"引文不在所标 chunk 内（{cit.chunk_id}）—— 出处指向了错误的块")
        return errs

    cit.variant = v_chunk
    return errs


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def self_test(conn: sqlite3.Connection) -> bool:
    """注入两个必须被拒绝的引用，证明闸门真的会响。

    只测"伪造引用被拒"是不够的——一个把所有引用都判失败的 linter 也能通过那一测。
    所以第二个用例用一段真实存在的原文、只把年份改错，要求它以「年份不匹配」失败。
    两个方向都过，才说明三键校验是活的。
    """
    ok = True
    print(C.sep())
    print("self-test：闸门自检")
    print(C.sep())

    # 用例 1：完全伪造
    fake = Citation("__selftest__", 1)
    fake.year, fake.chunk_id = 1996, "letters:1996:0001"
    fake.quote = "This sentence was never written by Warren Buffett."
    errs = verify_citation(conn, fake)
    if errs and any("找不到" in e for e in errs):
        print("  ✓ 伪造引用被正确拒绝")
    else:
        print(f"  ✗ 伪造引用竟然通过了！（errs={errs}）—— 闸门失效")
        ok = False

    # 用例 2：真实原文 + 错年份。三段式校验里只有 ② 能抓住它。
    row = conn.execute(
        "SELECT doc_id, year, full_text FROM documents WHERE source = 'letters'"
        " AND char_len > 20000 ORDER BY year LIMIT 1").fetchone()
    if not row:
        print("  ✗ 语料为空，无法做错年份自检（先运行 fetch_letters.py + build_index.py）")
        return False
    chunk = conn.execute(
        "SELECT chunk_id, text FROM chunks WHERE doc_id = ? AND n_chars > 300"
        " ORDER BY seq LIMIT 1", (row["doc_id"],)).fetchone()
    real_year, wrong_year = row["year"], row["year"] + 7
    probe = Citation("__selftest__", 2)
    probe.year = wrong_year
    probe.chunk_id = chunk["chunk_id"]
    probe.quote = chunk["text"][:160].strip()
    errs = verify_citation(conn, probe)
    if errs and any("年份不匹配" in e for e in errs):
        print(f"  ✓ 年份标错的真实引用被抓出（{real_year} 年原文标成 {wrong_year} 年）")
    else:
        print(f"  ✗ 错年份未被识别！（{real_year} 年原文标成 {wrong_year} 年，errs={errs}）")
        ok = False

    # 用例 3：真实原文 + 正确年份 → 必须通过（否则说明 linter 只会说"不"）
    good = Citation("__selftest__", 3)
    good.year, good.chunk_id, good.quote = real_year, chunk["chunk_id"], probe.quote
    if not verify_citation(conn, good):
        print(f"  ✓ 真实引用 + 正确出处 正常通过（{chunk['chunk_id']}）")
    else:
        print(f"  ✗ 真实引用被误判为失败：{verify_citation(conn, good)}")
        ok = False

    # 用例 4：正文里编造的 chunk_id → 必须被抓。三键校验只覆盖引用块，抓不到这类断言。
    bogus = "反面案例见 `letters:1999:9999` 与 `annual_meeting:1999:9999`，另见 `letters:abc`。"
    n_bogus = len(find_dangling_refs(conn, bogus))
    if n_bogus == 2:
        print("  ✓ 正文里编造的 chunk_id 被抓出（2 处）")
    else:
        print(f"  ✗ 编造的 chunk_id 未被正确识别（期望 2 处，实得 {n_bogus}）—— 闸门失效")
        ok = False

    # 用例 5：真实的 chunk_id → 不该被误报
    real_cid = chunk["chunk_id"]
    if not find_dangling_refs(conn, f"反面案例见 `{real_cid}`。"):
        print(f"  ✓ 正文里真实的 chunk_id 正常通过（{real_cid}）")
    else:
        print(f"  ✗ 真实的 chunk_id 被误报：{find_dangling_refs(conn, f'`{real_cid}`')}")
        ok = False

    print(C.line())
    print("self-test 通过：闸门既会拒绝伪造，也放行真实。" if ok
          else "self-test 失败：闸门行为不符合预期。")
    return ok


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def cmd_lint(args) -> int:
    if not C.CORPUS_DB_PATH.is_file():
        print(f"索引库不存在: {C.CORPUS_DB_PATH}", file=sys.stderr)
        print("请先运行 fetch_letters.py fetch --all / fetch-meetings，"
              "再运行 build_index.py build。", file=sys.stderr)
        return 1
    conn = C.get_corpus_conn()

    if args.self_test and not args.cards:
        return 0 if self_test(conn) else 1

    cards_dir = Path(args.cards or DEFAULT_CARDS_DIR)
    if not cards_dir.is_dir():
        print(f"卡片目录不存在: {cards_dir}", file=sys.stderr)
        return 1
    files = sorted(p for p in cards_dir.glob("*.md") if p.name != "INDEX.md")
    if not files:
        print(f"{cards_dir} 下没有 .md 卡片。", file=sys.stderr)
        return 1

    ok = True
    if args.self_test:
        ok = self_test(conn) and ok
        print()

    n_total = n_pass = 0
    all_fail: list[tuple[str, Citation | None, str]] = []
    exports: list[tuple[str, ...]] = []

    print(C.sep())
    print(f"引用校验：{cards_dir}")
    print(C.sep())
    for f in files:
        citations, missing = parse_card(f)
        card_fail = 0
        for cit in citations:
            n_total += 1
            errs = verify_citation(conn, cit)
            if errs:
                card_fail += 1
                for e in errs:
                    all_fail.append((f.name, cit, e))
            else:
                n_pass += 1
                exports.append((f.stem, str(cit.year), cit.chunk_id or "",
                                getattr(cit, "variant", ""), cit.quote[:70]))
        for m in missing:
            all_fail.append((f.name, None, f"缺少结构要素：{m}"))
            card_fail += 1
        for lineno, err in find_dangling_refs(conn, f.read_text(encoding="utf-8")):
            all_fail.append((f"{f.name}:{lineno}", None, err))
            card_fail += 1
        status = "✓" if card_fail == 0 else "✗"
        print(f"  {status} {C.pad(f.name, 34)} 引用 {len(citations):>2} 条"
              f"{'' if card_fail == 0 else f'  问题 {card_fail}'}"
              f"{'' if not missing else '  结构缺失: ' + ', '.join(missing)}")

    print(C.line())
    if all_fail:
        ok = False
        print("失败明细：")
        for name, cit, err in all_fail:
            loc = f"{name}:{cit.line_no}" if cit else name
            print(f"  ✗ {loc}")
            print(f"      {err}")
            if cit and cit.quote:
                print(f"      引用: “{cit.quote[:80]}”")
        print(C.line())

    print(f"引用校验: {n_pass}/{n_total} 通过"
          f"{'' if n_total else '（未发现任何引用，卡片可能未按格式撰写）'}")
    if n_total and not n_pass:
        ok = False
    if not n_total:
        ok = False

    if args.export:
        out = Path(args.export)
        with out.open("w", encoding="utf-8") as fh:
            fh.write("card\tyear\tchunk_id\tmatch_variant\tquote\n")
            for row in sorted(exports):
                fh.write("\t".join(row.replace("\t", " ") for row in row) + "\n")
        print(f"已导出 {len(exports)} 条引用 → {out}")

    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(
        description="buffett-lens 引用真实性闸门",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""三键校验:
  ① 引文出现在该年文档全文中（归一化后）
  ② 引文所标年份 == 文档年份 == chunk_id 年份
  ③ 引文确实位于所标的 chunk 内

第 ④ 条（覆盖三键管不到的正文旁证）:
  文中任意位置出现的 chunk_id 都必须存在于索引，且与自身年份自洽

示例:
  lint_citations.py --cards principles/ --self-test
  lint_citations.py --cards principles/ --export citations.tsv
""")
    p.add_argument("--cards", help=f"卡片目录（默认 {DEFAULT_CARDS_DIR}）")
    p.add_argument("--export", help="把通过的引用导出为 TSV")
    p.add_argument("--self-test", dest="self_test", action="store_true",
                   help="先跑闸门自检（伪造引用必须被拒）")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    if not args.cards and not args.self_test:
        args.cards = str(DEFAULT_CARDS_DIR)
    try:
        return cmd_lint(args)
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            print("索引库尚未初始化，请先运行 build_index.py build。", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
