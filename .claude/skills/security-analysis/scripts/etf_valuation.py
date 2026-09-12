#!/usr/bin/env python3
"""
etf_valuation.py - ETF估值(PE/PB/分红率)查询 + 历史分红数据采集 CLI 工具（独立版）

所有逻辑内置于本脚本，无需依赖外部模块（仅需 akshare + pandas + requests）。

子命令:
    valuation  - 查询ETF成份股占比并计算加权PE/PB/分红率
    dividend   - 采集所有A股历史分红汇总数据存入数据库
    query      - 查询数据库中的分红数据
    tables     - 列出数据库中所有表及行数

用法:
    python etf_valuation.py valuation --fund-code 510300
    python etf_valuation.py valuation --fund-code 159008 --market sz
    python etf_valuation.py valuation --fund-code 510300 --save
    python etf_valuation.py dividend
    python etf_valuation.py dividend --db-dir /path/to/db
    python etf_valuation.py query --code 600519
    python etf_valuation.py query --top 50
    python etf_valuation.py tables

数据库默认路径: 通过 FINANCIAL_DATA_DIR 环境变量指定，或项目根目录。
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# 数据库配置
# ---------------------------------------------------------------------------

def _resolve_data_dir() -> str:
    """定位数据目录：FINANCIAL_DATA_DIR 环境变量 > 当前工作目录 > 向上查找含 financial_data.db 的目录。"""
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

_FINANCIAL_DATA_DIR = _resolve_data_dir()
DB_PATH = str(Path(_FINANCIAL_DATA_DIR) / "financial_data.db")

_DC_PAGE_SIZE = 100
_DC_INTERVAL = 1.0


# ---------------------------------------------------------------------------
# 交易日解析
# ---------------------------------------------------------------------------

# 各市场用于判定交易日的指数（腾讯行情代码）。A股沪深两市共用上证指数日历。
_TD_INDEX_SYMBOL = {"sh": "sh000001", "sz": "sh000001", "hk": "hkHSI"}
_TD_CALENDAR_DAYS = 60          # 单次拉取的交易日条数（用于向前回退）
_TD_CACHE = {}                  # market -> [YYYY-MM-DD, ...] 升序


def _fetch_trading_dates(market, count=_TD_CALENDAR_DAYS):
    """拉取指定市场最近 count 个交易日（升序）。失败返回空列表。"""
    sym = _TD_INDEX_SYMBOL.get(market)
    if not sym:
        return []
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={sym},day,,,{count},qfq")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            node = (resp.json().get("data") or {}).get(sym) or {}
            bars = node.get("day") or node.get("qfqday") or []
            dates = sorted({str(b[0])[:10] for b in bars if b and b[0]})
            if dates:
                return dates
        except Exception:
            if attempt < 2:
                time.sleep(2)
    return []


def _market_calendar(market):
    """取指定市场的交易日历（进程内缓存），接口不可用时返回空列表。"""
    if market not in _TD_CACHE:
        _TD_CACHE[market] = _fetch_trading_dates(market)
    return _TD_CACHE[market]


def _prev_weekday(date_str):
    """向前退一个工作日（周一~周五）。仅在交易日历不可用时兜底，不含节假日。"""
    d = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _norm_date(date_str):
    """把 YYYY-MM-DD / YYYYMMDD / YYYY/MM/DD 统一为 YYYY-MM-DD。"""
    s = str(date_str).strip()[:10].replace("/", "-")
    if "-" not in s and len(s) == 8:
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _parse_ref_date(ref_date):
    """把日期字符串解析为 datetime；None 时返回当前自然日。"""
    if ref_date:
        try:
            return datetime.strptime(_norm_date(ref_date), "%Y-%m-%d")
        except ValueError:
            pass
    return datetime.now()


def last_trading_day(market, ref_date=None):
    """返回指定市场在 ref_date（含）当日或之前的最近一个交易日。

    自然日若本身不是交易日（周末/节假日），自动回退到该市场的上一交易日。
    market: 'sh' / 'sz' -> A股日历；'hk' -> 港股日历。两市场日历相互独立，
    因此A股与港股交易日不一致时，各自取各自市场的上一交易日。
    日历接口不可用时按周一~周五兜底。
    """
    ref = _norm_date(ref_date) if ref_date else datetime.now().strftime("%Y-%m-%d")
    cal = _market_calendar(market)
    if cal:
        eligible = [d for d in cal if d <= ref]
        if eligible:
            return eligible[-1]
    return _prev_weekday(ref)


# ---------------------------------------------------------------------------
# 数据库工具
# ---------------------------------------------------------------------------

def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    """创建并返回 SQLite 连接（WAL 模式）"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_valuation (
            stock_code     TEXT NOT NULL,
            date           TEXT NOT NULL,
            pe             REAL,
            pb             REAL,
            dividend_yield REAL,
            price          REAL,
            updated_at     TEXT NOT NULL,
            PRIMARY KEY (stock_code, date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hk_yield_cache (
            stock_code     TEXT NOT NULL,
            date           TEXT NOT NULL,
            price          REAL,
            dividend_yield REAL,
            buyback_yield  REAL,
            total_yield    REAL,
            note           TEXT,
            updated_at     TEXT NOT NULL,
            PRIMARY KEY (stock_code, date)
        )
    """)
    # 老库补 note 列（CREATE TABLE IF NOT EXISTS 不会给已存在的表加列）
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hk_yield_cache)")}
    if "note" not in cols:
        conn.execute("ALTER TABLE hk_yield_cache ADD COLUMN note TEXT")
    conn.commit()
    return conn


def _read_valuation_cache(stock_codes, date=None, conn=None):
    """
    从本地缓存读取估值数据（只返回股息率有效的记录）

    返回:
        dict: {code: {"pe": float|None, "pb": float|None, "dy": float|None, "price": float|None}}
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    placeholders = ",".join("?" * len(stock_codes))
    sql = f"""
        SELECT stock_code, pe, pb, dividend_yield, price
        FROM stock_valuation
        WHERE stock_code IN ({placeholders}) AND date = ?
              AND dividend_yield IS NOT NULL
    """
    rows = conn.execute(sql, list(stock_codes) + [date]).fetchall()
    if own_conn:
        conn.close()
    return {r[0]: {"pe": r[1], "pb": r[2], "dy": r[3], "price": r[4]} for r in rows}


def _save_valuation_cache(data_map, date=None, conn=None):
    """批量写入估值数据到本地缓存"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    conn.executemany(
        """INSERT OR REPLACE INTO stock_valuation
           (stock_code, date, pe, pb, dividend_yield, price, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (code, date, v.get("pe"), v.get("pb"), v.get("dy"), v.get("price"), now)
            for code, v in data_map.items()
        ],
    )
    conn.commit()
    if own_conn:
        conn.close()
    print(f"[缓存] 已写入 {len(data_map)} 只股票估值数据 (date={date})")


def _find_raw_code(data_map, code_5):
    """在 data_map 中查找与 5 位标准化代码对应的原始 key"""
    for c in data_map.keys():
        if str(c).strip().zfill(5) == str(code_5).zfill(5):
            return c
    return None


def _read_hk_yield_cache(stock_codes, date=None, conn=None):
    """从本地缓存读取港股股东回报数据（股息+回购+合计）"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    placeholders = ",".join("?" * len(stock_codes))
    sql = f"""
        SELECT stock_code, price, dividend_yield, buyback_yield, total_yield, note
        FROM hk_yield_cache
        WHERE stock_code IN ({placeholders}) AND date = ?
    """
    rows = conn.execute(sql, list(stock_codes) + [date]).fetchall()
    if own_conn:
        conn.close()
    return {
        r[0]: {"price": r[1], "dy": r[2], "by": r[3], "total": r[4], "note": r[5]}
        for r in rows if r[2] is not None or r[3] is not None
    }


def _save_hk_yield_cache(data_map, date=None, conn=None):
    """批量写入港股股东回报数据到本地缓存（code 用 5 位零填充）"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    rows = []
    for code, v in data_map.items():
        code_5 = str(code).strip().zfill(5)
        dy = v.get("dy")
        by = v.get("by")
        total = v.get("total")
        if dy is None and by is None:
            continue
        rows.append((code_5, date, v.get("price"), dy, by, total,
                     v.get("note"), now))
    if rows:
        conn.executemany(
            """INSERT OR REPLACE INTO hk_yield_cache
               (stock_code, date, price, dividend_yield, buyback_yield, total_yield,
                note, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        print(f"[港股缓存] 已写入 {len(rows)} 只港股股东回报数据 (date={date})")
    if own_conn:
        conn.close()


# ---------------------------------------------------------------------------
# 上交所(SSE) ETF 申赎清单
# ---------------------------------------------------------------------------

def query_sse_etf_list(etf_class="", fund_code="", keyword="", page_size=100):
    """查询上交所ETF申赎清单列表"""
    url = "https://query.sse.com.cn/commonQuery.do"
    timestamp = int(time.time() * 1000)
    params = {
        "isPagination": "true", "pageHelp.pageSize": str(page_size),
        "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
        "sqlId": "COMMON_SSE_PL_ETFGGSGSHQD_L", "ETF_CLASS": etf_class,
        "type": "inParams", "FUND_CODE": fund_code, "KEY_WORDS": keyword,
        "_": str(timestamp),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.sse.com.cn/disclosure/fund/etflist/",
        "Host": "query.sse.com.cn",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        text = resp.text
    except requests.RequestException as e:
        print(f"[上交所] 请求失败: {e}")
        return pd.DataFrame()

    json_match = re.search(r'jsonpCallback\d*\((.*)\)', text, re.DOTALL)
    json_str = json_match.group(1) if json_match else text
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return pd.DataFrame()

    result_list = data.get("result", [])
    if not result_list:
        return pd.DataFrame()

    rows = [
        {
            "基金代码": item.get("FUNDID2", ""),
            "基金名称": item.get("ETF_FULLNAME", ""),
            "管理公司": item.get("FUND_COMP_NAME", ""),
            "基金份额净值": item.get("NAV", ""),
            "交易日期": item.get("TRADING_DAY", ""),
            "市场": "sh",
        }
        for item in result_list
    ]
    return pd.DataFrame(rows)


def query_sse_etf_detail(fund_code):
    """查询上交所单只ETF申赎清单详细信息（基本信息 + 成份股明细）"""
    timestamp = int(time.time() * 1000)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml?fundid={fund_code}",
        "Host": "query.sse.com.cn",
    }
    base_url = "https://query.sse.com.cn/commonQuery.do"

    # 基本信息
    basic_params = {
        "isPagination": "false", "FUNDID2": fund_code,
        "sqlId": "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C",
        "_": str(timestamp),
    }
    basic_info = None
    try:
        resp = requests.get(base_url, params=basic_params, headers=headers, timeout=15)
        text = resp.text
        json_match = re.search(r'jsonpCallback\d*\((.*)\)', text, re.DOTALL)
        json_str = json_match.group(1) if json_match else text
        data = json.loads(json_str)
        result = data.get("result", [])
        if result:
            item = result[0]
            basic_info = {
                "基金名称": item.get("FUND_NAME", ""),
                "管理公司": item.get("FUND_COMP_NAME", ""),
                "基金代码": item.get("TRADE_CODE", ""),
                "交易日期": item.get("TRADING_DAY", ""),
                "上一交易日": item.get("PRE_TRADING_DAY", ""),
                "基金份额净值": item.get("NAV", ""),
                "上一交易日现金差额": item.get("PRE_CASH_COMPONENT", ""),
                "预估现金差额": item.get("ESTIMATED_CASH_COMPONENT", ""),
                "最小申赎单位净值": item.get("NAVPERCU", ""),
                "现金替代比例上限": item.get("MAX_CASH_RATIO", ""),
                "申赎单位": item.get("CREATION_REDEMPTION_UNIT", ""),
                "申购上限": item.get("CREATION_LIMIT", ""),
                "赎回上限": item.get("REDEMPTION_LIMIT", ""),
                "是否发布IOPV": item.get("PUBLISH_IOPV", ""),
                "成份股数量": item.get("RECORD_NUM", ""),
                "申赎状态": item.get("CREATION_REDEMPTION", ""),
            }
            basic_info = {k: v for k, v in basic_info.items() if v}
    except Exception as e:
        print(f"[上交所] 获取基本信息失败: {e}")

    # 成份股明细
    component_params = {
        "isPagination": "false", "FUNDID2": fund_code,
        "sqlId": "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_COMPONENT_C",
        "_": str(timestamp + 1),
    }
    stock_df = pd.DataFrame()
    try:
        resp = requests.get(base_url, params=component_params, headers=headers, timeout=15)
        text = resp.text
        json_match = re.search(r'jsonpCallback\d*\((.*)\)', text, re.DOTALL)
        json_str = json_match.group(1) if json_match else text
        data = json.loads(json_str)
        stock_list = data.get("result", [])
        if stock_list:
            rows = [
                {
                    "证券代码": s.get("INSTRUMENT_ID", ""),
                    "证券名称": s.get("INSTRUMENT_NAME", ""),
                    "数量": s.get("QUANTITY", ""),
                    "现金替代标志": s.get("SUBSTITUTION_FLAG", ""),
                    "申购现金替代溢价比例": s.get("CREATION_PREMIUM_RATE", ""),
                    "赎回现金替代折价比例": s.get("REDEMPTION_DISCOUNT_RATE", ""),
                    "替代金额": s.get("SUBSTITUTION_CASH_AMOUNT", ""),
                    "成份证券标识": s.get("UNDERLYION_SECURITY_ID", ""),
                }
                for s in stock_list
            ]
            stock_df = pd.DataFrame(rows)
    except Exception as e:
        print(f"[上交所] 获取成份股明细失败: {e}")

    return basic_info, stock_df


# ---------------------------------------------------------------------------
# 深交所(SZSE) ETF 申赎清单
# ---------------------------------------------------------------------------

SZSE_PCF_BASE_URL = "https://reportdocs.static.szse.cn/files/text/etf/"


def query_szse_etf_list(fund_code="", date=None):
    """查询深交所ETF申赎清单列表（默认查A股最近交易日）"""
    if date is None:
        date = last_trading_day("sh")
    else:
        date = _norm_date(date)
    url = "https://www.szse.cn/api/report/ShowReport/data"
    params = {
        "SHOWTYPE": "JSON", "CATALOGID": "sgshqd", "loading": "first",
        "TABKEY": "tab1", "txtJCorDH": fund_code,
        "txtStart": date, "txtEnd": date,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.szse.cn/disclosure/fund/currency/index.html",
        "Host": "www.szse.cn",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
    except Exception as e:
        print(f"[深交所] 请求失败: {e}")
        return pd.DataFrame()

    if not data or not isinstance(data, list):
        return pd.DataFrame()

    rows = []
    for tab in data:
        tab_data = tab.get("data", [])
        for item in tab_data:
            jjdm_html = item.get("jjdm", "")
            code_match = re.search(r'ETF(\d{6})', jjdm_html)
            fund_code_raw = code_match.group(1) if code_match else ""
            name_match = re.search(r'>([^<]+)申购赎回清单', jjdm_html)
            fund_name_raw = name_match.group(1).strip() if name_match else ""
            rows.append({
                "基金代码": fund_code_raw, "基金名称": fund_name_raw,
                "交易日期": date, "市场": "sz",
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _szse_pcf_candidates(date=None, max_fallback=5):
    """生成深交所PCF的候选日期（降序，YYYYMMDD）。

    未指定 date 时以 A股最近交易日为起点；非交易日/未发布时沿交易日历向前回退。
    交易日历不可用时按工作日回退。
    """
    anchor = _norm_date(date) if date else last_trading_day("sh")
    cal = _market_calendar("sh")
    if cal:
        prior = [d for d in cal if d <= anchor]
        if prior:
            return [d.replace("-", "") for d in reversed(prior[-max_fallback:])]
    out, cur = [], anchor
    while len(out) < max_fallback:
        out.append(cur.replace("-", ""))
        cur = _prev_weekday(cur)
    return out


def download_szse_pcf(fund_code, date=None):
    """下载并解析深交所ETF申赎清单PCF文本文件。

    深交所PCF按「交易日+日期」命名，非交易日无文件（HTTP 404）。因此失败时
    沿交易日历向前回退，最多尝试 5 个交易日；显式传入 date 时同样带此回退。
    """
    candidates = _szse_pcf_candidates(date)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.szse.cn/disclosure/fund/currency/index.html",
    }
    for pcf_date in candidates:
        pcf_url = f"{SZSE_PCF_BASE_URL}ETF{fund_code}{pcf_date}.txt"
        try:
            resp = requests.get(pcf_url, headers=headers, timeout=15)
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="replace")
        except requests.RequestException:
            continue
        info, stock_df = _parse_szse_pcf_text(text)
        if stock_df.empty:
            continue
        if pcf_date != candidates[0]:
            print(f"[深交所] {candidates[0]} 无PCF文件，已回退至 {pcf_date}")
        return info, stock_df
    print(f"[深交所] 下载PCF文件失败（已尝试 {'、'.join(candidates)}，均无有效文件）")
    return None, pd.DataFrame()


def _parse_szse_pcf_text(text):
    """解析深交所PCF文本文件"""
    lines = text.splitlines()
    basic_info, stock_rows = {}, []
    section = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("-"):
            if section == 0 and line.startswith("-"): section = 1
            elif section == 1 and line.startswith("-"): section = 2
            elif section == 2 and line.startswith("-"): section = 3
            elif section >= 3 and line.startswith("-"): section = 5
            continue
        kv_match = re.match(r'^(.+?)：\s*(.+?)\s*$', line)
        if kv_match and section < 4:
            basic_info[kv_match.group(1).strip()] = kv_match.group(2).strip()
            continue
        stock_match = re.match(r'^(\d{3,6})\s+(.+)$', line)
        if stock_match:
            code = stock_match.group(1)
            fields = stock_match.group(2).split()
            if len(fields) >= 3:
                row = {"证券代码": code}
                if len(fields) >= 8:
                    # A股格式（8字段）: 含申购替代金额 + 赎回替代金额 + 市场
                    field_names = ["证券名称", "数量", "现金替代标志",
                                   "申购现金替代保证金率", "赎回现金替代保证金率",
                                   "申购现金替代金额", "赎回现金替代金额", "市场"]
                elif len(fields) >= 7:
                    # 跨境ETF格式（7字段）: 无赎回替代金额，申购替代金额后直接是市场
                    field_names = ["证券名称", "数量", "现金替代标志",
                                   "申购现金替代保证金率", "赎回现金替代保证金率",
                                   "申购现金替代金额", "市场"]
                else:
                    field_names = ["证券名称", "数量", "现金替代标志",
                                   "申购现金替代保证金率", "赎回现金替代保证金率",
                                   "申购现金替代金额", "赎回现金替代金额", "市场",
                                   "映射代码", "是否实行约定"]
                for i, fname in enumerate(field_names):
                    row[fname] = fields[i] if i < len(fields) else ""
                stock_rows.append(row)

    info_summary = {}
    key_mapping = {
        "基金名称": ["基金名称"], "管理公司": ["管理公司"], "基金代码": ["基金代码"],
        "目标指数": ["目标指数"], "交易日期": ["申赎清单日期"],
        "现金差额": ["现金差额"], "申赎单位净值": ["赎回单位资产", "最小申赎单位资产"],
        "基金份额净值": ["基金份额净值"], "预估现金差额": ["预估现金差额"],
    }
    for out_key, src_keys in key_mapping.items():
        for src_key in src_keys:
            for bk, bv in basic_info.items():
                if src_key in bk:
                    info_summary[out_key] = bv
                    break
            if out_key in info_summary:
                break

    stock_df = pd.DataFrame(stock_rows) if stock_rows else pd.DataFrame()
    return info_summary, stock_df


# ---------------------------------------------------------------------------
# ETF 统一查询接口
# ---------------------------------------------------------------------------

def _guess_market(fund_code):
    """根据基金代码猜测所属市场"""
    code = fund_code.strip()
    if code.startswith(("51", "56", "58", "50")):
        return "sh"
    if code.startswith(("15", "16")):
        return "sz"
    return ""


def query_etf_detail(fund_code, market=None, date=None):
    """查询单只ETF申赎清单详细信息"""
    if market is None:
        market = _guess_market(fund_code)
    if market == "sh":
        return query_sse_etf_detail(fund_code)
    elif market == "sz":
        return download_szse_pcf(fund_code, date=date)
    return None, pd.DataFrame()


# ---------------------------------------------------------------------------
# 行情数据：腾讯财经（优先） + 东方财富（备用）
# ---------------------------------------------------------------------------

def _code_to_tencent_symbol(code):
    """转为腾讯行情symbol格式。返回 (symbol, is_hk)
    
    A股代码固定6位（sh/sz前缀），港股代码3-5位（hk前缀+5位零填充）。
    仅按长度判断即可区分：<=5位一定是港股。
    """
    code = str(code).strip()
    if len(code) <= 5:
        return f"hk{code.zfill(5)}", True
    return (f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"), False


def _get_prices_tencent(stock_codes):
    """腾讯财经批量获取价格/PE/PB（支持A股+港股）"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64) AppleWebKit/537.36"}
    data_map = {}
    batch_size = 60
    codes = list(stock_codes)
    # 构建 标准化代码 -> 原始代码 的映射（腾讯返回5位港股代码如00700）
    norm_to_orig = {}
    all_symbols = []
    for c in codes:
        sym, _ = _code_to_tencent_symbol(c)
        all_symbols.append(sym)
        norm_code = sym[2:]  # 去掉 sh/sz/hk 前缀
        norm_to_orig[norm_code] = str(c).strip()
    for i in range(0, len(all_symbols), batch_size):
        batch_syms = all_symbols[i:i + batch_size]
        symbols = ",".join(batch_syms)
        url = f"http://qt.gtimg.cn/q={symbols}"
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                text = resp.content.decode("gbk", errors="replace")
                for line in text.strip().splitlines():
                    line = line.strip().rstrip(";")
                    if '="' not in line:
                        continue
                    parts = line.split('"')[1].split("~")
                    if len(parts) >= 47:
                        raw_code = parts[2].strip()
                        is_hk = line.startswith("v_hk")
                        info = {"price": None, "pe": None, "pb": None, "shares": None, "mktcap": None}
                        try:
                            p = float(parts[3])
                            if p > 0: info["price"] = p
                        except (ValueError, IndexError): pass
                        try:
                            pe = float(parts[39])
                            if pe != 0: info["pe"] = pe  # 允许负PE（亏损股）
                        except (ValueError, IndexError): pass
                        try:
                            # A股PB在[46]；港股[46]为英文ticker、[47]为TTM股息率、[58]才是市净率
                            pb_idx = 58 if is_hk and len(parts) > 58 else 46
                            pb = float(parts[pb_idx])
                            if pb > 0: info["pb"] = pb
                        except (ValueError, IndexError): pass
                        try:
                            # 港股总市值[45](亿HKD)与总股本[69]，用于计算回购收益率
                            if is_hk and len(parts) > 69:
                                mcap_yi = float(parts[45])
                                if mcap_yi > 0:
                                    info["mktcap"] = mcap_yi * 1e8  # 转HKD
                                shares = float(parts[69])
                                if shares > 0:
                                    info["shares"] = shares
                        except (ValueError, IndexError): pass
                        if info["price"] is not None:
                            orig = norm_to_orig.get(raw_code, raw_code)
                            data_map[orig] = info
                break
            except Exception as e:
                print(f"  [腾讯] 获取失败(第{attempt+1}次): {e}")
                if attempt < 2: time.sleep(3)
        print(f"  [腾讯] 批次 {i // batch_size + 1}/{(len(codes) + batch_size - 1) // batch_size} 完成")
        time.sleep(0.5)
    print(f"[腾讯] 成功获取 {len(data_map)}/{len(codes)} 只股票行情(含PE/PB)")
    return data_map


def _get_prices_eastmoney(stock_codes):
    """东方财富接口获取价格（备用）"""
    url = "http://82.push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f12,f2",
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    price_map = {}
    target_codes = set(str(c).strip() for c in stock_codes)
    page, max_pages = 1, 80
    session = requests.Session()
    session.headers.update(headers)
    while page <= max_pages:
        params["pn"] = str(page)
        data = None
        for attempt in range(3):
            try:
                resp = session.get(url, params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt < 2: time.sleep(5)
        if data is None: break
        items = data.get("data", {}).get("diff", [])
        if not items: break
        for item in items:
            code = str(item.get("f12", ""))
            price = item.get("f2")
            if code in target_codes and price is not None and price != "-":
                price_map[code] = float(price)
        if len(price_map) >= len(target_codes) or len(items) < int(params["pz"]): break
        page += 1
        time.sleep(1.5)
    session.close()
    print(f"[东方财富] 成功获取 {len(price_map)}/{len(target_codes)} 只目标股票价格")
    return price_map


# ---------------------------------------------------------------------------
# 东方财富 datacenter-web 批量获取TTM股息率
# ---------------------------------------------------------------------------

def _get_dividend_yield_datacenter(stock_codes, existing_map, ref_date=None):
    """通过东方财富 datacenter-web 接口获取分红记录，计算TTM股息率

    除净日须落在 [ref_date-365, ref_date] 闭区间内：已宣派但尚未除净的分红不计入。
    ref_date 默认为A股最近交易日。
    """
    target_codes = set(str(c).strip() for c in stock_codes)
    target_codes -= {c for c, v in existing_map.items() if v.get("dy") is not None and v.get("dy") >= 0}
    if not target_codes:
        print("[datacenter] 股息率缓存完整，跳过网络请求")
        return {}

    print(f"[datacenter] 需获取 {len(target_codes)} 只股票TTM股息率...")
    code_list = list(target_codes)
    in_vals = ",".join(f'"{c}"' for c in code_list)
    code_filter = f"(SECURITY_CODE in ({in_vals}))"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    session = requests.Session()
    session.headers.update(headers)
    ref = _parse_ref_date(ref_date)
    dividend_map = defaultdict(float)
    page, max_pages = 1, 20

    while page <= max_pages:
        url = (
            "https://datacenter-web.eastmoney.com/api/data/v1/get?"
            f"sortColumns=EX_DIVIDEND_DAYS&sortTypes=1"
            f"&pageSize={_DC_PAGE_SIZE}&pageNumber={page}"
            "&reportName=RPT_SHAREBONUS_DET"
            "&columns=SECURITY_CODE,PRETAX_BONUS_RMB,EX_DIVIDEND_DATE,ASSIGN_PROGRESS"
            f"&filter={code_filter}"
        )
        data = None
        for attempt in range(3):
            try:
                resp = session.get(url, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt < 2: time.sleep(3)
        if data is None: break
        items = (data.get("result") or {}).get("data") or []
        if not items: break
        stop_paging = False
        for item in items:
            code = str(item.get("SECURITY_CODE", "")).strip()
            if code not in target_codes: continue
            ex_date_str = item.get("EX_DIVIDEND_DATE")
            bonus = item.get("PRETAX_BONUS_RMB")
            progress = item.get("ASSIGN_PROGRESS")
            if not ex_date_str or progress != "实施分配" or bonus is None: continue
            try:
                ex_date = datetime.strptime(ex_date_str[:10], "%Y-%m-%d")
            except (ValueError, TypeError): continue
            days = (ref - ex_date).days
            if days > 365:
                # 记录按除净日倒序返回，越界后其后记录只会更旧
                stop_paging = True
            elif days >= 0:
                dividend_map[code] += float(bonus)
            # days < 0: 已宣派未除净，跳过；不可终止分页（后续记录仍可能有效）
        total_count = (data.get("result") or {}).get("count", 0)
        if stop_paging or len(items) < _DC_PAGE_SIZE or page * _DC_PAGE_SIZE >= total_count: break
        page += 1
        time.sleep(_DC_INTERVAL)
    session.close()

    dy_map = {}
    for code, total_per_10 in dividend_map.items():
        div_per_share = total_per_10 / 10.0
        price = existing_map.get(code, {}).get("price")
        if price and price > 0:
            dy_map[code] = {"dy": round(div_per_share / price * 100, 4)}
        else:
            dy_map[code] = {"dy": None}
    print(f"[datacenter] 成功计算 {len(dy_map)}/{len(target_codes)} 只TTM股息率")
    return dy_map


# ---------------------------------------------------------------------------
# 东方财富 EM 接口获取港股分红，计算TTM股息率
# ---------------------------------------------------------------------------

# 汇率近似值（USD->HKD, CNY->HKD），当EM接口提供港币等价值时优先使用
_FX_USD_HKD = 7.80
_FX_CNY_HKD = 1.10


def _parse_hk_dividend_hkd(fhfa):
    """从东方财富港股分红方案文本中提取每股分红金额（港币）。

    支持格式:
      - "每股派港币X元"
      - "每股派美元X元(相当于港币Y元)"  → 优先取港币等价值
      - "每股派人民币X元(相当于港币Y元)" → 优先取港币等价值
      - "未派发或宣派股息" / "不分红" → 返回 None
    """
    if not fhfa or "未派发" in fhfa or "不分红" in fhfa or "不派发" in fhfa:
        return None
    # 优先取港币等价值
    m = re.search(r"相当于港币\s*([\d.]+)", fhfa)
    if m:
        return float(m.group(1))
    m = re.search(r"每股派港币\s*([\d.]+)", fhfa)
    if m:
        return float(m.group(1))
    m = re.search(r"每股派\s*([\d.]+)\s*港元", fhfa)
    if m:
        return float(m.group(1))
    # 人民币分红（无港币等价值时近似转换）
    m = re.search(r"每股派人民币\s*([\d.]+)", fhfa)
    if m:
        return float(m.group(1)) * _FX_CNY_HKD
    # 美元分红（无港币等价值时近似转换）
    m = re.search(r"每股派美元\s*([\d.]+)", fhfa)
    if m:
        return float(m.group(1)) * _FX_USD_HKD
    return None


_HK_DIV_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
# 主源与备用源都取不到该股分红记录时的说明（区别于「确认窗口内无分红」）
_NOTE_NO_DIVIDEND_DATA = "两源均无分红记录，股息率按0计（或存在低估）"


def _sum_hk_ttm_dividend(records, ref):
    """汇总 [ref-365, ref] 窗口内的每股港币股息（TTM）。

    两个数据源共用本函数，确保口径完全一致。

    参数:
        records: 可迭代的 (除净日字符串 "YYYY/MM/DD", 分红方案文本)
        ref:     datetime，统计基准日
    口径要点:
      - 除净日须落在 [ref-365, ref] 闭区间内。已宣派但尚未除净（除净日晚于 ref）
        以及超出窗口的记录一律不计。
      - 同一 (除净日, 方案原文) 只计一次。东财两个源都会把同一笔分红重复返回
        （如「年度分配」与「特别分配」两行除净日、公告日、金额全同），
        不去重会导致股息率翻倍。
    """
    seen = set()
    total = 0.0
    for date_str, plan in records:
        key = (str(date_str)[:10], plan)
        if key in seen:
            continue
        try:
            ex_date = datetime.strptime(str(date_str)[:10], "%Y/%m/%d")
        except (ValueError, TypeError):
            continue
        days = (ref - ex_date).days
        if not (0 <= days <= 365):
            continue
        seen.add(key)
        amt = _parse_hk_dividend_hkd(plan)
        if amt:
            total += amt
    return total


def _fetch_hk_dividend_primary(session, code_5):
    """主源：东方财富 EM CoreReading。返回 (是否有记录, [(除净日, 方案)])。

    该接口只返回最近约3条分红记录（可能漏掉窗口内的更早一笔，属已知少算）。
    无 fhpx 字段表示该股在此接口无数据（如次新股），不代表无分红。
    """
    url = (f"https://emweb.securities.eastmoney.com/"
           f"PC_HKF10/CoreReading/PageAjax?code={code_5}")
    fhpx = session.get(url, timeout=15).json().get("fhpx", [])
    if not fhpx:
        return False, []
    return True, [(str(it.get("pxr", ""))[:10], it.get("fhfa", "")) for it in fhpx]


def _fetch_hk_dividend_backup(session, code_5):
    """备用源：东方财富 datacenter RPT_HKF10_MAIN_DIVBASIC。返回 (是否成功, [(除净日, 方案)])。

    仅在主源无数据时调用，且**不与主源结果合并**——两源覆盖同一批分红，
    合并必然重复计数，故只作整体替换。
    口径要点:
      - 必须过滤 IS_BFP="0"（真实分红方案）。IS_BFP="1" 是「未派发或宣派股息」
        占位行，其 EX_DIVIDEND_DATE 为空，在降序排序里排在最前，会把真实分红
        挤出首页分页，导致静默少算。
      - 365天窗口一律在本地过滤，不用接口的日期条件：实测 (EX_DIVIDEND_DATE>=...)
        会连窗口边界内的记录一起滤掉，反而引入少算。
      - 接口对无效代码返回 200 + 空结果，故须校验返回行的代码归属，不能只看状态码。
    """
    params = {
        "reportName": "RPT_HKF10_MAIN_DIVBASIC",
        "columns": "SECURITY_CODE,EX_DIVIDEND_DATE,PLAN_EXPLAIN",
        "filter": f'(SECUCODE="{code_5}.HK")(IS_BFP="0")',
        "pageNumber": 1, "pageSize": 100,
        "sortColumns": "EX_DIVIDEND_DATE", "sortTypes": -1,
        "source": "F10", "client": "PC",
    }
    rows = (session.get(_HK_DIV_DC_URL, params=params, timeout=15).json()
            .get("result") or {}).get("data") or []
    for r in rows:
        if str(r.get("SECURITY_CODE", "")).strip() != code_5:
            return False, []
    return True, [(str(r.get("EX_DIVIDEND_DATE") or "")[:10],
                   r.get("PLAN_EXPLAIN") or "") for r in rows]


def _get_hk_dividend_yield_em(hk_codes, existing_map, ref_date=None):
    """获取港股分红记录，计算TTM股息率。

    主源为东方财富 EM CoreReading；主源无该股记录时（如新上市H股）回退到
    datacenter RPT_HKF10_MAIN_DIVBASIC。**只做回退，不做合并**：主源有数据就
    完全采用主源，避免两源对同一笔分红重复计数。宁可少算，不可多算。

    口径要点：
      - 除净日须落在 [ref_date-365, ref_date] 闭区间内。已宣派但尚未除净的分红
        （除净日在 ref_date 之后）不计入 TTM。
      - 同一 (除净日, 分红方案) 若被接口重复返回，只计一次，避免双计。
      - ref_date 默认为港股最近交易日；返回 dy=None 表示两个源都取不到数据
        （下次重试），dy=0.0 表示确认窗口内无分红（不再重试）。
    """
    target_codes = set(str(c).strip() for c in hk_codes)
    target_codes -= {c for c, v in existing_map.items()
                     if v.get("dy") is not None and v.get("dy") >= 0}
    if not target_codes:
        print("[港股分红] 缓存完整，跳过网络请求")
        return {}

    print(f"[港股分红] 需获取 {len(target_codes)} 只港股TTM股息率...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://emweb.securities.eastmoney.com/",
    }
    session = requests.Session()
    session.headers.update(headers)
    ref = _parse_ref_date(ref_date)
    dividend_map = {}  # code -> TTM HKD dividend per share(float) | None(抓取失败)

    fallback_used = 0
    note_map = {}
    for code in sorted(target_codes):
        code_5 = code.zfill(5)
        try:
            has_rec, recs = _fetch_hk_dividend_primary(session, code_5)
        except Exception:
            has_rec, recs = False, []       # 网络异常等同主源无数据，转备用源
        if has_rec:
            dividend_map[code] = _sum_hk_ttm_dividend(recs, ref)
        else:
            # 主源无该股分红数据：整体改用备用源（不合并两源）。
            try:
                ok, recs = _fetch_hk_dividend_backup(session, code_5)
            except Exception:
                ok, recs = False, []
            if ok and recs:
                dividend_map[code] = _sum_hk_ttm_dividend(recs, ref)
                fallback_used += 1
            else:
                # 两源皆无记录：无从判断分红，按0计（保守，进分母压低股息率），
                # 但附说明以便区分于「确认无分红」。
                dividend_map[code] = 0.0
                note_map[code] = _NOTE_NO_DIVIDEND_DATA
        time.sleep(0.3)
    session.close()
    if fallback_used:
        print(f"[港股分红] 其中 {fallback_used} 只由备用源(datacenter)补全")

    dy_map = {}
    dy_count = 0
    for code, ttm_div in dividend_map.items():
        note = note_map.get(code)
        if ttm_div is None:
            dy_map[code] = {"dy": None, "note": note}
            continue
        if ttm_div <= 0:
            # note 非空表示「两源皆无记录，按0计」；note 为空才是确认无分红
            dy_map[code] = {"dy": 0.0, "note": note}
            continue
        price = existing_map.get(code, {}).get("price")
        if price and price > 0:
            dy_map[code] = {"dy": round(ttm_div / price * 100, 4), "note": None}
            dy_count += 1
        else:
            dy_map[code] = {"dy": None, "note": note}

    zero_count = sum(1 for v in dy_map.values() if v.get("dy") == 0.0)
    assumed = len(note_map)
    unknown = sum(1 for v in dy_map.values()
                  if v.get("dy") is None and not v.get("note"))
    print(f"[港股分红] 成功计算 {dy_count} 只有分红 / {zero_count - assumed} 只确认无分红 / "
          f"{assumed} 只无数据按0计 / {unknown} 只未知（共 {len(target_codes)} 只）")
    return dy_map


def _get_hk_buyback_yield_em(hk_codes, existing_map, ref_date=None):
    """通过东方财富 datacenter RPT_HK_BUYBACK 获取港股近365天场内回购数据，计算回购收益率。

    港股公司多以场内回购替代现金分红。港交所历史上要求购回股份须注销；
    2024-06-11 起废除该要求，公司可注销或留作库存股（依注册地法律与章程）。
    无论注销/库存，回购现金均流出公司返还股东，近似视作等效股息：
        回购收益率(%) = TTM回购金额(HKD) / 总市值(HKD) × 100
    注：RPT_HK_BUYBACK 数据不区分注销与库存。

    ref_date 默认为港股最近交易日，仅统计 [ref_date-365, ref_date] 区间内的回购。
    返回: {code: {"by": 回购收益率%|None}}
    """
    target_codes = set(str(c).strip() for c in hk_codes)
    if not target_codes:
        return {}
    already = {c for c, v in existing_map.items()
               if v.get("by") is not None and v.get("by") >= 0}
    target_codes -= {str(c).strip() for c in already}
    if not target_codes:
        print("[港股回购] 缓存完整，跳过网络请求")
        return {}

    print(f"[港股回购] 需获取 {len(target_codes)} 只港股TTM回购数据...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    session = requests.Session()
    session.headers.update(headers)
    ref = _parse_ref_date(ref_date)
    cutoff = (ref - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    ref_str = ref.strftime("%Y-%m-%d")

    # 组装多代码 + 日期过滤，分页拉取
    code_list = sorted(target_codes)
    buyback_map = defaultdict(float)
    chunk_size = 20
    for i in range(0, len(code_list), chunk_size):
        chunk = code_list[i:i + chunk_size]
        in_vals = ",".join(f'"{c.zfill(5)}.HK"' for c in chunk)
        date_filter = f"(TRADE_DATE>='{cutoff}')(TRADE_DATE<='{ref_str} 23:59:59')"
        code_filter = f"(SECUCODE in ({in_vals}))"
        page, max_pages = 1, 50
        while page <= max_pages:
            url = (
                "https://datacenter-web.eastmoney.com/api/data/v1/get?"
                "reportName=RPT_HK_BUYBACK"
                "&columns=SECUCODE,TRADE_DATE,REPO_AMT,CURRENCY"
                f"&sortColumns=TRADE_DATE&sortTypes=-1"
                f"&pageSize=100&pageNumber={page}"
                f"&filter={code_filter}{date_filter}&source=WEB&client=PC"
            )
            data = None
            for attempt in range(3):
                try:
                    resp = session.get(url, timeout=20)
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(3)
            if data is None:
                break
            items = (data.get("result") or {}).get("data") or []
            if not items:
                break
            for item in items:
                secucode = str(item.get("SECUCODE", ""))
                code = secucode.split(".")[0].lstrip("0")
                code = code.zfill(5)
                if code.zfill(5) not in {c.zfill(5) for c in chunk}:
                    continue
                try:
                    amt = float(item.get("REPO_AMT") or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                ccy = str(item.get("CURRENCY") or "HKD").upper()
                if ccy == "CNY":
                    amt *= _FX_CNY_HKD
                elif ccy == "USD":
                    amt *= _FX_USD_HKD
                buyback_map[code.zfill(5)] += amt
            total_count = (data.get("result") or {}).get("count", 0)
            if len(items) < 100 or page * 100 >= total_count:
                break
            page += 1
            time.sleep(0.5)
    session.close()

    # 转回购收益率
    by_map = {}
    by_count = 0
    for code in code_list:
        code_5 = code.zfill(5)
        amt = buyback_map.get(code_5, 0.0)
        if amt <= 0:
            by_map[code] = {"by": 0.0}
            continue
        mktcap = existing_map.get(code, {}).get("mktcap")
        if mktcap and mktcap > 0:
            by_map[code] = {"by": round(amt / mktcap * 100, 4)}
            by_count += 1
        else:
            by_map[code] = {"by": None}
    print(f"[港股回购] 成功计算 {by_count}/{len(code_list)} 只港股回购收益率")
    return by_map


# ---------------------------------------------------------------------------
# 批量获取行情（价格 + PE + PB + 股息率）
# ---------------------------------------------------------------------------

def get_batch_data(stock_codes, market_map=None):
    """
    获取股票批量行情数据（价格、PE、PB、股息率）

    参数:
        stock_codes: 证券代码列表
        market_map: {code: "hk"|"sh"|"sz"} 市场类型映射
    """
    if market_map is None:
        market_map = {}
    print("[数据源] 尝试腾讯财经接口...")
    data_map = _get_prices_tencent(stock_codes)
    missing = [c for c in stock_codes if str(c).strip() not in data_map]
    if missing and len(missing) > len(stock_codes) * 0.2:
        print(f"\n[数据源] 腾讯财经缺失 {len(missing)} 只，尝试东方财富接口兑底...")
        em_map = _get_prices_eastmoney(missing)
        for code, price in em_map.items():
            data_map[code] = {"price": price, "pe": None, "pb": None}
    print(f"\n合计获取 {len(data_map)}/{len(stock_codes)} 只股票行情")

    # 仅对A股使用标准化代码查询缓存（港股代码格式不同，暂不缓存）
    a_share_data_keys = [
        c for c in data_map.keys()
        if market_map.get(str(c).strip(), "") != "hk"
    ]
    # 缓存与TTM窗口按各市场交易日打标：自然日非交易日时取该市场上一交易日；
    # A股与港股节假日不同，各取各自日历
    hk_codes = [
        c for c in data_map.keys()
        if market_map.get(str(c).strip(), "") == "hk"
    ]
    a_trade_day = last_trading_day("sh")
    hk_trade_day = last_trading_day("hk") if hk_codes else None
    if hk_trade_day:
        print(f"[交易日] A股={a_trade_day}  港股={hk_trade_day}")
    else:
        print(f"[交易日] A股={a_trade_day}")

    # 注意：stock_valuation 仅缓存A股。命中数与分母都只统计A股成份股，
    # 否则港股跨境ETF会恒显示「命中 0/N」，误判为缓存失效。
    cached = _read_valuation_cache(a_share_data_keys, date=a_trade_day) if a_share_data_keys else {}
    for code, info in data_map.items():
        info["dy"] = cached.get(code, {}).get("dy")
    cached_dy = sum(1 for c in a_share_data_keys if data_map.get(c, {}).get("dy") is not None)
    print(f"[缓存] A股股息率命中 {cached_dy}/{len(a_share_data_keys)} 只 (date={a_trade_day})")

    # A股: 调用 datacenter 获取TTM股息率
    a_share_codes = [
        c for c in data_map.keys()
        if market_map.get(str(c).strip(), "") != "hk"
    ]
    if cached_dy < len(a_share_codes) * 0.8 and a_share_codes:
        dy_map = _get_dividend_yield_datacenter(a_share_codes, data_map, ref_date=a_trade_day)
        for code, dy_info in dy_map.items():
            if code in data_map:
                data_map[code]["dy"] = dy_info.get("dy")

    # 港股: 现金股息(TTM) + 场内回购(TTM)，回购现金(注销或库存)等效计入股东回报
    if hk_codes:
        hk_codes_5 = sorted({str(c).strip().zfill(5) for c in hk_codes})
        hk_cached = _read_hk_yield_cache(hk_codes_5, date=hk_trade_day)
        for c5, v in hk_cached.items():
            raw = _find_raw_code(data_map, c5)
            if raw is not None:
                data_map[raw]["dy"] = v.get("dy")
                data_map[raw]["by"] = v.get("by")
                data_map[raw]["total"] = v.get("total")
                data_map[raw]["note"] = v.get("note")

        # 股息与回购各自独立判断是否缺失：只要某字段仍为 None 就补取。
        # 不可要求两者同时为 None——否则回购已取到(哪怕为0)的股票，其缺失的
        # 股息将永远不再重试。0.0 表示已确认无该项，不再重取。
        need_dy = [c for c in hk_codes if data_map[c].get("dy") is None]
        need_by = [c for c in hk_codes if data_map[c].get("by") is None]
        if need_dy:
            hk_dy_map = _get_hk_dividend_yield_em(need_dy, data_map, ref_date=hk_trade_day)
            for code, dy_info in hk_dy_map.items():
                if code in data_map:
                    data_map[code]["dy"] = dy_info.get("dy")
                    data_map[code]["note"] = dy_info.get("note")
        if need_by:
            hk_by_map = _get_hk_buyback_yield_em(need_by, data_map, ref_date=hk_trade_day)
            for code, by_info in hk_by_map.items():
                if code in data_map:
                    data_map[code]["by"] = by_info.get("by")

        # 计算股东总回报率 = 现金股息率 + 回购收益率，并写缓存。
        # 两者须同时已知：任一项未知则总回报未知（None），不可用部分和冒充总和
        # （否则「股息未知、仅有回购」的股票会以回购额充当总回报，系统性低估）。
        # 注：A股成份股无回购口径，其总回报=现金股息，在A股分支中处理。
        hk_save = {}
        for code in hk_codes:
            dy = data_map[code].get("dy")
            by = data_map[code].get("by")
            total = round(dy + by, 4) if (dy is not None and by is not None) else None
            data_map[code]["total"] = total
            hk_save[str(code).strip().zfill(5)] = {
                "price": data_map[code].get("price"),
                "dy": dy, "by": by, "total": total,
                "note": data_map[code].get("note"),
            }
        _save_hk_yield_cache(hk_save, date=hk_trade_day)

    # 仅缓存A股估值数据
    a_share_data = {
        c: v for c, v in data_map.items()
        if market_map.get(str(c).strip(), "") != "hk"
    }
    if a_share_data:
        for code, v in a_share_data.items():
            v.setdefault("by", None)
            v["total"] = v.get("dy")
        _save_valuation_cache(a_share_data, date=a_trade_day)
    return data_map


# ---------------------------------------------------------------------------
# ETF 估值计算
# ---------------------------------------------------------------------------

def _parse_nav_total(basic_info, stock_df):
    """解析占比计算所用的分母（最小申赎单位净值）"""
    for key in ("最小申赎单位净值", "申赎单位净值"):
        raw = basic_info.get(key, "")
        if not raw: continue
        cleaned = str(raw).replace("￥", "").replace("元", "").replace(",", "").strip()
        try:
            val = float(cleaned)
            if val > 0:
                stock_sum = stock_df["市值"].sum()
                print(f"使用「{key}」作为占比分母: {val:,.2f}（成份股市值之和: {stock_sum:,.2f}）")
                return val
        except ValueError: pass
    stock_sum = stock_df["市值"].sum()
    print(f"未找到最小申赎单位净值，使用成份股市值之和作为分母: {stock_sum:,.2f}")
    return stock_sum


def _calc_etf_pe_pb(stock_df):
    """
    根据成份股市值占比计算 ETF 加权 PE / PB / 股息率

    计算方法（与官方口径一致）：
        整体PE   = Σ(占比) / Σ(占比 / PE)   调和平均
        整体PB   = Σ(占比) / Σ(占比 / PB)   调和平均
        加权股息率 = Σ(占比 × 股息率) / Σ(占比)  算术加权平均
    """
    result = {}
    valid = stock_df[stock_df["市值"].notna() & (stock_df["市值"] > 0)].copy()
    if valid.empty:
        return result

    pe_valid = valid[valid["PE"].notna() & (valid["PE"] > 0)].copy()
    if not pe_valid.empty:
        w_pe = pe_valid["占比(%)"].sum()
        denom = (pe_valid["占比(%)"] / pe_valid["PE"]).sum()
        etf_pe = w_pe / denom if denom > 0 else 0
        result["整体PE"] = round(etf_pe, 2)
        result["PE覆盖率"] = f"{len(pe_valid)}/{len(valid)}"

    pb_valid = valid[valid["PB"].notna() & (valid["PB"] > 0)].copy()
    if not pb_valid.empty:
        w_pb = pb_valid["占比(%)"].sum()
        denom_pb = (pb_valid["占比(%)"] / pb_valid["PB"]).sum()
        etf_pb = w_pb / denom_pb if denom_pb > 0 else 0
        result["整体PB"] = round(etf_pb, 2)
        result["PB覆盖率"] = f"{len(pb_valid)}/{len(valid)}"

    # 收益率类指标用 >=0：股息率=0 是「确认无分红」的有效值，须计入分母；
    # 仅 NaN（未知/已宣派未除净）才排除。用 >0 会把零股息股剔除，虚高加权股息率。
    # PE/PB 仍用 >0，按惯例排除亏损股与负净资产股。
    dy_col = "股息率(%)" if "股息率(%)" in valid.columns else None
    if dy_col:
        dy_valid = valid[valid[dy_col].notna() & (valid[dy_col] >= 0)].copy()
        if not dy_valid.empty:
            w_dy = dy_valid["占比(%)"].sum()
            weighted_dy = (dy_valid["占比(%)"] * dy_valid[dy_col]).sum() / w_dy if w_dy > 0 else 0
            result["加权股息率(%)"] = round(weighted_dy, 2)
            result["股息率覆盖率"] = f"{len(dy_valid)}/{len(valid)}"

    # 港股回购收益率（含注销/库存，数据不分，等效股东回报）
    for col, key in (("回购收益率(%)", "加权回购收益率(%)"), ("股东总回报率(%)", "加权股东总回报率(%)")):
        if col not in valid.columns:
            continue
        sub = valid[valid[col].notna() & (valid[col] >= 0)].copy()
        if sub.empty:
            continue
        w = sub["占比(%)"].sum()
        weighted = (sub["占比(%)"] * sub[col]).sum() / w if w > 0 else 0
        result[key] = round(weighted, 2)
        result[f"{key}覆盖率"] = f"{len(sub)}/{len(valid)}"

    return result


def calc_etf_valuation(fund_code, market=None, date=None):
    """
    查询ETF申赎清单并计算成份股占比及ETF加权PE/PB/分红率

    参数:
        fund_code: 基金代码(6位数字)
        market: 市场 "sh" 或 "sz"，不指定时自动判断
        date: 日期(仅深交所使用)
    返回:
        (基本信息dict, 带占比的成份股DataFrame, ETF估值指标dict)
    """
    basic_info, stock_df = query_etf_detail(fund_code, market=market, date=date)
    if stock_df is None or stock_df.empty:
        print("未查询到成份股明细")
        return basic_info, pd.DataFrame(), {}

    print(f"\n成份股共 {len(stock_df)} 只，正在批量获取行情...")
    stock_codes = [str(row["证券代码"]).strip() for _, row in stock_df.iterrows()]

    # 检测成份股市场类型（港股/A股），用于行情查询和股息率处理
    # 说明：上交所跨市场ETF的申赎清单无「市场」列，需按代码长度推断（A股恒为6位，港股≤5位）
    market_map = {}
    if "市场" in stock_df.columns:
        for _, row in stock_df.iterrows():
            code = str(row["证券代码"]).strip()
            mkt = str(row.get("市场", "")).strip()
            if "香港" in mkt or "hk" in mkt.lower():
                market_map[code] = "hk"
                # 同时添加腾讯标准化代码（5位零填充）作为 key
                market_map[code.zfill(5)] = "hk"
            elif "深圳" in mkt:
                market_map[code] = "sz"
            elif "上海" in mkt:
                market_map[code] = "sh"
    for _, row in stock_df.iterrows():
        code = str(row["证券代码"]).strip()
        if code in market_map:
            continue
        if len(code) <= 5:
            market_map[code] = "hk"
            market_map[code.zfill(5)] = "hk"
        elif code.startswith(("6", "9")):
            market_map[code] = "sh"
        else:
            market_map[code] = "sz"
    hk_count = sum(1 for _, row in stock_df.iterrows()
                   if market_map.get(str(row["证券代码"]).strip()) == "hk")
    if hk_count > 0:
        print(f"  检测到 {hk_count} 只港股成份股，将使用腾讯港股行情接口")

    batch_data = get_batch_data(stock_codes, market_map=market_map)
    if not batch_data:
        print("无法获取行情数据")
        return basic_info, pd.DataFrame(), {}

    close_prices, pe_list, pb_list, dy_list = [], [], [], []
    by_list, total_list, note_list = [], [], []
    matched = 0
    for _, row in stock_df.iterrows():
        code = str(row["证券代码"]).strip()
        # 标准化代码用于查找行情（腾讯返回5位港股代码）
        sym, _ = _code_to_tencent_symbol(code)
        norm_code = sym[2:]  # 去掉 sh/sz/hk 前缀
        info = batch_data.get(code, batch_data.get(norm_code, {}))
        close_prices.append(info.get("price"))
        pe_list.append(info.get("pe"))
        pb_list.append(info.get("pb"))
        dy_list.append(info.get("dy"))
        by_list.append(info.get("by"))
        total_list.append(info.get("total"))
        note_list.append(info.get("note"))
        if info.get("price") is not None:
            matched += 1
    print(f"成功匹配 {matched}/{len(stock_df)} 只成份股行情")

    stock_df["收盘价"] = close_prices
    stock_df["PE"] = pe_list
    stock_df["PB"] = pb_list
    stock_df["股息率(%)"] = dy_list
    stock_df["回购收益率(%)"] = by_list
    stock_df["股东总回报率(%)"] = total_list
    stock_df["数据说明"] = note_list

    stock_df["数量"] = pd.to_numeric(stock_df["数量"].astype(str).str.replace(",", "", regex=False), errors="coerce")
    stock_df["市值"] = stock_df["数量"] * stock_df["收盘价"]
    nav_total = _parse_nav_total(basic_info, stock_df)
    stock_df["占比(%)"] = (stock_df["市值"] / nav_total * 100).round(4)
    etf_metrics = _calc_etf_pe_pb(stock_df)
    stock_df = stock_df.sort_values("占比(%)", ascending=False).reset_index(drop=True)

    return basic_info, stock_df, etf_metrics


# ---------------------------------------------------------------------------
# 历史分红数据采集（stock_dividend 逻辑）
# ---------------------------------------------------------------------------

def _infer_market(stock_code: str) -> str:
    """根据股票代码推断市场"""
    return "sh" if stock_code.startswith("6") else "sz"


def collect_stock_dividend(conn=None):
    """
    获取所有A股历史分红汇总并存入数据库

    接口: ak.stock_history_dividend()
    输出: 代码、名称、上市日期、累计股息(%)、年均股息(%)、分红次数、融资总额(亿)、融资次数
    """
    print("正在获取所有 A 股历史分红数据（新浪财经）...")
    df = ak.stock_history_dividend()
    df = df.rename(columns={"代码": "stock_code"})
    df["market"] = df["stock_code"].apply(_infer_market)
    cols = ["stock_code", "market"] + [c for c in df.columns if c not in ("stock_code", "market")]
    df = df[cols]
    print(f"共获取 {len(df)} 只股票的历史分红数据")

    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    table = "stock_dividend"
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if cursor.fetchone():
        cursor.execute(f"DELETE FROM {table}")
    df.to_sql(table, conn, if_exists="append", index=False)
    print(f"  -> 已写入 {table} 表，共 {len(df)} 条记录")
    if own_conn:
        conn.close()

    # 展示累计股息 TOP 20
    print(f"\n=== 累计股息 TOP 20 ===")
    top_df = df.nlargest(20, "累计股息")[["stock_code", "market", "名称", "累计股息", "年均股息", "分红次数"]]
    print(top_df.to_string(index=False))
    return df


# ---------------------------------------------------------------------------
# CLI 命令
# ---------------------------------------------------------------------------

def cmd_valuation(args):
    """valuation 子命令：查询ETF估值"""
    basic_info, stock_df, etf_metrics = calc_etf_valuation(
        args.fund_code, market=args.market, date=args.date
    )

    if basic_info:
        print(f"\n{'=' * 70}")
        print("ETF基本信息:")
        for key, value in basic_info.items():
            print(f"  {key}: {value}")

    if etf_metrics:
        print(f"\n{'=' * 70}")
        print("ETF 估值指标（按市值占比加权）:")
        for key, value in etf_metrics.items():
            print(f"  {key}: {value}")

    if not stock_df.empty:
        print(f"\n{'=' * 70}")
        print(f"成份股占比明细 (共 {len(stock_df)} 只)")
        print(f"{'=' * 70}")
        display_cols = ["证券代码", "证券名称", "数量", "收盘价", "PE", "PB", "股息率(%)", "回购收益率(%)", "股东总回报率(%)", "市值", "占比(%)"]
        available_cols = [c for c in display_cols if c in stock_df.columns]
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 160)
        pd.set_option("display.float_format", lambda x: f"{x:.4f}")
        print(stock_df[available_cols].head(args.max_rows).to_string(index=False))
        if len(stock_df) > args.max_rows:
            print(f"\n... 仅显示前 {args.max_rows} 条，共 {len(stock_df)} 条")

        print(f"\n--- 前10大权重股 ---")
        for _, row in stock_df.head(10).iterrows():
            pe_str = f"PE:{row['PE']:.1f}" if pd.notna(row.get('PE')) and row.get('PE', 0) > 0 else "PE:-"
            pb_str = f"PB:{row['PB']:.2f}" if pd.notna(row.get('PB')) and row.get('PB', 0) > 0 else "PB:-"
            if pd.notna(row.get('股息率(%)')):
                dy_str = f"股息:{row['股息率(%)']:.2f}%"
                if row.get("数据说明"):
                    dy_str += "*"          # * = 见下方数据说明
            else:
                dy_str = "股息:-"
            if pd.notna(row.get('股东总回报率(%)')) and row.get('股东总回报率(%)', 0) > 0:
                dy_str += f"  总回报:{row['股东总回报率(%)']:.2f}%"
            print(f"  {row['证券代码']} {row['证券名称']:<8s}  "
                  f"占比: {row['占比(%)']:.2f}%  收盘价: {row['收盘价']:.2f}  "
                  f"{pe_str}  {pb_str}  {dy_str}")

        # 逐只列出被标注的证券，说明其股息率为何按0计（区分「确认无分红」）
        noted = stock_df[stock_df["数据说明"].notna()]
        if not noted.empty:
            print(f"\n--- 数据说明（{len(noted)} 只，股息率按0计）---")
            for _, row in noted.iterrows():
                print(f"  {row['证券代码']} {row['证券名称']}: {row['数据说明']}")

    if args.save and not stock_df.empty:
        filename = f"etf_valuation_{args.fund_code}_{datetime.now().strftime('%Y%m%d')}.csv"
        stock_df.to_csv(filename, index=False, encoding="utf-8-sig")
        print(f"\n结果已保存到: {filename}")


def cmd_dividend(args):
    """dividend 子命令：采集历史分红数据"""
    conn = get_conn(args.db_dir)
    try:
        collect_stock_dividend(conn)
    finally:
        conn.close()
    print(f"\n采集完成，数据已存入 {args.db_dir}")


def cmd_query(args):
    """query 子命令：查询分红数据"""
    conn = get_conn(args.db_dir)
    try:
        table = "stock_dividend"
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if not cursor.fetchone():
            print(f"错误: 表 {table} 不存在，请先运行 dividend 子命令采集数据")
            sys.exit(1)

        if args.top:
            sql = (f"SELECT stock_code, market, 名称, 累计股息, 年均股息, 分红次数, 融资总额 "
                   f"FROM {table} ORDER BY 累计股息 DESC LIMIT {args.top}")
            print(f"SQL: {sql}\n")
            df = pd.read_sql(sql, conn)
        elif args.code:
            sql = f"SELECT * FROM {table} WHERE stock_code = ?"
            df = pd.read_sql(sql, conn, params=[args.code])
        else:
            sql = f"SELECT * FROM {table} LIMIT 50"
            df = pd.read_sql(sql, conn)

        print(f"共 {len(df)} 条")
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 200)
        print(df.to_string(index=False))
    finally:
        conn.close()


def cmd_tables(args):
    """tables 子命令：列出所有表"""
    conn = get_conn(args.db_dir)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cursor.fetchall()]
        if not tables:
            print("数据库中暂无数据表")
            return
        print(f"数据库: {args.db_dir}\n")
        print(f"{'表名':<35} {'行数':>8}")
        print("-" * 45)
        total = 0
        for t in tables:
            cursor.execute(f"SELECT COUNT(*) FROM [{t}]")
            cnt = cursor.fetchone()[0]
            total += cnt
            print(f"{t:<35} {cnt:>8,}")
        print("-" * 45)
        print(f"{'合计':<35} {total:>8,}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db-dir", default=DB_PATH,
                        help="数据库文件路径 (默认: FINANCIAL_DATA_DIR/financial_data.db)")

    parser = argparse.ArgumentParser(
        description="ETF估值(PE/PB/分红率)查询 + 历史分红数据采集 CLI 工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s valuation --fund-code 510300
  %(prog)s valuation --fund-code 159008 --market sz
  %(prog)s valuation --fund-code 510300 --save
  %(prog)s dividend
  %(prog)s query --code 600519
  %(prog)s query --top 50
  %(prog)s tables
        """,
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # valuation
    p = sub.add_parser("valuation", parents=[common],
                       help="查询ETF成份股并计算加权PE/PB/分红率")
    p.add_argument("--fund-code", required=True, help="ETF基金代码(6位数字)")
    p.add_argument("--market", choices=["sh", "sz"], default=None,
                   help="市场: sh=上交所, sz=深交所 (默认自动判断)")
    p.add_argument("--date", default=None, help="日期 YYYY-MM-DD (仅深交所使用)")
    p.add_argument("--save", action="store_true", help="将结果保存为CSV文件")
    p.add_argument("--max-rows", type=int, default=100, help="最大显示行数 (默认: 100)")
    p.set_defaults(func=cmd_valuation)

    # dividend
    p = sub.add_parser("dividend", parents=[common],
                       help="采集所有A股历史分红汇总数据")
    p.set_defaults(func=cmd_dividend)

    # query
    p = sub.add_parser("query", parents=[common],
                       help="查询分红数据")
    p.add_argument("--code", help="股票代码")
    p.add_argument("--top", type=int, help="累计股息 TOP N")
    p.set_defaults(func=cmd_query)

    # tables
    p = sub.add_parser("tables", parents=[common], help="列出数据库中所有表")
    p.set_defaults(func=cmd_tables)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
