#!/usr/bin/env python3
"""
collect_financial_data.py - A股 + 港股财报数据采集与查询 CLI 工具（独立版）

所有采集逻辑均内置于本脚本，无需依赖外部模块（仅需 akshare + pandas）。

子命令:
    collect  - 采集单只股票的财报数据
    batch    - 批量采集（从文件读取股票列表）
    query    - 查询数据库中的财报数据
    tables   - 列出数据库中所有表及行数

用法:
    python collect_financial_data.py collect --code 600519 --market sh --start-year 2016
    python collect_financial_data.py collect --code 00700 --market hk
    python collect_financial_data.py batch --file stocks.txt
    python collect_financial_data.py query --code 600519 --table em_financial_indicator --year 2024
    python collect_financial_data.py tables

数据库默认保存在项目根目录的 financial_data.db（通过 FINANCIAL_DATA_DIR 环境变量或向上定位项目根目录），可用 --db-dir 指定。
"""

import argparse
import datetime
import os
import sqlite3
import sys
from pathlib import Path

import akshare as ak
import pandas as pd

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

TABLE_MAP = {
    "资产负债表": "balance_sheet",
    "利润表":     "income_statement",
    "现金流量表": "cash_flow",
}


# ---------------------------------------------------------------------------
# 数据库工具
# ---------------------------------------------------------------------------

def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    """创建并返回 SQLite 连接（WAL 模式）"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def save_to_db(df: pd.DataFrame, table: str, stock_code: str, market: str,
               conn: sqlite3.Connection):
    """
    将 DataFrame 写入 SQLite 指定表。
    - 自动插入 stock_code、market 列（market 统一小写）
    - 幂等更新：先删旧数据再插入
    """
    market = market.lower()
    df = df.copy()
    df.insert(0, "stock_code", stock_code)
    df.insert(1, "market", market)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if cursor.fetchone():
        cursor.execute(
            f"DELETE FROM {table} WHERE stock_code = ? AND market = ?",
            (stock_code, market),
        )
    df.to_sql(table, conn, if_exists="append", index=False)
    print(f"  -> 已写入 {table}，{stock_code}.{market}，{len(df)} 条")


# ---------------------------------------------------------------------------
# A 股财务指标
# ---------------------------------------------------------------------------

def get_financial_report(stock_code: str, start_year: str = None) -> pd.DataFrame:
    """新浪-财务指标（宽表 86 项）"""
    if start_year is None:
        start_year = str(datetime.date.today().year - 10)
    print(f"获取 {stock_code} 财务指标（新浪）...")
    df = ak.stock_financial_analysis_indicator(symbol=stock_code, start_year=start_year)
    print(f"  共 {len(df)} 条，{len(df.columns)} 个指标")
    return df


def get_financial_report_em(stock_code: str, market: str = "sh") -> pd.DataFrame:
    """东财-主要指标（宽表 140 项，A 股）"""
    symbol = f"{stock_code}.{market.upper()}"
    print(f"获取 {symbol} 主要指标（东财）...")
    df = ak.stock_financial_analysis_indicator_em(symbol=symbol, indicator="按报告期")
    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
    ten_years_ago = pd.Timestamp.now() - pd.DateOffset(years=10)
    df = df[df["REPORT_DATE"] >= ten_years_ago].copy()
    print(f"  近10年 {len(df)} 条")
    return df


# ---------------------------------------------------------------------------
# A 股三大报表
# ---------------------------------------------------------------------------

def get_financial_statements_sina(stock_code: str, market_prefix: str = "sh") -> dict:
    """新浪-三大报表（宽表）"""
    stock = f"{market_prefix}{stock_code}"
    reports = {}
    for name in ["资产负债表", "利润表", "现金流量表"]:
        print(f"  获取 {stock} {name}（新浪）...")
        df = ak.stock_financial_report_sina(stock=stock, symbol=name)
        df["报告日"] = pd.to_datetime(df["报告日"], format="%Y%m%d")
        ten_years_ago = pd.Timestamp.now() - pd.DateOffset(years=10)
        df = df[df["报告日"] >= ten_years_ago].copy()
        df["year"] = df["报告日"].dt.year
        df["quarter"] = df["报告日"].dt.month // 3
        reports[name] = df
        print(f"    {len(df)} 条")
    return reports


def get_financial_statements_ths(stock_code: str) -> dict:
    """同花顺-三大报表（长表）"""
    api_map = {
        "资产负债表": ak.stock_financial_debt_new_ths,
        "利润表":     ak.stock_financial_benefit_new_ths,
        "现金流量表": ak.stock_financial_cash_new_ths,
    }
    reports = {}
    current_year = datetime.date.today().year
    for name, func in api_map.items():
        print(f"  获取 {stock_code} {name}（同花顺）...")
        df = func(symbol=stock_code, indicator="按报告期")
        df["year"] = df["report_period"].str.split("-").str[0].astype(int)
        df["quarter"] = df["report_period"].str.split("-").str[1].astype(int)
        df = df[df["year"] >= current_year - 10].copy()
        reports[name] = df
        print(f"    {len(df)} 条")
    return reports


def get_financial_statements_em(stock_code: str, market: str = "sh") -> dict:
    """东财-三大报表（宽表 300+ 列）"""
    symbol = f"{market.upper()}{stock_code}"
    api_map = {
        "资产负债表": ak.stock_balance_sheet_by_report_em,
        "利润表":     ak.stock_profit_sheet_by_report_em,
        "现金流量表": ak.stock_cash_flow_sheet_by_report_em,
    }
    reports = {}
    ten_years_ago = pd.Timestamp.now() - pd.DateOffset(years=10)
    for name, func in api_map.items():
        print(f"  获取 {symbol} {name}（东财）...")
        df = func(symbol=symbol)
        df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
        df = df[df["REPORT_DATE"] >= ten_years_ago].copy()
        df["year"] = df["REPORT_DATE"].dt.year
        df["quarter"] = df["REPORT_DATE"].dt.month // 3
        reports[name] = df
        print(f"    {len(df)} 条，{len(df.columns)} 列")
    return reports


# ---------------------------------------------------------------------------
# 港股
# ---------------------------------------------------------------------------

def get_financial_report_hk(stock_code: str) -> pd.DataFrame:
    """东财-港股主要指标（宽表 36 项）"""
    print(f"获取港股 {stock_code} 主要指标（东财）...")
    df = ak.stock_financial_hk_analysis_indicator_em(symbol=stock_code, indicator="报告期")
    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
    ten_years_ago = pd.Timestamp.now() - pd.DateOffset(years=10)
    df = df[df["REPORT_DATE"] >= ten_years_ago].copy()
    df["year"] = df["REPORT_DATE"].dt.year
    df["quarter"] = df["REPORT_DATE"].dt.month // 3
    print(f"  近10年 {len(df)} 条，{len(df.columns)} 项")
    return df


def get_financial_statements_hk(stock_code: str) -> dict:
    """东财-港股三大报表（长表）"""
    reports = {}
    ten_years_ago = pd.Timestamp.now() - pd.DateOffset(years=10)
    for name in ["资产负债表", "利润表", "现金流量表"]:
        print(f"  获取港股 {stock_code} {name}（东财）...")
        df = ak.stock_financial_hk_report_em(stock=stock_code, symbol=name, indicator="年度")
        df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
        df = df[df["REPORT_DATE"] >= ten_years_ago].copy()
        df["year"] = df["REPORT_DATE"].dt.year
        df["quarter"] = df["REPORT_DATE"].dt.month // 3
        reports[name] = df
        print(f"    {len(df)} 条")
    return reports


# ---------------------------------------------------------------------------
# collect / batch
# ---------------------------------------------------------------------------

def _collect_a(code, market, start_year, conn):
    print("【1】新浪 - 财务指标")
    save_to_db(get_financial_report(code, start_year), "sina_financial_indicator", code, market, conn)
    print("【2】东财 - 主要指标")
    save_to_db(get_financial_report_em(code, market), "em_financial_indicator", code, market, conn)
    print("【3】新浪 - 三大报表")
    for n, d in get_financial_statements_sina(code, market).items():
        save_to_db(d, f"sina_{TABLE_MAP[n]}", code, market, conn)
    print("【4】同花顺 - 三大报表")
    for n, d in get_financial_statements_ths(code).items():
        save_to_db(d, f"ths_{TABLE_MAP[n]}", code, market, conn)
    print("【5】东财 - 三大报表")
    for n, d in get_financial_statements_em(code, market).items():
        save_to_db(d, f"em_{TABLE_MAP[n]}", code, market, conn)


def _collect_hk(code, market, conn):
    print("【1】东财 - 港股主要指标")
    save_to_db(get_financial_report_hk(code), "hk_financial_indicator", code, market, conn)
    print("【2】东财 - 港股三大报表")
    for n, d in get_financial_statements_hk(code).items():
        save_to_db(d, f"hk_{TABLE_MAP[n]}", code, market, conn)


def _collect_stock(code, market, start_year, conn):
    print(f"\n{'='*50}")
    print(f"  {code}.{market}  起始年: {start_year}")
    print(f"{'='*50}")
    if market.lower() == "hk":
        _collect_hk(code, market, conn)
    else:
        _collect_a(code, market, start_year, conn)


def cmd_collect(args):
    conn = get_conn(args.db_dir)
    try:
        _collect_stock(args.code, args.market, args.start_year, conn)
    finally:
        conn.close()
    print(f"\n采集完成: {args.code}.{args.market}")


def _parse_stock_file(filepath):
    stocks = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 3:
                stocks.append((parts[0], parts[1], parts[2]))
            elif len(parts) == 2:
                stocks.append((parts[0], parts[1], "2016"))
    return stocks


def cmd_batch(args):
    if not os.path.exists(args.file):
        print(f"错误: 文件不存在 - {args.file}"); sys.exit(1)
    stocks = _parse_stock_file(args.file)
    if not stocks:
        print("错误: 文件中没有有效记录"); sys.exit(1)
    print(f"共 {len(stocks)} 只股票待采集\n")
    conn = get_conn(args.db_dir)
    try:
        for c, m, y in stocks:
            _collect_stock(c, m, y, conn)
    finally:
        conn.close()
    print(f"\n全部完成: {len(stocks)} 只")


# ---------------------------------------------------------------------------
# query / tables
# ---------------------------------------------------------------------------

def cmd_query(args):
    conn = get_conn(args.db_dir)
    try:
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info([{args.table}])")
        columns = [r[1] for r in cursor.fetchall()]
        if not columns:
            print(f"错误: 表 {args.table} 不存在"); sys.exit(1)

        cond, params = ["stock_code = ?"], [args.code]
        if args.market:
            cond.append("market = ? COLLATE NOCASE"); params.append(args.market)
        if args.year:
            if "year" in columns:
                cond.append("year = ?"); params.append(args.year)
            elif "REPORT_DATE" in columns:
                cond.append("CAST(strftime('%Y', REPORT_DATE) AS INTEGER) = ?"); params.append(args.year)
            elif "日期" in columns:
                cond.append("日期 LIKE ?"); params.append(f"{args.year}%")
        if args.quarter:
            if "quarter" in columns:
                cond.append("quarter = ?"); params.append(args.quarter)
            elif "REPORT_DATE" in columns:
                cond.append("CAST(strftime('%m', REPORT_DATE) AS INTEGER) / 3 = ?"); params.append(args.quarter)

        sql = f"SELECT * FROM [{args.table}] WHERE {' AND '.join(cond)} LIMIT {args.limit or 50}"
        print(f"SQL: {sql}\n")
        df = pd.read_sql(sql, conn, params=params)
        print(f"共 {len(df)} 条")
        if args.columns:
            cols = ["stock_code", "market"] + args.columns.split(",")
            df = df[[c for c in cols if c in df.columns]]
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 200)
        print(df.to_string(index=False))
    finally:
        conn.close()


def cmd_tables(args):
    conn = get_conn(args.db_dir)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [r[0] for r in cursor.fetchall()]
        if not tables:
            print("数据库中暂无数据表"); return
        print(f"数据库: {args.db_dir}\n")
        print(f"{'表名':<30} {'行数':>8}")
        print("-" * 40)
        total = 0
        for t in tables:
            cursor.execute(f"SELECT COUNT(*) FROM [{t}]")
            cnt = cursor.fetchone()[0]
            total += cnt
            print(f"{t:<30} {cnt:>8,}")
        print("-" * 40)
        print(f"{'合计':<30} {total:>8,}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db-dir", default=DB_PATH,
                        help="数据库文件路径 (默认: 脚本同目录)")

    parser = argparse.ArgumentParser(
        description="A股 + 港股财报数据采集与查询 CLI 工具（独立版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s collect --code 600519 --market sh --start-year 2016
  %(prog)s collect --code 00700 --market hk
  %(prog)s batch --file stocks.txt
  %(prog)s query --code 600519 --table em_financial_indicator --year 2024
  %(prog)s query --code 600519 --table em_income_statement --year 2024 --quarter 4
  %(prog)s tables
        """,
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    p = sub.add_parser("collect", parents=[common], help="采集单只股票")
    p.add_argument("--code", required=True, help="股票代码")
    p.add_argument("--market", required=True, choices=["sh", "sz", "hk"], help="市场")
    p.add_argument("--start-year", default="2016", help="起始年 (默认 2016)")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("batch", parents=[common], help="批量采集")
    p.add_argument("--file", required=True, help="股票列表文件")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("query", parents=[common], help="查询数据")
    p.add_argument("--code", required=True, help="股票代码")
    p.add_argument("--table", required=True, help="表名")
    p.add_argument("--market", help="市场 (可选)")
    p.add_argument("--year", type=int, help="年份 (可选)")
    p.add_argument("--quarter", type=int, choices=[1, 2, 3, 4], help="季度 (可选)")
    p.add_argument("--columns", help="显示列，逗号分隔")
    p.add_argument("--limit", type=int, help="最大行数 (默认 50)")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("tables", parents=[common], help="列出所有表")
    p.set_defaults(func=cmd_tables)

    args = parser.parse_args()
    if not args.command:
        parser.print_help(); sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
