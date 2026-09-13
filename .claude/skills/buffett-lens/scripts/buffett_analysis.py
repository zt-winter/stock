#!/usr/bin/env python3
"""
buffett_analysis.py - 巴菲特视角的量化筛查

功能:
    读 financial_data.db，按八个可实算的指标给一只股票打 PASS / QUESTION / REJECT。
    这是 buffett-lens 三层的中间层：原则卡给准则、这个脚本给数字、语料检索给原话。

    本脚本不采集数据。表里没有就先跑 security-analysis 的 collect_financial_data.py。

子命令:
    data-check --code X                     摸清这只股票有哪些数据、缺什么
    screen     --code X [--years N] [--json] 八项读数筛查 + 汇总
    owner-earnings --code X                 只看股东盈余
    retention  --code X [--start-year Y]    一美元留存测试
    valuation  --code X                     只看估值分位
    batch --file stocks.txt                 批量筛查

判定口径的重要前提:
    · NA 不等于 PASS。样本年数不足时一律记 NA——把"没数据"说成"通过"是这个
      工具最容易犯也最有害的错误。
    · 周期股的 PE 分位语义是反的（周期顶 PE 低、周期底 PE 高），此时以 PB 为主
      信号，并提示参阅 cyclical-analysis-guide.md。
    · 所有修正项都会显式打印，不静默应用。

依赖:
    无第三方库（只用 sqlite3 + buffett_common 的库定位）
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

PASS, QUESTION, REJECT, NA = "PASS", "QUESTION", "REJECT", "NA"
_ORDER = {REJECT: 0, QUESTION: 1, PASS: 2, NA: 3}

# PE 分位语义反转的品种。中铝/中远海控是典型的强周期；其余靠盈利波动率启发式提示。
CYCLICAL_CODES = {"601600", "601919", "600019", "000898", "601088", "600362"}

# 数据不足的最少年数。低于此值不判 PASS，避免用三年好光景冒充"长期一致"。
MIN_YEARS_ROE = 7
MIN_YEARS_MARGIN = 7
MIN_YEARS_QUALITY = 7
MIN_YEARS_RETENTION = 5

# 估值缓存有效期，与 A 股 cyclical_stock_analysis.py 的 VALUATION_CACHE_DAYS 同口径。
# 财报表是历史事实、可永久缓存；估值是市价，必须过期，否则会把去年的价格当今天用。
VALUATION_CACHE_DAYS = 7


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------
def q_annual_indicator(conn, code: str) -> list[dict]:
    """取年报口径的 em_financial_indicator。该表没有 year/quarter 列，靠 REPORT_YEAR
    + REPORT_DATE_NAME LIKE '%年报' 过滤——用季报数据算 ROE 一致性会得出错误结论。"""
    try:
        rows = conn.execute(
            "SELECT REPORT_YEAR, SECURITY_NAME_ABBR, ROEJQ, ROEKCJQ, XSMLL, XSJLL,"
            " ZCFZL, INTEREST_DEBT_RATIO, PARENTNETPROFIT, EPSJB, BPS, TOTALOPERATEREVE,"
            " KCFJCXSYJLR FROM em_financial_indicator"
            " WHERE stock_code = ? AND REPORT_DATE_NAME LIKE '%年报'"
            " AND REPORT_YEAR IS NOT NULL ORDER BY REPORT_YEAR", (code,)).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        d = dict(r)
        y = _to_int(d.get("REPORT_YEAR"))
        if y is None:
            continue
        d["year"] = y
        out.append(d)
    return out


def q_annual_cashflow(conn, code: str) -> dict[int, dict]:
    """年报现金流。流量是年初至今累计，所以只取 quarter=4，绝不 ×4/3 折算。
    FA_IR_DEPR / IA_AMORTIZE 也只有 quarter=4 才有值。"""
    try:
        rows = conn.execute(
            "SELECT year, quarter, NETCASH_OPERATE, CONSTRUCT_LONG_ASSET,"
            " ASSIGN_DIVIDEND_PORFIT, FA_IR_DEPR, IA_AMORTIZE, LPE_AMORTIZE"
            " FROM em_cash_flow WHERE stock_code = ? AND quarter = 4 ORDER BY year",
            (code,)).fetchall()
    except sqlite3.Error:
        return {}
    return {_to_int(r["year"]): dict(r) for r in rows if _to_int(r["year"]) is not None}


def q_annual_income(conn, code: str) -> dict[int, dict]:
    try:
        rows = conn.execute(
            "SELECT year, quarter, PARENT_NETPROFIT, BASIC_EPS, TOTAL_OPERATE_INCOME"
            " FROM em_income_statement WHERE stock_code = ? AND quarter = 4 ORDER BY year",
            (code,)).fetchall()
    except sqlite3.Error:
        return {}
    return {_to_int(r["year"]): dict(r) for r in rows if _to_int(r["year"]) is not None}


def q_valuation(conn, code: str) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(
            "SELECT date, pe_ttm, pb FROM stock_valuation_history"
            " WHERE stock_code = ? AND pe_ttm IS NOT NULL ORDER BY date", (code,))]
    except sqlite3.Error:
        return []


def q_dividend_per_share(conn, code: str) -> dict[int, float]:
    try:
        return {_to_int(r["year"]): r["dividend_per_share"] for r in conn.execute(
            "SELECT year, dividend_per_share FROM dividend_annual_yield"
            " WHERE stock_code = ?", (code,)) if _to_int(r["year"]) is not None
            and r["dividend_per_share"] is not None}
    except sqlite3.Error:
        return {}


def _to_int(v):
    if v is None:
        return None
    try:
        return int(str(v).strip()[:4])
    except (ValueError, TypeError):
        return None


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN


# ---------------------------------------------------------------------------
# 取数 · 港股
#
# 港股三张报表是 EAV 长表（一行一个科目，靠 STD_ITEM_CODE 取值），与 A 股的宽表结构
# 完全不同。这一层唯一的职责是「透视成与 A 股同名的键」——这样下面八个指标函数一行
# 都不用改，加港股支持的成本就锁死在这里。
#
# 口径陷阱（逐条实测确认过，改动前请复核）：
#   · 金额单位是「元」，币种是**人民币**。hk_financial_indicator.CURRENCY 标 'HKD' 是
#     源端按上市地硬编码的错标：00322 2025 营业额 79,068,022,000 与公司年报的人民币
#     790.68 亿逐位吻合，若真是港元应放大约 8%。
#   · 「股东权益」(004030999) 是**归母**口径，「净资产」(004028999) 含少数股东权益，
#     00322 在 2025 年两者差 35.19 亿。ROE 分母必须用前者，误用会低估约 19%。
#   · 2016 年是美元报表按人民币重述的比较数（2017-01-01 起呈报货币由 USD 改 RMB），
#     且三表最早只到 2016 → 2016 年没有期初权益，平均 ROE 不可算，故 ROE 序列自 2017 起。
#   · hk_cash_flow.AMOUNT 在 SQLite 里是 INTEGER，比值前必须先转 float（_num 已做）。
#   · 「营业额」(004001001) 与「营运收入」(004001999) 是两行**同值镜像**，切勿相加。
# ---------------------------------------------------------------------------
HK_CF_ITEMS = {
    "NETCASH_OPERATE": ("003999",),                 # 经营业务现金净额
    "CONSTRUCT_LONG_ASSET": ("005005", "005007"),   # 购建固定资产 + 购建无形资产及其他资产
    "ASSIGN_DIVIDEND_PORFIT": ("007004",),          # 已付股息(融资)
    "FA_IR_DEPR": ("001009",),                      # 加:折旧及摊销
}
HK_INC_ITEMS = {
    "PARENT_NETPROFIT": ("004025002",),             # 股东应占溢利
    "BASIC_EPS": ("004027002",),                    # 每股基本盈利
    "TOTAL_OPERATE_INCOME": ("004001999",),         # 营运收入
}
# 港股资产负债表里没有「有息负债」这一行，只能合成。A 股的 INTEREST_DEBT_RATIO 实测
# 口径为「有息负债 ÷ 总资产」（已用 601600 历年比对到小数点后三位），故此处照此合成，
# 阈值才可比。BS 无独立债券行，发行债券按源端口径推定并入贷款科目。
HK_IB_CODES = ("004011010", "004020001", "004011006", "004020005")  # 短贷+长贷+租赁(流动+非流动)


def detect_market(conn, code: str) -> str:
    """判定 A 股还是港股。先按代码位数，位数不标准时看数据实际落在哪张表——
    表里有数据是事实，比猜代码规则可靠。"""
    if code.isdigit() and len(code) == 5:
        return "hk"
    if code.isdigit() and len(code) == 6:
        return "a"
    for tbl, mk in (("em_financial_indicator", "a"), ("hk_financial_indicator", "hk")):
        try:
            if conn.execute(f"SELECT 1 FROM {tbl} WHERE stock_code = ? LIMIT 1",
                            (code,)).fetchone():
                return mk
        except sqlite3.Error:
            pass
    return "a"


def _hk_long(conn, table: str, code: str) -> dict[int, dict[str, float]]:
    """把港股 EAV 长表透视成 {年: {科目代码: 金额}}。三张表实测只有年报一种粒度。"""
    try:
        rows = conn.execute(
            f"SELECT year, STD_ITEM_CODE, AMOUNT FROM {table}"
            f" WHERE stock_code = ? AND quarter = 4", (code,)).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[int, dict[str, float]] = {}
    for r in rows:
        y, amt = _to_int(r["year"]), _num(r["AMOUNT"])
        if y is None or amt is None:
            continue
        out.setdefault(y, {})[r["STD_ITEM_CODE"]] = amt
    return out


def _hk_sum(items: dict, codes) -> float | None:
    """按科目代码求和。全部缺失时返回 None 而不是 0——0 会被下游当成真实读数。"""
    vals = [items.get(c) for c in codes]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def _hk_name(conn, code: str) -> str | None:
    for tbl in ("hk_financial_indicator", "hk_income_statement", "hk_cash_flow"):
        try:
            r = conn.execute(
                f"SELECT SECURITY_NAME_ABBR FROM {tbl} WHERE stock_code = ?"
                f" AND SECURITY_NAME_ABBR IS NOT NULL LIMIT 1", (code,)).fetchone()
        except sqlite3.Error:
            r = None
        if r and r[0]:
            return r[0]
    return None


def q_annual_indicator_hk(conn, code: str) -> list[dict]:
    """合成与 em_financial_indicator 同名的字段。

    ROEJQ = 股东应占溢利 ÷ 平均股东权益(归母)。已实测与 hk_financial_indicator.ROE_AVG
    在 2022-2025 四年逐位吻合（2025 双方都是 30.82），所以这个合成口径是可信的。
    ROEKCJQ 无港股对应科目，恒为 None → ind_roe 的扣非交叉校验会自动跳过（不报错，
    但「利润里有多少是非经常性」这个问题在港股上就没有答案了，报告里须说明）。
    """
    bs = _hk_long(conn, "hk_balance_sheet", code)
    inc = _hk_long(conn, "hk_income_statement", code)
    if not inc:
        return []
    name = _hk_name(conn, code)
    out = []
    for y in sorted(inc):
        i, b, bprev = inc[y], bs.get(y, {}), bs.get(y - 1, {})
        np_ = _num(i.get("004025002"))
        rev = _num(i.get("004001999"))
        gp = _num(i.get("004007999"))
        eq, eq_prev = _num(b.get("004030999")), _num(bprev.get("004030999"))
        ta, tl = _num(b.get("004009999")), _num(b.get("004025999"))
        ib = _hk_sum(b, HK_IB_CODES)
        eps = _num(i.get("004027002"))
        roe = None
        if np_ is not None and eq and eq_prev and (eq + eq_prev):
            roe = 100.0 * np_ / ((eq + eq_prev) / 2)  # 2016 无期初权益 → None
        # BPS 由 归母权益 ÷ 股本 反推，股本 = 净利 ÷ EPS；供市值交叉验证用
        bps = (eq * eps / np_) if (eq and eps and np_) else None
        out.append({
            "year": y, "SECURITY_NAME_ABBR": name,
            "ROEJQ": roe,
            "ROEKCJQ": None,
            "XSMLL": (100.0 * gp / rev) if (gp is not None and rev) else None,
            "XSJLL": (100.0 * np_ / rev) if (np_ is not None and rev) else None,
            "ZCFZL": (100.0 * tl / ta) if (tl is not None and ta) else None,
            "INTEREST_DEBT_RATIO": (100.0 * ib / ta) if (ib is not None and ta) else None,
            "PARENTNETPROFIT": np_, "EPSJB": eps, "BPS": bps,
            "TOTALOPERATEREVE": rev, "KCFJCXSYJLR": None,
        })
    return out


def q_annual_cashflow_hk(conn, code: str) -> dict[int, dict]:
    out = {}
    for y, items in _hk_long(conn, "hk_cash_flow", code).items():
        d = {"year": y, "quarter": 4}
        for key, codes in HK_CF_ITEMS.items():
            d[key] = _hk_sum(items, codes)
        out[y] = d
    return out


def q_annual_income_hk(conn, code: str) -> dict[int, dict]:
    out = {}
    for y, items in _hk_long(conn, "hk_income_statement", code).items():
        d = {"year": y, "quarter": 4}
        for key, codes in HK_INC_ITEMS.items():
            d[key] = _hk_sum(items, codes)
        out[y] = d
    return out


# ---------------------------------------------------------------------------
# 基础统计
# ---------------------------------------------------------------------------
def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def stdev(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def percentile_rank(series: list[float], value: float) -> float | None:
    xs = [x for x in series if x is not None]
    if len(xs) < 20 or value is None:
        return None
    return 100.0 * sum(1 for x in xs if x <= value) / len(xs)


class Ind:
    """一个指标的结论。detail 是给人看的算式，note 是必须显式打印的修正项。"""

    __slots__ = ("key", "name", "verdict", "detail", "notes")

    def __init__(self, key: str, name: str, verdict: str, detail: str, notes=None):
        self.key, self.name, self.verdict = key, name, verdict
        self.detail = detail
        self.notes = notes or []

    def as_dict(self):
        return {"key": self.key, "name": self.name, "verdict": self.verdict,
                "detail": self.detail, "notes": self.notes}


# ---------------------------------------------------------------------------
# 指标 1：股东盈余
# ---------------------------------------------------------------------------
def ind_owner_earnings(cf: dict[int, dict], inc: dict[int, dict], years: int) -> Ind:
    """股东盈余 ≈ 经营现金流 − 维持性资本开支。用 CONSTRUCT_LONG_ASSET 作 capex 的
    代理（购建固定资产、无形资产和其他长期资产支付的现金）。"""
    yrs = sorted(set(cf) & set(inc))[-years:]
    if not yrs:
        return Ind("owner_earnings", "股东盈余", NA, "缺 em_cash_flow / em_income_statement 年报数据")
    rows, oe_sum, np_sum = [], 0.0, 0.0
    for y in yrs:
        ocf = _num(cf[y].get("NETCASH_OPERATE"))
        capex = _num(cf[y].get("CONSTRUCT_LONG_ASSET")) or 0.0
        np_ = _num(inc[y].get("PARENT_NETPROFIT"))
        if ocf is None or np_ is None:
            continue
        oe = ocf - capex
        rows.append((y, oe, np_, ocf, capex))
        oe_sum += oe
        np_sum += np_
    if not rows or np_sum == 0:
        return Ind("owner_earnings", "股东盈余", NA, "年报现金流或净利缺失，无法计算")
    ratio = oe_sum / abs(np_sum) if np_sum else None
    latest = rows[-1]
    detail = (f"近 {len(rows)} 年（{rows[0][0]}-{rows[-1][0]}）累计："
              f"股东盈余 {oe_sum / 1e8:,.1f} 亿 vs 归母净利 {np_sum / 1e8:,.1f} 亿，"
              f"比值 {ratio:.2f}；最近一年 {latest[0]}："
              f"{latest[1] / 1e8:,.1f} 亿 vs {latest[2] / 1e8:,.1f} 亿")
    notes = []
    if ratio is None:
        return Ind("owner_earnings", "股东盈余", NA, detail)
    if oe_sum < 0:
        return Ind("owner_earnings", "股东盈余", REJECT,
                   detail + " —— 累计股东盈余为负，生意在消耗现金", notes)
    if ratio >= 1.0:
        return Ind("owner_earnings", "股东盈余", PASS, detail, notes)
    if ratio >= 0.7:
        notes.append("股东盈余低于账面净利，差额多来自资本开支或营运资金占用——"
                     "这正是巴菲特强调「账面利润≠股东到手现金」的地方")
        return Ind("owner_earnings", "股东盈余", QUESTION, detail, notes)
    return Ind("owner_earnings", "股东盈余", REJECT,
               detail + " —— 赚到的现金远少于账面利润", notes)


# ---------------------------------------------------------------------------
# 指标 2：ROE 一致性
# ---------------------------------------------------------------------------
def ind_roe(rows: list[dict], years: int) -> Ind:
    yrs = rows[-years:]
    vals, notes = [], []
    dropped = []
    for r in yrs:
        roe = _num(r.get("ROEJQ"))
        lev = _num(r.get("ZCFZL"))
        if roe is None:
            continue
        if lev is not None and lev > 60:
            # 高杠杆推高的 ROE 不是护城河的证据，剔出 min 判定，但不能静默。
            # 剔除时必须连被剔的 ROE 一起报出来——只报 ZCFZL 的话，读者看得到「丢了几年」，
            # 却看不到「丢掉的这几年本来是多少」，等于把判断依据藏起来了。
            dropped.append(f"{r['year']}(ROE {roe:.1f}%/ZCFZL {lev:.0f}%)")
            continue
        vals.append(roe)
    if dropped:
        notes.append(f"以下年度因资产负债率>60% 被剔出最低值判定（高杠杆虚高）："
                     f"{', '.join(dropped)}")
    if len(vals) < MIN_YEARS_ROE:
        return Ind("roe", "10年ROE一致性", NA,
                   f"有效年报样本仅 {len(vals)} 年（需 ≥{MIN_YEARS_ROE} 年），不足以判断一致性",
                   notes + ["NA 不是 PASS —— 样本不足时不给通过"])
    mn, avg, sd = min(vals), mean(vals), stdev(vals)
    ge15 = sum(1 for v in vals if v >= 15)
    kc = [_num(r.get("ROEKCJQ")) for r in yrs]
    kc = [v for v in kc if v is not None]
    detail = (f"{len(vals)} 年：最低 {mn:.1f}% / 平均 {avg:.1f}%"
              f"{f' / 标准差 {sd:.1f}pct' if sd is not None else ''}"
              f" / ≥15% 的有 {ge15} 年")
    if kc:
        gap = mean(vals) - mean(kc)
        detail += f"；扣非 ROE 均值 {mean(kc):.1f}%（差 {gap:+.1f}pct）"
        if gap > 5:
            notes.append(f"ROEJQ 与 ROEKCJQ 持续相差 {gap:.1f}pct，利润含较多非经常性损益，判定降级")
            if mn >= 15 and avg >= 18:
                return Ind("roe", "10年ROE一致性", QUESTION, detail, notes)
    if mn >= 15 and avg >= 18:
        return Ind("roe", "10年ROE一致性", PASS, detail, notes)
    if mn >= 10 and avg >= 15:
        return Ind("roe", "10年ROE一致性", QUESTION, detail, notes)
    return Ind("roe", "10年ROE一致性", REJECT,
               detail + " —— 长期回报率没有稳定站在高位", notes)


# ---------------------------------------------------------------------------
# 指标 3：毛利率稳定性
# ---------------------------------------------------------------------------
def ind_margin(rows: list[dict], years: int) -> Ind:
    vals = [(_num(r.get("XSMLL")), r["year"]) for r in rows[-years:]]
    vals = [(v, y) for v, y in vals if v is not None]
    if len(vals) < MIN_YEARS_MARGIN:
        return Ind("margin", "毛利率稳定性", NA,
                   f"有效年报样本仅 {len(vals)} 年（需 ≥{MIN_YEARS_MARGIN} 年）")
    xs = [v for v, _ in vals]
    sd, avg, rng = stdev(xs), mean(xs), max(xs) - min(xs)
    detail = (f"{len(xs)} 年（{vals[0][1]}-{vals[-1][1]}）："
              f"平均 {avg:.1f}% / 标准差 {sd:.1f}pct / 极差 {rng:.1f}pct")
    notes = ["毛利率的绝对水平是行业相对的：消费品 40% 与重资产 20% 不可直接比较，"
             "本判定只看稳定性与绝对水平的组合，不含行业基准"]
    if sd <= 3 and rng <= 10 and avg >= 30:
        return Ind("margin", "毛利率稳定性", PASS, detail, notes)
    if sd <= 5 and avg >= 20:
        return Ind("margin", "毛利率稳定性", QUESTION, detail, notes)
    return Ind("margin", "毛利率稳定性", REJECT,
               detail + " —— 毛利率波动大或水平偏低，定价权存疑", notes)


# ---------------------------------------------------------------------------
# 指标 4：资本开支强度
# ---------------------------------------------------------------------------
def ind_capex(cf: dict[int, dict], inc: dict[int, dict], years: int) -> Ind:
    yrs = sorted(set(cf) & set(inc))[-years:]
    if not yrs:
        return Ind("capex", "资本开支强度", NA, "缺年报现金流或利润表数据")
    ocfs, capexs, revs, deprs = [], [], [], []
    for y in yrs:
        ocf = _num(cf[y].get("NETCASH_OPERATE"))
        capex = _num(cf[y].get("CONSTRUCT_LONG_ASSET"))
        rev = _num(inc[y].get("TOTAL_OPERATE_INCOME"))
        dep = sum(x for x in (_num(cf[y].get("FA_IR_DEPR")), _num(cf[y].get("IA_AMORTIZE")),
                              _num(cf[y].get("LPE_AMORTIZE"))) if x)
        if ocf is None or capex is None:
            continue
        ocfs.append(ocf)
        capexs.append(capex)
        if rev:
            revs.append(rev)
        if dep:
            deprs.append(dep)
    if not ocfs:
        return Ind("capex", "资本开支强度", NA, "缺 CONSTRUCT_LONG_ASSET 或经营现金流")
    sum_ocf, sum_capex = sum(ocfs), sum(capexs)
    if sum_ocf <= 0:
        return Ind("capex", "资本开支强度", REJECT,
                   f"近 {len(ocfs)} 年累计经营现金流为负（{sum_ocf / 1e8:,.1f} 亿），无法自我供血")
    r_ocf = sum_capex / sum_ocf
    notes = []
    detail = f"近 {len(ocfs)} 年：capex/OCF = {r_ocf:.2f}"
    if revs:
        r_rev = sum(capexs) / sum(revs)
        detail += f" / capex/营收 = {r_rev:.3f}"
    else:
        r_rev = None
    if deprs:
        r_dep = sum(capexs) / sum(deprs)
        detail += f" / capex/折旧摊销 = {r_dep:.2f}"
        if r_dep < 1.2:
            notes.append(f"capex/折旧摊销 ≈ {r_dep:.1f}，接近维持性开支，扩张性投入少")
        elif r_dep > 2.0:
            notes.append(f"capex/折旧摊销 ≈ {r_dep:.1f}，属于扩张性投入——"
                         "要额外问：这些产能投出去能赚回原来的回报率吗")
    if r_rev is not None and r_rev > 0.10:
        return Ind("capex", "资本开支强度", REJECT,
                   detail + " —— 每 1 元收入要投入超过 1 毛钱资本开支，是重资产生意",
                   notes + ["这类生意即便赚钱也留不下现金，正是巴菲特反复提醒的类型"])
    if r_ocf <= 0.25 and (r_rev is None or r_rev <= 0.05):
        return Ind("capex", "资本开支强度", PASS, detail, notes)
    if r_ocf <= 0.50:
        return Ind("capex", "资本开支强度", QUESTION, detail, notes)
    return Ind("capex", "资本开支强度", REJECT,
               detail + " —— 半数以上经营现金流被资本开支吃掉", notes)


# ---------------------------------------------------------------------------
# 指标 5：盈利质量
# ---------------------------------------------------------------------------
def ind_quality(cf: dict[int, dict], inc: dict[int, dict], years: int) -> Ind:
    yrs = sorted(set(cf) & set(inc))[-years:]
    pairs = []
    for y in yrs:
        ocf, np_ = _num(cf[y].get("NETCASH_OPERATE")), _num(inc[y].get("PARENT_NETPROFIT"))
        if ocf is not None and np_:
            pairs.append((y, ocf, np_, ocf / np_))
    if len(pairs) < MIN_YEARS_QUALITY:
        return Ind("quality", "盈利质量", NA,
                   f"有效样本仅 {len(pairs)} 年（需 ≥{MIN_YEARS_QUALITY} 年）")
    agg = sum(p[1] for p in pairs) / sum(p[2] for p in pairs)
    n_ge1 = sum(1 for p in pairs if p[3] >= 1.0)
    detail = (f"{len(pairs)} 年（{pairs[0][0]}-{pairs[-1][0]}）："
              f"累计 OCF/累计净利 = {agg:.2f}；逐年 ≥1 的有 {n_ge1}/{len(pairs)} 年")
    notes = []
    if agg >= 1.0 and n_ge1 >= 7:
        return Ind("quality", "盈利质量", PASS, detail, notes)
    if 0.8 <= agg <= 1.0:
        notes.append("利润的现金含量略低，留意应收账款与存货的增速是否长期快于收入")
        return Ind("quality", "盈利质量", QUESTION, detail, notes)
    return Ind("quality", "盈利质量", REJECT,
               detail + " —— 利润没有变成现金", notes)


# ---------------------------------------------------------------------------
# 指标 6：杠杆
# ---------------------------------------------------------------------------
def ind_leverage(rows: list[dict], inc: dict[int, dict], years: int) -> Ind:
    rows = [r for r in rows[-years:] if _num(r.get("ZCFZL")) is not None]
    if not rows:
        return Ind("leverage", "杠杆", NA, "缺 ZCFZL / INTEREST_DEBT_RATIO 数据")
    r0 = rows[-1]
    zcfzl = _num(r0.get("ZCFZL"))
    idr = _num(r0.get("INTEREST_DEBT_RATIO"))
    np_ = _num(r0.get("PARENTNETPROFIT"))
    detail = f"{r0['year']} 年报：资产负债率 {zcfzl:.1f}%" if zcfzl is not None else f"{r0['year']} 年报"
    notes = []
    if idr is not None:
        detail += f" / 有息负债率 {idr:.1f}%"
        if np_ and np_ > 0:
            # 有息负债/净利润 → 用几年利润才能还清。这是巴菲特看债务的方式。
            notes.append("偿还年数 = 有息负债 / 净利润，用最近一年口径近似")
    if zcfzl is not None and zcfzl > 60:
        return Ind("leverage", "杠杆", REJECT,
                   detail + " —— 资产负债率过高，任何一次意外都可能致命", notes)
    if idr is not None and idr > 40:
        return Ind("leverage", "杠杆", REJECT, detail + " —— 有息负债率过高", notes)
    if zcfzl is not None and zcfzl <= 40 and (idr is None or idr <= 15):
        return Ind("leverage", "杠杆", PASS, detail, notes)
    if zcfzl is not None and zcfzl <= 55 and (idr is None or idr <= 30):
        return Ind("leverage", "杠杆", QUESTION, detail, notes)
    return Ind("leverage", "杠杆", QUESTION, detail + " —— 杠杆处于中间地带，需逐项看债务结构", notes)


# ---------------------------------------------------------------------------
# 指标 7：一美元留存测试
# ---------------------------------------------------------------------------
def q_valuation_hk(conn, code: str) -> list[dict]:
    """港股估值行。market_cap 单位是港元（百度源直接给总市值），需折人民币后再用。
    列不存在时退回不带市值的版本——老库没跑过 fetch-valuation 时不该直接崩。"""
    try:
        return [dict(r) for r in conn.execute(
            "SELECT date, pe_ttm, pb, market_cap FROM stock_valuation_history"
            " WHERE stock_code = ? AND pe_ttm IS NOT NULL ORDER BY date", (code,))]
    except sqlite3.Error:
        return q_valuation(conn, code)


def hk_yield_snapshot(conn, code: str) -> dict | None:
    """hk_yield_cache 的最新一条快照（股息率/回购收益率，单位 %）。缺表、缺行都返回 None。

    这张表由 security-analysis 的 etf_valuation.py 写入，**只有单日快照、没有时间序列**
    （实测全表只有一个交易日）。所以它只能做「当前收益率」的交叉验证，不能喂留存测试
    那种需要逐年序列的计算——把它当成年内数据用会静默算错。
    """
    try:
        r = conn.execute(
            "SELECT date, price, dividend_yield, buyback_yield, total_yield"
            " FROM hk_yield_cache WHERE stock_code = ? ORDER BY date DESC LIMIT 1",
            (code,)).fetchone()
    except sqlite3.Error:
        return None
    if not r:
        return None
    return {"date": r[0], "price": _num(r[1]), "dividend_yield": _num(r[2]),
            "buyback_yield": _num(r[3]), "total_yield": _num(r[4])}


def fx_cny_per_hkd(conn, year: int, spot: bool = False) -> float | None:
    """该年港元兑人民币汇率（1 港元 = ? 人民币）。缺失返回 None，由调用方跳过该年，
    绝不用 1:1 或别年的汇率冒充。

    spot=True 取**年末最后一个交易日的即期价**，用于折算年末时点观测到的存量（市值）；
    spot=False 取年均价，用于折算年内流量（利润、股息）。
    两者不可互换：拿年均价折时点市值，等于假装全年市值都停留在均价上——2020 年港元
    从年初 0.89 走到年末 0.84，用均价 0.8895 折年末市值会高估约 5.6%。
    """
    col = "spot_year_end" if spot else "cny_per_hkd"
    try:
        r = conn.execute(f"SELECT {col} FROM hk_fx_rate WHERE year = ?",
                         (year,)).fetchone()
        return _num(r[0]) if r and r[0] is not None else None
    except sqlite3.Error:
        return None


def _market_cap_series_hk(conn, code: str, rows: list[dict],
                          inc: dict | None = None) -> tuple[dict[int, float], list[str]]:
    """港股市值序列，**直接取观测到的总市值**，不做推算。

    为什么这里与 A 股走两条路：A 股的 stock_valuation_history 没有价格/市值列，只能靠
    PE×净利反推；港股源直接给总市值，是观测量而非推导量，没有理由是退而求其次。
    实测百度源的 PE 序列 rebuild 不出净利润（2018 年 市值/PE=31.2 亿 vs 实际 24.6 亿），
    所以用它反推市值反而更差——这也是本函数不用 PE×净利做主线的原因。

    币种：总市值是港元，利润和留存收益是人民币，必须按年折汇率后再比。此处取的是
    **年末时点**市值，所以用**年末即期汇率**折；用年均价折会引入系统性偏差（2020 年
    港元年初 0.89→年末 0.84，均价高估约 5.6%，而留存测试的结论完全由幅度承载）。
    汇率缺失的年份跳过并告警，这会直接减少样本年数，不是可以静默忽略的小事。
    """
    val = [r for r in q_valuation_hk(conn, code) if _num(r.get("market_cap"))]
    if not val:
        return {}, ["stock_valuation_history 无该股 market_cap 数据，市值序列无法建立。"
                    "先跑 fetch-valuation --code <代码> 拉取港股估值历史。"]
    prof = {r["year"]: _num(r.get("PARENTNETPROFIT")) for r in rows}
    if inc:
        for y, r in inc.items():
            prof.setdefault(y, _num(r.get("PARENT_NETPROFIT")))
    prof = {y: v for y, v in prof.items() if v}
    by_year, warnings = {}, []
    for y, np_ in prof.items():
        target = f"{y}-12-31"
        cand = sorted(val, key=lambda r: abs(_day_diff(r["date"], target)))[:1]
        if not cand or _day_diff(cand[0]["date"], target) > 10:
            continue
        cap_hkd = _num(cand[0]["market_cap"])
        if not cap_hkd:
            continue
        # 取的是**年末时点**观测到的市值，就必须用年末即期汇率折，不能用年均价。
        fx = fx_cny_per_hkd(conn, y, spot=True)
        if not fx:
            fx = fx_cny_per_hkd(conn, y, spot=False)
            if fx:
                warnings.append(
                    f"{y} 年缺年末即期汇率，退回年均汇率折算——该年市值有口径偏差，"
                    f"重跑 fetch-valuation 可回填即期价")
        if not fx:
            warnings.append(f"{y} 年缺港元/人民币汇率，该年市值跳过（样本年数因此减少）")
            continue
        mc = cap_hkd * fx
        pe = _num(cand[0]["pe_ttm"])
        if pe and np_ and mc > 0:
            mc2 = pe * np_
            if abs(mc - mc2) / mc > 0.20:
                warnings.append(
                    f"{y} 年市值两法分歧：总市值×汇率={mc / 1e8:,.0f} 亿 vs PE×净利={mc2 / 1e8:,.0f} 亿"
                    f"（差 {abs(mc - mc2) / mc * 100:.0f}%）。港股 PE 源为混合币种口径，"
                    f"本测试以直接观测的总市值为准，该年可靠性下降")
        by_year[y] = mc
    return by_year, warnings


def _market_cap_series(conn, code: str, rows: list[dict],
                       inc: dict | None = None) -> tuple[dict[int, float], list[str]]:
    """推算逐年市值：市值 = pe_ttm × 归母净利_TTM，取 date 最接近 Y-12-31 的估值行。

    stock_valuation_history 没有价格列，市值必须推导。再用 pb × BPS × 股本 交叉验证，
    两法背离 >20% 时明确告警——推出来的数不能假装是查出来的。
    """
    if detect_market(conn, code) == "hk":
        return _market_cap_series_hk(conn, code, rows, inc)
    val = q_valuation(conn, code)
    if not val:
        return {}, ["stock_valuation_history 无该股数据，市值无法推算"]
    by_year, warnings = {}, []
    prof = {r["year"]: _num(r.get("PARENTNETPROFIT")) for r in rows}
    if inc:   # em_financial_indicator 未覆盖该股时，净利用利润表补，否则市值一律推不出来
        for y, r in inc.items():
            prof.setdefault(y, _num(r.get("PARENT_NETPROFIT")))
    prof = {y: v for y, v in prof.items() if v}
    bps = {r["year"]: _num(r.get("BPS")) for r in rows}
    eps = {r["year"]: _num(r.get("EPSJB")) for r in rows}
    for y, np_ in prof.items():
        if not np_:
            continue
        target = f"{y}-12-31"
        cand = sorted(val, key=lambda r: abs(_day_diff(r["date"], target)))[:1]
        if not cand or _day_diff(cand[0]["date"], target) > 10:
            continue
        pe = _num(cand[0]["pe_ttm"])
        if not pe:
            continue
        mc = pe * np_
        pb = _num(cand[0]["pb"])
        if pb and bps.get(y) and eps.get(y) and eps[y]:
            shares = np_ / eps[y]
            mc2 = pb * bps[y] * shares
            if mc2 > 0 and abs(mc - mc2) / mc > 0.20:
                warnings.append(
                    f"{y} 年市值推算分歧：PE×净利={mc / 1e8:,.0f} 亿 vs PB×净资产={mc2 / 1e8:,.0f} 亿"
                    f"（差 {abs(mc - mc2) / mc * 100:.0f}%），该年结论可靠性下降")
        by_year[y] = mc
    return by_year, warnings


def _day_diff(date_str: str, target: str) -> int:
    from datetime import date as _d
    try:
        a = _d.fromisoformat(str(date_str)[:10])
        b = _d.fromisoformat(target)
        return abs((a - b).days)
    except (ValueError, TypeError):
        return 9999


def ind_retention(conn, code: str, rows: list[dict], cf: dict, inc: dict,
                  start_year: int | None = None) -> Ind:
    """一美元留存测试：每留存 1 元，市值是否至少增加 1 元。"""
    mc, warns = _market_cap_series(conn, code, rows, inc)
    if len(mc) < MIN_YEARS_RETENTION:
        return Ind("retention", "一美元留存测试", NA,
                   f"可推算市值的年份仅 {len(mc)} 年（需 ≥{MIN_YEARS_RETENTION} 年）", warns)

    dps = q_dividend_per_share(conn, code)
    np_by_year = {r["year"]: _num(r.get("PARENTNETPROFIT")) for r in rows}
    for y, r in inc.items():
        np_by_year.setdefault(y, _num(r.get("PARENT_NETPROFIT")))
    share_map = {}
    for r in rows:
        np_, eps_ = _num(r.get("PARENTNETPROFIT")), _num(r.get("EPSJB"))
        if np_ and eps_:
            share_map[r["year"]] = np_ / eps_
    for r in inc.values():
        y, eps_ = _to_int(r.get("year")), _num(r.get("BASIC_EPS"))
        if y and eps_ and y not in share_map and np_by_year.get(y):
            share_map[y] = np_by_year[y] / eps_

    div_source, div_by_year, notes = None, {}, list(warns)
    if dps and share_map:
        div_source = "dividend_annual_yield（每股股息 × 股本，口径最干净）"
        for y, per in dps.items():
            if y in share_map:
                div_by_year[y] = per * share_map[y]
    if not div_by_year:
        hk = detect_market(conn, code) == "hk"
        for y, c in cf.items():
            amt = _num(c.get("ASSIGN_DIVIDEND_PORFIT"))
            if amt is not None:
                div_by_year[y] = amt
        if div_by_year:
            if hk:
                div_source = "hk_cash_flow.007004 已付股息(融资)"
                notes.append("股息取自港股现金流量表「已付股息(融资)」——该科目只含股息、"
                             "不含利息，口径比 A 股的 ASSIGN_DIVIDEND_PORFIT 干净，"
                             "不存在股息被高估的问题。注意该科目是**已付**（收付实现），"
                             "与利润表口径的应付股息存在时点差。")
            else:
                div_source = "em_cash_flow.ASSIGN_DIVIDEND_PORFIT"
                notes.append("股息取自 ASSIGN_DIVIDEND_PORFIT（分配股利、利润或偿付利息支付的现金）"
                             "——这个科目把利息支付也算了进去，股息被高估 → 留存收益被低估 → "
                             "本测试偏保守。看数时请记住这一点。")
    if not div_by_year:
        return Ind("retention", "一美元留存测试", NA,
                   "股息数据缺失（dividend_annual_yield 无该股，ASSIGN_DIVIDEND_PORFIT 也为空）。"
                   "数据不足时记 NA —— 绝不默认 PASS。", notes)

    yrs = sorted(set(mc) & set(np_by_year))
    if start_year:
        yrs = [y for y in yrs if y >= start_year]
    if len(yrs) < MIN_YEARS_RETENTION:
        return Ind("retention", "一美元留存测试", NA,
                   f"市值与净利重叠年份仅 {len(yrs)} 年（需 ≥{MIN_YEARS_RETENTION} 年）", notes)

    y0, y1 = yrs[0], yrs[-1]
    retained = sum(np_by_year[y] - div_by_year.get(y, 0.0) for y in yrs[1:])
    dmc = mc[y1] - mc[y0]
    detail = (f"{y0}-{y1}：累计留存 {retained / 1e8:,.1f} 亿，市值变化 {dmc / 1e8:,.1f} 亿"
              f"（{mc[y0] / 1e8:,.0f} → {mc[y1] / 1e8:,.0f} 亿）")
    notes.append(f"股息来源：{div_source}")
    if detect_market(conn, code) == "hk":
        # 港股多一张 hk_yield_cache，带回购收益率——正是本测试缺的那一块。
        # 但它是单日快照（无时间序列），只能做交叉验证，绝不能当成逐年数据参与计算。
        snap = hk_yield_snapshot(conn, code)
        if snap:
            notes.append(
                f"当前收益率快照（hk_yield_cache {snap['date']}）：股息率 "
                f"{C.fmt(snap['dividend_yield'])}% / 回购收益率 "
                f"{C.fmt(snap['buyback_yield'])}%（合计 {C.fmt(snap['total_yield'])}%）。"
                f"该表是**单日快照**，仅用于与上面的逐年股息对照，未参与本测试计算。")
            if snap["buyback_yield"]:
                notes.append(
                    f"⚠ 该公司有回购（{C.fmt(snap['buyback_yield'])}%）：回购与股息同属资本"
                    f"返还，而本测试的留存只减了股息、**没减回购** → 留存被高估、比值偏保守。"
                    f"逐年回购数据不在库内（stock_repurchase 无该股），无法精确修正。")
        else:
            notes.append("hk_yield_cache 无该股快照（该表由 etf_valuation.py 写入），"
                         "当前股息率与回购收益率无法交叉验证。")
    if retained <= 0:
        return Ind("retention", "一美元留存测试", NA,
                   detail + " —— 期间累计留存为负（分红超过利润），该测试不适用", notes)
    ratio = dmc / retained
    detail += f"，每留存 1 元创造 {ratio:.2f} 元市值"
    if ratio >= 1.0:
        return Ind("retention", "一美元留存测试", PASS, detail, notes)
    if ratio >= 0.7:
        return Ind("retention", "一美元留存测试", QUESTION, detail, notes)
    return Ind("retention", "一美元留存测试", REJECT,
               detail + " —— 留存的每一块钱没有创造出一块钱的市值，钱被浪费了", notes)


# ---------------------------------------------------------------------------
# 指标 8：安全边际
# ---------------------------------------------------------------------------
def ind_valuation(conn, code: str) -> Ind:
    val = q_valuation(conn, code)
    if len(val) < 20:
        return Ind("valuation", "安全边际", NA,
                   f"stock_valuation_history 仅 {len(val)} 条记录（需 ≥20 条才能谈分位）")
    pes = [_num(r["pe_ttm"]) for r in val]
    pbs = [_num(r["pb"]) for r in val]
    cur_pe, cur_pb = pes[-1], pbs[-1]
    pe_pct = percentile_rank(pes, cur_pe)
    pb_pct = percentile_rank(pbs, cur_pb)
    if pe_pct is None:
        return Ind("valuation", "安全边际", NA, "PE 分位无法计算")
    cyclical = code in CYCLICAL_CODES
    notes = []
    detail = (f"{val[0]['date']}~{val[-1]['date']}（{len(val)} 条）："
              f"PE_TTM {C.fmt(cur_pe)}（{pe_pct:.0f} 分位）"
              f" / PB {C.fmt(cur_pb)}（{C.fmt(pb_pct, '{:.0f}')} 分位）")
    if cyclical:
        notes.append("**周期股：PE 分位的语义是反的** —— 周期顶部利润高、PE 低，"
                     "周期底部利润薄、PE 高。此判定改以 PB 分位为主信号，详见 "
                     ".claude/skills/security-analysis/cyclical-analysis-guide.md §一")
        primary = pb_pct
    else:
        primary = pe_pct
    if cur_pe is not None and cur_pe > 40:
        notes.append(f"硬约束：PE_TTM {cur_pe:.0f} > 40，无论分位多低，最高只能给 QUESTION")
        return Ind("valuation", "安全边际",
                   QUESTION if primary is not None and primary <= 60 else QUESTION, detail, notes)
    if primary is None:
        return Ind("valuation", "安全边际", NA, detail, notes)
    if primary <= 30 or (pe_pct <= 40 and pb_pct is not None and pb_pct <= 30):
        return Ind("valuation", "安全边际", PASS, detail, notes)
    if primary <= 60:
        return Ind("valuation", "安全边际", QUESTION, detail, notes)
    return Ind("valuation", "安全边际", REJECT,
               detail + " —— 估值处于历史高位，没有留出安全边际", notes)


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
# 一票否决：护城河（毛利率稳定性、资本开支强度、杠杆）、盈利质量、杠杆
VETO_KEYS = {"margin", "capex", "quality", "leverage"}


def summarize(inds: list[Ind]) -> tuple[str, list[str]]:
    by_key = {i.key: i for i in inds}
    n_pass = sum(1 for i in inds if i.verdict == PASS)
    n_reject = sum(1 for i in inds if i.verdict == REJECT)
    n_na = sum(1 for i in inds if i.verdict == NA)
    vetoed = [i.name for i in inds if i.key in VETO_KEYS and i.verdict == REJECT]
    lines = [f"{n_pass} PASS / {sum(1 for i in inds if i.verdict == QUESTION)} QUESTION"
             f" / {n_reject} REJECT / {n_na} NA"]
    if vetoed:
        lines.append(f"一票否决：{', '.join(vetoed)}")
        # 文案必须与 VETO_KEYS 逐项对齐：少了「资本开支」的话，读者会以为被否的
        # 只有护城河/盈利质量/杠杆，而实际上这一票可能正是资本开支投出来的。
        lines.append("巴菲特不会因为便宜而买烂生意 —— 护城河（毛利率稳定性、资本开支强度）、"
                     "盈利质量、杠杆任一项被否，估值再低也不是机会。")
        return "否决", lines
    if n_na >= 4:
        lines.append(f"{n_na} 项因数据不足无法判断 —— 这不是通过，是「不知道」。"
                     "先补齐财报数据（collect_financial_data.py collect）再筛。")
        return "数据不足", lines
    if n_pass >= 6 and n_reject == 0:
        lines.append("通过初筛。下一步：按适用性挑 4-6 张原则卡，检索原文逐条对照，"
                     "重点看能力圈与护城河。")
        return "通过初筛", lines
    lines.append("未通过初筛：PASS 不足 6 项或存在 REJECT。"
                 "REJECT 指向的是生意的性质，不是价格；降价不改变结论。")
    return "未通过初筛", lines


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def collect(conn, code: str, years: int) -> dict:
    market = detect_market(conn, code)
    if market == "hk":
        # 港股走长表透视，产出与 A 股同名的键；下面指标层因此完全无需分支
        rows = q_annual_indicator_hk(conn, code)
        cf = q_annual_cashflow_hk(conn, code)
        inc = q_annual_income_hk(conn, code)
        name = rows[-1].get("SECURITY_NAME_ABBR") if rows else None
        return {"market": market, "rows": rows, "cf": cf, "inc": inc, "name": name or code}
    rows = q_annual_indicator(conn, code)
    cf = q_annual_cashflow(conn, code)
    inc = q_annual_income(conn, code)
    name = rows[-1].get("SECURITY_NAME_ABBR") if rows else None
    if not name:
        for tbl in ("em_income_statement", "em_cash_flow"):
            try:
                r = conn.execute(
                    f"SELECT SECURITY_NAME_ABBR FROM {tbl} WHERE stock_code = ?"
                    f" AND SECURITY_NAME_ABBR IS NOT NULL LIMIT 1", (code,)).fetchone()
            except sqlite3.Error:
                r = None
            if r and r[0]:
                name = r[0]
                break
    return {"market": market, "rows": rows, "cf": cf, "inc": inc, "name": name or code}


def build_report(conn, code: str, years: int) -> dict:
    d = collect(conn, code, years)
    rows, cf, inc = d["rows"], d["cf"], d["inc"]
    inds = [
        ind_owner_earnings(cf, inc, years),
        ind_roe(rows, years),
        ind_margin(rows, years),
        ind_capex(cf, inc, years),
        ind_quality(cf, inc, years),
        ind_leverage(rows, inc, years),
        ind_retention(conn, code, rows, cf, inc),
        ind_valuation(conn, code),
    ]
    verdict, lines = summarize(inds)
    return {"code": code, "name": d["name"], "years": years, "indicators": inds,
            "verdict": verdict, "summary": lines}


def print_report(rep: dict) -> None:
    code, name = rep["code"], rep["name"]
    n = [0]

    def H(title, note=""):
        n[0] += 1
        print()
        print(C.heading(n[0], title) + (f"  {note}" if note else ""))

    print(C.sep())
    print(f"巴菲特视角量化筛查 · {name}（{code}）  近 {rep['years']} 年年报口径")
    print(C.sep())

    H("能力圈")
    print("  N/A — 需判定")
    print("  这一步脚本做不了。先用一句话说清「这家公司靠什么赚钱」，再列出 3 个竞争对手；")
    print("  说不清就是能力圈外，到此为止——后面的数字再漂亮也不该看。")

    for ind in rep["indicators"]:
        H(ind.name, f"[{ind.verdict}]")
        for ln in C.wrap(ind.detail):
            print("  " + ln)
        for note in ind.notes:
            for i, ln in enumerate(C.wrap(note, 80)):
                print(("  ⚠ " if i == 0 else "    ") + ln)

    H("汇总")
    for ln in rep["summary"]:
        for seg in C.wrap(ln, 82):
            print("  " + seg)
    print()
    print(C.line())
    print("数字只回答「生意好不好、价格贵不贵」，回答不了「你是否真的懂它」。")
    print("下一步：按判定结果挑原则卡（principles/INDEX.md），并用 search_corpus.py")
    print("检索原文对照——卡片里的引用都能逐字复核，报告里的每句话都应如此。")


def cmd_screen(args) -> int:
    conn = C.get_conn(args.db)
    rep = build_report(conn, args.code, args.years)
    if args.json:
        print(json.dumps({**rep, "indicators": [i.as_dict() for i in rep["indicators"]]},
                         ensure_ascii=False, indent=2))
    else:
        print_report(rep)
    if args.strict and rep["verdict"] != "通过初筛":
        return 1
    return 0


def cmd_data_check(args) -> int:
    conn = C.get_conn(args.db)
    code = args.code
    market = detect_market(conn, code)
    print(C.sep())
    print(f"数据体检 · {code}（{'港股' if market == 'hk' else 'A股'}）")
    print(C.sep())
    if market == "hk":
        # 港股的三张长表才是主力；hk_financial_indicator 只覆盖近年（00322 仅 2022 起），
        # 所以它不算必需——指标层是从长表合成的，不依赖它。
        checks = [
            ("hk_balance_sheet（年报）", "SELECT COUNT(*) FROM hk_balance_sheet"
             " WHERE stock_code=? AND quarter=4", True),
            ("hk_income_statement（年报）", "SELECT COUNT(*) FROM hk_income_statement"
             " WHERE stock_code=? AND quarter=4", True),
            ("hk_cash_flow（年报）", "SELECT COUNT(*) FROM hk_cash_flow"
             " WHERE stock_code=? AND quarter=4", True),
            ("hk_financial_indicator（年报）", "SELECT COUNT(*) FROM hk_financial_indicator"
             " WHERE stock_code=? AND quarter=4", False),
            ("stock_valuation_history（含总市值）",
             "SELECT COUNT(*) FROM stock_valuation_history"
             " WHERE stock_code=? AND pe_ttm IS NOT NULL", False),
            ("hk_fx_rate（港元年均汇率）", "SELECT COUNT(*) FROM hk_fx_rate", False),
        ]
        fix = (f"补齐财报：python .claude/skills/security-analysis/scripts/"
               f"collect_financial_data.py collect --code {code} --market hk")
        fill = (f"补齐估值：python .claude/skills/buffett-lens/scripts/"
                f"buffett_analysis.py fetch-valuation --code {code}")
    else:
        checks = [
            ("em_financial_indicator（年报）", "SELECT COUNT(*) FROM em_financial_indicator"
             " WHERE stock_code=? AND REPORT_DATE_NAME LIKE '%年报'", True),
            ("em_cash_flow（q4）", "SELECT COUNT(*) FROM em_cash_flow"
             " WHERE stock_code=? AND quarter=4", True),
            ("em_income_statement（q4）", "SELECT COUNT(*) FROM em_income_statement"
             " WHERE stock_code=? AND quarter=4", True),
            ("stock_valuation_history", "SELECT COUNT(*) FROM stock_valuation_history"
             " WHERE stock_code=?", False),
            ("dividend_annual_yield", "SELECT COUNT(*) FROM dividend_annual_yield"
             " WHERE stock_code=?", False),
        ]
        fix = (f"补齐：python .claude/skills/security-analysis/scripts/"
               f"collect_financial_data.py collect --code {code} --market "
               f"{'sh' if code.startswith('6') else 'sz'}")
        fill = None
    missing = []
    for label, sql, required in checks:
        # hk_fx_rate 那条是全表计数、没有占位符——无条件传 (code,) 会让 sqlite3 抛
        # 「绑定数不符」，被下面的 except 吞成 -1，于是显示成「-1 行」。
        # 按?出现与否决定是否传参，才能让「查不到」和「没查」区分开。
        params = (code,) if "?" in sql else ()
        try:
            cnt = conn.execute(sql, params).fetchone()[0]
        except sqlite3.Error:
            cnt = -1
        mark = "✓" if cnt > 0 else ("✗" if required else "·")
        if cnt <= 0 and required:
            missing.append(label)
        print(f"  {mark} {C.pad(label, 32)} {cnt:>6} 行")
    d = collect(conn, code, args.years)
    if d["rows"]:
        ys = sorted(r["year"] for r in d["rows"])
        print(f"  {C.line()}")
        print(f"  年报年份：{ys[0]}-{ys[-1]}（{len(ys)} 年）")
        gaps = [y for y in range(ys[0], ys[-1] + 1) if y not in set(ys)]
        if gaps:
            print(f"  缺年：{', '.join(str(y) for y in gaps)}")
    print(C.line())
    if missing:
        print(f"缺失：{', '.join(missing)}")
        print(fix)
        print("相关指标会记 NA —— NA 不是 PASS。")
    else:
        print("八项指标所需财报数据齐备"
              + ("；估值与汇率缺失时指标⑦⑧会记 NA。" if fill else "。"))
    if fill and not any("stock_valuation_history" in m for m in missing):
        # 估值不是「必需」，缺了不该报「缺失」，但它决定指标⑦⑧能不能算，必须说出来
        try:
            n = conn.execute("SELECT COUNT(*) FROM stock_valuation_history"
                             " WHERE stock_code=? AND pe_ttm IS NOT NULL", (code,)).fetchone()[0]
        except sqlite3.Error:
            n = 0
        if n <= 0:
            print(fill)
            print("港股估值历史为空 → 指标⑦（留存测试）与⑧（安全边际）会记 NA。")
    return 0


def cmd_owner_earnings(args) -> int:
    conn = C.get_conn(args.db)
    d = collect(conn, args.code, args.years)
    ind = ind_owner_earnings(d["cf"], d["inc"], args.years)
    print(C.sep())
    print(f"股东盈余 · {d['name']}（{args.code}）")
    print(C.sep())
    print(f"[{ind.verdict}] {ind.detail}")
    for note in ind.notes:
        print(f"  ⚠ {note}")
    print(C.line())
    print("股东盈余 = 经营现金流 − 维持性资本开支。巴菲特用它替代账面净利，")
    print("因为折旧摊销是会计假设，而资本开支是真实支出的钱。")
    print("检索原文：search_corpus.py \"owner earnings\" --source letters")
    return 0


def cmd_retention(args) -> int:
    conn = C.get_conn(args.db)
    d = collect(conn, args.code, args.years)
    ind = ind_retention(conn, args.code, d["rows"], d["cf"], d["inc"], args.start_year)
    print(C.sep())
    print(f"一美元留存测试 · {d['name']}（{args.code}）")
    print(C.sep())
    print(f"[{ind.verdict}] {ind.detail}")
    for note in ind.notes:
        print(f"  ⚠ {note}")
    print(C.line())
    print("测的是管理层把利润留下来再投资的纪律。留存本身不是美德，")
    print("只有留下的每一块钱能变成超过一块钱市值时才成立。")
    print("检索原文：search_corpus.py \"one dollar\" --source letters")
    return 0


# 分部利润率的分母门槛：占当年总收入不到这个比例的分部不列利润率。
# 康师傅「其他」分部收入仅占总收入 0.8%，却摊着投资控股/租金/支援职能的开支，
# 算出来的 -24%～-34% 是总部分摊的产物，不是那个分部的经营质量。
_MARGIN_MIN_SHARE = 0.05


def segment_series(conn, code: str) -> dict:
    """读 hk_segment_revenue，返回 {metric: {year: {segment: 元}}} 与元信息。"""
    try:
        rows = conn.execute(
            "SELECT fiscal_year, metric, segment, segment_en, kind, amount,"
            " company, note_ref FROM hk_segment_revenue WHERE stock_code=?"
            " ORDER BY fiscal_year", (code,)).fetchall()
    except sqlite3.Error:
        return {}
    if not rows:
        return {}
    out: dict = {"company": rows[0][6], "note_ref": rows[0][7], "metrics": {}}
    for yr, metric, seg, seg_en, kind, amt, _c, _n in rows:
        m = out["metrics"].setdefault(metric, {"segments": {}, "kinds": {}, "en": {}})
        m["segments"].setdefault(seg, {})[yr] = float(amt)
        m["kinds"][seg] = kind
        m["en"][seg] = seg_en
    return out


def cmd_segment(args) -> int:
    """分部收入/业绩走势 —— ⓪ 能力圈（钱从哪来）与 ① 定价权（提价能不能落地）的原始证据。"""
    conn = C.get_conn(args.db)
    data = segment_series(conn, args.code)
    print(C.sep())
    if not data:
        print(f"分部数据 · {args.code}")
        print(C.sep())
        print("  库内没有该股的分部数据（hk_segment_revenue）。")
        print("  用 financial-report-pdf-extractor 抽取年报分部附注后入库：")
        print("    .venv/bin/python .claude/skills/financial-report-pdf-extractor/"
              "scripts/extract_segment_note.py \\")
        print("        --pdf report/xxx_2025.pdf --code " + args.code + " --store")
        return 0
    print(f"分部数据 · {data['company']}（{args.code}）  {data['note_ref']}")
    print(C.sep())

    names = list(data["metrics"])
    metrics = [args.metric] if args.metric else names
    for metric in metrics:
        if metric not in data["metrics"]:
            print(f"\n  无 {metric} 口径。库内有：{names}")
            continue
        m = data["metrics"][metric]
        yrs = sorted({y for s in m["segments"].values() for y in s})
        # 按最新一年的规模降序 —— 一眼看出钱主要从哪来，这是能力圈那个问题。
        segs = sorted((s for s, k in m["kinds"].items() if k == "segment"),
                      key=lambda s: -(m["segments"][s].get(yrs[-1]) or 0))
        print(f"\n【{metric}】（单位：亿元）")
        print("  " + C.pad("分部", 16) + "".join(C.pad(str(y), 10) for y in yrs))
        for s in segs:
            cells = []
            for y in yrs:
                v = m["segments"][s].get(y)
                cells.append(C.pad(f"{v / 1e8:,.1f}" if v is not None else "—", 10))
            print("  " + C.pad(s, 16) + "".join(cells))
        tot = [s for s, k in m["kinds"].items() if k == "total"]
        if tot:
            print("  " + "-" * (16 + 10 * len(yrs)))
            print("  " + C.pad(tot[0], 16)
                  + "".join(C.pad(f"{m['segments'][tot[0]].get(y, 0) / 1e8:,.1f}", 10)
                            for y in yrs))

        # 总规模的 CAGR：一段够长的增长，比任何单年数字都更能说明生意的性质。
        if tot and len(yrs) >= 3:
            a, b = m["segments"][tot[0]].get(yrs[0]), m["segments"][tot[0]].get(yrs[-1])
            n = len(yrs) - 1
            if a and b and a > 0:
                cagr = (b / a) ** (1 / n) - 1
                print(f"    → {yrs[0]}-{yrs[-1]} 总规模 {a / 1e8:,.1f} → {b / 1e8:,.1f} 亿，"
                      f"年均 {cagr * 100:,.1f}%")

        # 分部利润率的走向才是「定价权」的正着。收入增长可能只是铺货，
        # 而利润率不塌，才说明涨价没被销量下滑吃掉。
        if metric == "external_revenue" and "segment_result" in data["metrics"]:
            res = data["metrics"]["segment_result"]["segments"]
            print(f"\n  {metric} 的分部利润率（分部业绩 / 分部收入）：")
            print(f"    只列占当年总收入 ≥{_MARGIN_MIN_SHARE:.0%} 的分部——分母太小的话，"
                  f"总部分摊一进来比率就失去意义。")
            tseg = m["segments"][tot[0]] if tot else {}
            for s in segs:
                cells, shown = [], False
                for y in yrs:
                    rev, rr = m["segments"][s].get(y), res.get(s, {}).get(y)
                    t = tseg.get(y)
                    if rev and rr is not None and t and rev / t >= _MARGIN_MIN_SHARE:
                        cells.append(C.pad(f"{rr / rev * 100:,.1f}%", 10))
                        shown = True
                    else:
                        cells.append(C.pad("—", 10))
                if shown:
                    print("    " + C.pad(s, 14) + "".join(cells))
                else:
                    print("    " + C.pad(s, 14) + "".join(cells) + "  占比过小，略")

    print("\n" + C.line())
    print("读数纪律：**分部收入增长本身不是定价权**。收入涨可能只是铺货、并表或降价换量。")
    print("  定价权要看到「收入增长的同时，分部利润率没有塌」——上面的利润率行才是正着。")
    print("  另：外部口径与含分部间销售的口径不可混用（如康师傅两者差着租金等")
    print("  「其他來源之收入」）。本表按 metric 分列，切勿跨口径比较。")
    return 0


def cmd_valuation(args) -> int:
    conn = C.get_conn(args.db)
    d = collect(conn, args.code, args.years)
    ind = ind_valuation(conn, args.code)
    print(C.sep())
    print(f"安全边际 · {d['name']}（{args.code}）")
    print(C.sep())
    print(f"[{ind.verdict}] {ind.detail}")
    for note in ind.notes:
        print(f"  ⚠ {note}")
    print(C.line())
    print("「便宜」是相对历史与生意质地说的，不是相对昨天的股价。")
    print("检索原文：search_corpus.py \"margin of safety\" --source letters")
    return 0


# ---------------------------------------------------------------------------
# 港股估值取数
#
# 这是本脚本**唯一会写 financial_data.db 的子命令**。写的是估值缓存表与汇率表，
# 不碰任何财报表——财报数据的采集仍归 security-analysis 的 collect_financial_data.py。
# 之所以要单独一个命令而不是让 screen 顺手拉：screen 是只读的，会重复跑；
# 网络拉取应当是一次显式动作，失败了也不该污染筛查结果。
# ---------------------------------------------------------------------------
def _ensure_valuation_schema(conn) -> None:
    """补 market_cap 列 + 建港元年均汇率表（含年末即期列）。都幂等，重复执行无副作用。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_valuation_history)")}
    if not cols:
        raise sqlite3.OperationalError("no such table: stock_valuation_history")
    if "market_cap" not in cols:
        conn.execute("ALTER TABLE stock_valuation_history ADD COLUMN market_cap REAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hk_fx_rate ("
        " year INTEGER PRIMARY KEY, cny_per_hkd REAL, source TEXT, updated_at TEXT)")
    # 年均价折流量、年末即期价折时点——用途不同，必须分列存。
    # 老库里只有年均列，这里补列后由 _fetch_hk_fx 回填（见那里的 filled 判据）。
    fx_cols = {r[1] for r in conn.execute("PRAGMA table_info(hk_fx_rate)")}
    if "spot_year_end" not in fx_cols:
        conn.execute("ALTER TABLE hk_fx_rate ADD COLUMN spot_year_end REAL")
    conn.commit()


def _valuation_cache_fresh(conn, code: str) -> str | None:
    """该股估值缓存若在 TTL 内，返回上次写入时间；过期或无数据返回 None。

    与 A 股 cyclical_stock_analysis.py 的判据同构（比对 updated_at 与 7 天窗口），
    这样两边的"多久算过期"不会各说各话。
    """
    import datetime as _dt
    try:
        r = conn.execute("SELECT MAX(updated_at) FROM stock_valuation_history"
                         " WHERE stock_code = ?", (code,)).fetchone()
    except sqlite3.Error:
        return None
    if not r or not r[0]:
        return None
    try:
        last = _dt.datetime.fromisoformat(r[0])
    except (TypeError, ValueError):
        return None  # 时间戳不可解析 → 当作过期，宁可重取
    if _dt.datetime.now() - last < _dt.timedelta(days=VALUATION_CACHE_DAYS):
        return r[0]
    return None


def _fetch_hk_fx(conn, refresh: bool) -> int:
    """港元兑人民币汇率（1 港元 = ? 人民币），取自中国银行牌价。

    存两个口径，用途不同、不可混用：
      cny_per_hkd   —— 年内**日均价的平均**，折流量（利润、股息）
      spot_year_end —— 年内**最后一个交易日**的即期价，折时点存量（年末市值）

    为什么必须落库而不是每次实时取：留存测试要把港元市值折成人民币再与人民币留存收益
    比较，汇率是这个折算里唯一的假设。把它存下来，事后能复核「当时用的是哪个数」；
    实时取则每次跑出来的结论都可能不一样。
    """
    import datetime as _dt
    import akshare as ak
    have = conn.execute("SELECT COUNT(*) FROM hk_fx_rate").fetchone()[0]
    # 老库只有年均列、即期列为 NULL —— 光看 have 会让补齐逻辑永远不触发，
    # 所以要单独看即期列填没填。
    filled = conn.execute(
        "SELECT COUNT(*) FROM hk_fx_rate WHERE spot_year_end IS NOT NULL").fetchone()[0]
    if have and filled and not refresh:
        return have
    df = ak.currency_boc_sina(symbol="港币", start_date="20100101", end_date="20261231")
    col = "中行折算价" if "中行折算价" in df.columns else "央行中间价"
    df = df[["日期", col]].dropna()
    df["year"] = df["日期"].astype(str).str[:4].astype(int)
    df = df.sort_values("日期")
    # 牌价是「每 100 港元折多少人民币」，所以要除以 100
    by_year = (df.groupby("year")[col].mean() / 100.0).to_dict()
    spot = (df.groupby("year")[col].last() / 100.0).to_dict()
    now = _dt.datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO hk_fx_rate (year, cny_per_hkd, spot_year_end, source, updated_at)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(year) DO UPDATE SET cny_per_hkd=excluded.cny_per_hkd,"
        " spot_year_end=excluded.spot_year_end,"
        " source=excluded.source, updated_at=excluded.updated_at",
        [(y, v, spot.get(y), f"中国银行牌价·{col}年均+年末即期", now)
         for y, v in by_year.items()])
    conn.commit()
    return len(by_year)


def cmd_fetch_valuation(args) -> int:
    conn = C.get_conn(args.db)
    code = args.code
    if detect_market(conn, code) != "hk":
        print("本命令目前只服务港股；A 股的估值缓存由 security-analysis 采集。",
              file=sys.stderr)
        return 1
    try:
        import akshare as ak
    except ImportError:
        print("需要 akshare：pip install akshare", file=sys.stderr)
        return 1
    print(C.sep())
    print(f"港股估值取数 · {code}")
    print(C.sep())
    try:
        _ensure_valuation_schema(conn)
    except sqlite3.OperationalError as e:
        print(f"{e}。请先运行 security-analysis 的 collect_financial_data.py。",
              file=sys.stderr)
        return 1
    n_fx = _fetch_hk_fx(conn, args.refresh)
    n_spot = conn.execute(
        "SELECT COUNT(*) FROM hk_fx_rate WHERE spot_year_end IS NOT NULL").fetchone()[0]
    print(f"  港元汇率：{n_fx} 个年份（中国银行牌价），其中年末即期价 {n_spot} 个")
    if not args.refresh:
        fresh = _valuation_cache_fresh(conn, code)
        if fresh:
            got = conn.execute(
                "SELECT COUNT(*), MIN(date), MAX(date) FROM stock_valuation_history"
                " WHERE stock_code=?", (code,)).fetchone()
            print(f"  估值缓存仍在有效期内（{VALUATION_CACHE_DAYS} 天，更新于 {fresh}），"
                  f"跳过重取")
            print(f"  库内 {got[0]} 行（{got[1]} ~ {got[2]}）；--refresh 可强制重取")
            print(C.line())
            print("  汇率已按上表刷新（汇率单独判缓存，不受估值 TTL 影响）。")
            return 0
    try:
        series = {}
        for key, ind in (("pe_ttm", "市盈率(TTM)"), ("pb", "市净率"), ("market_cap", "总市值")):
            df = ak.stock_hk_valuation_baidu(symbol=code, indicator=ind, period="全部")
            if df is None or df.empty:
                print(f"  ✗ {ind} 无数据", file=sys.stderr)
                return 1
            s = df.assign(d=df["date"].astype(str).str[:10]).set_index("d")["value"]
            series[key] = {d: _num(v) for d, v in s.items()}
    except Exception as e:  # 网络/接口变动都要给出可读原因，不能吞掉
        print(f"取数失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    dates = sorted(set(series["pe_ttm"]) | set(series["pb"]) | set(series["market_cap"]))
    import datetime as _dt
    now = _dt.datetime.now().isoformat(timespec="seconds")
    rows = []
    for d in dates:
        y = _to_int(d)
        if args.start_year and y and y < args.start_year:
            continue
        mc = series["market_cap"].get(d)
        rows.append((code, d, series["pe_ttm"].get(d), series["pb"].get(d),
                     mc * 1e8 if mc is not None else None, now))  # 百度给的是亿港元
    conn.executemany(
        "INSERT INTO stock_valuation_history"
        " (stock_code, date, pe_ttm, pb, market_cap, updated_at) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(stock_code, date) DO UPDATE SET"
        " pe_ttm=COALESCE(excluded.pe_ttm, pe_ttm),"
        " pb=COALESCE(excluded.pb, pb),"
        " market_cap=COALESCE(excluded.market_cap, market_cap),"
        " updated_at=excluded.updated_at", rows)
    conn.commit()
    got = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM stock_valuation_history"
                       " WHERE stock_code=?", (code,)).fetchone()
    print(f"  PE/PB/总市值：写入 {len(rows)} 行，库内累计 {got[0]} 行（{got[1]} ~ {got[2]}）")
    print(C.line())
    print("  注意：港股 PE/PB 由百度源提供，其口径为混合币种（价格港元、盈利人民币），")
    print("  用于分位比较是自洽的，但**不可用它反推绝对市值**——所以指标⑦用的是")
    print("  直接观测的总市值×**年末即期汇率**（年末时点存量配年末即期价；年均价只用来")
    print("  折年度流量）。这条差异会在报告里显式打印。")
    return 0


def cmd_batch(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1
    codes = [ln.split("#")[0].strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    codes = [c for c in codes if c]
    if not codes:
        print("文件里没有股票代码。", file=sys.stderr)
        return 1
    conn = C.get_conn(args.db)
    print(C.sep())
    print(f"批量筛查 · {len(codes)} 只")
    print(C.sep())
    print(f"  {C.pad('代码', 8)} {C.pad('名称', 12)} {C.pad('通过', 5)} "
          f"{C.pad('待查', 5)} {C.pad('否决', 5)} {C.pad('数据不足', 7)} 结论")
    print("  " + "-" * 66)
    fails = 0
    for code in codes:
        try:
            rep = build_report(conn, code, args.years)
        except Exception as e:  # 单只失败不该中断整批
            print(f"  {C.pad(code, 8)} <异常: {e}>")
            fails += 1
            continue
        vs = [i.verdict for i in rep["indicators"]]
        print(f"  {C.pad(code, 8)} {C.pad(rep['name'][:10], 12)} "
              f"{C.pad(vs.count(PASS), 5)} {C.pad(vs.count(QUESTION), 5)} "
              f"{C.pad(vs.count(REJECT), 5)} {C.pad(vs.count(NA), 7)} {rep['verdict']}")
    print(C.line())
    print("批量结果只用于排序注意力，不构成结论——每只都要单独看数据体检。")
    return 1 if fails else 0


# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(
        description="巴菲特视角量化筛查（读 financial_data.db）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""判定记号:
  PASS      达到巴菲特式标准
  QUESTION  中间地带，需人工追问
  REJECT    不符合，且不因价格改变
  NA        数据不足。NA 不是 PASS —— 样本不够时绝不给通过

示例:
  buffett_analysis.py data-check --code 600519
  buffett_analysis.py screen --code 600519
  buffett_analysis.py screen --code 600519 --json
  buffett_analysis.py retention --code 600519 --start-year 2016
  buffett_analysis.py batch --file stocks.txt

港股:
  代码给 5 位数字（如 00322）即自动按港股取数，无需 --market。
  港股估值历史需先取一次：buffett_analysis.py fetch-valuation --code 00322
""")
    p.add_argument("--db", help="financial_data.db 路径（默认自动定位）")
    sub = p.add_subparsers(dest="cmd")

    def common(sp, need_code=True):
        if need_code:
            sp.add_argument("--code", required=True)
        sp.add_argument("--years", type=int, default=10)
        return sp

    common(sub.add_parser("data-check", help="数据覆盖体检"),
           ).set_defaults(func=cmd_data_check)
    s = common(sub.add_parser("screen", help="八项读数筛查"))
    s.add_argument("--json", action="store_true")
    s.add_argument("--strict", action="store_true", help="未通过初筛则 exit 1")
    s.set_defaults(func=cmd_screen)
    common(sub.add_parser("owner-earnings", help="只看股东盈余"),
           ).set_defaults(func=cmd_owner_earnings)
    sg = common(sub.add_parser("segment", help="分部收入/业绩走势（能力圈与定价权证据）"))
    sg.add_argument("--metric", help="只看向该口径：external_revenue/segment_revenue/"
                                     "segment_result/depreciation/capex")
    sg.set_defaults(func=cmd_segment)
    r = common(sub.add_parser("retention", help="一美元留存测试"))
    r.add_argument("--start-year", dest="start_year", type=int)
    r.set_defaults(func=cmd_retention)
    common(sub.add_parser("valuation", help="只看估值分位"),
           ).set_defaults(func=cmd_valuation)
    fv = common(sub.add_parser("fetch-valuation", help="拉取港股 PE/PB/总市值与汇率并落库"))
    fv.add_argument("--start-year", dest="start_year", type=int)
    fv.add_argument("--refresh", action="store_true",
                    help=f"忽略 {VALUATION_CACHE_DAYS} 天缓存，强制重取估值与汇率")
    fv.set_defaults(func=cmd_fetch_valuation)
    b = sub.add_parser("batch", help="批量筛查")
    b.add_argument("--file", required=True)
    b.add_argument("--years", type=int, default=10)
    b.set_defaults(func=cmd_batch)

    args = p.parse_args()
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    try:
        return args.func(args)
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            print("financial_data.db 缺表。请先运行 security-analysis 的"
                  "collect_financial_data.py 采集数据。", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
