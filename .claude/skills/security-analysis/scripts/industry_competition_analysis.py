"""
industry_competition_analysis.py - 行业竞争格局分析工具

功能:
    1. 根据股票代码查找公司所属行业（东方财富行业分类）
    2. 获取该行业板块内所有成份股
    3. 采集行业内主要公司的市值、营收、净利润数据
    4. 分析行业竞争格局：集中度、头部差距、竞争态势判断

数据源:
    - 公司行业信息: akshare stock_individual_info_em (东方财富)
    - 行业板块成份股: akshare stock_board_industry_cons_em (东方财富)
    - 实时行情(含市值): akshare stock_zh_a_spot_em (东方财富)
    - 业绩报表(营收/利润): akshare stock_yjbb_em (东方财富)

使用方法:
    # 分析指定股票所在行业的竞争格局
    python scripts/industry_competition_analysis.py analyze --code 600519

    # 指定行业名称直接分析（跳过行业识别步骤）
    python scripts/industry_competition_analysis.py analyze --industry 白酒

    # 指定数据库路径
    python scripts/industry_competition_analysis.py analyze --code 600519 --db /path/to/db

输出:
    - 公司所属行业
    - 行业内前10名公司的市值、营收、净利润排名
    - 行业集中度指标（CR3/CR5/HHI）
    - 竞争格局判断（寡头垄断/充分竞争/一超多强等）

依赖:
    pip install akshare pandas requests
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

# Windows 终端 GBK 编码兼容
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 配置
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

DB_PATH = str(Path(_resolve_data_dir()) / "financial_data.db")

# 请求间隔（秒），避免频繁调用被限流
REQUEST_INTERVAL = 1.5


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def safe_val(v, default=0.0):
    """将 None / NaN / 空字符串安全转为浮点数"""
    if v is None:
        return default
    if isinstance(v, float) and pd.isna(v):
        return default
    if isinstance(v, str) and v.strip() in ("", "-", "--"):
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def fmt_yi(v):
    """格式化为亿元"""
    if v == 0 or v is None:
        return "N/A"
    return f"{v/1e8:.2f}亿"


def fmt_pct(v):
    """格式化为百分比"""
    if v == 0 or v is None:
        return "N/A"
    return f"{v:.2f}%"


def retry_call(func, retries=3, delay=3, *args, **kwargs):
    """带重试的API调用"""
    for attempt in range(retries):
        try:
            time.sleep(REQUEST_INTERVAL)
            return func(*args, **kwargs)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.RequestException,
                Exception) as e:
            if attempt < retries - 1:
                wait = delay * (attempt + 1)
                print(f"  [重试 {attempt+1}/{retries}] API调用失败: {e}，{wait}秒后重试...")
                time.sleep(wait)
            else:
                print(f"  [失败] API调用失败且重试耗尽: {e}")
                raise


def get_latest_report_date():
    """获取最近一期可用的业绩报表日期（滞后约4个月）"""
    today = datetime.now()
    year = today.year
    month = today.month
    # 业绩报表发布日期大约滞后4个月
    # 一季报(03-31): 4月底前发布  -> 可用月份: 5月+
    # 中报(06-30):  8月底前发布  -> 可用月份: 9月+
    # 三季报(09-30): 10月底前发布 -> 可用月份: 11月+
    # 年报(12-31):  4月底前发布  -> 可用月份: 5月+
    if month >= 11:
        return f"{year}0930"
    elif month >= 9:
        return f"{year}0630"
    elif month >= 5:
        return f"{year-1}1231"
    else:
        return f"{year-1}0930"


# ---------------------------------------------------------------------------
# Step 1: 获取公司所属行业
# ---------------------------------------------------------------------------

def get_stock_industry(stock_code):
    """
    获取指定股票的行业分类（东方财富行业分类）

    Returns:
        dict: {
            'stock_name': 股票名称,
            'industry': 行业名称,
            'stock_code': 股票代码,
        }
    """
    print(f"\n[Step 1] 获取 {stock_code} 的行业信息...")
    code = stock_code.replace("sh", "").replace("sz", "").replace(".", "")

    df = retry_call(ak.stock_individual_info_em, symbol=code)
    print(f"  获取到 {len(df)} 条信息")

    info = {}
    for _, row in df.iterrows():
        key = str(row.iloc[0]) if len(row) > 0 else ""
        val = str(row.iloc[1]) if len(row) > 1 else ""
        info[key] = val

    stock_name = info.get("股票简称", "未知")
    industry = info.get("行业", "")

    if not industry:
        # 尝试从 DataFrame 的列名或内容找行业字段
        for k, v in info.items():
            if "行业" in str(k):
                industry = str(v)
                break

    if not industry:
        print(f"  [警告] 未能从API获取行业信息，返回原始数据: {info}")
        # 打印所有可用字段供调试
        for k, v in info.items():
            print(f"    {k}: {v}")

    result = {
        "stock_name": stock_name,
        "industry": industry,
        "stock_code": code,
    }
    print(f"  股票名称: {stock_name}")
    print(f"  所属行业: {industry}")
    return result


# ---------------------------------------------------------------------------
# Step 2: 获取行业成份股
# ---------------------------------------------------------------------------

def get_industry_stocks(industry_name):
    """
    获取指定行业板块的所有成份股

    Returns:
        DataFrame: 行业成份股列表，包含代码、名称等字段
    """
    print(f"\n[Step 2] 获取行业 [{industry_name}] 的成份股...")

    # 首先尝试精确匹配行业板块名称
    boards = retry_call(ak.stock_board_industry_name_em)
    print(f"  东方财富行业板块总数: {len(boards)}")

    # 精确匹配
    matched = boards[boards["板块名称"] == industry_name]
    if matched.empty:
        # 模糊匹配
        matched = boards[boards["板块名称"].str.contains(industry_name, na=False)]
        if matched.empty:
            # 反向模糊：行业名包含板块名
            matched = boards[boards["板块名称"].apply(
                lambda x: x in industry_name if isinstance(x, str) else False
            )]
        if matched.empty:
            print(f"  [错误] 未找到匹配的行业板块: {industry_name}")
            print(f"  可选行业板块（前20个）:")
            for name in boards["板块名称"].head(20).tolist():
                print(f"    - {name}")
            raise ValueError(f"行业板块匹配失败: {industry_name}")

    board_name = matched.iloc[0]["板块名称"]
    print(f"  匹配到行业板块: {board_name}")

    # 获取成份股
    cons = retry_call(ak.stock_board_industry_cons_em, symbol=board_name)
    print(f"  行业内股票总数: {len(cons)}")

    return cons, board_name


# ---------------------------------------------------------------------------
# Step 3: 获取财务数据
# ---------------------------------------------------------------------------

def get_financial_data(stock_codes):
    """
    获取一组股票的最新财务数据（营收、净利润、市值）

    策略：
    1. 先从 stock_zh_a_spot_em 获取实时市值和当前价格
    2. 再从 stock_yjbb_em 获取最近一期的营收和净利润

    Args:
        stock_codes: 股票代码列表（纯数字）

    Returns:
        DataFrame: 包含 stock_code, stock_name, market_cap, revenue, net_profit 等字段
    """
    print(f"\n[Step 3] 获取财务数据...")

    # ---- 3a. 实时行情数据（含市值）----
    print("  正在获取A股实时行情（市值）...")
    try:
        spot_df = retry_call(ak.stock_zh_a_spot_em)
        print(f"  A股行情总数: {len(spot_df)}")
        # 标准化列名
        spot_cols = spot_df.columns.tolist()
        print(f"  行情数据列: {spot_cols[:15]}...")
    except Exception as e:
        print(f"  [警告] 获取实时行情失败: {e}，将尝试备用方案")
        spot_df = None

    # ---- 3b. 业绩报表数据（营收、利润）----
    report_date = get_latest_report_date()
    print(f"  正在获取业绩报表（报告期: {report_date}）...")
    try:
        yjbb_df = retry_call(ak.stock_yjbb_em, date=report_date)
        print(f"  业绩报表记录数: {len(yjbb_df)}")
        yjbb_cols = yjbb_df.columns.tolist()
        print(f"  业绩报表列: {yjbb_cols[:15]}...")
    except Exception as e:
        print(f"  [警告] 获取业绩报表失败: {e}")
        yjbb_df = None

    return spot_df, yjbb_df, report_date


def build_industry_df(cons_df, spot_df, yjbb_df, stock_codes_set):
    """
    将成份股、行情、业绩数据合并，构建行业分析 DataFrame

    Returns:
        DataFrame: 包含排名所需核心字段的 DataFrame，按市值降序排列
    """
    print(f"\n  正在合并数据...")

    # 提取成份股的代码和名称
    cons_cols = cons_df.columns.tolist()
    print(f"  成份股列: {cons_cols}")

    # 识别代码和名称列
    code_col = None
    name_col = None
    for col in cons_cols:
        if "代码" in str(col) or "code" in str(col).lower():
            code_col = col
        if "名称" in str(col) or "name" in str(col).lower():
            name_col = col
    if not code_col:
        code_col = cons_cols[1] if len(cons_cols) > 1 else cons_cols[0]
    if not name_col:
        name_col = cons_cols[2] if len(cons_cols) > 2 else cons_cols[0]

    result = cons_df[[code_col, name_col]].copy()
    result.columns = ["stock_code", "stock_name"]
    result["stock_code"] = result["stock_code"].astype(str).str.zfill(6)

    # 合并市值数据
    if spot_df is not None:
        spot_code_col = None
        for col in spot_df.columns:
            if "代码" in str(col) or "code" in str(col).lower():
                spot_code_col = col
                break
        if spot_code_col:
            spot_tmp = spot_df.copy()
            spot_tmp["_code"] = spot_tmp[spot_code_col].astype(str).str.zfill(6)

            # 识别市值列
            mcap_col = None
            for col in spot_df.columns:
                if "总市值" in str(col) or ("市值" in str(col) and "流通" not in str(col)):
                    mcap_col = col
                    break
            if not mcap_col:
                for col in spot_df.columns:
                    if "市值" in str(col):
                        mcap_col = col
                        break

            if mcap_col:
                spot_map = dict(zip(spot_tmp["_code"], spot_tmp[mcap_col]))
                result["market_cap"] = result["stock_code"].map(spot_map).apply(safe_val)
            else:
                print("  [警告] 未找到市值列")
                result["market_cap"] = 0.0

            # 获取最新价
            price_col = None
            for col in spot_df.columns:
                if "最新价" in str(col) or "收盘" in str(col):
                    price_col = col
                    break
            if price_col:
                price_map = dict(zip(spot_tmp["_code"], spot_tmp[price_col]))
                result["price"] = result["stock_code"].map(price_map).apply(safe_val)
        else:
            result["market_cap"] = 0.0
            result["price"] = 0.0
    else:
        result["market_cap"] = 0.0
        result["price"] = 0.0

    # 合并业绩数据（营收、净利润）
    if yjbb_df is not None:
        yjbb_code_col = None
        for col in yjbb_df.columns:
            if "代码" in str(col) or "code" in str(col).lower() or "股票代码" in str(col):
                yjbb_code_col = col
                break
        if not yjbb_code_col:
            yjbb_code_col = yjbb_df.columns[1] if len(yjbb_df.columns) > 1 else yjbb_df.columns[0]

        yjbb_tmp = yjbb_df.copy()
        yjbb_tmp["_code"] = yjbb_tmp[yjbb_code_col].astype(str).str.zfill(6)

        # 营收列
        rev_col = None
        for col in yjbb_df.columns:
            if "营业总收入" in str(col) or ("营业收入" in str(col) and "同比" not in str(col)):
                rev_col = col
                break
        if not rev_col:
            for col in yjbb_df.columns:
                if "营收" in str(col) and "同比" not in str(col):
                    rev_col = col
                    break

        # 净利润列
        profit_col = None
        for col in yjbb_df.columns:
            if "归母净利润" in str(col) and "同比" not in str(col) and "扣非" not in str(col):
                profit_col = col
                break
        if not profit_col:
            for col in yjbb_df.columns:
                if "净利润" in str(col) and "同比" not in str(col) and "扣非" not in str(col) and "率" not in str(col):
                    profit_col = col
                    break

        # 营收同比
        rev_yoy_col = None
        for col in yjbb_df.columns:
            if "营业收入同比" in str(col) or "营收同比" in str(col):
                rev_yoy_col = col
                break

        # 净利润同比
        profit_yoy_col = None
        for col in yjbb_df.columns:
            if "净利润同比" in str(col) and "扣非" not in str(col):
                profit_yoy_col = col
                break

        if rev_col:
            rev_map = dict(zip(yjbb_tmp["_code"], yjbb_tmp[rev_col]))
            result["revenue"] = result["stock_code"].map(rev_map).apply(safe_val)
        else:
            result["revenue"] = 0.0

        if profit_col:
            profit_map = dict(zip(yjbb_tmp["_code"], yjbb_tmp[profit_col]))
            result["net_profit"] = result["stock_code"].map(profit_map).apply(safe_val)
        else:
            result["net_profit"] = 0.0

        if rev_yoy_col:
            rev_yoy_map = dict(zip(yjbb_tmp["_code"], yjbb_tmp[rev_yoy_col]))
            result["revenue_yoy"] = result["stock_code"].map(rev_yoy_map).apply(safe_val)
        else:
            result["revenue_yoy"] = 0.0

        if profit_yoy_col:
            profit_yoy_map = dict(zip(yjbb_tmp["_code"], yjbb_tmp[profit_yoy_col]))
            result["profit_yoy"] = result["stock_code"].map(profit_yoy_map).apply(safe_val)
        else:
            result["profit_yoy"] = 0.0

    else:
        result["revenue"] = 0.0
        result["net_profit"] = 0.0
        result["revenue_yoy"] = 0.0
        result["profit_yoy"] = 0.0

    # 过滤无效数据并按市值降序排列
    result = result[result["market_cap"] > 0].copy()
    result = result.sort_values("market_cap", ascending=False).reset_index(drop=True)

    return result


# ---------------------------------------------------------------------------
# Step 4: 竞争格局分析
# ---------------------------------------------------------------------------

def analyze_competition(df, top_n=10, target_stock_code=None):
    """
    分析行业竞争格局

    分析维度:
    1. 市场集中度: CR3/CR5/CR10（前N名市场份额占比）
    2. 龙头优势倍数: 第1名 vs 第2/3/5名 的市值、营收、利润倍数
    3. 头部断层分析: 相邻排名之间的差距
    4. HHI指数: 赫芬达尔-赫希曼指数

    Args:
        df: 行业公司 DataFrame（已按市值降序排列）
        top_n: 分析前N家公司
        target_stock_code: 目标股票代码（标注用）

    Returns:
        dict: 分析结果
    """
    print(f"\n[Step 4] 竞争格局分析（前 {min(top_n, len(df))} 名）...")

    n = min(top_n, len(df))
    top_df = df.head(n).copy()
    top_df["rank"] = range(1, n + 1)

    result = {
        "total_companies": len(df),
        "analyzed": n,
        "top_companies": [],
        "concentration": {},
        "dominance": {},
        "gaps": [],
        "hhi": {},
        "competition_verdict": "",
    }

    # ---- 基础数据 ----
    total_mcap = df["market_cap"].sum()
    total_revenue = df["revenue"].sum() if df["revenue"].sum() > 0 else 1
    total_profit = df["net_profit"].sum() if df["net_profit"].sum() > 0 else 1

    for _, row in top_df.iterrows():
        is_target = str(row["stock_code"]).replace("sh","").replace("sz","") == str(target_stock_code).replace("sh","").replace("sz","") if target_stock_code else False
        company = {
            "rank": int(row["rank"]),
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "market_cap": row["market_cap"],
            "revenue": row["revenue"],
            "net_profit": row["net_profit"],
            "revenue_yoy": row.get("revenue_yoy", 0),
            "profit_yoy": row.get("profit_yoy", 0),
            "mcap_share": row["market_cap"] / total_mcap * 100 if total_mcap > 0 else 0,
            "revenue_share": row["revenue"] / total_revenue * 100 if total_revenue > 0 else 0,
            "profit_share": row["net_profit"] / total_profit * 100 if total_profit > 0 else 0,
            "is_target": is_target,
        }
        result["top_companies"].append(company)

    # ---- 市场集中度 (CR_n) ----
    def cr(k, field, total):
        top_k = top_df.head(min(k, n))[field].sum()
        return top_k / total * 100 if total > 0 else 0

    result["concentration"] = {
        "CR3_mcap": cr(3, "market_cap", total_mcap),
        "CR5_mcap": cr(5, "market_cap", total_mcap),
        "CR10_mcap": cr(10, "market_cap", total_mcap),
        "CR3_revenue": cr(3, "revenue", total_revenue),
        "CR5_revenue": cr(5, "revenue", total_revenue),
        "CR3_profit": cr(3, "net_profit", total_profit),
        "CR5_profit": cr(5, "net_profit", total_profit),
    }

    # ---- 龙头优势倍数 ----
    if n >= 2:
        first = result["top_companies"][0]
        second = result["top_companies"][1] if n >= 2 else first
        third = result["top_companies"][2] if n >= 3 else first
        fifth = result["top_companies"][4] if n >= 5 else first

        def ratio(a, b):
            return a / b if b and b > 0 else float("inf")

        result["dominance"] = {
            "No1_vs_No2_mcap": ratio(first["market_cap"], second["market_cap"]),
            "No1_vs_No3_mcap": ratio(first["market_cap"], third["market_cap"]),
            "No1_vs_No2_revenue": ratio(first["revenue"], second["revenue"]),
            "No1_vs_No3_revenue": ratio(first["revenue"], third["revenue"]),
            "No1_vs_No2_profit": ratio(first["net_profit"], second["net_profit"]),
            "No1_vs_No3_profit": ratio(first["net_profit"], third["net_profit"]),
            "No1_vs_rest_sum_mcap": ratio(
                first["market_cap"],
                sum(c["market_cap"] for c in result["top_companies"][1:])
            ),
            "No1_vs_rest_sum_revenue": ratio(
                first["revenue"],
                sum(c["revenue"] for c in result["top_companies"][1:])
            ),
            "No1_vs_rest_sum_profit": ratio(
                first["net_profit"],
                sum(c["net_profit"] for c in result["top_companies"][1:])
            ),
            "No2_vs_No3_mcap": ratio(second["market_cap"], third["market_cap"]),
            "No2_vs_No3_revenue": ratio(second["revenue"], third["revenue"]),
        }

    # ---- 相邻排名断层分析 ----
    for i in range(n - 1):
        curr = result["top_companies"][i]
        next_ = result["top_companies"][i + 1]
        gap = {
            "from_rank": i + 1,
            "to_rank": i + 2,
            "mcap_ratio": curr["market_cap"] / next_["market_cap"] if next_["market_cap"] > 0 else float("inf"),
            "revenue_ratio": curr["revenue"] / next_["revenue"] if next_["revenue"] > 0 else float("inf"),
            "profit_ratio": curr["net_profit"] / next_["net_profit"] if next_["net_profit"] > 0 else float("inf"),
        }
        result["gaps"].append(gap)

    # ---- HHI 指数（按市值）----
    # HHI = sum(share^2)，share 为百分比形式
    # HHI < 1500: 竞争型市场；1500-2500: 适度集中；> 2500: 高度集中
    shares_mcap = [c["mcap_share"] for c in result["top_companies"]]
    shares_revenue = [c["revenue_share"] for c in result["top_companies"]]
    shares_profit = [c["profit_share"] for c in result["top_companies"]]

    result["hhi"] = {
        "mcap": sum(s ** 2 for s in shares_mcap),
        "revenue": sum(s ** 2 for s in shares_revenue),
        "profit": sum(s ** 2 for s in shares_profit),
    }

    # ---- 竞争格局综合判断 ----
    result["competition_verdict"] = judge_competition(result)

    return result


def judge_competition(result):
    """
    根据多维度指标综合判断行业竞争格局

    Returns:
        dict: {
            'pattern': 格局类型 (寡头垄断/一超多强/群雄逐鹿/充分竞争),
            'intensity': 竞争激烈程度 (低/中/高),
            'price_war_risk': 恶性价格战风险 (低/中/高),
            'analysis': 详细分析文字,
        }
    """
    dom = result.get("dominance", {})
    conc = result.get("concentration", {})
    hhi = result.get("hhi", {})
    companies = result.get("top_companies", [])
    gaps = result.get("gaps", [])

    no1_vs_rest_mcap = dom.get("No1_vs_rest_sum_mcap", 0)
    no1_vs_rest_revenue = dom.get("No1_vs_rest_sum_revenue", 0)
    no1_vs_rest_profit = dom.get("No1_vs_rest_sum_profit", 0)
    no1_vs_no2_mcap = dom.get("No1_vs_No2_mcap", 1)
    no1_vs_no2_revenue = dom.get("No1_vs_No2_revenue", 1)
    no2_vs_no3_mcap = dom.get("No2_vs_No3_mcap", 1)

    cr3_mcap = conc.get("CR3_mcap", 0)
    cr5_mcap = conc.get("CR5_mcap", 0)
    hhi_mcap = hhi.get("mcap", 0)

    # 判断逻辑
    analysis_parts = []

    # 1. 一超多强：龙头市值/营收/利润是其余所有之和的2倍以上
    is_super_dominant = (
        no1_vs_rest_mcap >= 2.0 or
        no1_vs_rest_revenue >= 2.0 or
        no1_vs_rest_profit >= 2.0
    )

    # 2. 寡头格局：CR3 > 60% 且 HHI > 2500
    is_oligopoly = cr3_mcap > 60 and hhi_mcap > 2500

    # 3. 充分竞争：CR5 < 40% 且 HHI < 1500
    is_competitive = cr5_mcap < 40 and hhi_mcap < 1500

    # 4. 断层分析：第1与第2的差距、第2与第3的差距
    has_big_gap_1_2 = no1_vs_no2_mcap >= 2.0 or no1_vs_no2_revenue >= 2.0
    has_big_gap_2_3 = no2_vs_no3_mcap >= 1.5

    if is_super_dominant:
        pattern = "一超多强"
        intensity = "低"
        price_war_risk = "低"
        analysis_parts.append(
            f"行业龙头优势极为突出：市值是其余前10名之和的 {no1_vs_rest_mcap:.1f} 倍，"
            f"营收是其余之和的 {no1_vs_rest_revenue:.1f} 倍，"
            f"净利润是其余之和的 {no1_vs_rest_profit:.1f} 倍。"
        )
        analysis_parts.append("龙头地位稳固，行业竞争格局已基本确定，发生恶性价格竞争的概率较低。")
        if has_big_gap_2_3:
            analysis_parts.append(
                f"第二名与第三名之间也存在明显断层（市值倍数 {no2_vs_no3_mcap:.1f}x），"
                "行业梯队分化清晰。"
            )

    elif is_oligopoly:
        pattern = "寡头垄断"
        intensity = "中低"
        price_war_risk = "低"
        analysis_parts.append(
            f"行业前三家合计市值占比 {cr3_mcap:.1f}%，HHI 指数 {hhi_mcap:.0f}（>2500为高度集中）。"
        )
        analysis_parts.append("少数几家企业主导市场，竞争格局相对稳定，恶性价格战风险较低。")
        if has_big_gap_1_2:
            analysis_parts.append("但龙头与第二名之间差距较大，龙头地位较为稳固。")

    elif is_competitive:
        pattern = "充分竞争"
        intensity = "高"
        price_war_risk = "高"
        analysis_parts.append(
            f"行业前五家合计市值占比仅 {cr5_mcap:.1f}%，HHI 指数 {hhi_mcap:.0f}（<1500为竞争型市场）。"
        )
        analysis_parts.append("市场参与者众多且实力接近，竞争激烈，存在发生价格战的风险。")

    else:
        # 介于寡头和充分竞争之间
        if cr3_mcap > 45:
            pattern = "寡头竞争"
            intensity = "中"
            price_war_risk = "中"
            analysis_parts.append(
                f"行业前三家合计市值占比 {cr3_mcap:.1f}%，具有一定集中度，"
                f"HHI 指数 {hhi_mcap:.0f}，属于适度集中市场。"
            )
            analysis_parts.append("头部企业有一定优势，但彼此之间竞争依然存在。")
        else:
            pattern = "群雄逐鹿"
            intensity = "中高"
            price_war_risk = "中高"
            analysis_parts.append(
                f"行业前五家合计市值占比 {cr5_mcap:.1f}%，市场相对分散，"
                f"HHI 指数 {hhi_mcap:.0f}。"
            )
            analysis_parts.append("多家企业实力相近，竞争较为激烈，需关注行业整合趋势。")

    # 补充断层分析
    big_gaps = [g for g in gaps if g["mcap_ratio"] >= 2.0]
    if big_gaps:
        gap_desc = ", ".join([f"第{g['from_rank']}→{g['to_rank']}名（{g['mcap_ratio']:.1f}x）" for g in big_gaps])
        analysis_parts.append(f"明显断层出现在：{gap_desc}。")

    return {
        "pattern": pattern,
        "intensity": intensity,
        "price_war_risk": price_war_risk,
        "analysis": "\n".join(analysis_parts),
    }


# ---------------------------------------------------------------------------
# 输出报告
# ---------------------------------------------------------------------------

def print_report(industry_name, target_info, result):
    """打印竞争格局分析报告"""
    print("\n" + "=" * 70)
    print(f"  {industry_name} 行业竞争格局分析报告")
    print(f"  目标股票: {target_info['stock_name']}（{target_info['stock_code']}）")
    print(f"  分析日期: {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 70)

    # ---- 行业概况 ----
    print(f"\n## 行业概况")
    print(f"  行业内上市公司总数: {result['total_companies']} 家")
    print(f"  本次分析前 N 名:  {result['analyzed']} 家")

    # ---- 前N名公司排名 ----
    print(f"\n## 前 {result['analyzed']} 名公司排名（按市值）")
    print("-" * 100)
    header = f"{'排名':<4} {'代码':<8} {'名称':<10} {'总市值':>12} {'营收':>12} {'净利润':>12} {'市值占比':>8}"
    print(header)
    print("-" * 100)
    for c in result["top_companies"]:
        marker = " *" if c["is_target"] else ""
        mcap_str = fmt_yi(c["market_cap"])
        rev_str = fmt_yi(c["revenue"])
        profit_str = fmt_yi(c["net_profit"])
        share_str = f"{c['mcap_share']:.1f}%"
        print(f"{c['rank']:<4} {c['stock_code']:<8} {c['stock_name']:<10}{marker:<3} {mcap_str:>12} {rev_str:>12} {profit_str:>12} {share_str:>8}")
    print("-" * 100)
    print("  * 标记为目标分析股票")

    # ---- 营收和利润排名 ----
    print(f"\n## 营收与利润份额")
    print("-" * 80)
    print(f"{'排名':<4} {'名称':<12} {'营收占比':>10} {'营收同比':>10} {'利润占比':>10} {'利润同比':>10}")
    print("-" * 80)
    for c in result["top_companies"]:
        rev_share = f"{c['revenue_share']:.1f}%"
        profit_share = f"{c['profit_share']:.1f}%"
        rev_yoy = f"{c['revenue_yoy']:.1f}%" if c['revenue_yoy'] != 0 else "N/A"
        profit_yoy = f"{c['profit_yoy']:.1f}%" if c['profit_yoy'] != 0 else "N/A"
        print(f"{c['rank']:<4} {c['stock_name']:<12} {rev_share:>10} {rev_yoy:>10} {profit_share:>10} {profit_yoy:>10}")
    print("-" * 80)

    # ---- 市场集中度 ----
    print(f"\n## 市场集中度指标")
    conc = result["concentration"]
    print(f"  市值 CR3: {conc['CR3_mcap']:.1f}%   CR5: {conc['CR5_mcap']:.1f}%   CR10: {conc['CR10_mcap']:.1f}%")
    print(f"  营收 CR3: {conc['CR3_revenue']:.1f}%   CR5: {conc['CR5_revenue']:.1f}%")
    print(f"  利润 CR3: {conc['CR3_profit']:.1f}%   CR5: {conc['CR5_profit']:.1f}%")

    hhi = result["hhi"]
    print(f"\n  HHI 指数（按市值）: {hhi['mcap']:.0f}")
    if hhi["mcap"] < 1500:
        hhi_desc = "竞争型市场（<1500）"
    elif hhi["mcap"] < 2500:
        hhi_desc = "适度集中（1500-2500）"
    else:
        hhi_desc = "高度集中（>2500）"
    print(f"  HHI 评价: {hhi_desc}")

    # ---- 龙头优势倍数 ----
    print(f"\n## 龙头优势分析")
    dom = result["dominance"]
    print(f"  第1名 vs 第2名（市值倍数）: {dom.get('No1_vs_No2_mcap', 0):.2f}x")
    print(f"  第1名 vs 第3名（市值倍数）: {dom.get('No1_vs_No3_mcap', 0):.2f}x")
    print(f"  第1名 vs 其余前10之和（市值）: {dom.get('No1_vs_rest_sum_mcap', 0):.2f}x")
    print(f"  第1名 vs 其余前10之和（营收）: {dom.get('No1_vs_rest_sum_revenue', 0):.2f}x")
    print(f"  第1名 vs 其余前10之和（利润）: {dom.get('No1_vs_rest_sum_profit', 0):.2f}x")
    print(f"  第2名 vs 第3名（市值倍数）:   {dom.get('No2_vs_No3_mcap', 0):.2f}x")

    # ---- 断层分析 ----
    print(f"\n## 相邻排名断层分析（市值倍数，越大说明断层越明显）")
    for g in result["gaps"]:
        bar = "█" * min(int(g["mcap_ratio"] * 2), 20)
        print(f"  第{g['from_rank']}→{g['to_rank']}名: {g['mcap_ratio']:.2f}x {bar}")

    # ---- 综合判断 ----
    print(f"\n## 竞争格局综合判断")
    verdict = result["competition_verdict"]
    print(f"  竞争格局类型: {verdict['pattern']}")
    print(f"  竞争激烈程度: {verdict['intensity']}")
    print(f"  恶性价格战风险: {verdict['price_war_risk']}")
    print(f"\n  分析详情:")
    for line in verdict["analysis"].split("\n"):
        print(f"    {line}")

    print("\n" + "=" * 70)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="行业竞争格局分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 分析贵州茅台所在行业（白酒）的竞争格局
  python scripts/industry_competition_analysis.py analyze --code 600519

  # 直接指定行业名称
  python scripts/industry_competition_analysis.py analyze --industry 白酒

  # 指定分析前N名（默认10）
  python scripts/industry_competition_analysis.py analyze --code 600519 --top 8
        """
    )
    subparsers = parser.add_subparsers(dest="command")

    # analyze 子命令
    analyze_parser = subparsers.add_parser("analyze", help="分析行业竞争格局")
    analyze_parser.add_argument("--code", help="股票代码，如 600519")
    analyze_parser.add_argument("--industry", help="直接指定行业名称（跳过行业识别）")
    analyze_parser.add_argument("--top", type=int, default=10, help="分析前N名公司（默认10）")
    analyze_parser.add_argument("--db", help="数据库路径（默认项目根目录）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "analyze":
        if not args.code and not args.industry:
            print("[错误] 必须指定 --code 或 --industry 参数")
            sys.exit(1)

        target_info = {}
        industry_name = args.industry

        # Step 1: 获取行业
        if args.code:
            target_info = get_stock_industry(args.code)
            if not industry_name:
                industry_name = target_info.get("industry", "")
                if not industry_name:
                    print("[错误] 无法识别公司所属行业，请使用 --industry 手动指定")
                    sys.exit(1)
        else:
            target_info = {"stock_name": "未指定", "stock_code": "未指定", "industry": industry_name}

        # Step 2: 获取行业成份股
        cons_df, board_name = get_industry_stocks(industry_name)

        # Step 3: 获取财务数据
        stock_codes_set = set(cons_df.iloc[:, 1].astype(str).str.zfill(6).tolist())
        spot_df, yjbb_df, report_date = get_financial_data(stock_codes_set)

        # 合并数据
        ind_df = build_industry_df(cons_df, spot_df, yjbb_df, stock_codes_set)

        if len(ind_df) == 0:
            print("[错误] 未能获取到行业内任何公司的有效数据")
            sys.exit(1)

        print(f"\n  成功获取 {len(ind_df)} 家公司的有效数据（报告期: {report_date}）")

        # Step 4: 竞争格局分析
        result = analyze_competition(ind_df, top_n=args.top, target_stock_code=args.code)

        # 输出报告
        print_report(board_name, target_info, result)


if __name__ == "__main__":
    main()
