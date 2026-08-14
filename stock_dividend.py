"""
stock_dividend.py - A股历史分红数据采集与存储工具

功能概述:
    从新浪财经采集指定 A 股股票的历史分红明细数据，
    统一存入本地 SQLite 数据库，支持按证券代码筛选。

数据源与接口:
    ┌──────────────┬──────────────────────────────────────┬──────────────────────────┐
    │ 数据源       │ akshare 接口                        │ 数据格式                 │
    ├──────────────┼──────────────────────────────────────┼──────────────────────────┤
    │ 新浪财经     │ stock_history_dividend_detail       │ 明细表（逐只股票查询）   │
    └──────────────┴──────────────────────────────────────┴──────────────────────────┘

数据库表结构:
    ┌──────────────────────────┬─────────────────────────────────────────────┐
    │ 表名                      │ 说明                                       │
    ├──────────────────────────┼─────────────────────────────────────────────┤
    │ stock_dividend_detail    │ 指定股票分红明细（公告日期、派息、送股等）   │
    └──────────────────────────┴─────────────────────────────────────────────┘

依赖:
    pip install akshare pandas

使用方法:
    # 命令行直接运行
    python stock_dividend.py

    # 作为模块导入
    from stock_dividend import get_dividend_detail, fetch_dividends, get_conn

API 文档:
    https://akshare.akfamily.xyz/data/stock/stock.html#id238
"""

import time
import sqlite3
from pathlib import Path

import akshare as ak
import pandas as pd

# ---------------------------------------------------------------------------
# 数据库配置
# ---------------------------------------------------------------------------

DB_PATH = str(Path(__file__).parent / "financial_data.db")


# ---------------------------------------------------------------------------
# 数据库工具函数
# ---------------------------------------------------------------------------


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
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def save_to_db(df: pd.DataFrame, table: str, stock_code: str, conn: sqlite3.Connection):
    """
    将 DataFrame 写入 SQLite 指定表（按股票代码幂等更新）。

    行为说明:
        写入前先删除该 stock_code 的旧数据，实现幂等更新；
        表不存在时自动创建（由 pandas to_sql 完成）。

    参数:
        df:         待写入的 DataFrame（已包含"代码"列）
        table:      目标表名
        stock_code: 股票代码，用于删除旧数据
        conn:       SQLite 连接对象

    示例:
        >>> save_to_db(df, "stock_dividend_detail", "600519", conn)
    """
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if cursor.fetchone():
        cursor.execute(f"DELETE FROM {table} WHERE 代码 = ?", (stock_code,))
    df.to_sql(table, conn, if_exists="append", index=False)
    print(f"  -> 已写入 {table} 表，代码={stock_code}，共 {len(df)} 条")


# ---------------------------------------------------------------------------
# 分红数据采集函数
# ---------------------------------------------------------------------------


def get_dividend_detail(stock_code: str, max_retries: int = 3, retry_delay: float = 1.0) -> pd.DataFrame | None:
    """
    获取指定股票的历史分红明细数据（新浪财经）。

    接口: stock_history_dividend_detail
    文档: https://akshare.akfamily.xyz/data/stock/stock.html#id238
    特点: 返回该股票全部历史分红记录（公告日期、送股、转增、派息等）。

    参数:
        stock_code:  纯数字股票代码，如 "600519"
        max_retries: 最大重试次数（默认 3）
        retry_delay: 重试间隔秒数（默认 1.0）

    返回:
        DataFrame，9 列:
        - 代码:        股票代码（由本函数自动插入）
        - 公告日期:    分红公告日期
        - 送股:        每10股送股数（股）
        - 转增:        每10股转增数（股）
        - 派息:        每10股派息金额（元，税前）
        - 进度:        实施状态
        - 除权除息日:  除权除息日期
        - 股权登记日:  股权登记日期
        - 红股上市日:  红股上市日期
        若无分红记录或请求失败则返回 None

    示例:
        >>> df = get_dividend_detail("600519")
    """
    for attempt in range(1, max_retries + 1):
        try:
            df = ak.stock_history_dividend_detail(symbol=stock_code, indicator="分红")
            if df is not None and not df.empty:
                df.insert(0, "代码", stock_code)
                return df
            return None
        except Exception as e:
            if attempt < max_retries:
                time.sleep(retry_delay * attempt)
            else:
                print(f"  [!] {stock_code} 获取分红明细失败（重试 {max_retries} 次）: {e}")
                return None
    return None


def fetch_dividends(stock_codes: list[str], delay: float = 0.5):
    """
    采集指定股票的分红明细数据，并存入数据库。

    流程:
        遍历传入的股票代码列表，逐只调用 stock_history_dividend_detail 获取分红明细，
        每只股票的数据按幂等方式（先删旧数据再追加）写入 stock_dividend_detail 表。

    参数:
        stock_codes: 股票代码列表，如 ["600519", "000858"]
        delay:       每次请求间隔秒数（默认 0.5），避免请求过于频繁被限流

    示例:
        >>> fetch_dividends(["600519", "000858"])
    """
    conn = get_conn()

    try:
        total = len(stock_codes)
        print("=" * 60)
        print(f"  采集 {total} 只股票的分红明细数据")
        print("=" * 60)
        print()

        success_count = 0
        fail_count = 0

        for i, code in enumerate(stock_codes, 1):
            print(f"[{i}/{total}] {code}")
            df = get_dividend_detail(code)
            if df is not None and not df.empty:
                save_to_db(df, "stock_dividend_detail", code, conn)
                success_count += 1
            else:
                print(f"  （无分红记录或获取失败）")
                fail_count += 1

            if i < total:
                time.sleep(delay)

        print()
        print("=" * 60)
        print(f"  采集完成：成功 {success_count} 只，无分红/失败 {fail_count} 只")
        print(f"  数据已存入 {DB_PATH}")
        print("=" * 60)

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 主程序入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ====== 配置区域 ======
    # 在此处指定需要采集分红数据的股票代码列表
    # 新增股票只需在此列表追加一行即可
    STOCKS = [
        "600519",   # 贵州茅台
        "000858",   # 五粮液
        # 继续添加...
    ]
    # ======================

    print("=" * 60)
    print(f"  A股历史分红数据采集")
    print(f"  数据库路径: {DB_PATH}")
    print("=" * 60)
    print()

    fetch_dividends(STOCKS)

    # ========== 读取示例 ==========
    # conn = sqlite3.connect(DB_PATH)
    #
    # # 查询指定股票的分红明细
    # df = pd.read_sql(
    #     "SELECT * FROM stock_dividend_detail "
    #     "WHERE 代码='600519' ORDER BY 公告日期 DESC",
    #     conn
    # )
    # print("贵州茅台分红明细:")
    # print(df.to_string(index=False))
    #
    # # 统计每只股票的分红次数
    # df = pd.read_sql(
    #     "SELECT 代码, COUNT(*) AS 分红次数 FROM stock_dividend_detail "
    #     "GROUP BY 代码 ORDER BY 分红次数 DESC LIMIT 20",
    #     conn
    # )
    # print("\n分红次数 Top 20:")
    # print(df.to_string(index=False))
    #
    # conn.close()
