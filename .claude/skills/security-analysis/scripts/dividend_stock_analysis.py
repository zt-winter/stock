"""
dividend_stock_analysis.py - 红利股分析工具

功能:
    分析指定A股是否为优质红利股，从四个维度评估：
    1. 近10年股息率趋势：股息率高低、稳定性
    2. 股票回购注销：回购注销金额计入股息（仅限注销用途）
    3. 自由现金流：是否能保障分红可持续
    4. 营收与扣非经营现金流：判断企业是否衰退

数据源:
    - 分红明细: akshare stock_history_dividend_detail (新浪财经)
    - 分红送配(含股息率): akshare stock_fhps_em (东方财富)
    - 历史股价: akshare stock_zh_a_hist (东方财富)
    - 股票回购: akshare stock_repurchase_em (东方财富)
    - 财报数据: financial_data.db 中的 em_cash_flow / em_income_statement

数据库表:
    - dividend_annual_yield: 年度股息率缓存
    - stock_repurchase: 股票回购记录

使用方法:
    # 采集数据（分红+回购）
    python scripts/dividend_stock_analysis.py collect --code 600519 --market sh

    # 完整分析
    python scripts/dividend_stock_analysis.py analyze --code 600519 --market sh

    # 仅采集分红收益率（从东方财富stock_fhps_em按报告期采集）
    python scripts/dividend_stock_analysis.py collect-yield --code 600519

    # 仅采集回购数据
    python scripts/dividend_stock_analysis.py collect-repurchase --code 600519

依赖:
    需先通过 collect_financial_data.py 采集目标股票的财报数据（em_cash_flow / em_income_statement）
    pip install akshare pandas
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
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
CURRENT_YEAR = datetime.now().year
ANALYSIS_YEARS = 10  # 分析近N年

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def safe_val(v, default=0.0):
    """将 None / NaN / 空字符串安全转为浮点数"""
    if v is None:
        return default
    if isinstance(v, float) and pd.isna(v):
        return default
    if isinstance(v, str) and v.strip() == "":
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def safe_str(v, default=""):
    """安全转为字符串"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    return str(v).strip()


def fmt_yi(v):
    """格式化为亿元"""
    if v is None or pd.isna(v):
        return "N/A"
    return f"{v / 1e8:>12.2f}"


def fmt_wan(v):
    """格式化为万元"""
    if v is None or pd.isna(v):
        return "N/A"
    return f"{v / 1e4:>12.2f}"


def fmt_pct(v, digits=2):
    """格式化百分比"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "N/A"
    return f"{v:>{6+digits}.{digits}f}%"


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    """创建 SQLite 连接（WAL 模式）"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------

def init_tables(conn: sqlite3.Connection):
    """创建所需的数据库表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dividend_annual_yield (
            stock_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            dividend_per_share REAL,
            dividend_yield REAL,
            cash_dividend_ratio REAL,
            eps REAL,
            bps REAL,
            year_end_price REAL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (stock_code, year)
        )
    """)
    # 兼容旧表：添加 year_end_price 列（如果不存在）
    try:
        conn.execute("ALTER TABLE dividend_annual_yield ADD COLUMN year_end_price REAL")
        conn.commit()
    except Exception:
        pass  # 列已存在
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_repurchase (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            repurchase_amount_lower REAL,
            repurchase_amount_upper REAL,
            repurchased_amount REAL,
            repurchased_qty REAL,
            progress TEXT,
            start_date TEXT,
            latest_announce_date TEXT,
            price_lower REAL,
            price_upper REAL,
            repurchased_price_lower REAL,
            repurchased_price_upper REAL,
            total_shares_ratio_lower REAL,
            total_shares_ratio_upper REAL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# 数据采集: 分红明细（逐笔）
# ---------------------------------------------------------------------------

def collect_dividend_detail(stock_code: str, conn: sqlite3.Connection,
                            max_retries: int = 3, retry_delay: float = 1.0) -> pd.DataFrame | None:
    """
    采集指定股票的历史分红明细（新浪财经），存入 stock_dividend_detail 表。
    返回原始 DataFrame。
    """
    for attempt in range(1, max_retries + 1):
        try:
            df = ak.stock_history_dividend_detail(symbol=stock_code, indicator="分红")
            if df is not None and not df.empty:
                df.insert(0, "代码", stock_code)
                # 幂等写入
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_dividend_detail'")
                if cursor.fetchone():
                    cursor.execute("DELETE FROM stock_dividend_detail WHERE 代码 = ?", (stock_code,))
                df.to_sql("stock_dividend_detail", conn, if_exists="append", index=False)
                conn.commit()
                print(f"  -> 分红明细: {len(df)} 条记录已存入 stock_dividend_detail")
                return df
            return None
        except Exception as e:
            if attempt < max_retries:
                time.sleep(retry_delay * attempt)
            else:
                print(f"  [!] {stock_code} 获取分红明细失败（重试 {max_retries} 次）: {e}")
                return None
    return None


# ---------------------------------------------------------------------------
# 数据采集: 历史年末股价
# ---------------------------------------------------------------------------

def get_year_end_prices(stock_code: str, market: str = "sh",
                        years: int = ANALYSIS_YEARS,
                        max_retries: int = 3, retry_delay: float = 3.0) -> dict[int, float]:
    """
    从腾讯行情API获取历史年末收盘价（按年分段请求，避免超时）。

    返回: {year: closing_price} 字典
    """
    end_year = CURRENT_YEAR - 1
    start_year = end_year - years + 1
    result = {}
    prefix = market.lower()  # sh 或 sz

    for year in range(start_year, end_year + 1):
        s_date = f"{year}-01-01"
        e_date = f"{year}-12-31"
        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={prefix}{stock_code},day,{s_date},{e_date},300,"
        )
        for attempt in range(1, max_retries + 1):
            try:
                time.sleep(0.8)
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                data = r.json().get("data", {}).get(f"{prefix}{stock_code}", {})
                # 数据格式: [date, open, close, high, low, volume]
                days = data.get("day") or data.get("qfqday") or []
                if days:
                    last = days[-1]
                    price = float(last[2])  # close at index 2
                    result[year] = price
                    print(f"    {year}: 年末收盘价 {price:.2f}")
                else:
                    print(f"    {year}: 无数据")
                break  # 成功则跳出重试
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(retry_delay * attempt)
                else:
                    print(f"    {year}: 获取失败（重试 {max_retries} 次）: {e}")

    print(f"  -> 获取到 {len(result)}/{end_year - start_year + 1} 年的年末收盘价")
    return result


# ---------------------------------------------------------------------------
# 数据采集: 年度股息率（stock_fhps_em）
# ---------------------------------------------------------------------------

def collect_annual_yield(stock_code: str, conn: sqlite3.Connection,
                         years: int = ANALYSIS_YEARS):
    """
    从东方财富 stock_fhps_em 采集近N年年报的股息率数据。
    每年调用一次 stock_fhps_em(date='YYYY1231')，提取目标股票的数据。
    结果存入 dividend_annual_yield 表。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    collected = 0
    end_year = CURRENT_YEAR - 1  # 从上一个完整年度开始
    start_year = end_year - years + 1

    print(f"  采集 {stock_code} 年度股息率 ({start_year}-{end_year})...")

    for year in range(start_year, end_year + 1):
        date_str = f"{year}1231"
        try:
            df = ak.stock_fhps_em(date=date_str)
            if df is None or df.empty:
                print(f"    {year}: 无数据")
                time.sleep(0.5)
                continue

            # 筛选目标股票
            row = df[df["代码"] == stock_code]
            if row.empty:
                print(f"    {year}: 未找到该股票")
                time.sleep(0.5)
                continue

            row = row.iloc[0]
            dividend_yield = safe_val(row.get("现金分红-股息率"))
            cash_div_ratio = safe_val(row.get("现金分红-现金分红比例"))
            eps = safe_val(row.get("每股收益"))
            bps = safe_val(row.get("每股净资产"))

            # 从股息率和现金分红比例反算每股派息
            # 股息率 = 每股派息 / 股价 * 100
            # 现金分红比例 = 每股派息 / 每股收益 * 100
            dps = None
            if eps > 0 and cash_div_ratio > 0:
                dps = eps * cash_div_ratio / 100
            elif dividend_yield > 0 and bps > 0:
                # 粗略估算
                dps = None

            conn.execute("""
                INSERT OR REPLACE INTO dividend_annual_yield
                (stock_code, year, dividend_per_share, dividend_yield,
                 cash_dividend_ratio, eps, bps, year_end_price, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (stock_code, year, dps, dividend_yield,
                  cash_div_ratio, eps, bps, None, now))
            collected += 1
            print(f"    {year}: 股息率={fmt_pct(dividend_yield)}, 每股派息={dps}")
            time.sleep(0.5)

        except Exception as e:
            print(f"    {year}: 获取失败 - {e}")
            time.sleep(1.0)

    conn.commit()
    print(f"  -> 年度股息率: 成功采集 {collected} 年数据")
    return collected


# ---------------------------------------------------------------------------
# 数据采集: 股票回购
# ---------------------------------------------------------------------------

def collect_repurchase(stock_code: str, conn: sqlite3.Connection,
                       max_retries: int = 3, retry_delay: float = 1.0):
    """
    采集全市场股票回购数据，筛选目标股票，存入 stock_repurchase 表。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for attempt in range(1, max_retries + 1):
        try:
            print(f"  采集 {stock_code} 回购数据（全市场扫描）...")
            df = ak.stock_repurchase_em()
            if df is None or df.empty:
                print("  [!] 回购数据为空")
                return 0

            # 筛选目标股票
            mask = df["股票代码"].astype(str).str.strip() == stock_code
            stock_df = df[mask].copy()

            if stock_df.empty:
                print(f"  -> {stock_code} 无回购记录")
                return 0

            # 先删除旧数据
            conn.execute("DELETE FROM stock_repurchase WHERE stock_code = ?", (stock_code,))

            inserted = 0
            for _, row in stock_df.iterrows():
                conn.execute("""
                    INSERT INTO stock_repurchase
                    (stock_code, stock_name, repurchase_amount_lower, repurchase_amount_upper,
                     repurchased_amount, repurchased_qty, progress, start_date,
                     latest_announce_date, price_lower, price_upper,
                     repurchased_price_lower, repurchased_price_upper,
                     total_shares_ratio_lower, total_shares_ratio_upper, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    stock_code,
                    safe_str(row.get("股票简称")),
                    safe_val(row.get("计划回购金额区间-下限")),
                    safe_val(row.get("计划回购金额区间-上限")),
                    safe_val(row.get("已回购金额")),
                    safe_val(row.get("已回购股份数量")),
                    safe_str(row.get("实施进度")),
                    safe_str(row.get("回购起始时间")),
                    safe_str(row.get("最新公告日期")),
                    safe_val(row.get("计划回购价格区间-下限")),
                    safe_val(row.get("计划回购价格区间-上限")),
                    safe_val(row.get("已回购股份价格区间-下限")),
                    safe_val(row.get("已回购股份价格区间-上限")),
                    safe_val(row.get("占公告前一日总股本比例-下限")),
                    safe_val(row.get("占公告前一日总股本比例-上限")),
                    now,
                ))
                inserted += 1

            conn.commit()
            print(f"  -> 回购记录: {inserted} 条已存入 stock_repurchase")
            return inserted

        except Exception as e:
            if attempt < max_retries:
                time.sleep(retry_delay * attempt)
            else:
                print(f"  [!] {stock_code} 获取回购数据失败（重试 {max_retries} 次）: {e}")
                return 0
    return 0


# ---------------------------------------------------------------------------
# 综合分析
# ---------------------------------------------------------------------------

def analyze(stock_code: str, market: str, db_path: str = DB_PATH):
    """
    执行红利股四维分析，返回结构化结果 dict。
    """
    conn = get_conn(db_path)
    try:
        end_year = CURRENT_YEAR - 1
        start_year = end_year - ANALYSIS_YEARS + 1
        years_range = list(range(start_year, end_year + 1))

        result = {
            "stock_code": stock_code,
            "market": market,
            "start_year": start_year,
            "end_year": end_year,
            "dividends": [],        # 年度分红汇总
            "yields": [],           # 年度股息率
            "repurchases": [],      # 回购记录
            "cash_flows": [],       # 自由现金流
            "revenue": [],          # 营收与扣非利润
        }

        # ---- 1. 年度股息率 ----
        for year in years_range:
            row = conn.execute("""
                SELECT * FROM dividend_annual_yield
                WHERE stock_code = ? AND year = ?
            """, (stock_code, year)).fetchone()

            if row:
                cols = [d[0] for d in conn.execute("SELECT * FROM dividend_annual_yield LIMIT 0").description]
                rd = dict(zip(cols, row))
                result["yields"].append(rd)

        # ---- 2. 分红明细（按年汇总）+ 获取年末股价计算股息率 ----
        try:
            div_df = pd.read_sql("""
                SELECT * FROM stock_dividend_detail
                WHERE 代码 = ? ORDER BY 公告日期
            """, conn, params=(stock_code,))

            if not div_df.empty:
                # 按公告日期的年份汇总派息
                div_df["年份"] = pd.to_datetime(div_df["公告日期"], errors="coerce").dt.year
                div_df["派息"] = pd.to_numeric(div_df["派息"], errors="coerce")
                annual_div = div_df.groupby("年份").agg(
                    派息合计=("派息", "sum"),
                    分红次数=("派息", "count"),
                ).reset_index()
                annual_div = annual_div[
                    (annual_div["年份"] >= start_year) & (annual_div["年份"] <= end_year)
                ]

                # 获取历史年末收盘价
                year_end_prices = get_year_end_prices(stock_code, market, ANALYSIS_YEARS)

                # 加载 dividend_annual_yield 中的 EPS 数据（用于计算股息支付率）
                eps_map = {}  # year -> eps
                for year in years_range:
                    eps_row = conn.execute("""
                        SELECT year, eps FROM dividend_annual_yield
                        WHERE stock_code = ? AND year = ?
                    """, (stock_code, year)).fetchone()
                    if eps_row and eps_row[1] and eps_row[1] > 0:
                        eps_map[eps_row[0]] = eps_row[1]

                # 计算每股派息（每10股派X元 -> 每股 X/10）和股息率
                div_records = []
                for _, row in annual_div.iterrows():
                    yr = int(row["年份"])
                    div_per_10share = float(row["派息合计"] or 0)
                    dps = div_per_10share / 10  # 每股派息
                    price = year_end_prices.get(yr)
                    calc_yield = None
                    if price and price > 0 and dps > 0:
                        calc_yield = dps / price * 100  # 股息率 %
                    # 股息支付率 = 每股派息 / EPS * 100%
                    eps_val = eps_map.get(yr)
                    payout_ratio = None
                    if eps_val and eps_val > 0 and dps > 0:
                        payout_ratio = dps / eps_val * 100
                    div_records.append({
                        "年份": yr,
                        "派息合计": div_per_10share,
                        "分红次数": int(row["分红次数"]),
                        "每股派息": dps,
                        "年末收盘价": price,
                        "股息率": calc_yield,
                        "EPS": eps_val,
                        "股息支付率": payout_ratio,
                    })
                result["dividends"] = div_records
        except Exception:
            pass

        # ---- 3. 回购记录 ----
        try:
            rep_df = pd.read_sql("""
                SELECT * FROM stock_repurchase WHERE stock_code = ?
            """, conn, params=(stock_code,))

            if not rep_df.empty:
                result["repurchases"] = rep_df.to_dict("records")
        except Exception:
            pass

        # ---- 4. 自由现金流（从 em_cash_flow） ----
        for year in years_range:
            cf_row = conn.execute("""
                SELECT * FROM em_cash_flow
                WHERE stock_code = ? AND year = ? AND quarter = 4
                ORDER BY REPORT_DATE DESC LIMIT 1
            """, (stock_code, year)).fetchone()

            if cf_row:
                cols = [d[0] for d in conn.execute("SELECT * FROM em_cash_flow LIMIT 0").description]
                cf = dict(zip(cols, cf_row))

                netcash_operate = safe_val(cf.get("NETCASH_OPERATE"))
                # 购建固定资产、无形资产和其他长期资产支付的现金
                buy_fixasset = safe_val(cf.get("CONSTRUCT_LONG_ASSET"))
                # 自由现金流 = 经营现金流 - 资本支出
                fcf = netcash_operate - buy_fixasset

                result["cash_flows"].append({
                    "year": year,
                    "netcash_operate": netcash_operate,
                    "capex": buy_fixasset,
                    "fcf": fcf,
                })

        # ---- 5. 营收与扣非净利润（从 em_income_statement） ----
        for year in years_range:
            inc_row = conn.execute("""
                SELECT * FROM em_income_statement
                WHERE stock_code = ? AND year = ? AND quarter = 4
                ORDER BY REPORT_DATE DESC LIMIT 1
            """, (stock_code, year)).fetchone()

            if inc_row:
                cols = [d[0] for d in conn.execute("SELECT * FROM em_income_statement LIMIT 0").description]
                inc = dict(zip(cols, inc_row))

                revenue = safe_val(inc.get("TOTAL_OPERATE_INCOME"))
                deduct_profit = safe_val(inc.get("DEDUCT_PARENT_NETPROFIT"))
                parent_profit = safe_val(inc.get("PARENT_NETPROFIT"))

                # 同时获取该年经营现金流（用于扣非经营现金流对比）
                core_cf = None
                if result["cash_flows"]:
                    cf_item = next((c for c in result["cash_flows"] if c["year"] == year), None)
                    if cf_item:
                        core_cf = cf_item["netcash_operate"]

                result["revenue"].append({
                    "year": year,
                    "revenue": revenue,
                    "parent_profit": parent_profit,
                    "deduct_profit": deduct_profit,
                    "netcash_operate": core_cf,
                })

        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_report(r: dict):
    """格式化打印红利股分析报告"""
    sep = "=" * 70
    line = "-" * 70

    print()
    print(sep)
    print(f"  红利股分析报告")
    print(f"  {r['stock_code']}.{r['market']}  ({r['start_year']}-{r['end_year']})")
    print(sep)

    # ==== 一、股息率趋势 ====
    print()
    print("【一、近10年股息率趋势】")
    print(line)
    # 优先显示基于分红明细+年末股价计算的股息率
    dividends = r.get("dividends", [])
    if dividends:
        print(f"  {'年份':>6s}  {'每10股派息':>12s}  {'每股派息':>8s}  {'年末收盘价':>10s}  {'股息率':>8s}  {'股息支付率':>10s}  {'分红次数':>8s}")
        print(f"  {'----':>6s}  {'----------':>12s}  {'------':>8s}  {'--------':>10s}  {'------':>8s}  {'--------':>10s}  {'------':>8s}")
        calc_yield_values = []
        payout_values = []
        for d in dividends:
            yr = int(d.get("年份", 0))
            div_per_10 = d.get("派息合计", 0) or 0
            dps = d.get("每股派息", 0) or 0
            price = d.get("年末收盘价")
            calc_yield = d.get("股息率")
            payout = d.get("股息支付率")
            cnt = d.get("分红次数", 0) or 0
            if calc_yield is not None and calc_yield > 0:
                calc_yield_values.append(calc_yield)
            if payout is not None and payout > 0:
                payout_values.append(payout)
            price_str = f"{price:.2f}" if price else "N/A"
            yield_str = f"{calc_yield:.2f}%" if calc_yield is not None else "N/A"
            payout_str = f"{payout:.2f}%" if payout is not None else "N/A"
            dps_str = f"{dps:.4f}" if dps > 0 else "0"
            print(f"  {yr:>6d}  {f'{div_per_10:.4f}':>12s}  {dps_str:>8s}  {price_str:>10s}  {yield_str:>8s}  {payout_str:>10s}  {cnt:>8d}")
        if calc_yield_values:
            avg_yield = sum(calc_yield_values) / len(calc_yield_values)
            min_yield = min(calc_yield_values)
            max_yield = max(calc_yield_values)
            std_yield = (sum((v - avg_yield) ** 2 for v in calc_yield_values) / len(calc_yield_values)) ** 0.5
            print()
            print(f"  股息率统计（仅有分红的年份）: 均值={avg_yield:.2f}%  最低={min_yield:.2f}%  "
                  f"最高={max_yield:.2f}%  标准差={std_yield:.2f}%")
            print(f"  股息率稳定性: {'优秀' if std_yield < 1.0 else '良好' if std_yield < 2.0 else '波动较大'}"
                  f"（标准差<1%为优秀, <2%为良好）")
        if payout_values:
            avg_payout = sum(payout_values) / len(payout_values)
            min_payout = min(payout_values)
            max_payout = max(payout_values)
            print(f"  股息支付率统计: 均值={avg_payout:.2f}%  最低={min_payout:.2f}%  最高={max_payout:.2f}%")
            # 支付率评价
            if avg_payout > 80:
                payout_eval = "支付率过高，分红可能不可持续（>80%）"
            elif avg_payout > 50:
                payout_eval = "支付率偏高，需关注利润波动对分红的影响（50-80%）"
            elif avg_payout >= 20:
                payout_eval = "支付率合理，兼顾分红与留存发展（20-50%）"
            else:
                payout_eval = "支付率偏低，分红不够慷慨（<20%）"
            print(f"  股息支付率评价: {payout_eval}")
        if calc_yield_values:
            print(f"  计算方式: 股息率 = 每股派息 / 当年末收盘价 × 100%")
            print(f"           股息支付率 = 每股派息 / EPS × 100%")
        else:
            print()
            print("  （无有效股息率计算结果）")
    else:
        yields = r.get("yields", [])
        if yields:
            print(f"  {'年份':>6s}  {'股息率':>8s}  {'每股派息':>10s}  {'EPS':>8s}  {'分红比例':>8s}")
            print(f"  {'----':>6s}  {'------':>8s}  {'--------':>10s}  {'---':>8s}  {'------':>8s}")
            for y in yields:
                dy = y.get("dividend_yield")
                dps = y.get("dividend_per_share")
                eps = y.get("eps")
                ratio = y.get("cash_dividend_ratio")
                print(f"  {y['year']:>6d}  {fmt_pct(dy):>8s}  "
                      f"{f'{dps:.4f}' if dps and dps > 0 else 'N/A':>10s}  "
                      f"{f'{eps:.2f}' if eps and eps > 0 else 'N/A':>8s}  "
                      f"{fmt_pct(ratio):>8s}")
            print()
            print("  （以上股息率来自东方财富分红送配数据，非实际计算值）")
        else:
            print("  （无年度股息率数据，请先执行 collect）")

    # ==== 二、股票回购注销 ====
    print()
    print("【二、股票回购注销情况】")
    print(line)
    repurchases = r.get("repurchases", [])
    if repurchases:
        total_amount = 0
        print(f"  {'进度':<10s}  {'已回购金额(元)':>16s}  {'计划金额下限(元)':>16s}  {'计划金额上限(元)':>16s}  {'起始时间':<12s}")
        for rp in repurchases:
            progress = safe_str(rp.get("progress"), "未知")
            repurchased = safe_val(rp.get("repurchased_amount"))
            amount_lower = safe_val(rp.get("repurchase_amount_lower"))
            amount_upper = safe_val(rp.get("repurchase_amount_upper"))
            start = safe_str(rp.get("start_date"))[:10]

            if repurchased > 0 and progress in ("实施完成", "实施中"):
                total_amount += repurchased

            print(f"  {progress:<10s}  {repurchased:>16.0f}  {amount_lower:>16.0f}  {amount_upper:>16.0f}  {start:<12s}")

        print()
        print(f"  已完成/进行中回购总金额: {total_amount/1e8:.2f} 亿元")
        print()
        print("  ** 注意: 仅「回购注销」用途的金额可计入股息。")
        print("     请根据公告确认回购用途（注销 vs 员工持股/股权激励/可转债等）。")
        print("     非注销用途（如员工持股计划、股权激励）不计入股东回报。")
    else:
        print("  （无回购记录）")

    # ==== 三、自由现金流与分红可持续性 ====
    print()
    print("【三、自由现金流与分红可持续性】")
    print(line)
    cash_flows = r.get("cash_flows", [])
    # yields_map removed - now using dividends data

    if cash_flows:
        print(f"  {'年份':>6s}  {'经营现金流(亿)':>14s}  {'资本支出(亿)':>12s}  {'自由现金流(亿)':>14s}  {'FCF是否覆盖分红':>16s}")
        print(f"  {'----':>6s}  {'------------':>14s}  {'----------':>12s}  {'--------------':>14s}  {'--------------':>16s}")

        # 获取分红数据（需要每股派息和总股本来估算总分红）
        div_map = {}
        for d in dividends:
            yr = int(d.get("年份", 0))
            div_map[yr] = d.get("派息合计", 0) or 0

        for cf in cash_flows:
            yr = cf["year"]
            ocf = cf["netcash_operate"]
            capex = cf["capex"]
            fcf = cf["fcf"]

            # 判断FCF是否能覆盖分红
            coverage = "N/A"
            if fcf > 0:
                coverage = "正"
            elif fcf < 0:
                coverage = "负（需关注）"

            print(f"  {yr:>6d}  {ocf/1e8:>14.2f}  {capex/1e8:>12.2f}  {fcf/1e8:>14.2f}  {coverage:>16s}")

        # FCF 统计
        fcf_values = [cf["fcf"] for cf in cash_flows]
        positive_count = sum(1 for v in fcf_values if v > 0)
        print()
        print(f"  FCF为正的年份: {positive_count}/{len(fcf_values)}")
        if positive_count >= len(fcf_values) * 0.8:
            print("  评价: 自由现金流充裕，分红可持续性强")
        elif positive_count >= len(fcf_values) * 0.5:
            print("  评价: 自由现金流基本为正，分红可持续性中等")
        else:
            print("  评价: 自由现金流多数为负，分红可持续性存疑")
    else:
        print("  （无现金流量表数据，请先通过 collect_financial_data.py 采集财报）")

    # ==== 四、营收与扣非经营现金流（衰退判断） ====
    print()
    print("【四、营收与扣非经营现金流（衰退判断）】")
    print(line)
    revenue_data = r.get("revenue", [])

    if revenue_data:
        print(f"  {'年份':>6s}  {'营收(亿)':>12s}  {'营收增速':>8s}  {'扣非净利润(亿)':>14s}  {'经营现金流(亿)':>14s}  {'现金流/扣非利润':>16s}")
        print(f"  {'----':>6s}  {'--------':>12s}  {'------':>8s}  {'--------------':>14s}  {'--------------':>14s}  {'--------------':>16s}")

        prev_revenue = None
        for item in revenue_data:
            yr = item["year"]
            rev = item["revenue"]
            deduct = item["deduct_profit"]
            ocf = item["netcash_operate"]

            # 营收增速
            growth = "N/A"
            if prev_revenue and prev_revenue > 0:
                g = (rev - prev_revenue) / prev_revenue * 100
                growth = f"{g:>7.1f}%"
            prev_revenue = rev

            # 现金流/扣非利润比
            cf_ratio = "N/A"
            if ocf and deduct and deduct > 0:
                ratio = ocf / deduct
                cf_ratio = f"{ratio:>14.2f}x"

            print(f"  {yr:>6d}  {rev/1e8:>12.2f}  {growth:>8s}  {deduct/1e8:>14.2f}  "
                  f"{ocf/1e8 if ocf else 0:>14.2f}  {cf_ratio:>16s}")

        # 衰退判断
        print()
        revenues = [item["revenue"] for item in revenue_data if item["revenue"] > 0]
        deducts = [item["deduct_profit"] for item in revenue_data if item["deduct_profit"]]

        if len(revenues) >= 3:
            recent_3 = revenues[-3:]
            declining_revenue = all(recent_3[i] >= recent_3[i+1] for i in range(len(recent_3)-1))
            avg_deduct_recent = sum(deducts[-3:]) / min(3, len(deducts)) if deducts else 0
            avg_deduct_early = sum(deducts[:3]) / min(3, len(deducts)) if deducts else 0

            if declining_revenue and avg_deduct_recent < avg_deduct_early * 0.7:
                print("  警告: 近3年营收持续下降，且扣非利润显著萎缩，可能是衰退企业")
            elif declining_revenue:
                print("  注意: 近3年营收持续下降，需关注是否进入衰退期")
            else:
                print("  评价: 营收未出现持续下降，企业经营基本正常")

        if len(deducts) >= 3:
            negative_count = sum(1 for d in deducts[-3:] if d < 0)
            if negative_count >= 2:
                print("  警告: 近3年扣非净利润多次为负，盈利能力堪忧")
    else:
        print("  （无利润表数据，请先通过 collect_financial_data.py 采集财报）")

    print()
    print(sep)
    print("  分析完成。请结合以上四个维度综合判断该证券是否为优质红利股。")
    print(sep)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="红利股分析工具 - 从股息率、回购注销、自由现金流、营收健康度四维度评估",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 1. 采集财报数据（如尚未采集）
  python scripts/collect_financial_data.py collect --code 600519 --market sh --start-year 2016

  # 2. 采集红利分析数据（分红+回购）
  python scripts/dividend_stock_analysis.py collect --code 600519 --market sh

  # 3. 运行分析
  python scripts/dividend_stock_analysis.py analyze --code 600519 --market sh

  # 也可以只采集部分数据:
  python scripts/dividend_stock_analysis.py collect-yield --code 600519
  python scripts/dividend_stock_analysis.py collect-repurchase --code 600519
        """
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # collect: 采集全部数据（分红明细+股息率+回购）
    p_collect = subparsers.add_parser("collect", help="采集分红+回购数据")
    p_collect.add_argument("--code", required=True, help="股票代码")
    p_collect.add_argument("--market", required=True, help="市场: sh/sz")
    p_collect.add_argument("--years", type=int, default=ANALYSIS_YEARS, help="分析年数(默认10)")

    # collect-yield: 仅采集年度股息率
    p_yield = subparsers.add_parser("collect-yield", help="仅采集年度股息率(stock_fhps_em)")
    p_yield.add_argument("--code", required=True, help="股票代码")
    p_yield.add_argument("--years", type=int, default=ANALYSIS_YEARS, help="分析年数(默认10)")

    # collect-repurchase: 仅采集回购数据
    p_rep = subparsers.add_parser("collect-repurchase", help="仅采集股票回购数据")
    p_rep.add_argument("--code", required=True, help="股票代码")

    # analyze: 运行分析
    p_analyze = subparsers.add_parser("analyze", help="运行红利股分析")
    p_analyze.add_argument("--code", required=True, help="股票代码")
    p_analyze.add_argument("--market", required=True, help="市场: sh/sz")
    p_analyze.add_argument("--db", default=DB_PATH, help="数据库路径")

    args = parser.parse_args()

    conn = get_conn()
    init_tables(conn)
    conn.close()

    if args.command == "collect":
        print("=" * 60)
        print(f"  采集红利分析数据: {args.code}.{args.market}")
        print("=" * 60)

        conn = get_conn()
        try:
            print("\n[1/3] 采集分红明细...")
            collect_dividend_detail(args.code, conn)
            time.sleep(0.5)

            print("\n[2/3] 采集年度股息率...")
            collect_annual_yield(args.code, conn, args.years)

            print("\n[3/3] 采集股票回购数据...")
            collect_repurchase(args.code, conn)
        finally:
            conn.close()

        print("\n采集完成。下一步运行: analyze --code {} --market {}".format(args.code, args.market))

    elif args.command == "collect-yield":
        conn = get_conn()
        try:
            collect_annual_yield(args.code, conn, args.years)
        finally:
            conn.close()

    elif args.command == "collect-repurchase":
        conn = get_conn()
        try:
            collect_repurchase(args.code, conn)
        finally:
            conn.close()

    elif args.command == "analyze":
        result = analyze(args.code, args.market, args.db)
        print_report(result)


if __name__ == "__main__":
    main()
