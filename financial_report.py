"""
financial_report.py - A股 + 港股财报数据采集与存储工具

功能概述:
    从多个数据源采集 A 股（新浪/同花顺/东方财富）和港股（东方财富）的财报数据，
    统一格式后存入本地 SQLite 数据库，支持按证券代码、市场、年份、季度筛选。

数据源与接口:
    ┌───────────┬───────┬───────────────────────────────────────┬─────────────────────┐
    │ 数据源    │ 市场  │ akshare 接口                         │ 数据格式            │
    ├───────────┼───────┼───────────────────────────────────────┼─────────────────────┤
    │ 新浪      │ A股   │ stock_financial_analysis_indicator    │ 宽表（86项财务指标）│
    │           │       │ stock_financial_report_sina           │ 宽表（三大报表）    │
    │ 同花顺    │ A股   │ stock_financial_debt_new_ths          │ 长表（每行一个指标）│
    │           │       │ stock_financial_benefit_new_ths       │                     │
    │           │       │ stock_financial_cash_new_ths          │                     │
    │ 东方财富  │ A股   │ stock_financial_analysis_indicator_em │ 宽表（140项指标）   │
    │           │       │ stock_balance_sheet_by_report_em      │ 宽表（319列）       │
    │           │       │ stock_profit_sheet_by_report_em       │ 宽表（203列）       │
    │           │       │ stock_cash_flow_sheet_by_report_em    │ 宽表（252列）       │
    │ 东方财富  │ 港股  │ stock_financial_hk_analysis_indicator │ 宽表（36项指标）    │
    │           │       │ stock_financial_hk_report_em          │ 长表（科目+金额）   │
    └───────────┴───────┴───────────────────────────────────────┴─────────────────────┘

数据库表结构（均含 stock_code、market 列）:
    ┌────────────────────────────┬───────┬──────────────────────────────┐
    │ 表名                        │ 市场  │ 数据源 / 特有字段             │
    ├────────────────────────────┼───────┼──────────────────────────────┤
    │ sina_financial_indicator   │ A股   │ 新浪-财务指标（86项）         │
    │ em_financial_indicator     │ A股   │ 东财-主要指标（140项）        │
    │ sina_balance_sheet         │ A股   │ 新浪-资产负债表               │
    │ sina_income_statement      │ A股   │ 新浪-利润表                   │
    │ sina_cash_flow             │ A股   │ 新浪-现金流量表               │
    │ ths_balance_sheet          │ A股   │ 同花顺-资产负债表（长表）     │
    │ ths_income_statement       │ A股   │ 同花顺-利润表（长表）         │
    │ ths_cash_flow              │ A股   │ 同花顺-现金流量表（长表）     │
    │ em_balance_sheet           │ A股   │ 东财-资产负债表（宽表）       │
    │ em_income_statement        │ A股   │ 东财-利润表（宽表）           │
    │ em_cash_flow               │ A股   │ 东财-现金流量表（宽表）       │
    ├────────────────────────────┼───────┼──────────────────────────────┤
    │ hk_financial_indicator     │ 港股  │ 东财-主要指标（36项）         │
    │ hk_balance_sheet           │ 港股  │ 东财-资产负债表（长表）       │
    │ hk_income_statement        │ 港股  │ 东财-利润表（长表）           │
    │ hk_cash_flow               │ 港股  │ 东财-现金流量表（长表）       │
    └────────────────────────────┴───────┴──────────────────────────────┘

统一字段约定:
    - stock_code: 纯数字股票代码，如 A股 "600519" / 港股 "00700"
    - market: 市场标识，统一小写存储（"sh" 上海 / "sz" 深圳 / "hk" 港股）
    - year: 报告年份，整数，如 2024
    - quarter: 报告期次，整数 1-4（1=一季度 2=半年报 3=三季报 4=年报）

依赖:
    pip install akshare pandas

使用方法:
    # 命令行直接运行
    python financial_report.py

    # 作为模块导入
    from financial_report import get_financial_report, get_conn, save_to_db

API 文档:
    https://akshare.akfamily.xyz/data/stock/stock.html
"""

import datetime
import sqlite3
from pathlib import Path

import akshare as ak
import pandas as pd

# ---------------------------------------------------------------------------
# 数据库配置
# ---------------------------------------------------------------------------

# 数据库文件固定在脚本同级目录下，保证可移植性
DB_PATH = str(Path(__file__).parent / "financial_data.db")



# ---------------------------------------------------------------------------
# 数据库工具函数
# ---------------------------------------------------------------------------


def save_to_db(df: pd.DataFrame, table: str, stock_code: str, market: str, conn: sqlite3.Connection):
    """
    将 DataFrame 写入 SQLite 指定表。

    行为说明:
        1. 自动在 DataFrame 头部插入 stock_code 和 market 两列
        2. market 统一转为小写存储，保证查询一致性
        3. 写入前先删除该 (stock_code, market) 的旧数据，实现幂等更新
        4. 表不存在时自动创建（由 pandas to_sql 完成）

    参数:
        df:         待写入的 DataFrame（不含 stock_code/market 列）
        table:      目标表名，如 "sina_balance_sheet"
        stock_code: 股票代码，如 "600519"
        market:     市场标识，大小写均可，存入时统一转小写
        conn:       SQLite 连接对象

    示例:
        >>> conn = get_conn()
        >>> save_to_db(df, "sina_balance_sheet", "600519", "sh", conn)
    """
    market = market.lower()  # 统一小写存储
    df = df.copy()
    df.insert(0, "stock_code", stock_code)
    df.insert(1, "market", market)

    cursor = conn.cursor()
    # 表不存在时跳过删除
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if cursor.fetchone():
        cursor.execute(
            f"DELETE FROM {table} WHERE stock_code = ? AND market = ?",
            (stock_code, market)
        )
    df.to_sql(table, conn, if_exists="append", index=False)
    print(f"  -> 已写入 {table} 表，stock_code={stock_code}, market={market}，共 {len(df)} 条")


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    """
    创建并返回 SQLite 数据库连接。

    特性:
        - 启用 WAL (Write-Ahead Logging) 模式，提高并发读性能
        - 默认连接到脚本同目录下的 financial_data.db

    参数:
        db_path: 数据库文件路径，默认为 DB_PATH

    示例:
        >>> conn = get_conn()
        >>> df = pd.read_sql("SELECT * FROM sina_balance_sheet LIMIT 5", conn)
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# 财务指标采集函数（摘要级指标，非三大报表明细）
# ---------------------------------------------------------------------------


def get_financial_report(stock_code: str, start_year: str = None) -> pd.DataFrame:
    """
    获取指定公司的财务指标数据（新浪财经）。

    接口: stock_financial_analysis_indicator
    文档: https://akshare.akfamily.xyz/data/stock/stock.html#id204
    特点: 宽表格式，包含 86 项财务指标，每行对应一个报告期。

    参数:
        stock_code: 纯数字股票代码，如 "600519"（贵州茅台）
        start_year: 起始年份字符串，默认为当前年份 - 10

    返回:
        DataFrame，86 列，含 日期、每股收益、净资产收益率、毛利率、资产负债率等

    示例:
        >>> df = get_financial_report("600519", start_year="2016")
    """
    if start_year is None:
        start_year = str(datetime.date.today().year - 10)

    print(f"正在获取 {stock_code} 从 {start_year} 年至今的财务指标数据...")
    df = ak.stock_financial_analysis_indicator(symbol=stock_code, start_year=start_year)
    print(f"共获取 {len(df)} 条记录，{len(df.columns)} 个指标")
    print(f"数据时间范围: {df['日期'].iloc[-1]} ~ {df['日期'].iloc[0]}")
    print()

    # 展示关键财务指标
    key_columns = [
        '日期', '摊薄每股收益(元)', '加权每股收益(元)', '每股净资产_调整前(元)',
        '每股经营性现金流(元)', '净资产收益率(%)', '销售净利率(%)',
        '销售毛利率(%)', '资产负债率(%)', '流动比率', '速动比率',
        '主营业务收入增长率(%)', '净利润增长率(%)', '总资产增长率(%)',
    ]
    available_columns = [col for col in key_columns if col in df.columns]
    print("=== 关键财务指标（新浪财经）===")
    print(df[available_columns].to_string(index=False))

    return df


def get_financial_report_em(stock_code: str, market: str = "sh") -> pd.DataFrame:
    """
    获取 A 股公司的主要财务指标数据（东方财富）。

    接口: stock_financial_analysis_indicator_em
    特点: 宽表格式，包含 140 项指标，返回所有历史数据后筛选近 10 年。
    适用: A 股（market = sh / sz），不适用于港股。

    参数:
        stock_code: 纯数字股票代码，如 "600519"（贵州茅台）
        market:     市场标识，大小写均可（内部自动转大写调接口），如 "sh" / "SH"

    返回:
        DataFrame，140 列，含 REPORT_DATE、EPSJB(每股收益)、ROEJQ(ROE)、XSJLL(净利率) 等

    示例:
        >>> df = get_financial_report_em("600519", market="sh")
    """
    symbol = f"{stock_code}.{market.upper()}"  # 东方财富接口要求大写后缀
    print(f"正在获取 {symbol} 的所有历史主要指标数据（东方财富）...")
    df = ak.stock_financial_analysis_indicator_em(symbol=symbol, indicator="按报告期")

    # 筛选近10年数据
    df['REPORT_DATE'] = pd.to_datetime(df['REPORT_DATE'])
    ten_years_ago = pd.Timestamp.now() - pd.DateOffset(years=10)
    df_filtered = df[df['REPORT_DATE'] >= ten_years_ago].copy()

    print(f"近10年共 {len(df_filtered)} 条记录")
    print()

    # 展示关键指标（字段名含义见下方注释）
    # EPSJB=基本每股收益, EPSKCJB=扣非每股收益, BPS=每股净资产
    # TOTALOPERATEREVE=营业总收入, PARENTNETPROFIT=归属净利润
    # ROEJQ=加权ROE, XSJLL=净利率, XSMLL=毛利率, ZCFZL=资产负债率
    key_cols = [
        'REPORT_DATE', 'SECURITY_NAME_ABBR', 'EPSJB', 'EPSKCJB', 'BPS',
        'TOTALOPERATEREVE', 'PARENTNETPROFIT', 'KCFJCXSYJLR',
        'TOTALOPERATEREVETZ', 'PARENTNETPROFITTZ',
        'ROEJQ', 'ROEKCJQ', 'XSJLL', 'XSMLL', 'ZCFZL', 'LD', 'SD',
    ]
    available_cols = [col for col in key_cols if col in df_filtered.columns]
    print("=== 关键主要指标（东方财富）===")
    print(df_filtered[available_cols].to_string(index=False))

    return df_filtered


# ---------------------------------------------------------------------------
# 三大财务报表采集函数（资产负债表、利润表、现金流量表）
# 三个函数均返回 dict，key 为中文报表名，value 为已筛选近 10 年、含 year/quarter 列的 DataFrame
# ---------------------------------------------------------------------------


def get_financial_statements_sina(stock_code: str, market_prefix: str = "sh") -> dict:
    """
    获取指定公司的三大财务报表（新浪财经）。

    接口: stock_financial_report_sina
    特点: 宽表格式，每行一期报告，字段为中文。

    参数:
        stock_code:    纯数字股票代码，如 "600519"
        market_prefix: 市场前缀（小写），"sh" 上海 / "sz" 深圳

    返回:
        dict，结构为 {"资产负债表": DataFrame, "利润表": DataFrame, "现金流量表": DataFrame}
        每个 DataFrame 含 year(int) 和 quarter(int) 列

    quarter 映射规则:
        报告日 03-31 → quarter=1 (一季度)
        报告日 06-30 → quarter=2 (半年报)
        报告日 09-30 → quarter=3 (三季报)
        报告日 12-31 → quarter=4 (年报)

    示例:
        >>> stmts = get_financial_statements_sina("600519", market_prefix="sh")
        >>> balance_df = stmts["资产负债表"]
    """
    stock = f"{market_prefix}{stock_code}"
    reports = {}

    for report_name in ["资产负债表", "利润表", "现金流量表"]:
        print(f"正在获取 {stock} 的 {report_name}...")
        df = ak.stock_financial_report_sina(stock=stock, symbol=report_name)

        # 解析报告日并筛选近10年数据
        df['报告日'] = pd.to_datetime(df['报告日'], format='%Y%m%d')
        ten_years_ago = pd.Timestamp.now() - pd.DateOffset(years=10)
        df_filtered = df[df['报告日'] >= ten_years_ago].copy()

        # 新增 year / quarter 字段: 03-31->1, 06-30->2, 09-30->3, 12-31->4
        df_filtered['year']    = df_filtered['报告日'].dt.year
        df_filtered['quarter'] = df_filtered['报告日'].dt.month // 3

        reports[report_name] = df_filtered
        print(f"  {report_name}: 近10年共 {len(df_filtered)} 条记录")

    return reports


def get_financial_statements_ths(stock_code: str) -> dict:
    """
    获取指定公司的三大财务报表（同花顺）。

    接口: stock_financial_debt_new_ths / stock_financial_benefit_new_ths / stock_financial_cash_new_ths
    特点: 长表格式，每行一个指标，同一报告日期有多行（metric_name 区分指标）。
          同花顺接口无需传入市场参数，纯代码即可查询。

    参数:
        stock_code: 纯数字股票代码，如 "600519"

    返回:
        dict，结构为 {"资产负债表": DataFrame, "利润表": DataFrame, "现金流量表": DataFrame}
        每个 DataFrame 含 year(int)、quarter(int) 列
        特有字段: report_period("2024-4")、metric_name(指标名)、value(数值)、yoy(同比)

    示例:
        >>> stmts = get_financial_statements_ths("600519")
        >>> income_df = stmts["利润表"]
        >>> revenue = income_df[income_df['metric_name'] == '营业总收入']
    """
    api_map = {
        "资产负债表": ak.stock_financial_debt_new_ths,
        "利润表":     ak.stock_financial_benefit_new_ths,
        "现金流量表": ak.stock_financial_cash_new_ths,
    }
    reports = {}
    current_year = datetime.date.today().year

    for report_name, api_func in api_map.items():
        print(f"正在获取 {stock_code} 的 {report_name}（同花顺）...")
        df = api_func(symbol=stock_code, indicator="按报告期")

        # report_period 格式如 "2024-4"，由此提取 year 和 quarter
        df['year']    = df['report_period'].str.split('-').str[0].astype(int)
        df['quarter'] = df['report_period'].str.split('-').str[1].astype(int)

        # 筛选近10年
        df_filtered = df[df['year'] >= current_year - 10].copy()

        reports[report_name] = df_filtered
        print(f"  {report_name}: 近10年共 {len(df_filtered)} 条记录")

    return reports


def get_financial_statements_em(stock_code: str, market: str = "sh") -> dict:
    """
    获取指定公司的三大财务报表（东方财富）。

    接口: stock_balance_sheet_by_report_em / stock_profit_sheet_by_report_em / stock_cash_flow_sheet_by_report_em
    特点: 宽表格式，每行一期完整报表，列数多达 300+。

    参数:
        stock_code: 纯数字股票代码，如 "600519"
        market:     市场标识，大小写均可（内部转大写拼接为 "SH600519" 调接口）

    返回:
        dict，结构为 {"资产负债表": DataFrame, "利润表": DataFrame, "现金流量表": DataFrame}
        每个 DataFrame 含 year(int)、quarter(int) 列
        东方财富关键字段: REPORT_DATE、SECURITY_CODE、TOTAL_ASSETS、TOTAL_OPERATE_INCOME 等

    示例:
        >>> stmts = get_financial_statements_em("600519", market="sh")
        >>> balance_df = stmts["资产负债表"]
    """
    symbol = f"{market.upper()}{stock_code}"  # 东方财富要求 "SH600519" 格式
    api_map = {
        "资产负债表": ak.stock_balance_sheet_by_report_em,
        "利润表":     ak.stock_profit_sheet_by_report_em,
        "现金流量表": ak.stock_cash_flow_sheet_by_report_em,
    }
    reports = {}
    ten_years_ago = pd.Timestamp.now() - pd.DateOffset(years=10)

    for report_name, api_func in api_map.items():
        print(f"正在获取 {symbol} 的 {report_name}（东方财富）...")
        df = api_func(symbol=symbol)

        # REPORT_DATE 转为日期类型，筛选近10年
        df['REPORT_DATE'] = pd.to_datetime(df['REPORT_DATE'])
        df_filtered = df[df['REPORT_DATE'] >= ten_years_ago].copy()

        # 新增 year / quarter（1-4月->1, 5-8月->2, 9-12月->3... 按报告期月份推算）
        df_filtered['year']    = df_filtered['REPORT_DATE'].dt.year
        df_filtered['quarter'] = df_filtered['REPORT_DATE'].dt.month // 3

        reports[report_name] = df_filtered
        print(f"  {report_name}: 近10年共 {len(df_filtered)} 条记录，{len(df_filtered.columns)} 列")

    return reports


# ---------------------------------------------------------------------------
# 港股财务数据采集函数（仅东方财富有港股接口）
# ---------------------------------------------------------------------------


def get_financial_report_hk(stock_code: str) -> pd.DataFrame:
    """
    获取港股公司的主要财务指标数据（东方财富）。

    接口: stock_financial_hk_analysis_indicator_em
    特点: 宽表格式，包含 36 项指标（BASIC_EPS、OPERATE_INCOME、HOLDER_PROFIT、ROE_AVG 等）。
          返回所有历史数据后筛选近 10 年。
    注意: 港股货币单位可能为 HKD 或 USD，详见 CURRENCY 列。

    参数:
        stock_code: 港股代码，如 "00700"（腾讯控股）、"09988"（阿里巴巴）

    返回:
        DataFrame，36 列，含 REPORT_DATE、BASIC_EPS、BPS、OPERATE_INCOME、HOLDER_PROFIT 等

    示例:
        >>> df = get_financial_report_hk("00700")
    """
    print(f"正在获取港股 {stock_code} 的主要财务指标（东方财富）...")
    df = ak.stock_financial_hk_analysis_indicator_em(symbol=stock_code, indicator="报告期")

    # 筛选近10年数据
    df['REPORT_DATE'] = pd.to_datetime(df['REPORT_DATE'])
    ten_years_ago = pd.Timestamp.now() - pd.DateOffset(years=10)
    df_filtered = df[df['REPORT_DATE'] >= ten_years_ago].copy()

    # 新增 year / quarter
    df_filtered['year']    = df_filtered['REPORT_DATE'].dt.year
    df_filtered['quarter'] = df_filtered['REPORT_DATE'].dt.month // 3

    print(f"近10年共 {len(df_filtered)} 条记录，{len(df_filtered.columns)} 项指标")

    # 展示关键指标
    key_cols = [
        'REPORT_DATE', 'SECURITY_NAME_ABBR', 'BASIC_EPS', 'BPS',
        'OPERATE_INCOME', 'HOLDER_PROFIT', 'GROSS_PROFIT_RATIO',
        'NET_PROFIT_RATIO', 'ROE_AVG', 'ROA', 'DEBT_ASSET_RATIO',
        'CURRENT_RATIO', 'CURRENCY',
    ]
    available_cols = [col for col in key_cols if col in df_filtered.columns]
    print("=== 港股关键指标（东方财富）===")
    print(df_filtered[available_cols].to_string(index=False))
    print()

    return df_filtered


def get_financial_statements_hk(stock_code: str) -> dict:
    """
    获取港股公司的三大财务报表（东方财富）。

    接口: stock_financial_hk_report_em
    特点: 长表格式，每行一个科目（STD_ITEM_NAME）对应一个金额（AMOUNT），
          同一报告日期有多行。返回所有历史数据后筛选近 10 年。

    参数:
        stock_code: 港股代码，如 "00700"（腾讯控股）

    返回:
        dict，结构为 {"资产负债表": DataFrame, "利润表": DataFrame, "现金流量表": DataFrame}
        每个 DataFrame 含 year(int)、quarter(int) 列
        特有字段: STD_ITEM_NAME(科目名)、AMOUNT(金额)、FISCAL_YEAR(年结日)、CURRENCY(货币)

    示例:
        >>> stmts = get_financial_statements_hk("00700")
        >>> income_df = stmts["利润表"]
        >>> revenue = income_df[income_df['STD_ITEM_NAME'] == '营业额']
    """
    reports = {}
    ten_years_ago = pd.Timestamp.now() - pd.DateOffset(years=10)

    for report_name in ["资产负债表", "利润表", "现金流量表"]:
        print(f"正在获取港股 {stock_code} 的 {report_name}（东方财富）...")
        df = ak.stock_financial_hk_report_em(
            stock=stock_code, symbol=report_name, indicator="年度"
        )

        # REPORT_DATE 转为日期类型，筛选近10年
        df['REPORT_DATE'] = pd.to_datetime(df['REPORT_DATE'])
        df_filtered = df[df['REPORT_DATE'] >= ten_years_ago].copy()

        # 新增 year / quarter
        df_filtered['year']    = df_filtered['REPORT_DATE'].dt.year
        df_filtered['quarter'] = df_filtered['REPORT_DATE'].dt.month // 3

        reports[report_name] = df_filtered
        print(f"  {report_name}: 近10年共 {len(df_filtered)} 条记录")

    return reports


# ---------------------------------------------------------------------------
# 主程序入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ====== 配置区域 ======
    # 支持同时采集多只股票，每项为 (股票代码, 市场, 起始年)
    # market 支持: sh=上海, sz=深圳, hk=港股
    # A 股会采集新浪/同花顺/东方财富三个数据源，港股仅采集东方财富
    # 新增股票只需在此列表追加一行即可
    STOCKS = [
        ("600519", "sh", "2016"),   # 贵州茅台（上海）
        ("00700",  "hk", "2016"),   # 腾讯控股（港股）
        ("601600", "sh", "2016"),   # 中国铝业（上海）
        # 继续添加...
    ]
    # ======================

    print("=" * 60)
    print(f"  采集 {len(STOCKS)} 只股票近10年财报数据（A股+港股）")
    print(f"  数据库路径: {DB_PATH}")
    print("=" * 60)
    print()

    conn = get_conn()

    # 报表名称 -> 表名后缀的映射
    TABLE_MAP = {
        "资产负债表": "balance_sheet",
        "利润表":     "income_statement",
        "现金流量表": "cash_flow",
    }

    try:
        for stock_code, market, start_year in STOCKS:
            print()
            print(f"======== 正在采集 {stock_code}.{market} ========")
            print()

            if market.lower() == "hk":
                # ---- 港股流程（仅东方财富数据源）----

                # 1. 东方财富-港股主要指标（36项）
                print("【1】东方财富 - 港股主要指标")
                print("-" * 60)
                hk_df = get_financial_report_hk(stock_code)
                save_to_db(hk_df, "hk_financial_indicator", stock_code, market, conn)
                print()

                # 2. 东方财富-港股三大财务报表（长表格式）
                print("【2】东方财富 - 港股三大财务报表")
                print("-" * 60)
                hk_stmts = get_financial_statements_hk(stock_code)
                for name, df in hk_stmts.items():
                    save_to_db(df, f"hk_{TABLE_MAP[name]}", stock_code, market, conn)

            else:
                # ---- A 股流程（新浪 + 同花顺 + 东方财富三个数据源）----

                # 1. 新浪财经-财务指标（86项指标）
                print("【1】新浪财经 - 财务指标")
                print("-" * 60)
                indicator_df = get_financial_report(stock_code, start_year=start_year)
                save_to_db(indicator_df, "sina_financial_indicator", stock_code, market, conn)
                print()

                # 2. 东方财富-主要指标（140项指标）
                print("【2】东方财富 - 主要指标")
                print("-" * 60)
                em_df = get_financial_report_em(stock_code, market=market)
                save_to_db(em_df, "em_financial_indicator", stock_code, market, conn)
                print()

                # 3. 新浪财经-三大财务报表
                print("【3】新浪财经 - 三大财务报表")
                print("-" * 60)
                sina_stmts = get_financial_statements_sina(stock_code, market_prefix=market)
                for name, df in sina_stmts.items():
                    save_to_db(df, f"sina_{TABLE_MAP[name]}", stock_code, market, conn)
                print()

                # 4. 同花顺-三大财务报表（长表格式）
                print("【4】同花顺 - 三大财务报表")
                print("-" * 60)
                ths_stmts = get_financial_statements_ths(stock_code)
                for name, df in ths_stmts.items():
                    save_to_db(df, f"ths_{TABLE_MAP[name]}", stock_code, market, conn)
                print()

                # 5. 东方财富-三大财务报表（宽表格式）
                print("【5】东方财富 - 三大财务报表")
                print("-" * 60)
                em_stmts = get_financial_statements_em(stock_code, market=market)
                for name, df in em_stmts.items():
                    save_to_db(df, f"em_{TABLE_MAP[name]}", stock_code, market, conn)

        print()
        print("=" * 60)
        print(f"  全部采集完成，数据已存入 {DB_PATH}")
        print("=" * 60)
        print()
        print("数据库表说明：")
        print("  【A股 - 财务指标】")
        print("  sina_financial_indicator  - 新浪财经财务指标（86项）")
        print("  em_financial_indicator    - 东方财富主要指标（140项）")
        print()
        print("  【A股 - 三大财务报表】")
        print("  sina_* / ths_* / em_*     - 新浪/同花顺/东方财富")
        print("  *_balance_sheet / *_income_statement / *_cash_flow")
        print()
        print("  【港股 - 东方财富】")
        print("  hk_financial_indicator    - 港股主要指标（36项）")
        print("  hk_balance_sheet          - 资产负债表（长表，科目+金额）")
        print("  hk_income_statement       - 利润表（长表）")
        print("  hk_cash_flow              - 现金流量表（长表）")
        print()
        print("market 支持: sh / sz / hk，统一小写存储")
        print("year/quarter 统一口径: quarter 1=一季度 2=半年报 3=三季报 4=年报")

    finally:
        conn.close()

    # ========== 读取示例 ==========
    # conn = sqlite3.connect(DB_PATH)
    #
    # # 查询单只股票（小写或大写均可匹配）
    # df = pd.read_sql(
    #     "SELECT * FROM sina_financial_indicator "
    #     "WHERE stock_code='600519' AND market='sh' COLLATE NOCASE",
    #     conn
    # )
    #
    # # 多只股票净利润对比
    # df = pd.read_sql(
    #     "SELECT stock_code, market, 日期, 净资产收益率(%) FROM sina_financial_indicator "
    #     "WHERE stock_code IN ('600519','000858') ORDER BY stock_code, 日期 DESC",
    #     conn
    # )
    #
    # # 查询新浪年报数据（quarter=4）
    # df = pd.read_sql(
    #     "SELECT stock_code, 报告日, year, quarter, * FROM sina_income_statement "
    #     "WHERE stock_code='600519' AND quarter=4 ORDER BY 报告日 DESC",
    #     conn
    # )
    #
    # # 查询同花顺利润表的某个具体指标（长表格式，按 metric_name 筛选）
    # df = pd.read_sql(
    #     "SELECT stock_code, market, year, quarter, metric_name, value "
    #     "FROM ths_income_statement "
    #     "WHERE stock_code='600519' AND metric_name='营业总收入'",
    #     conn
    # )
    #
    # # 查询东方财富资产负债表（宽表格式，直接取列）
    # df = pd.read_sql(
    #     "SELECT stock_code, market, year, quarter, REPORT_DATE, TOTAL_ASSETS "
    #     "FROM em_balance_sheet "
    #     "WHERE stock_code='600519' AND market='SH' COLLATE NOCASE "
    #     "ORDER BY REPORT_DATE DESC",
    #     conn
    # )
    # conn.close()

