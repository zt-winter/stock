"""
cyclical_stock_analysis.py - 周期股分析工具

功能:
    1. 从百度股市通获取近10年 PE(TTM) 和市净率历史数据
    2. 从 financial_data.db 获取近10年季度利润、经营现金流、存货、应付等数据
    3. 分析 PE/PB 周期反转现象（周期底部高 PE，顶部低 PE）
    4. 识别库存周期四阶段（上升/扩张/收缩/衰退）

使用方法:
    # 仅获取估值数据（PE/PB）
    python scripts/cyclical_stock_analysis.py valuation --code 600519

    # 获取估值 + 季度财务数据
    python scripts/cyclical_stock_analysis.py full --code 600519 --market sh

    # 获取估值 + 季度财务 + 库存周期分析
    python scripts/cyclical_stock_analysis.py inventory --code 600519 --market sh

    # 指定数据库路径
    python scripts/cyclical_stock_analysis.py inventory --code 600519 --market sh --db /path/to/db

输出:
    - 季度末 PE(TTM)、PB 值及历史分位统计
    - 每季度归母净利润（TTM）和经营现金流（TTM）
    - 存货周期四阶段识别（库存量、毛利率、应付账款、预收/合同负债）
    - PE/PB 反转分析（周期底部高PE、顶部低PE 现象）

依赖:
    pip install akshare pandas
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

# 默认数据库路径: 不依赖脚本所在目录的固定层级，依次尝试
# FINANCIAL_DATA_DIR 环境变量 > 当前工作目录 > 向上查找含 financial_data.db 的目录
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

DB_PATH = str(Path(_resolve_data_dir()) / "financial_data.db")


def safe_float(v, default=0.0):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# 1. 估值数据采集（PE/PB）- 支持数据库缓存
# ---------------------------------------------------------------------------

VALUATION_CACHE_TABLE = "stock_valuation_history"
VALUATION_CACHE_DAYS = 7  # 缓存有效期（天）


def _ensure_valuation_table(db_path: str):
    """确保估值缓存表存在。"""
    conn = sqlite3.connect(db_path)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {VALUATION_CACHE_TABLE} (
            stock_code TEXT,
            date TEXT,
            pe_ttm REAL,
            pb REAL,
            updated_at TEXT,
            PRIMARY KEY (stock_code, date)
        )
    """)
    conn.close()


def _load_valuation_cache(stock_code: str, db_path: str) -> pd.DataFrame:
    """从数据库加载估值缓存，检查是否新鲜。"""
    _ensure_valuation_table(db_path)
    conn = sqlite3.connect(db_path)
    
    # 检查最近更新时间
    cur = conn.execute(
        f"SELECT MAX(updated_at) FROM {VALUATION_CACHE_TABLE} WHERE stock_code=?",
        (stock_code,)
    )
    row = cur.fetchone()
    last_update = row[0] if row and row[0] else None
    
    if last_update:
        from datetime import datetime, timedelta
        last_dt = datetime.fromisoformat(last_update)
        if datetime.now() - last_dt < timedelta(days=VALUATION_CACHE_DAYS):
            # 缓存有效，加载数据
            df = pd.read_sql(
                f"SELECT date, pe_ttm, pb FROM {VALUATION_CACHE_TABLE} WHERE stock_code=? ORDER BY date",
                conn, params=(stock_code,)
            )
            df["date"] = pd.to_datetime(df["date"])
            conn.close()
            print(f"[缓存] 从数据库加载 {stock_code} 估值数据（{len(df)} 条，更新于 {last_dt.strftime('%Y-%m-%d')}）")
            return df
    
    conn.close()
    return pd.DataFrame()


def _save_valuation_cache(stock_code: str, df: pd.DataFrame, db_path: str):
    """保存估值数据到数据库缓存。"""
    _ensure_valuation_table(db_path)
    conn = sqlite3.connect(db_path)
    
    from datetime import datetime
    updated_at = datetime.now().isoformat()
    
    # 删除旧数据
    conn.execute(f"DELETE FROM {VALUATION_CACHE_TABLE} WHERE stock_code=?", (stock_code,))
    
    # 插入新数据
    for _, row in df.iterrows():
        conn.execute(
            f"INSERT OR REPLACE INTO {VALUATION_CACHE_TABLE} (stock_code, date, pe_ttm, pb, updated_at) VALUES (?, ?, ?, ?, ?)",
            (stock_code, row["date"].strftime("%Y-%m-%d"), row["pe_ttm"], row["pb"], updated_at)
        )
    
    conn.commit()
    conn.close()
    print(f"[缓存] 已保存 {stock_code} 估值数据到数据库（{len(df)} 条）")


def fetch_valuation(stock_code: str, db_path: str = DB_PATH, refresh: bool = False) -> pd.DataFrame:
    """
    获取近10年 PE(TTM) 和市净率历史数据。

    优先从数据库缓存读取，若缓存过期（>7天）或不存在则从百度股市通获取。
    
    参数:
        stock_code: 股票代码
        db_path: 数据库路径
        refresh: 是否强制刷新（忽略缓存）
    
    返回 DataFrame，列: date, pe_ttm, pb
    数据频率约每5天一个点。
    """
    import akshare as ak
    
    # 尝试从缓存加载
    if not refresh:
        cached = _load_valuation_cache(stock_code, db_path)
        if not cached.empty:
            return cached
    
    # 从 API 获取
    print(f"正在获取 {stock_code} 的 PE(TTM) 历史数据...")
    pe_df = ak.stock_zh_valuation_baidu(
        symbol=stock_code, indicator="市盈率(TTM)", period="近十年"
    )
    pe_df.columns = ["date", "pe_ttm"]
    pe_df["date"] = pd.to_datetime(pe_df["date"])

    print(f"正在获取 {stock_code} 的 PB 历史数据...")
    pb_df = ak.stock_zh_valuation_baidu(
        symbol=stock_code, indicator="市净率", period="近十年"
    )
    pb_df.columns = ["date", "pb"]
    pb_df["date"] = pd.to_datetime(pb_df["date"])

    # 合并 PE 和 PB
    merged = pd.merge(pe_df, pb_df, on="date", how="outer").sort_values("date").reset_index(drop=True)
    merged["pe_ttm"] = pd.to_numeric(merged["pe_ttm"], errors="coerce")
    merged["pb"] = pd.to_numeric(merged["pb"], errors="coerce")

    print(f"共获取 {len(merged)} 条估值记录，时间范围: {merged['date'].iloc[0].strftime('%Y-%m-%d')} ~ {merged['date'].iloc[-1].strftime('%Y-%m-%d')}")
    
    # 保存到缓存
    _save_valuation_cache(stock_code, merged, db_path)
    
    return merged


def resample_quarter(val_df: pd.DataFrame) -> pd.DataFrame:
    """
    将估值数据重采样为季度末值。
    """
    val_df = val_df.set_index("date")
    q_pe = val_df["pe_ttm"].resample("QE").last()
    q_pb = val_df["pb"].resample("QE").last()
    q_df = pd.DataFrame({"date": q_pe.index, "pe_ttm": q_pe.values, "pb": q_pb.values})
    q_df = q_df.dropna(subset=["pe_ttm", "pb"], how="all").reset_index(drop=True)
    return q_df


# ---------------------------------------------------------------------------
# 2. 季度财务数据（利润 + 经营现金流）
# ---------------------------------------------------------------------------

def fetch_quarterly_financials(stock_code: str, market: str,
                               db_path: str = DB_PATH) -> pd.DataFrame:
    """
    从 financial_data.db 获取季度利润和经营现金流数据。

    返回 DataFrame，列:
        year, quarter, report_date, net_profit, parent_net_profit,
        deduct_parent_net_profit, netcash_operate,
        total_operate_income, total_assets, total_equity
    """
    conn = sqlite3.connect(db_path)

    # 检查表是否存在
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='em_income_statement'")
    if not cur.fetchone():
        print(f"警告: 数据库 {db_path} 中未找到 em_income_statement 表")
        print("请先通过 financial_report.py 采集数据")
        conn.close()
        return pd.DataFrame()

    # 利润表数据
    income_sql = """
        SELECT stock_code, year, quarter, REPORT_DATE,
               NETPROFIT, PARENT_NETPROFIT, DEDUCT_PARENT_NETPROFIT,
               TOTAL_OPERATE_INCOME, OPERATE_INCOME, OPERATE_COST
        FROM em_income_statement
        WHERE stock_code = ? AND market = ?
        ORDER BY year, quarter
    """
    income_df = pd.read_sql(income_sql, conn, params=(stock_code, market.lower()))

    # 现金流量表数据
    cash_sql = """
        SELECT stock_code, year, quarter, REPORT_DATE,
               NETCASH_OPERATE, TOTAL_OPERATE_INFLOW, TOTAL_OPERATE_OUTFLOW,
               INVENTORY_REDUCE, OPERATE_RECE_REDUCE, OPERATE_PAYABLE_ADD
        FROM em_cash_flow
        WHERE stock_code = ? AND market = ?
        ORDER BY year, quarter
    """
    cash_df = pd.read_sql(cash_sql, conn, params=(stock_code, market.lower()))

    # 资产负债表数据（取期末总资产、净资产、存货、应付账款、预收/合同负债）
    balance_sql = """
        SELECT stock_code, year, quarter, REPORT_DATE,
               TOTAL_ASSETS, TOTAL_EQUITY, TOTAL_PARENT_EQUITY,
               INVENTORY, ACCOUNTS_PAYABLE, NOTE_PAYABLE,
               ADVANCE_RECEIVABLES, CONTRACT_LIAB, PREPAYMENT
        FROM em_balance_sheet
        WHERE stock_code = ? AND market = ?
        ORDER BY year, quarter
    """
    balance_df = pd.read_sql(balance_sql, conn, params=(stock_code, market.lower()))

    conn.close()

    if income_df.empty and cash_df.empty:
        print(f"警告: 数据库中未找到 {stock_code}.{market} 的财报数据")
        print("请先通过 financial_report.py 采集数据")
        return pd.DataFrame()

    # 合并三表
    key_cols = ["stock_code", "year", "quarter"]
    result = income_df

    if not cash_df.empty:
        cash_cols = key_cols + [c for c in cash_df.columns if c not in key_cols and c != "REPORT_DATE"]
        result = pd.merge(result, cash_df[cash_cols], on=key_cols, how="outer")

    if not balance_df.empty:
        bal_cols = key_cols + [c for c in balance_df.columns if c not in key_cols and c != "REPORT_DATE"]
        result = pd.merge(result, balance_df[bal_cols], on=key_cols, how="outer")

    # 确保数值列
    num_cols = ["NETPROFIT", "PARENT_NETPROFIT", "DEDUCT_PARENT_NETPROFIT",
                "NETCASH_OPERATE", "TOTAL_OPERATE_INCOME", "TOTAL_ASSETS", "TOTAL_EQUITY",
                "OPERATE_INCOME", "OPERATE_COST",
                "INVENTORY", "ACCOUNTS_PAYABLE", "NOTE_PAYABLE",
                "ADVANCE_RECEIVABLES", "CONTRACT_LIAB", "PREPAYMENT",
                "INVENTORY_REDUCE", "OPERATE_RECE_REDUCE", "OPERATE_PAYABLE_ADD"]
    for col in num_cols:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    result = result.sort_values(["year", "quarter"]).reset_index(drop=True)

    print(f"共获取 {len(result)} 个季度的财务数据")
    return result


def compute_ttm(df: pd.DataFrame, col: str) -> pd.Series:
    """
    计算滚动 TTM（Trailing Twelve Months）值。
    对于年报(quarter=4)直接使用当年值，其他季度用 当前累计 + 去年年报 - 去年同期累计。
    """
    if col not in df.columns:
        return pd.Series(dtype=float)

    ttm = pd.Series(index=df.index, dtype=float)

    for i, row in df.iterrows():
        q = int(row["quarter"])
        y = int(row["year"])

        if q == 4:
            ttm.iloc[i] = row[col]
        else:
            # 找去年年报
            prev_annual = df[(df["year"] == y - 1) & (df["quarter"] == 4)]
            # 找去年同期
            prev_same = df[(df["year"] == y - 1) & (df["quarter"] == q)]

            if not prev_annual.empty and not prev_same.empty:
                ttm.iloc[i] = row[col] + prev_annual[col].iloc[0] - prev_same[col].iloc[0]
            elif not prev_annual.empty:
                ttm.iloc[i] = row[col]  # 无法精确计算 TTM，用累计值近似
            else:
                ttm.iloc[i] = float("nan")

    return ttm


# ---------------------------------------------------------------------------
# 2.5 库存周期分析
# ---------------------------------------------------------------------------

def compute_single_quarter(df: pd.DataFrame, col: str) -> pd.Series:
    """
    将累计利润表字段转换为单季度值。
    Q1 直接用累计值，Q2/Q3/Q4 用当前累计 - 上季累计。
    """
    if col not in df.columns:
        return pd.Series(dtype=float)
    single = pd.Series(index=df.index, dtype=float)
    for i, row in df.iterrows():
        q = int(row["quarter"])
        y = int(row["year"])
        if q == 1:
            single.iloc[i] = row[col] if pd.notna(row[col]) else float("nan")
        else:
            prev = df[(df["year"] == y) & (df["quarter"] == q - 1)]
            if not prev.empty and pd.notna(prev[col].iloc[0]) and pd.notna(row[col]):
                single.iloc[i] = row[col] - prev[col].iloc[0]
            else:
                single.iloc[i] = float("nan")
    return single


def analyze_inventory_cycle(financials: pd.DataFrame) -> pd.DataFrame:
    """
    基于财报数据分析库存周期四阶段。

    库存周期四阶段特征:
        阶段1-上升期: 库存下降 + 毛利率上升（供不应求，去库提价）
        阶段2-扩张期: 库存上升 + 毛利率上升/平稳（扩产补库，价格高位）
        阶段3-收缩期: 库存上升 + 毛利率下降（供过于求，价格下跌但惯性生产）
        阶段4-衰退期: 库存下降 + 毛利率下降（减产去库，价格下跌）

    返回 DataFrame 增加列: gross_margin, gross_margin_qoq, inventory_qoq,
        advance_qoq, phase, phase_name
    """
    df = financials.copy()

    # 单季度营业收入和成本
    df["revenue_q"] = compute_single_quarter(df, "OPERATE_INCOME")
    df["cost_q"] = compute_single_quarter(df, "OPERATE_COST")

    # 毛利率 = 1 - cost/revenue
    df["gross_margin"] = 1 - df["cost_q"] / df["revenue_q"]
    df.loc[df["revenue_q"] == 0, "gross_margin"] = float("nan")

    # 毛利率环比变化（用移动平均平滑季度波动）
    # 先算 4季度滚动平均毛利率，再看环比
    df["gm_4q_avg"] = df["gross_margin"].rolling(4, min_periods=2).mean()
    df["gross_margin_qoq"] = df["gm_4q_avg"].diff()

    # 存货环比变化（资产负债表是时点数，直接 diff）
    if "INVENTORY" in df.columns:
        df["inventory_qoq"] = df["INVENTORY"].diff()
        df["inventory_yoy"] = df["INVENTORY"].pct_change(4)  # 同比变化
    else:
        df["inventory_qoq"] = float("nan")
        df["inventory_yoy"] = float("nan")

    # 预收款/合同负债环比（下游需求代理指标）
    # 2020年前用 ADVANCE_RECEIVABLES，2020年后用 CONTRACT_LIAB
    if "ADVANCE_RECEIVABLES" in df.columns and "CONTRACT_LIAB" in df.columns:
        df["advance_total"] = df["ADVANCE_RECEIVABLES"].fillna(0) + df["CONTRACT_LIAB"].fillna(0)
    elif "ADVANCE_RECEIVABLES" in df.columns:
        df["advance_total"] = df["ADVANCE_RECEIVABLES"]
    elif "CONTRACT_LIAB" in df.columns:
        df["advance_total"] = df["CONTRACT_LIAB"]
    else:
        df["advance_total"] = float("nan")
    df["advance_qoq"] = df["advance_total"].diff()

    # 应付账款环比（下游承销商/供应商的货款）
    if "ACCOUNTS_PAYABLE" in df.columns:
        df["ap_qoq"] = df["ACCOUNTS_PAYABLE"].diff()
    else:
        df["ap_qoq"] = float("nan")

    # --- 库存周期阶段判断 ---
    # 基于: 存货环比方向 + 毛利率趋势
    # inv_dir: +1=库存上升, -1=库存下降, 0=持平
    # gm_dir: +1=毛利率上升, -1=毛利率下降, 0=持平
    phases = []
    phase_names = []

    for i, row in df.iterrows():
        inv_chg = row.get("inventory_qoq", float("nan"))
        gm_chg = row.get("gross_margin_qoq", float("nan"))

        if pd.isna(inv_chg) or pd.isna(gm_chg):
            phases.append(0)
            phase_names.append("")
            continue

        # 设定阈值避免微小波动干扰
        inv_threshold = abs(row.get("INVENTORY", 1)) * 0.005  # 0.5% 的存货作为阈值
        gm_threshold = 0.003  # 毛利率变化 0.3 个百分点

        inv_up = inv_chg > inv_threshold
        inv_down = inv_chg < -inv_threshold
        gm_up = gm_chg > gm_threshold
        gm_down = gm_chg < -gm_threshold

        if inv_down and gm_up:
            phases.append(1)
            phase_names.append("1-\u4e0a\u5347")    # 1-上升
        elif inv_up and (gm_up or not gm_down):
            phases.append(2)
            phase_names.append("2-\u6269\u5f20")    # 2-扩张
        elif inv_up and gm_down:
            phases.append(3)
            phase_names.append("3-\u6536\u7f29")    # 3-收缩
        elif inv_down and gm_down:
            phases.append(4)
            phase_names.append("4-\u8870\u9000")    # 4-衰退
        elif inv_down and not gm_up and not gm_down:
            phases.append(1)  # 库存下降但价格平稳，归入上升期
            phase_names.append("1-\u4e0a\u5347")
        elif inv_up and not gm_up and not gm_down:
            phases.append(2)  # 库存上升但价格平稳，归入扩张期
            phase_names.append("2-\u6269\u5f20")
        else:
            phases.append(0)
            phase_names.append("-")

    df["phase"] = phases
    df["phase_name"] = phase_names

    return df


# ---------------------------------------------------------------------------
# 3. 合并输出
# ---------------------------------------------------------------------------

def merge_analysis(valuation_q: pd.DataFrame,
                   financials: pd.DataFrame) -> pd.DataFrame:
    """
    将季度估值与季度财务数据合并为分析表。
    """
    if financials.empty:
        return valuation_q

    # 计算 TTM 指标
    financials["net_profit_ttm"] = compute_ttm(financials, "NETPROFIT")
    financials["parent_profit_ttm"] = compute_ttm(financials, "PARENT_NETPROFIT")
    financials["operate_cashflow_ttm"] = compute_ttm(financials, "NETCASH_OPERATE")

    # 选取关键列（包含库存周期相关字段）
    fin_cols = ["year", "quarter"]
    for c in ["parent_profit_ttm", "operate_cashflow_ttm", "TOTAL_ASSETS", "TOTAL_EQUITY",
              "INVENTORY", "ACCOUNTS_PAYABLE", "ADVANCE_RECEIVABLES", "CONTRACT_LIAB",
              "PREPAYMENT", "OPERATE_INCOME", "OPERATE_COST",
              "INVENTORY_REDUCE", "OPERATE_PAYABLE_ADD"]:
        if c in financials.columns:
            fin_cols.append(c)

    fin_subset = financials[fin_cols].copy()

    # 估值表添加 year/quarter
    valuation_q["year"] = valuation_q["date"].dt.year
    valuation_q["quarter"] = valuation_q["date"].dt.quarter

    # 合并（按 year+quarter 匹配）
    result = pd.merge(valuation_q, fin_subset, on=["year", "quarter"], how="left")
    return result


# ---------------------------------------------------------------------------
# 4. 输出展示
# ---------------------------------------------------------------------------

def print_analysis(df: pd.DataFrame, stock_code: str):
    """打印分析表格"""
    sep = "=" * 80

    print()
    print(sep)
    print(f"  周期股分析 - {stock_code} 季度估值与财务数据")
    print(sep)

    if "pe_ttm" in df.columns:
        print(f"\n  估值统计（近10年）:")
        pe = df["pe_ttm"].dropna()
        pb = df["pb"].dropna()
        if len(pe) > 0:
            print(f"    PE(TTM): 最低 {pe.min():.2f}  中位数 {pe.median():.2f}  最高 {pe.max():.2f}  当前 {pe.iloc[-1]:.2f}")
        if len(pb) > 0:
            print(f"    PB:      最低 {pb.min():.2f}  中位数 {pb.median():.2f}  最高 {pb.max():.2f}  当前 {pb.iloc[-1]:.2f}")

    # 打印季度明细表
    print(f"\n  季度明细:")
    print(f"  {'日期':<12} {'PE(TTM)':>10} {'PB':>8} {'归母净利润TTM(亿)':>18} {'经营现金流TTM(亿)':>18}")
    print(f"  {'-'*12} {'-'*10} {'-'*8} {'-'*18} {'-'*18}")

    for _, row in df.iterrows():
        date_str = row.get("date", "")
        if hasattr(date_str, "strftime"):
            date_str = date_str.strftime("%Y-%m-%d")
        else:
            date_str = str(date_str)[:10]

        pe_str = f"{row['pe_ttm']:.2f}" if pd.notna(row.get("pe_ttm")) else "N/A"
        pb_str = f"{row['pb']:.2f}" if pd.notna(row.get("pb")) else "N/A"

        profit = row.get("parent_profit_ttm")
        profit_str = f"{profit / 1e8:.2f}" if pd.notna(profit) else "N/A"

        cf = row.get("operate_cashflow_ttm")
        cf_str = f"{cf / 1e8:.2f}" if pd.notna(cf) else "N/A"

        print(f"  {date_str:<12} {pe_str:>10} {pb_str:>8} {profit_str:>18} {cf_str:>18}")

    print()
    print(sep)



def print_cyclical_confirmation(financials: pd.DataFrame, merged: pd.DataFrame, stock_code: str):
    """[章节1] 周期股确认: 验证营收和利润的周期性波动"""
    line = "-" * 90
    
    print()
    print("[1] 周期股确认")
    print(line)
    print("  判断标准: 营收/利润是否存在3-5年的大周期波动，毛利率是否大幅震荡")
    print()
    
    if "parent_profit_ttm" not in merged.columns:
        print("  （无TTM利润数据，无法判断）")
        return False
    
    # 按年汇总归母净利润TTM（取每年最后一个季度）
    annual = merged.dropna(subset=["parent_profit_ttm"]).copy()
    if annual.empty:
        print("  （无有效利润数据）")
        return False
    
    annual_data = annual.groupby(annual["date"].dt.year).last()
    
    profits = annual_data["parent_profit_ttm"].values
    years = annual_data.index.values
    
    if len(profits) < 3:
        print("  （数据不足3年，无法判断周期性）")
        return False
    
    # 计算波动性
    import numpy as np
    mean_profit = np.nanmean(profits)
    std_profit = np.nanstd(profits)
    cv = abs(std_profit / mean_profit) if mean_profit != 0 else float("inf")
    
    # 寻找峰值和谷值
    peaks = []
    troughs = []
    for i in range(1, len(profits) - 1):
        if profits[i] > profits[i-1] and profits[i] > profits[i+1]:
            peaks.append((years[i], profits[i]))
        elif profits[i] < profits[i-1] and profits[i] < profits[i+1]:
            troughs.append((years[i], profits[i]))
    
    # 检查首尾是否也是极值
    if len(profits) >= 2:
        if profits[0] > profits[1]:
            peaks.insert(0, (years[0], profits[0]))
        elif profits[0] < profits[1]:
            troughs.insert(0, (years[0], profits[0]))
        if profits[-1] > profits[-2]:
            peaks.append((years[-1], profits[-1]))
        elif profits[-1] < profits[-2]:
            troughs.append((years[-1], profits[-1]))
    
    print(f"  年度归母净利润TTM走势:")
    print(f"  {'年份':>6}  {'归母净利润TTM(亿)':>18}  {'趋势':>6}")
    print(f"  {'----':>6}  {'----------------':>18}  {'----':>6}")
    for i, (y, p) in enumerate(zip(years, profits)):
        trend = ""
        if i > 0:
            if p > profits[i-1] * 1.1:
                trend = "↑"
            elif p < profits[i-1] * 0.9:
                trend = "↓"
            else:
                trend = "→"
        print(f"  {int(y):>6}  {p/1e8:>18.2f}  {trend:>6}")
    
    print()
    print(f"  利润波动系数(CV): {cv:.2f}  （>0.5为强周期, >1.0为极强周期）")
    print(f"  峰值数量: {len(peaks)}个  谷值数量: {len(troughs)}个")
    
    if len(peaks) >= 1 and len(troughs) >= 1:
        peak_avg = np.mean([p for _, p in peaks])
        trough_avg = np.mean([p for _, p in troughs])
        ratio = peak_avg / abs(trough_avg) if trough_avg != 0 else float("inf")
        print(f"  峰值均值/谷值均值: {ratio:.1f}倍  （>2倍表明周期波动显著）")
    
    is_cyclical = cv > 0.5 and len(peaks) >= 1 and len(troughs) >= 1
    print()
    if is_cyclical:
        print(f"  结论: {stock_code} 利润波动剧烈（CV={cv:.2f}），符合周期股特征")
    else:
        print(f"  结论: {stock_code} 利润波动较温和（CV={cv:.2f}），周期特征不明显")
    
    return is_cyclical


def print_profit_cycle(merged: pd.DataFrame, stock_code: str):
    """[章节3] 利润周期分析: 识别3-5年的盈利大周期"""
    line = "-" * 90
    
    print()
    print("[3] 利润周期分析")
    print(line)
    print("  周期股盈利特征: 3-5年一个大周期，利润从低谷到高峰再回落")
    print()
    
    if "parent_profit_ttm" not in merged.columns:
        print("  （无TTM利润数据）")
        return
    
    valid = merged.dropna(subset=["parent_profit_ttm", "date"]).copy()
    if valid.empty:
        print("  （无有效数据）")
        return
    
    # 按季度展示利润走势
    print(f"  {'日期':<12} {'归母净利润TTM(亿)':>18} {'PE(TTM)':>10} {'盈利状态':>10}")
    print(f"  {'-'*12} {'-'*18} {'-'*10} {'-'*10}")
    
    for _, row in valid.iterrows():
        d = row["date"]
        d_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        profit = row["parent_profit_ttm"]
        pe = row.get("pe_ttm", float("nan"))
        pe_str = f"{pe:.1f}" if pd.notna(pe) else "N/A"
        status = "盈利" if profit > 0 else "亏损"
        print(f"  {d_str:<12} {profit/1e8:>18.2f} {pe_str:>10} {status:>10}")
    
    # 识别周期阶段
    profits = valid["parent_profit_ttm"].values
    dates = valid["date"].values
    
    print()
    # 找利润最高点
    max_idx = profits.argmax()
    min_idx = profits.argmin()
    max_d = pd.Timestamp(dates[max_idx])
    min_d = pd.Timestamp(dates[min_idx])
    
    print(f"  利润最高: {max_d.strftime('%Y-%m')}  归母净利润TTM={profits[max_idx]/1e8:.2f}亿")
    print(f"  利润最低: {min_d.strftime('%Y-%m')}  归母净利润TTM={profits[min_idx]/1e8:.2f}亿")
    
    # 当前处于周期什么位置
    recent = profits[-4:]  # 最近4个季度
    if len(recent) >= 2:
        if all(recent[i] <= recent[i+1] for i in range(len(recent)-1)):
            pos = "利润持续改善，可能处于周期上行阶段"
        elif all(recent[i] >= recent[i+1] for i in range(len(recent)-1)):
            pos = "利润持续下滑，可能处于周期下行阶段"
        else:
            pos = "利润波动，周期位置待确认"
        print(f"  近4季度趋势: {pos}")
    
    # 当前利润相对历史分位
    pctl = (profits < profits[-1]).sum() / len(profits) * 100
    print(f"  当前利润历史分位: {pctl:.0f}%  （0%=历史最低, 100%=历史最高）")
    print()


def print_cashflow_verification(merged: pd.DataFrame, stock_code: str):
    """[章节4] 现金流质量验证"""
    line = "-" * 90
    
    print()
    print("[4] 现金流质量验证")
    print(line)
    print("  核心指标: 经营现金流/净利润 > 1 表示利润有真金白银支撑")
    print()
    
    if "parent_profit_ttm" not in merged.columns or "operate_cashflow_ttm" not in merged.columns:
        print("  （缺少利润或现金流数据）")
        return
    
    valid = merged.dropna(subset=["parent_profit_ttm", "operate_cashflow_ttm", "date"]).copy()
    if valid.empty:
        print("  （无有效数据）")
        return
    
    print(f"  {'日期':<12} {'净利润TTM(亿)':>14} {'经营现金流TTM(亿)':>18} {'现金流/利润':>12} {'质量':>8}")
    print(f"  {'-'*12} {'-'*14} {'-'*18} {'-'*12} {'-'*8}")
    
    ratios = []
    for _, row in valid.iterrows():
        d = row["date"]
        d_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        profit = row["parent_profit_ttm"]
        ocf = row["operate_cashflow_ttm"]
        
        if profit > 0:
            ratio = ocf / profit
            ratio_str = f"{ratio:.2f}x"
            quality = "优" if ratio >= 1.0 else "良" if ratio >= 0.7 else "差"
            ratios.append(ratio)
        else:
            ratio_str = "N/A"
            quality = "-"
        
        print(f"  {d_str:<12} {profit/1e8:>14.2f} {ocf/1e8:>18.2f} {ratio_str:>12} {quality:>8}")
    
    if ratios:
        avg_ratio = sum(ratios) / len(ratios)
        good_count = sum(1 for r in ratios if r >= 1.0)
        print()
        print(f"  平均现金流/利润比: {avg_ratio:.2f}x")
        print(f"  现金流覆盖利润(≥1.0x)的季度数: {good_count}/{len(ratios)}")
        if avg_ratio >= 1.0:
            print(f"  评价: 利润质量高，经营现金流充裕")
        elif avg_ratio >= 0.7:
            print(f"  评价: 利润质量尚可，现金流基本匹配")
        else:
            print(f"  评价: 利润质量偏低，需关注应收账款和存货占用")
    print()


def print_comprehensive_judgment(financials: pd.DataFrame, merged: pd.DataFrame, 
                                  stock_code: str, is_cyclical: bool):
    """[章节7] 综合判断: 汇总所有维度给出周期位置判断"""
    line = "-" * 90
    
    print()
    print("[7] 综合判断")
    print(line)
    print()
    
    signals = []
    
    # 信号1: PE分位
    if "pe_ttm" in merged.columns:
        pe = merged["pe_ttm"].dropna()
        if len(pe) > 10:
            current_pe = pe.iloc[-1]
            pctl = (pe < current_pe).sum() / len(pe) * 100
            if pctl > 70:
                signals.append(("PE分位", f"{pctl:.0f}%（偏高）", "偏顶部/利润较好"))
            elif pctl < 30:
                signals.append(("PE分位", f"{pctl:.0f}%（偏低）", "偏底部/利润较好或估值低"))
            else:
                signals.append(("PE分位", f"{pctl:.0f}%（中位）", "中性"))
    
    # 信号2: PB分位
    if "pb" in merged.columns:
        pb = merged["pb"].dropna()
        if len(pb) > 10:
            current_pb = pb.iloc[-1]
            pctl = (pb < current_pb).sum() / len(pb) * 100
            if pctl > 70:
                signals.append(("PB分位", f"{pctl:.0f}%（偏高）", "估值偏高"))
            elif pctl < 30:
                signals.append(("PB分位", f"{pctl:.0f}%（偏低）", "估值偏低"))
            else:
                signals.append(("PB分位", f"{pctl:.0f}%（中位）", "中性"))
    
    # 信号3: 利润趋势
    if "parent_profit_ttm" in merged.columns:
        valid = merged.dropna(subset=["parent_profit_ttm"])
        if len(valid) >= 4:
            recent_4 = valid["parent_profit_ttm"].tail(4).values
            if all(recent_4[i] <= recent_4[i+1] for i in range(len(recent_4)-1)):
                signals.append(("利润趋势", "连续4季改善", "上行"))
            elif all(recent_4[i] >= recent_4[i+1] for i in range(len(recent_4)-1)):
                signals.append(("利润趋势", "连续4季下滑", "下行"))
            else:
                signals.append(("利润趋势", "波动", "待确认"))
    
    # 信号4: 库存周期阶段
    if "phase_name" in financials.columns:
        last_phase = financials["phase_name"].iloc[-1]
        if last_phase and last_phase != "-":
            signals.append(("库存周期", last_phase, "当前所处阶段"))
    
    # 信号5: 现金流质量
    if "operate_cashflow_ttm" in merged.columns and "parent_profit_ttm" in merged.columns:
        valid = merged.dropna(subset=["operate_cashflow_ttm", "parent_profit_ttm"])
        if len(valid) >= 4:
            recent = valid.tail(4)
            ratios = []
            for _, row in recent.iterrows():
                if row["parent_profit_ttm"] > 0:
                    ratios.append(row["operate_cashflow_ttm"] / row["parent_profit_ttm"])
            if ratios:
                avg = sum(ratios) / len(ratios)
                signals.append(("现金流/利润", f"{avg:.2f}x", "优" if avg >= 1.0 else "良" if avg >= 0.7 else "差"))
    
    print(f"  {'信号维度':<14} {'当前状态':<20} {'周期含义':<20}")
    print(f"  {'-'*14} {'-'*20} {'-'*20}")
    for dim, status, meaning in signals:
        print(f"  {dim:<14} {status:<20} {meaning:<20}")
    
    print()
    
    # 综合判断
    if is_cyclical:
        print(f"  综合结论: {stock_code} 是典型周期股")
    else:
        print(f"  综合结论: {stock_code} 周期特征不明显")
    
    # 当前位置判断
    bullish = 0
    bearish = 0
    for _, status, meaning in signals:
        if any(k in meaning for k in ["上行", "优", "偏低", "偏底部"]):
            bullish += 1
        elif any(k in meaning for k in ["下行", "差", "偏高", "偏顶部"]):
            bearish += 1
    
    if bullish > bearish + 1:
        print(f"  当前位置: 偏多（{bullish}个看多信号 vs {bearish}个看空信号）")
    elif bearish > bullish + 1:
        print(f"  当前位置: 偏空（{bullish}个看多信号 vs {bearish}个看空信号）")
    else:
        print(f"  当前位置: 中性（{bullish}个看多信号 vs {bearish}个看空信号）")
    
    print()
    print("  投资建议参考（非投资建议）:")
    print("  - 周期股适合在周期底部（高PE/低PB/利润低谷/库存衰退期）布局")
    print("  - 在周期顶部（低PE/高PB/利润高峰/库存扩张期）兑现收益")
    print("  - 需结合行业景气度、产品价格、产能利用率等基本面综合判断")
    print()


def print_inventory_analysis(financials: pd.DataFrame, merged: pd.DataFrame, stock_code: str):
    """打印完整七段式周期股分析报告"""
    sep = "=" * 90
    line = "-" * 90

    print()
    print(sep)
    print(f"  周期股深度分析 - {stock_code}")
    print(sep)

    # --- [1] 周期股确认 ---
    is_cyclical = print_cyclical_confirmation(financials, merged, stock_code)

    # --- [2] 估值统计与PE/PB反转分析 ---
    print()
    print("[2] 估值统计与PE/PB反转分析")
    print(line)
    print("  周期股特征: 底部高PE(利润低谷)、顶部低PE(利润爆发)，与常规股相反")
    print()

    if "pe_ttm" in merged.columns and "parent_profit_ttm" in merged.columns:
        valid = merged.dropna(subset=["pe_ttm", "parent_profit_ttm"])
        if len(valid) > 0:
            pe_max_idx = valid["pe_ttm"].idxmax()
            pe_min_idx = valid["pe_ttm"].idxmin()
            pe_max_row = valid.loc[pe_max_idx]
            pe_min_row = valid.loc[pe_min_idx]

            print(f"  PE 最高时:")
            date_str = pe_max_row.get("date", "")
            if hasattr(date_str, "strftime"): date_str = date_str.strftime("%Y-%m-%d")
            print(f"    日期: {date_str}  PE(TTM)={pe_max_row['pe_ttm']:.1f}  PB={pe_max_row.get('pb', float('nan')):.2f}")
            profit = pe_max_row.get("parent_profit_ttm", float("nan"))
            if pd.notna(profit):
                print(f"    归母净利润TTM: {profit/1e8:.2f} 亿元  <- 利润低谷，周期底部")

            print(f"  PE 最低时:")
            date_str = pe_min_row.get("date", "")
            if hasattr(date_str, "strftime"): date_str = date_str.strftime("%Y-%m-%d")
            print(f"    日期: {date_str}  PE(TTM)={pe_min_row['pe_ttm']:.1f}  PB={pe_min_row.get('pb', float('nan')):.2f}")
            profit = pe_min_row.get("parent_profit_ttm", float("nan"))
            if pd.notna(profit):
                print(f"    归母净利润TTM: {profit/1e8:.2f} 亿元  <- 利润高峰，周期顶部")

    # --- [3] 利润周期分析 ---
    print_profit_cycle(merged, stock_code)

    # --- [4] 现金流质量验证 ---
    print_cashflow_verification(merged, stock_code)

    # --- [5] 库存周期明细 ---
    print()
    print("[5] 库存周期分析")
    print(line)
    print("  四阶段: 1-上升(去库提价) 2-扩张(补库高位) 3-收缩(累库跌价) 4-衰退(减产去库)")
    print("  注: 阶段不是每次都完整，可能有一两个阶段不明显")
    print()
    header = f"  {'year':<6} {'Q':<3} {'库存(亿)':>10} {'库存环比':>10} {'毛利率':>8} {'趋势':>8} {'预收/合同负债':>12} {'应付账款':>10} {'阶段':<8}"
    print(header)
    print(f"  {'-'*6} {'-'*3} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*12} {'-'*10} {'-'*8}")

    inv_df = financials.copy()
    if "phase_name" not in inv_df.columns:
        inv_df = analyze_inventory_cycle(inv_df)

    for _, row in inv_df.iterrows():
        y = int(row["year"])
        q = int(row["quarter"])

        inv = row.get("INVENTORY", float("nan"))
        inv_str = f"{inv/1e8:.2f}" if pd.notna(inv) else "N/A"

        inv_chg = row.get("inventory_qoq", float("nan"))
        inv_chg_str = f"{inv_chg/1e8:+.2f}" if pd.notna(inv_chg) else "N/A"

        gm = row.get("gross_margin", float("nan"))
        gm_str = f"{gm*100:.1f}%" if pd.notna(gm) else "N/A"

        gm_trend = row.get("gross_margin_qoq", float("nan"))
        if pd.notna(gm_trend):
            if gm_trend > 0.003: gm_trend_str = "up"
            elif gm_trend < -0.003: gm_trend_str = "down"
            else: gm_trend_str = "flat"
        else:
            gm_trend_str = "N/A"

        adv = row.get("advance_total", float("nan"))
        adv_str = f"{adv/1e8:.2f}" if pd.notna(adv) else "N/A"

        ap = row.get("ACCOUNTS_PAYABLE", float("nan"))
        ap_str = f"{ap/1e8:.2f}" if pd.notna(ap) else "N/A"

        phase = row.get("phase_name", "")

        print(f"  {y:<6} Q{q:<2} {inv_str:>10} {inv_chg_str:>10} {gm_str:>8} {gm_trend_str:>8} {adv_str:>12} {ap_str:>10} {phase:<8}")

    # --- Part 3: 阶段转换汇总 ---
    print()
    print("[6] 阶段转换时间线")
    print(line)

    prev_phase = 0
    transitions = []
    for _, row in inv_df.iterrows():
        cur_phase = int(row.get("phase", 0))
        if cur_phase != prev_phase and cur_phase != 0 and prev_phase != 0:
            y = int(row["year"])
            q = int(row["quarter"])
            phase_name = row.get("phase_name", "")
            transitions.append(f"  {y}Q{q} -> {phase_name}")
        if cur_phase != 0:
            prev_phase = cur_phase

    if transitions:
        for t in transitions:
            print(t)
    else:
        print("  暂无明显的阶段转换记录")

    # --- [7] 综合判断 ---
    print_comprehensive_judgment(financials, merged, stock_code, is_cyclical)

    print(sep)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="周期股分析工具 - 获取近10年估值(PE/PB)与季度财务数据"
    )
    subparsers = parser.add_subparsers(dest="command")

    # valuation 子命令
    val_parser = subparsers.add_parser("valuation", help="仅获取估值数据(PE/PB)")
    val_parser.add_argument("--code", required=True, help="股票代码")
    val_parser.add_argument("--db", default=DB_PATH, help="数据库路径")
    val_parser.add_argument("--refresh", action="store_true", help="强制刷新估值数据（忽略缓存）")

    # full 子命令
    full_parser = subparsers.add_parser("full", help="获取估值 + 季度财务数据")
    full_parser.add_argument("--code", required=True, help="股票代码")
    full_parser.add_argument("--market", required=True, help="市场: sh/sz")
    full_parser.add_argument("--db", default=DB_PATH, help="数据库路径")
    full_parser.add_argument("--refresh", action="store_true", help="强制刷新估值数据（忽略缓存）")

    # combined 子命令: 周期股 + 红利股综合分析
    comb_parser = subparsers.add_parser("combined", help="周期股+红利股综合分析报告")
    comb_parser.add_argument("--code", required=True, help="股票代码")
    comb_parser.add_argument("--market", required=True, help="市场: sh/sz")
    comb_parser.add_argument("--db", default=DB_PATH, help="数据库路径")
    comb_parser.add_argument("--refresh", action="store_true", help="强制刷新估值数据")

    # inventory 子命令
    inv_parser = subparsers.add_parser("inventory", help="估值 + 财务 + 库存周期分析")
    inv_parser.add_argument("--code", required=True, help="股票代码")
    inv_parser.add_argument("--market", required=True, help="市场: sh/sz")
    inv_parser.add_argument("--db", default=DB_PATH, help="数据库路径")
    inv_parser.add_argument("--refresh", action="store_true", help="强制刷新估值数据（忽略缓存）")

    args = parser.parse_args()

    if args.command == "valuation":
        val_df = fetch_valuation(args.code, args.db, args.refresh)
        q_df = resample_quarter(val_df)
        print_analysis(q_df, args.code)

    elif args.command == "full":
        val_df = fetch_valuation(args.code, args.db, args.refresh)
        q_df = resample_quarter(val_df)
        fin_df = fetch_quarterly_financials(args.code, args.market, args.db)
        merged = merge_analysis(q_df, fin_df)
        print_analysis(merged, args.code)

    elif args.command == "inventory":
        val_df = fetch_valuation(args.code, args.db, args.refresh)
        q_df = resample_quarter(val_df)
        fin_df = fetch_quarterly_financials(args.code, args.market, args.db)
        merged = merge_analysis(q_df, fin_df)
        print_analysis(merged, args.code)

        if not fin_df.empty:
            fin_with_cycle = analyze_inventory_cycle(fin_df)
            print_inventory_analysis(fin_with_cycle, merged, args.code)

    elif args.command == "combined":
        # === 周期股分析 ===
        val_df = fetch_valuation(args.code, args.db, args.refresh)
        q_df = resample_quarter(val_df)
        fin_df = fetch_quarterly_financials(args.code, args.market, args.db)
        merged = merge_analysis(q_df, fin_df)
        print_analysis(merged, args.code)
        if not fin_df.empty:
            fin_with_cycle = analyze_inventory_cycle(fin_df)
            print_inventory_analysis(fin_with_cycle, merged, args.code)

        # === 红利股分析 ===
        sys.stdout.flush()
        print()
        print("=" * 90)
        print("  红利股分析")
        print("=" * 90)
        # 直接导入同目录下的 dividend_stock_analysis 模块
        import importlib.util
        _div_path = str(Path(__file__).resolve().parent / "dividend_stock_analysis.py")
        _spec = importlib.util.spec_from_file_location("dividend_stock_analysis", _div_path)
        _div_mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_div_mod)
        div_result = _div_mod.analyze(args.code, args.market, args.db)
        _div_mod.print_report(div_result)

    else:
        parser.print_help()


if __name__ == "__main__":
    # Windows UTF-8 输出兼容
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
