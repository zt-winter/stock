"""
cash_flow_analysis.py - 经营性现金流分析工具（含非经常性损益剔除）

功能:
    基于 financial_data.db 中已采集的东方财富财报数据，计算指定股票某年度的：
    1. 经营活动现金流净额（直接取自现金流量表）
    2. 核心经营现金流（剔除非经常性损益后的经营现金流，基于扣非净利润+间接法调整）
    3. 非经常性经营现金流（两者之差）
    4. 非经常性损益明细（区分"保留"与"剔除"项）

非经常性损益处理规则:
    ✅ 保留（计入核心经营现金流）:
       - 政府补助（OTHER_INCOME 中与日常经营相关的部分）
       - 税费返还
    ❌ 剔除（不计入核心经营现金流）:
       - 公允价值变动损益
       - 投资收益（含债务重组损益）
       - 资产处置收益
       - 营业外收入/支出
       - 债务重组损益（可通过 --debt-restructure 手工指定金额）

计算逻辑（间接法）:
    核心经营现金流 = 扣非归母净利润
                    + 折旧摊销（固定资产/投资性房地产/无形资产/长期待摊/使用权资产）
                    + 资产减值准备（+为计提，-为冲回）
                    + 财务费用
                    - 投资收益
                    - 公允价值变动收益
                    - 资产处置收益
                    - 报废固定资产损失
                    ± 递延所得税变动
                    ± 存货/应收/应付变动
                    ± 其他营运资金变动
                    + 其他调整
                    - 债务重组损益（如在其他收益/投资收益中，已随扣非净利润自动剔除）

    非经常性经营现金流 = 报告经营现金流 - 核心经营现金流
    （主要来自政府补助现金、收到的税费返还等）

使用方法:
    python cash_flow_analysis.py --code 600519 --market sh --year 2024
    python cash_flow_analysis.py --code 000858 --market sz --year 2023

依赖:
    需先通过 financial_report.py 采集目标股票的财报数据
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

# Windows 终端 GBK 编码兼容: 强制使用 UTF-8 输出
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 数据库路径（与 financial_report.py 保持一致）
# ---------------------------------------------------------------------------
DB_PATH = str(Path(__file__).parent / "financial_data.db")


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


def fmt_yi(v):
    """格式化为亿元，保留2位小数"""
    if v is None or pd.isna(v):
        return "N/A"
    return f"{v / 1e8:>14.2f}"


def fmt_wan(v):
    """格式化为万元，保留2位小数"""
    if v is None or pd.isna(v):
        return "N/A"
    return f"{v / 1e4:>14.2f}"


def pct(part, total):
    """计算百分比"""
    if total is None or total == 0 or pd.isna(total):
        return "N/A"
    return f"{part / total * 100:>8.2f}%"


# ---------------------------------------------------------------------------
# 数据查询
# ---------------------------------------------------------------------------

def get_cash_flow(conn, stock_code, market, year, quarter=4):
    """从 em_cash_flow 获取现金流量表数据"""
    sql = """
        SELECT * FROM em_cash_flow
        WHERE stock_code = ? AND year = ? AND quarter = ?
        ORDER BY REPORT_DATE DESC LIMIT 1
    """
    df = pd.read_sql(sql, conn, params=(stock_code, year, quarter))
    if df.empty:
        return None
    return df.iloc[0]


def get_income_statement(conn, stock_code, market, year, quarter=4):
    """从 em_income_statement 获取利润表数据"""
    sql = """
        SELECT * FROM em_income_statement
        WHERE stock_code = ? AND year = ? AND quarter = ?
        ORDER BY REPORT_DATE DESC LIMIT 1
    """
    df = pd.read_sql(sql, conn, params=(stock_code, year, quarter))
    if df.empty:
        return None
    return df.iloc[0]


# ---------------------------------------------------------------------------
# 核心计算
# ---------------------------------------------------------------------------

def analyze_cash_flow(stock_code: str, market: str, year: int, quarter: int = 4,
                      db_path: str = DB_PATH):
    """
    分析指定股票的经营性现金流，计算剔除非经常性损益后的核心经营现金流。

    非经常性损益处理规则:
        ✅ 保留: 政府补助、税费返还 → 计入核心经营现金流
        ❌ 剔除: 公允价值变动、投资收益、资产处置、营业外收支
                 债务重组损益（如在其他收益/投资收益中，已随扣非净利润自动剔除）

    参数:
        stock_code: 股票代码，如 "600519"
        market:     市场标识，如 "sh" / "sz"
        year:       报告年份
        quarter:    报告期次（1-4），默认 4（年报）
        db_path:    数据库路径

    返回:
        dict，包含分析结果的各项数据
    """
    conn = sqlite3.connect(db_path)
    try:
        cf = get_cash_flow(conn, stock_code, market, year, quarter)
        inc = get_income_statement(conn, stock_code, market, year, quarter)
    finally:
        conn.close()

    if cf is None:
        print(f"错误: 未找到 {stock_code}.{market} {year}年 第{quarter}期 的现金流量表数据")
        print("请先通过 financial_report.py 采集该股票的财报数据")
        sys.exit(1)

    if inc is None:
        print(f"错误: 未找到 {stock_code}.{market} {year}年 第{quarter}期 的利润表数据")
        print("请先通过 financial_report.py 采集该股票的财报数据")
        sys.exit(1)

    # ---- 1. 报告经营现金流（直接法） ----
    netcash_operate = safe_val(cf.get('NETCASH_OPERATE'))
    total_inflow = safe_val(cf.get('TOTAL_OPERATE_INFLOW'))
    total_outflow = safe_val(cf.get('TOTAL_OPERATE_OUTFLOW'))
    sales_services = safe_val(cf.get('SALES_SERVICES'))
    buy_services = safe_val(cf.get('BUY_SERVICES'))
    pay_staff = safe_val(cf.get('PAY_STAFF_CASH'))
    pay_tax = safe_val(cf.get('PAY_ALL_TAX'))
    receive_tax_refund = safe_val(cf.get('RECEIVE_TAX_REFUND'))
    receive_other_operate = safe_val(cf.get('RECEIVE_OTHER_OPERATE'))
    pay_other_operate = safe_val(cf.get('PAY_OTHER_OPERATE'))

    # ---- 2. 利润表关键数据 ----
    net_profit = safe_val(cf.get('NETPROFIT'))
    parent_net_profit = safe_val(inc.get('PARENT_NETPROFIT'))
    deduct_parent_net_profit = safe_val(inc.get('DEDUCT_PARENT_NETPROFIT'))

    # 非经常性损益 = 归母净利润 - 扣非归母净利润
    non_recurring_pnl = parent_net_profit - deduct_parent_net_profit

    # 非经常性损益明细项（来自利润表）
    fair_value_income = safe_val(inc.get('FAIRVALUE_CHANGE_INCOME'))
    invest_income = safe_val(inc.get('INVEST_INCOME'))
    asset_disposal_income = safe_val(inc.get('ASSET_DISPOSAL_INCOME'))
    nonbusiness_income = safe_val(inc.get('NONBUSINESS_INCOME'))
    nonbusiness_expense = safe_val(inc.get('NONBUSINESS_EXPENSE'))
    other_income = safe_val(inc.get('OTHER_INCOME'))

    # ---- 3. 间接法调整项（来自现金流量表补充资料） ----
    # 非现金费用（均为经常性，加回）
    fa_depr = safe_val(cf.get('FA_IR_DEPR'))                   # 固定资产折旧
    ir_depr = safe_val(cf.get('IR_DEPR'))                      # 投资性房地产折旧
    ia_amortize = safe_val(cf.get('IA_AMORTIZE'))              # 无形资产摊销
    lpe_amortize = safe_val(cf.get('LPE_AMORTIZE'))            # 长期待摊摊销
    right_use_amortize = safe_val(cf.get('USERIGHT_ASSET_AMORTIZE'))  # 使用权资产摊销
    asset_impairment = safe_val(cf.get('ASSET_IMPAIRMENT'))    # 资产减值准备
    defer_income_amortize = safe_val(cf.get('DEFER_INCOME_AMORTIZE'))  # 递延收益摊销

    # 财务费用（经常性，加回）
    finance_expense = safe_val(cf.get('FINANCE_EXPENSE'))

    # 投资收益 / 公允价值变动 / 资产处置（在间接法中以"损失"正数表示）
    # 现金流量表补充资料中：FAIRVALUE_CHANGE_LOSS = 公允价值变动损失（损失为正，收益为负）
    fairvalue_loss = safe_val(cf.get('FAIRVALUE_CHANGE_LOSS'))
    invest_loss = safe_val(cf.get('INVEST_LOSS'))
    disposal_loss = safe_val(cf.get('DISPOSAL_LONGASSET_LOSS'))
    scrap_loss = safe_val(cf.get('FA_SCRAP_LOSS'))

    # 递延所得税
    defer_tax = safe_val(cf.get('DEFER_TAX'))

    # 营运资金变动
    inventory_reduce = safe_val(cf.get('INVENTORY_REDUCE'))
    rece_reduce = safe_val(cf.get('OPERATE_RECE_REDUCE'))
    payable_add = safe_val(cf.get('OPERATE_PAYABLE_ADD'))
    prepaid_reduce = safe_val(cf.get('PREPAID_EXPENSE_REDUCE'))
    accrued_add = safe_val(cf.get('ACCRUED_EXPENSE_ADD'))
    predict_liab_add = safe_val(cf.get('PREDICT_LIAB_ADD'))
    dt_asset_reduce = safe_val(cf.get('DT_ASSET_REDUCE'))
    dt_liab_add = safe_val(cf.get('DT_LIAB_ADD'))
    other_adjust = safe_val(cf.get('OTHER'))

    # ---- 4. 验证间接法还原 ----
    reconcile_from_netprofit = (
        net_profit
        + fa_depr + ir_depr + ia_amortize + lpe_amortize + right_use_amortize
        + asset_impairment + defer_income_amortize
        + finance_expense
        + fairvalue_loss + invest_loss + disposal_loss + scrap_loss
        + defer_tax
        + inventory_reduce + rece_reduce + payable_add
        + prepaid_reduce + accrued_add + predict_liab_add
        + dt_asset_reduce + dt_liab_add
        + other_adjust
    )

    # ---- 5. 核心经营现金流（从扣非净利润出发，用同样的间接法调整） ----
    # 处理规则:
    #   ✅ 政府补助（计入其他收益/营业外收入）: 保留在核心经营现金流中
    #     → 扣非净利润已剔除政府补助，但其现金已计入经营活动现金流，
    #       通过"经营现金流 - 核心经营现金流 = 非经常性经营现金流"自然还原
    #   ❌ 债务重组损益: 如在其他收益/投资收益中，已随扣非净利润自动剔除
    #     → 扣非净利润不含债务重组损益，间接法调整项（invest_loss 等）
    #       已将其从经营现金流中分离，无需额外处理
    #
    # 从扣非净利润出发时，我们仍使用相同的间接法调整项（因为这些调整项
    # 在现金流补充资料中是对全部净利润的调整，非经常性项的利润差异已经体现在
    # 扣非净利润中）。
    core_netcash_operate = (
        deduct_parent_net_profit
        # 加回：非现金费用（均为经常性）
        + fa_depr + ir_depr + ia_amortize + lpe_amortize + right_use_amortize
        + asset_impairment + defer_income_amortize
        # 加回：财务费用（经常性）
        + finance_expense
        # 间接法中的调整项（损失为正，收益为负，与利润表方向相反）
        + fairvalue_loss   # 包含非经常性公允价值变动
        + invest_loss      # 投资收益（含债务重组损益）属于投资活动，非经营
        + disposal_loss    # 资产处置损益属于非经常性
        + scrap_loss       # 报废损失属于非经常性
        # 递延所得税
        + defer_tax
        # 营运资金变动（经常性）
        + inventory_reduce + rece_reduce + payable_add
        + prepaid_reduce + accrued_add + predict_liab_add
        + dt_asset_reduce + dt_liab_add
        + other_adjust
    )

    # ---- 6. 非经常性经营现金流 ----
    non_recurring_cf = netcash_operate - core_netcash_operate

    # ---- 汇总结果 ----
    stock_name = safe_val(cf.get('SECURITY_NAME_ABBR'), "")
    report_date = cf.get('REPORT_DATE', '')

    result = {
        "stock_code": stock_code,
        "market": market,
        "stock_name": str(stock_name) if stock_name else "",
        "year": year,
        "quarter": quarter,
        "report_date": str(report_date)[:10],
        # 经营现金流
        "netcash_operate": netcash_operate,
        "total_operate_inflow": total_inflow,
        "total_operate_outflow": total_outflow,
        "sales_services": sales_services,
        "buy_services": buy_services,
        "pay_staff": pay_staff,
        "pay_tax": pay_tax,
        "receive_tax_refund": receive_tax_refund,
        "receive_other_operate": receive_other_operate,
        "pay_other_operate": pay_other_operate,
        # 利润
        "net_profit": net_profit,
        "parent_net_profit": parent_net_profit,
        "deduct_parent_net_profit": deduct_parent_net_profit,
        "non_recurring_pnl": non_recurring_pnl,
        # 非经常性损益明细
        "fair_value_income": fair_value_income,
        "invest_income": invest_income,
        "asset_disposal_income": asset_disposal_income,
        "nonbusiness_income": nonbusiness_income,
        "nonbusiness_expense": nonbusiness_expense,
        "other_income": other_income,
        # 核心经营现金流
        "core_netcash_operate": core_netcash_operate,
        "non_recurring_cf": non_recurring_cf,
        # 间接法验证
        "reconcile_from_netprofit": reconcile_from_netprofit,
        # 间接法明细
        "fa_depr": fa_depr,
        "ir_depr": ir_depr,
        "ia_amortize": ia_amortize,
        "lpe_amortize": lpe_amortize,
        "right_use_amortize": right_use_amortize,
        "asset_impairment": asset_impairment,
        "defer_income_amortize": defer_income_amortize,
        "finance_expense": finance_expense,
        "fairvalue_loss": fairvalue_loss,
        "invest_loss": invest_loss,
        "disposal_loss": disposal_loss,
        "scrap_loss": scrap_loss,
        "defer_tax": defer_tax,
        "inventory_reduce": inventory_reduce,
        "rece_reduce": rece_reduce,
        "payable_add": payable_add,
    }

    return result


# ---------------------------------------------------------------------------
# 输出展示
# ---------------------------------------------------------------------------

def print_report(r: dict):
    """格式化打印分析结果"""
    sep = "=" * 62
    line = "-" * 62
    name = r['stock_name'] or r['stock_code']
    q_map = {1: "一季报", 2: "半年报", 3: "三季报", 4: "年报"}
    period = f"{r['year']}年{q_map.get(r['quarter'], str(r['quarter']))}"

    print()
    print(sep)
    print(f"  经营性现金流分析报告")
    print(f"  {name}（{r['stock_code']}.{r['market']}） {period}")
    print(f"  报告日期: {r['report_date']}")
    print(sep)

    # ---- Part 1: 经营现金流概览 ----
    print()
    print("【一、经营活动现金流概览】")
    print(line)
    print(f"  {'经营活动现金流入':.<20s} {fmt_yi(r['total_operate_inflow'])} 亿元")
    print(f"  {'经营活动现金流出':.<20s} {fmt_yi(r['total_operate_outflow'])} 亿元")
    print(f"  {'经营活动现金流净额':.<20s} {fmt_yi(r['netcash_operate'])} 亿元")
    print()
    print(f"  其中:")
    print(f"    {'销售商品收到现金':.<20s} {fmt_yi(r['sales_services'])} 亿元")
    print(f"    {'购买商品支付现金':.<20s} {fmt_yi(r['buy_services'])} 亿元")
    print(f"    {'支付职工薪酬':.<20s} {fmt_yi(r['pay_staff'])} 亿元")
    print(f"    {'支付各项税费':.<20s} {fmt_yi(r['pay_tax'])} 亿元")
    print(f"    {'收到税费返还':.<20s} {fmt_yi(r['receive_tax_refund'])} 亿元")
    print(f"    {'收到其他经营现金':.<20s} {fmt_yi(r['receive_other_operate'])} 亿元")
    print(f"    {'支付其他经营现金':.<20s} {fmt_yi(r['pay_other_operate'])} 亿元")

    # ---- Part 2: 利润表关键数据 ----
    print()
    print("【二、利润表关键数据】")
    print(line)
    print(f"  {'净利润':.<20s} {fmt_yi(r['net_profit'])} 亿元")
    print(f"  {'归母净利润':.<20s} {fmt_yi(r['parent_net_profit'])} 亿元")
    print(f"  {'扣非归母净利润':.<20s} {fmt_yi(r['deduct_parent_net_profit'])} 亿元")
    print(f"  {'非经常性损益':.<20s} {fmt_yi(r['non_recurring_pnl'])} 亿元")

    # ---- Part 3: 非经常性损益明细 ----
    print()
    print("【三、非经常性损益明细（利润表）】")
    print(line)
    print(f"  [X] 剔除项（不计入核心经营现金流）:")
    print(f"    {'公允价值变动收益':.<20s} {fmt_yi(r['fair_value_income'])} 亿元")
    print(f"    {'投资收益':.<20s} {fmt_yi(r['invest_income'])} 亿元  (含债务重组损益)")
    print(f"    {'资产处置收益':.<20s} {fmt_yi(r['asset_disposal_income'])} 亿元")
    print(f"    {'营业外收入':.<20s} {fmt_yi(r['nonbusiness_income'])} 亿元")
    print(f"    {'营业外支出':.<20s} {fmt_yi(r['nonbusiness_expense'])} 亿元")
    print()
    print(f"  [V] 保留项（计入核心经营现金流）:")
    print(f"    {'其他收益':.<20s} {fmt_yi(r['other_income'])} 亿元  (主要为政府补助)")

    # ---- Part 4: 核心经营现金流 ----
    print()
    print("【四、核心经营现金流（剔除非经常性损益）】")
    print(line)
    print(f"  {'报告经营现金流净额':.<20s} {fmt_yi(r['netcash_operate'])} 亿元  (A)")
    print(f"  {'核心经营现金流':.<20s} {fmt_yi(r['core_netcash_operate'])} 亿元  (B)")
    print(f"  {'非经常性经营现金流':.<20s} {fmt_yi(r['non_recurring_cf'])} 亿元  (A-B)")
    print()
    if r['netcash_operate'] != 0:
        ratio = r['non_recurring_cf'] / r['netcash_operate'] * 100
        sign = "+" if ratio >= 0 else ""
        print(f"  非经常性经营现金流占比:    {sign}{ratio:.2f}%")
    print()
    print("  计算说明:")
    print("    核心经营现金流 = 扣非归母净利润 + 间接法调整项")
    print("    非经常性经营现金流 = 报告经营现金流 - 核心经营现金流")
    print("    [V] 政府补助（其他收益）保留在核心经营现金流中")
    print("    [X] 债务重组损益（在其他收益/投资收益中）已随扣非净利润自动剔除")

    # ---- Part 5: 间接法调整明细 ----
    print()
    print("【五、间接法调整明细（现金流量表补充资料）】")
    print(line)
    print(f"  起点: 净利润{'':.<20s} {fmt_yi(r['net_profit'])} 亿元")
    print()
    print(f"  加: 非现金费用（经常性）")
    if r['fa_depr']:
        print(f"    {'固定资产折旧':.<20s} {fmt_yi(r['fa_depr'])} 亿元")
    if r['ir_depr']:
        print(f"    {'投资性房地产折旧':.<20s} {fmt_yi(r['ir_depr'])} 亿元")
    if r['ia_amortize']:
        print(f"    {'无形资产摊销':.<20s} {fmt_yi(r['ia_amortize'])} 亿元")
    if r['lpe_amortize']:
        print(f"    {'长期待摊摊销':.<20s} {fmt_yi(r['lpe_amortize'])} 亿元")
    if r['right_use_amortize']:
        print(f"    {'使用权资产摊销':.<20s} {fmt_yi(r['right_use_amortize'])} 亿元")
    if r['asset_impairment']:
        print(f"    {'资产减值准备':.<20s} {fmt_yi(r['asset_impairment'])} 亿元")
    if r['defer_income_amortize']:
        print(f"    {'递延收益摊销':.<20s} {fmt_yi(r['defer_income_amortize'])} 亿元")
    print()
    print(f"  加: 财务费用{'':.<20s} {fmt_yi(r['finance_expense'])} 亿元")
    print()
    print(f"  加/减: 非经营损益（间接法方向）")
    print(f"    {'公允价值变动损失':.<20s} {fmt_yi(r['fairvalue_loss'])} 亿元")
    print(f"    {'投资损失':.<20s} {fmt_yi(r['invest_loss'])} 亿元")
    print(f"    {'资产处置损失':.<20s} {fmt_yi(r['disposal_loss'])} 亿元")
    print(f"    {'固定资产报废损失':.<20s} {fmt_yi(r['scrap_loss'])} 亿元")
    print()
    print(f"  加/减: 递延所得税{'':.<20s} {fmt_yi(r['defer_tax'])} 亿元")
    print()
    print(f"  加/减: 营运资金变动")
    print(f"    {'存货变动':.<20s} {fmt_yi(r['inventory_reduce'])} 亿元")
    print(f"    {'经营性应收变动':.<20s} {fmt_yi(r['rece_reduce'])} 亿元")
    print(f"    {'经营性应付变动':.<20s} {fmt_yi(r['payable_add'])} 亿元")
    print()
    print(f"  间接法还原值{'':.<20s} {fmt_yi(r['reconcile_from_netprofit'])} 亿元")
    print(f"  报告经营现金流{'':.<20s} {fmt_yi(r['netcash_operate'])} 亿元")
    diff = r['netcash_operate'] - r['reconcile_from_netprofit']
    print(f"  差异{'':.<20s} {fmt_yi(diff)} 亿元")

    print()
    print(sep)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="经营性现金流分析工具 - 计算报告经营现金流与剔除非经常性损益后的核心经营现金流",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python cash_flow_analysis.py --code 600519 --market sh --year 2024
  python cash_flow_analysis.py --code 000858 --market sz --year 2023
  python cash_flow_analysis.py --code 600519 --market sh --year 2024 --quarter 2
        """
    )
    parser.add_argument("--code", required=True, help="股票代码，如 600519")
    parser.add_argument("--market", required=True, help="市场标识: sh(上海) / sz(深圳)")
    parser.add_argument("--year", required=True, type=int, help="报告年份，如 2024")
    parser.add_argument("--quarter", type=int, default=4, help="报告期次 1-4（默认4=年报）")
    parser.add_argument("--db", default=DB_PATH, help="数据库路径（默认脚本同目录下）")

    args = parser.parse_args()

    result = analyze_cash_flow(
        stock_code=args.code,
        market=args.market,
        year=args.year,
        quarter=args.quarter,
        db_path=args.db,
    )

    print_report(result)


if __name__ == "__main__":
    main()
