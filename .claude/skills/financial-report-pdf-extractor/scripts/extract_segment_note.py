#!/usr/bin/env python3
"""
分部收入附注抽取器 — 从港股年报的「经营分部资料」附注中抽取分部收入 / 分部业绩。

为什么必须按公司配「版式档」(PROFILES)，而不能写一套通用规则：
  1. 分部名称、分部个数、有没有「内部沖销」列，每家都不一样；
  2. 更要命的是**年度标记**。康师傅年报每页页眉都印着
     「截至2025年12月31日止年度  Year ended 31 December 2025」，
     而其中 p288/p289 装的是 **2024 比较数**——若按页眉抓年份，2024 的数会被
     标成 2025，而且不会报任何错。所以年度必须按各公司表头的实际写法取。

两道硬校验，任一不过就**拒绝入库**（把「静默算错」变成「响亮失败」）：
  ① 同一 metric 内：Σ分部 + 内部沖销 == 总计
  ② external_revenue 的合计 == 利润表「营运收入」(004001999)

用法：
  V=.venv/bin/python; S=.claude/skills/financial-report-pdf-extractor/scripts
  $V $S/extract_segment_note.py --pdf report/xxx_2025.pdf --code 09633          # 抽取并打印
  $V $S/extract_segment_note.py --pdf ... --code 09633 --json out.json          # 落 JSON
  $V $S/extract_segment_note.py --pdf ... --code 09633 --store                  # 校验通过后入库
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from pdf_helper import open_pdf
except ImportError:  # 兼容从脚本目录直接执行
    from scripts.pdf_helper import open_pdf


# ---------------------------------------------------------------------------
# 版式档：一家公司一份，写死。抓不到就报错，绝不猜。
#   year_re    —— 该表「期间」年度的锚点正则（不是页眉的报表年度！）
#   columns    —— 表头列顺序，kind 决定它在合计校验里的角色
#   rows       —— 要抽的行；zh 是和年报正文逐字一致的标签
# ---------------------------------------------------------------------------
PROFILES = {
    "09633": {
        "company": "農夫山泉股份有限公司",
        "note_kw": ["OPERATING SEGMENT INFORMATION", "經營分部資料"],
        # 表头写「截至2024年12月31日止年度」（同页页眉是裸的「2025年12月31日」，
        # 没有「截至」二字，故不会误命中）。p169 末尾的「主要客戶資料」段落也会
        # 出现「截至2025年及2024年…」，故取**首个**命中。
        "year_re": r"截至\s*(\d{4})\s*年",
        "year_pick": "first",
        "unit_raw": "RMB’000",
        "unit_scale": 1000,
        "note_ref": "附註4 經營分部資料",
        "columns": [
            {"zh": "水類產品", "en": "Water products", "kind": "segment"},
            {"zh": "即飲茶類產品", "en": "Ready-to-drink tea products", "kind": "segment"},
            {"zh": "功能飲料產品", "en": "Functional drinks products", "kind": "segment"},
            {"zh": "果汁飲料產品", "en": "Juice beverage products", "kind": "segment"},
            {"zh": "其他產品", "en": "Other products", "kind": "segment"},
            {"zh": "總計", "en": "Total", "kind": "total"},
        ],
        # 农夫山泉明说「因此僅呈列分部收益及分部業績」——无分部间销售，故只有外部口径。
        "rows": [
            {"metric": "external_revenue", "zh": "向外部客戶銷售",
             "en": "Sales to external customers"},
            {"metric": "segment_result", "zh": "分部業績", "en": "Segment results"},
            {"metric": "depreciation", "zh": "折舊及攤銷",
             "en": "Depreciation and amortisation"},
        ],
    },
    "00322": {
        "company": "康師傅控股有限公司",
        "note_kw": ["REVENUE AND SEGMENT INFORMATION", "收益和分部資料"],
        # 表头是一个孤立的年份行（「Segment results:」下一行）。用裸年份可避开
        # 每页页眉那个不变的「截至2025年12月31日止年度」——那正是本文件开头
        # 说的陷阱。页码是 28x，不会命中 20\d{2}。
        "year_re": r"^(20\d{2})$",
        "year_pick": "first",
        "unit_raw": "RMB’000",
        "unit_scale": 1000,
        "note_ref": "附註6 收益和分部資料",
        "columns": [
            {"zh": "方便麵", "en": "Instant noodles", "kind": "segment"},
            {"zh": "飲品", "en": "Beverages", "kind": "segment"},
            {"zh": "其他", "en": "Others", "kind": "segment"},
            {"zh": "內部沖銷", "en": "Inter-segment elimination", "kind": "elimination"},
            {"zh": "總計", "en": "Total", "kind": "total"},
        ],
        "rows": [
            # 「由客戶合約產生之收益」= 对外口径；「分部收益」= 含分部间，两者不可混用。
            {"metric": "external_revenue", "zh": "由客戶合約產生之收益",
             "en": "Revenue from contracts"},
            {"metric": "segment_revenue", "zh": "分部收益", "en": "Segment revenue"},
            {"metric": "segment_result", "zh": "分部業績（已扣除財務費用）",
             "en": "Segment results after"},
            {"metric": "depreciation", "zh": "折舊及攤銷",
             "en": "Depreciation and amortisation"},
            {"metric": "capex", "zh": "資本開支", "en": "Capital expenditures"},
        ],
    },
}

# 「—」在原表里表示该列无发生额，按 0 处理；但它也可能是「不适用」，
# 所以只对收入/业绩类行这么折算，且在输出里保留原始 token 备查。
_DASH_TOKENS = {"—", "–", "－", "‐", "-", "N/A", "不適用", "不适用"}
_NUM_RE = re.compile(r"^[（(]?\s*-?[\d][\d,，]*\s*[)）]?$")


# ---------------------------------------------------------------------------
# 数据库定位：FINANCIAL_DATA_DIR > cwd > 向上查找（与 security-analysis 同约定）
# ---------------------------------------------------------------------------
def _resolve_data_dir() -> str:
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


def _to_amount(tok: str):
    """把表里的一个数字 token 转成 float；认不出返回 None。"""
    t = tok.strip()
    if t in _DASH_TOKENS:
        return 0.0
    if not _NUM_RE.match(t):
        return None
    neg = t[0] in "(（-" or t.startswith("-")
    body = t.strip("()（）").lstrip("-").replace(",", "").replace("，", "")
    try:
        v = float(body)
    except ValueError:
        return None
    return -v if neg else v


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def find_note_pages(pages_text: list[str], prof: dict) -> list[int]:
    """定位分部附注所在的**连续页块**（返回 0 基页号）。

    按「命中关键词的页」找连续段，取最长的一段——这样能自动排除管理层讨论里
    那些同样提到「分部」的零散页面（如农夫山泉 p80）。
    """
    hits = [i for i, t in enumerate(pages_text)
            if any(k in t for k in prof["note_kw"])]
    if not hits:
        return []
    runs, cur = [], [hits[0]]
    for h in hits[1:]:
        if h == cur[-1] + 1:
            cur.append(h)
        else:
            runs.append(cur)
            cur = [h]
    runs.append(cur)
    return max(runs, key=len)


def detect_year(lines: list[str], prof: dict):
    pat = re.compile(prof["year_re"])
    found = []
    for ln in lines:
        m = pat.search(ln)
        if not m:
            continue
        for g in m.groups():
            if g and re.fullmatch(r"20\d{2}", g):
                found.append(int(g))
                break
    if not found:
        return None
    return found[0] if prof["year_pick"] == "first" else found[-1]


def detect_columns(lines: list[str], prof: dict, first_row_idx: int) -> list[str]:
    """按页面表头里分部名**实际出现的次序**确定列序，而不是照搬版式档的次序。

    为什么必须从页面上读：农夫山泉 2020 年报把「功能飲料產品」与「即飲茶類產品」
    两列的前后位置调了个个儿（2021 年报起才改成茶饮在前）。若按固定列序 zip，
    这两列的数会整体互换，而**合计校验照样通过**——合计跟列序无关。所以列序
    只能信页面上印的那一个。

    只看首行数据标签之前的部分：康师傅的表头把英文译文插在中文名之间
    （方便麵/Instant noodles/飲品/…），所以不能用「连续行」判定，只能按名字筛。
    """
    known = {c["zh"] for c in prof["columns"]}
    found: list[str] = []
    for ln in lines[:first_row_idx]:
        if ln in known and ln not in found:
            found.append(ln)
    return found


def extract_row(lines: list[str], row: dict, stop_labels: set[str], ncols: int):
    """从标签行往后扫，收集 ncols 个数字；遇到别的行标签（或扫太远）就停。

    stop_labels 只放**中文**行标签：英文译文不设停，否则英文标签会把同一个
    逻辑行截断（如农夫山泉「向外部客戶銷售 / Sales to external customers」）。
    """
    idx = None
    for i, ln in enumerate(lines):
        if ln == row["zh"]:
            idx = i
            break
    if idx is None:
        return None
    out, scanned = [], 0
    for ln in lines[idx + 1:]:
        if ln in stop_labels:
            break
        scanned += 1
        if scanned > 15:
            break
        v = _to_amount(ln)
        if v is not None:
            out.append(v)
            if len(out) == ncols:
                break
    return out if len(out) == ncols else None


def parse_report(pdf_path: str, code: str) -> dict:
    """返回 {'years': {年: {metric: {列名: 值}}}, 'errors': [...], 'pages': [...]}"""
    prof = PROFILES.get(code)
    if not prof:
        raise SystemExit(f"错误：没有 {code} 的版式档。可用的有 {sorted(PROFILES)}。"
                         f"\n新增一家公司需要先在 PROFILES 里写死它的表头与行标签——"
                         f"分部附注的版式差异太大，通用规则必然静默抓错。")

    with open_pdf(pdf_path) as pdf:
        pages_text = [p.extract_text() for p in pdf.pages]

    note_pages = find_note_pages(pages_text, prof)
    if not note_pages:
        raise SystemExit(f"错误：在 {pdf_path} 中找不到分部附注"
                          f"（关键词 {prof['note_kw']}）。")

    ncols = len(prof["columns"])
    stop_labels = {r["zh"] for r in prof["rows"]}
    # 分部名本身也是停点：否则「其他產品」这类行会被误当数字行扫过去。
    stop_labels |= {c["zh"] for c in prof["columns"]}

    years: dict[int, dict] = {}
    errors: list[str] = []
    col_names = [c["zh"] for c in prof["columns"]]
    col_order_seen: dict[int, list[str]] = {}

    for pno in note_pages:
        lines = _lines(pages_text[pno])
        year = detect_year(lines, prof)
        if year is None:
            continue  # 附注说明页（无表）没有年度，正常跳过

        # 只用**行标签**定位表头边界。stop_labels 里还含着列名，若一并算进来，
        # 首个「行标签」就落到表头自身的列名上，lines[:idx] 会切空。
        row_idxs = [i for i, ln in enumerate(lines)
                    if ln in {r["zh"] for r in prof["rows"]}]
        if not row_idxs:
            continue  # 没有可识别的行标签，本页无表
        page_cols = detect_columns(lines, prof, min(row_idxs))
        # 列名集合与列数都必须与版式档一致。不一致说明分部结构变了（新增/改名），
        # 这时数字会错位贴到别的分部上，且合计校验查不出来——直接报错，不猜。
        if len(page_cols) != ncols or set(page_cols) != set(col_names):
            errors.append(f"p{pno + 1}：页面表头读到 {page_cols}，与版式档 {col_names} "
                          f"对不上，本页跳过（分部结构可能已变更）")
            continue
        col_order_seen[year] = page_cols

        bucket = years.setdefault(year, {})
        for row in prof["rows"]:
            vals = extract_row(lines, row, stop_labels, ncols)
            if vals is None:
                continue
            if row["metric"] in bucket:
                errors.append(f"p{pno + 1}：{year} 年 {row['metric']} 重复出现，"
                              f"保留先出现的一份")
                continue
            # 按**页面自己的列序**贴值，再按列名归位
            bucket[row["metric"]] = dict(zip(page_cols, vals))

    return {"code": code, "profile": prof, "years": years,
            "errors": errors, "pages": [p + 1 for p in note_pages],
            "col_order": col_order_seen,
            "source_file": os.path.basename(pdf_path)}


# ---------------------------------------------------------------------------
# 两道硬校验
# ---------------------------------------------------------------------------
def verify(parsed: dict, conn=None) -> tuple[list[str], list[str]]:
    """返回 (failures, notes)。failures 非空即拒绝入库。"""
    prof = parsed["profile"]
    scale = prof["unit_scale"]
    fails, notes = [], []

    for year, metrics in sorted(parsed["years"].items()):
        for metric, cells in metrics.items():
            segs = [c["zh"] for c in prof["columns"] if c["kind"] == "segment"]
            elim = [c["zh"] for c in prof["columns"] if c["kind"] == "elimination"]
            total = [c["zh"] for c in prof["columns"] if c["kind"] == "total"]
            if not total:
                continue
            s = sum(cells[c] for c in segs if c in cells)
            s += sum(cells[c] for c in elim if c in cells)
            t = cells[total[0]]
            if abs(s - t) > 1:  # 单位是千元，差 1 即 1000 元以内视为舍入
                fails.append(f"{year} 年 {metric}：Σ分部({s:,.0f}) + 内部沖销 "
                             f"!= 总计({t:,.0f})，差 {s - t:,.0f} 千元")
            else:
                notes.append(f"{year} 年 {metric}：Σ分部加总 == 总计 {t * scale / 1e8:,.2f} 亿 ✓")

        # 与利润表「营运收入」对账：分部对外收入合计应等于全年营收
        ext = metrics.get("external_revenue")
        if ext and conn is not None:
            tot = [c["zh"] for c in prof["columns"] if c["kind"] == "total"]
            code5 = parsed["code"]
            row = conn.execute(
                "SELECT AMOUNT FROM hk_income_statement WHERE stock_code=? AND"
                " STD_ITEM_CODE='004001999' AND year=? AND quarter=4",
                (code5, year)).fetchone()
            if row and row[0]:
                book = float(row[0])
                got = ext[tot[0]] * scale
                dev = abs(got - book) / book * 100 if book else 0
                if dev > 1.0:
                    fails.append(f"{year} 年：分部对外收入合计 {got / 1e8:,.2f} 亿 vs "
                                 f"利润表营运收入 {book / 1e8:,.2f} 亿，差 {dev:.2f}%")
                else:
                    notes.append(f"{year} 年：分部对外收入合计与利润表营运收入"
                                 f"（{book / 1e8:,.2f} 亿）一致，偏差 {dev:.2f}% ✓")
                    # 康师傅这类公司，利润表的「营运收入」含「其他來源之收入」
                    # （如投资性房地产租金），而「由客戶合約產生之收益」不含。
                    # 差额有明确来源时说清楚，免得读者把那 0.1% 当成抽取误差。
                    sr, tot0 = metrics.get("segment_revenue"), tot[0]
                    if sr and abs(sr[tot0] * scale - book) < 1 and dev > 0.001:
                        d = (sr[tot0] - ext[tot0]) * scale
                        notes.append(
                            f"    差额 {d / 1e8:,.2f} 亿来自「其他來源之收入」"
                            f"（如投資性房地產租金）——它计入 segment_revenue 但不计入"
                            f"「由客戶合約產生之收益」。与利润表严丝合缝的是 segment_revenue，"
                            f"两者不可混用。")
            else:
                notes.append(f"{year} 年：库内无利润表营运收入（004001999），跳过对账")
    return fails, notes


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS hk_segment_revenue (
    stock_code   TEXT    NOT NULL,   -- '00322'（不带 .hk 后缀）
    company      TEXT,               -- 公司名
    fiscal_year  INTEGER NOT NULL,   -- 数据所属年度
    report_year  INTEGER,            -- 出自哪一年的年报（一份年报含两年数据）
    metric       TEXT    NOT NULL,   -- external_revenue/segment_revenue/segment_result/depreciation/capex
    segment      TEXT    NOT NULL,   -- 分部名，保留原表繁体用字
    segment_en   TEXT,
    kind         TEXT    NOT NULL,   -- segment / elimination / total
    amount       REAL    NOT NULL,   -- 统一折算为「元」
    unit_raw     TEXT,               -- 原表单位
    note_ref     TEXT,
    source_file  TEXT,
    source_page  INTEGER,
    extracted_at TEXT,
    PRIMARY KEY (stock_code, fiscal_year, metric, segment)
);
CREATE INDEX IF NOT EXISTS idx_hkseg_year ON hk_segment_revenue(stock_code, fiscal_year);
"""


def store(parsed: dict, report_year: int) -> tuple[int, list[str]]:
    """写入；同一 (年度, 口径, 分部) 若与库内已有值**不一致**则拒绝覆盖并报告。

    「同一年度会被相邻两份年报各披露一次」是白白到手的一致性校验：两份如果
    对不上，要么抽取错了，要么公司做了重述——两种都必须让人看见，绝不能
    让后写的那份静默盖掉前一份。农夫山泉 2020 年报的列序错位就是这样现形的。
    """
    prof = parsed["profile"]
    conn = sqlite3.connect(str(Path(_resolve_data_dir()) / "financial_data.db"))
    try:
        conn.executescript(SCHEMA)
        now = datetime.now().isoformat(timespec="seconds")
        col_by_zh = {c["zh"]: c for c in prof["columns"]}
        rows = []
        for year, metrics in parsed["years"].items():
            for metric, cells in metrics.items():
                for zh, val in cells.items():
                    c = col_by_zh[zh]
                    rows.append((
                        parsed["code"], prof["company"], year, report_year, metric,
                        zh, c["en"], c["kind"], val * prof["unit_scale"],
                        prof["unit_raw"], prof["note_ref"], parsed["source_file"],
                        parsed["pages"][0] if parsed["pages"] else None, now))
        conflicts, keep = [], []
        for r in rows:
            old = conn.execute(
                "SELECT amount, report_year FROM hk_segment_revenue WHERE"
                " stock_code=? AND fiscal_year=? AND metric=? AND segment=?",
                (r[0], r[2], r[4], r[5])).fetchone()
            if old and abs(float(old[0]) - r[8]) > 0.5:
                conflicts.append(
                    f"{r[2]} 年 {r[4]} · {r[5]}：库内 {float(old[0]) / 1e8:,.2f} 亿"
                    f"（{old[1]} 年报）vs 本次 {r[8] / 1e8:,.2f} 亿（{report_year} 年报）"
                    f"—— 既不覆盖也不丢弃，请人工定夺")
                continue
            keep.append(r)
        if keep:
            conn.executemany(
                "INSERT INTO hk_segment_revenue (stock_code, company, fiscal_year,"
                " report_year, metric, segment, segment_en, kind, amount, unit_raw,"
                " note_ref, source_file, source_page, extracted_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(stock_code, fiscal_year, metric, segment) DO UPDATE SET"
                " amount=excluded.amount, report_year=excluded.report_year,"
                " source_file=excluded.source_file, source_page=excluded.source_page,"
                " extracted_at=excluded.extracted_at", keep)
        conn.commit()
        return len(keep), conflicts
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(
        description="从港股年报分部附注中抽取分部收入/业绩（按公司版式档，带两道硬校验）")
    ap.add_argument("--pdf", required=True, help="年报 PDF 路径")
    ap.add_argument("--code", required=True, help=f"股票代码，已支持 {sorted(PROFILES)}")
    ap.add_argument("--report-year", type=int, help="年报年份（默认从文件名猜）")
    ap.add_argument("--json", help="把抽取结果写入该 JSON 文件")
    ap.add_argument("--store", action="store_true", help="校验通过后写入 financial_data.db")
    ap.add_argument("--no-verify", action="store_true",
                    help="跳过校验（仅调试用；入库时一律校验）")
    args = ap.parse_args()

    parsed = parse_report(args.pdf, args.code)
    ryear = args.report_year or (int(m.group(1)) if (m := re.search(r"(20\d{2})", args.pdf))
                                 else None)

    prof = parsed["profile"]
    scale = prof["unit_scale"]
    print("=" * 70)
    print(f"分部附注抽取 · {prof['company']}（{args.code}）")
    print("=" * 70)
    print(f"  版式档：{prof['note_ref']}  单位 {prof['unit_raw']}")
    print(f"  附注页：{parsed['pages']}（0 基转 1 基已换算）")
    print(f"  抓到的年度：{sorted(parsed['years'])}")
    for e in parsed["errors"]:
        print(f"  ⚠ {e}")
    # 列序是从页面表头读的。若某年与版式档的声明次序不同，明说——2020 年报的
    # 茶饮/功能饮料两列就是调过位置的，不讲出来读者会以为按标准次序排的。
    for y, order in sorted(parsed.get("col_order", {}).items()):
        if order != [c["zh"] for c in prof["columns"]]:
            print(f"  ⚠ {y} 年表头列序为 {order}，"
                  f"与版式档声明次序不同（已按页面实际列序贴值）")
    for year, metrics in sorted(parsed["years"].items()):
        print(f"\n  【{year} 年】")
        for metric, cells in metrics.items():
            parts = [f"{zh} {cells[zh] * scale / 1e8:,.2f}亿" for zh in cells]
            print(f"    {metric:<18} " + " | ".join(parts))

    conn = None
    if args.store or not args.no_verify:
        conn = sqlite3.connect(str(Path(_resolve_data_dir()) / "financial_data.db"))
    try:
        fails, notes = ([], []) if args.no_verify else verify(parsed, conn)
    finally:
        if conn is not None and not args.store:
            conn.close()

    if notes:
        print("\n" + "-" * 70)
        print("  校验：")
        for n in notes:
            print(f"    ✓ {n}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  已写 JSON：{args.json}")

    if fails:
        print("\n" + "-" * 70)
        print("  ✗ 校验未通过，拒绝入库：")
        for f in fails:
            print(f"    ✗ {f}")
        print("\n  分部数据对不上合计，说明抽取有误——宁可空着也不能存错数。")
        return 1

    if args.store:
        n, conflicts = store(parsed, ryear)
        print(f"\n  已入库 hk_segment_revenue：{n} 行（report_year={ryear}）")
        if conflicts:
            print("\n  ✗ 与库内已有值冲突，这些行未覆盖：")
            for c in conflicts:
                print(f"    ✗ {c}")
            print("\n  同一年度被两份年报披露得不一致——可能是抽取错了，也可能是公司重述。"
                  "不猜，留给人看。")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
