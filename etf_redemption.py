# -*- coding: utf-8 -*-
"""
ETF申赎清单查询工具
从上交所(SSE)和深交所(SZSE)网站获取当日ETF申购赎回清单数据。
支持：
  - 上交所: 通过 query.sse.com.cn API 查询ETF申赎清单列表和成份股明细
  - 深交所: 通过 szse.cn API 查询列表，并下载解析 reportdocs.static.szse.cn 的PCF文本文件

用法:
    python etf_redemption.py                                  # 查询全部ETF申赎清单
    python etf_redemption.py --market sh                      # 仅查询上交所
    python etf_redemption.py --market sz                      # 仅查询深交所
    python etf_redemption.py --fund-code 510300               # 按基金代码查询
    python etf_redemption.py --keyword 沪深300                # 按关键字查询
    python etf_redemption.py --date 2026-06-27                # 指定日期查询
    python etf_redemption.py --etf-class 01                   # 按ETF分类查询(01股票/02债券/06商品/33跨境)
    python etf_redemption.py --detail --fund-code 159008      # 查询深交所ETF详细申赎清单(PCF文件)
    python etf_redemption.py --detail --fund-code 510300      # 查询上交所ETF详细申赎清单
"""

import argparse
import json
import re
import time
from datetime import datetime

import pandas as pd
import requests


# ==================== 上交所(SSE) ====================

def query_sse_etf_list(etf_class="", fund_code="", keyword="", page_size=100):
    """
    查询上交所ETF申赎清单列表

    参数:
        etf_class: ETF分类代码，空字符串表示全部
                   01=股票ETF, 02=债券ETF, 06=商品ETF, 33/34/35=跨境ETF, 38=商品ETF
        fund_code: 基金代码(6位数字)，空字符串表示全部
        keyword: 关键字搜索(基金名称)，空字符串表示全部
        page_size: 每页条数，默认100
    返回:
        DataFrame，包含基金代码、名称、管理公司、净值、交易日期等字段
    """
    url = "https://query.sse.com.cn/commonQuery.do"
    timestamp = int(time.time() * 1000)

    params = {
        "isPagination": "true",
        "pageHelp.pageSize": str(page_size),
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1",
        "pageHelp.endPage": "1",
        "sqlId": "COMMON_SSE_PL_ETFGGSGSHQD_L",
        "ETF_CLASS": etf_class,
        "type": "inParams",
        "FUND_CODE": fund_code,
        "KEY_WORDS": keyword,
        "_": str(timestamp),
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
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

    # 响应为JSONP格式，需要提取JSON部分
    json_match = re.search(r'jsonpCallback\d*\((.*)\)', text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # 如果不是JSONP，直接尝试解析
        json_str = text

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"[上交所] JSON解析失败: {e}")
        return pd.DataFrame()

    result_list = data.get("result", [])
    if not result_list:
        print("[上交所] 未查询到数据")
        return pd.DataFrame()

    # 构建DataFrame
    rows = []
    for item in result_list:
        rows.append({
            "基金代码": item.get("FUNDID2", ""),
            "基金名称": item.get("ETF_FULLNAME", ""),
            "管理公司": item.get("FUND_COMP_NAME", ""),
            "基金份额净值": item.get("NAV", ""),
            "交易日期": item.get("TRADING_DAY", ""),
            "市场": "sh",
        })

    df = pd.DataFrame(rows)
    return df


def query_sse_etf_detail(fund_code, etf_type=""):
    """
    查询上交所单只ETF的申赎清单详细信息（成份股明细）

    通过 commonQuery.do 接口，无需预先获取ETF类型参数。
    数据来源页面: https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml?fundid={fund_code}

    参数:
        fund_code: 基金代码(6位数字)，如 510010
        etf_type: 已废弃，保留向后兼容，不再需要
    返回:
        (基本信息dict, 成份股列表DataFrame)
    """
    timestamp = int(time.time() * 1000)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": f"https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml?fundid={fund_code}",
        "Host": "query.sse.com.cn",
    }

    # ---- 获取基本信息 ----
    base_url = "https://query.sse.com.cn/commonQuery.do"
    basic_params = {
        "isPagination": "false",
        "FUNDID2": fund_code,
        "sqlId": "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C",
        "_": str(timestamp),
    }

    basic_info = None
    try:
        resp = requests.get(base_url, params=basic_params, headers=headers, timeout=15)
        resp.raise_for_status()
        text = resp.text
        json_match = re.search(r'jsonpCallback\d*\((.*)\)', text, re.DOTALL)
        json_str = json_match.group(1) if json_match else text
        data = json.loads(json_str)
        result = data.get("result", [])
        if result:
            item = result[0]
            # 将原始字段映射为中文友好名称
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
            # 移除空值字段
            basic_info = {k: v for k, v in basic_info.items() if v}
    except Exception as e:
        print(f"[上交所] 获取基本信息失败: {e}")

    # ---- 获取成份股明细 ----
    component_params = {
        "isPagination": "false",
        "FUNDID2": fund_code,
        "sqlId": "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_COMPONENT_C",
        "_": str(timestamp + 1),
    }
    stock_df = pd.DataFrame()
    try:
        resp = requests.get(base_url, params=component_params, headers=headers, timeout=15)
        resp.raise_for_status()
        text = resp.text
        json_match = re.search(r'jsonpCallback\d*\((.*)\)', text, re.DOTALL)
        json_str = json_match.group(1) if json_match else text
        data = json.loads(json_str)
        stock_list = data.get("result", [])
        if stock_list:
            rows = []
            for item in stock_list:
                rows.append({
                    "证券代码": item.get("INSTRUMENT_ID", ""),
                    "证券名称": item.get("INSTRUMENT_NAME", ""),
                    "数量": item.get("QUANTITY", ""),
                    "现金替代标志": item.get("SUBSTITUTION_FLAG", ""),
                    "申购现金替代溢价比例": item.get("CREATION_PREMIUM_RATE", ""),
                    "赎回现金替代折价比例": item.get("REDEMPTION_DISCOUNT_RATE", ""),
                    "替代金额": item.get("SUBSTITUTION_CASH_AMOUNT", ""),
                    "成份证券标识": item.get("UNDERLYION_SECURITY_ID", ""),
                })
            stock_df = pd.DataFrame(rows)
    except Exception as e:
        print(f"[上交所] 获取成份股明细失败: {e}")

    return basic_info, stock_df


def download_sse_pcf(fund_code, save_path=None):
    """
    下载上交所ETF申赎清单PCF文件

    数据来源: https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode={fund_code}

    参数:
        fund_code: 基金代码(6位数字)
        save_path: 保存路径，默认保存到当前目录
    返回:
        保存的文件路径，失败返回None
    """
    url = f"https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode={fund_code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml?fundid={fund_code}",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[上交所] 下载PCF文件失败: {e}")
        return None

    if save_path is None:
        date_str = datetime.now().strftime("%Y%m%d")
        save_path = f"ETF{fund_code}_{date_str}.txt"

    with open(save_path, "wb") as f:
        f.write(resp.content)
    print(f"[上交所] PCF文件已保存: {save_path}")
    return save_path


# ==================== 深交所(SZSE) ====================

# 深交所PCF文本文件基础URL
SZSE_PCF_BASE_URL = "https://reportdocs.static.szse.cn/files/text/etf/"


def query_szse_etf_list(fund_code="", date=None):
    """
    查询深交所ETF申赎清单列表

    参数:
        fund_code: 基金代码(6位数字)，空字符串表示全部
        date: 日期字符串，格式 "YYYY-MM-DD"，默认为当天
    返回:
        DataFrame，包含基金代码、基金名称、PCF文件链接、交易日期等字段
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    url = "https://www.szse.cn/api/report/ShowReport/data"
    date_compact = date.replace("-", "")  # YYYYMMDD

    params = {
        "SHOWTYPE": "JSON",
        "CATALOGID": "sgshqd",
        "loading": "first",
        "TABKEY": "tab1",
        "txtJCorDH": fund_code,
        "txtStart": date,
        "txtEnd": date,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://www.szse.cn/disclosure/fund/currency/index.html",
        "Host": "www.szse.cn",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[深交所] 请求失败: {e}")
        return pd.DataFrame()
    except json.JSONDecodeError as e:
        print(f"[深交所] JSON解析失败: {e}")
        return pd.DataFrame()

    # 深交所返回的数据结构：[{"metadata":{...}, "data":[...]}]
    if not data or not isinstance(data, list):
        print("[深交所] 未查询到数据")
        return pd.DataFrame()

    rows = []
    for tab in data:
        metadata = tab.get("metadata", {})
        total_records = metadata.get("recordcount", 0)
        tab_data = tab.get("data", [])
        for item in tab_data:
            # jjdm 字段包含 HTML，其中有 encode-open 属性指向PCF文件路径
            jjdm_html = item.get("jjdm", "")
            # 提取 encode-open 属性中的文件路径
            file_path_match = re.search(r"encode-open='([^']+)'", jjdm_html)
            pcf_file_url = ""
            if file_path_match:
                # encode-open 值为 /files/text/etf/ETF15900820260629.txt 格式
                pcf_path = file_path_match.group(1).lstrip("/")
                # 提取文件名部分 ETFxxxxxxxx.txt
                pcf_filename = pcf_path.split("/")[-1]
                pcf_file_url = f"{SZSE_PCF_BASE_URL}{pcf_filename}"

            # 从HTML中提取ETF代码
            code_match = re.search(r'ETF(\d{6})', jjdm_html)
            fund_code_raw = code_match.group(1) if code_match else ""

            # 提取基金名称
            name_match = re.search(r'>([^<]+)申购赎回清单', jjdm_html)
            fund_name_raw = name_match.group(1).strip() if name_match else ""

            rows.append({
                "基金代码": fund_code_raw,
                "基金名称": fund_name_raw,
                "交易日期": date,
                "PCF文件链接": pcf_file_url,
                "市场": "sz",
            })

    if not rows:
        print("[深交所] 未查询到数据")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    return df


def download_szse_pcf(fund_code, date=None):
    """
    下载并解析深交所ETF申赎清单PCF文本文件

    PCF文件格式（GBK编码）包含：
    - 基本信息：基金名称、管理公司、基金代码、目标指数、类型
    - 上一交易日信息：现金差额、申赎单位净值、基金份额净值
    - 当日信息：预估现金差额、现金替代比例上限、IOPV标志、申赎单位、申赎上限等
    - 成份股明细表：证券代码、名称、数量、现金替代标志、保证金率、替代金额等

    参数:
        fund_code: 基金代码(6位数字)
        date: 日期字符串，格式 "YYYY-MM-DD" 或 "YYYYMMDD"，默认为当天
    返回:
        (基本信息dict, 成份股列表DataFrame)
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    else:
        date = date.replace("-", "")  # 统一转为YYYYMMDD

    # 构造PCF文件URL
    pcf_url = f"{SZSE_PCF_BASE_URL}ETF{fund_code}{date}.txt"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.szse.cn/disclosure/fund/currency/index.html",
    }

    try:
        resp = requests.get(pcf_url, headers=headers, timeout=15)
        resp.raise_for_status()
        # PCF文件使用GBK编码
        text = resp.content.decode("gbk", errors="replace")
    except requests.RequestException as e:
        print(f"[深交所] 下载PCF文件失败 ({pcf_url}): {e}")
        return None, pd.DataFrame()

    return _parse_szse_pcf_text(text)


def _parse_szse_pcf_text(text):
    """
    解析深交所PCF文本文件内容

    返回:
        (基本信息dict, 成份股列表DataFrame)
    """
    lines = text.splitlines()
    basic_info = {}
    stock_rows = []

    # 状态机：0=头部, 1=上一交易日, 2=当日信息, 3=成份股表头, 4=成份股数据
    section = 0

    for line in lines:
        line = line.strip()
        if not line or line.startswith("-"):
            if section == 0 and line.startswith("-"):
                section = 1
            elif section == 1 and line.startswith("-"):
                section = 2
            elif section == 2 and line.startswith("-"):
                section = 3
            elif section >= 3 and line.startswith("-"):
                section = 5  # 结束
            continue

        # 解析键值对（基本信息和交易日信息）
        kv_match = re.match(r'^(.+?)：\s*(.+?)\s*$', line)
        if kv_match and section < 4:
            key = kv_match.group(1).strip()
            value = kv_match.group(2).strip()
            basic_info[key] = value
            continue

        # 解析成份股数据行（以3-6位数字代码开头，支持港股短码）
        stock_match = re.match(r'^(\d{3,6})\s+(.+)$', line)
        if stock_match:
            code = stock_match.group(1)
            rest = stock_match.group(2)
            # 按空白分割剩余字段
            fields = rest.split()
            if len(fields) >= 3:
                row = {"证券代码": code}
                if len(fields) >= 8:
                    # A股格式（8字段）: 含申购替代金额 + 赎回替代金额 + 市场
                    field_names = [
                        "证券名称", "数量", "现金替代标志",
                        "申购现金替代保证金率", "赎回现金替代保证金率",
                        "申购现金替代金额", "赎回现金替代金额", "市场",
                    ]
                elif len(fields) >= 7:
                    # 跨境ETF格式（7字段）: 无赎回替代金额，申购替代金额后直接是市场
                    field_names = [
                        "证券名称", "数量", "现金替代标志",
                        "申购现金替代保证金率", "赎回现金替代保证金率",
                        "申购现金替代金额", "市场",
                    ]
                else:
                    field_names = [
                        "证券名称", "数量", "现金替代标志",
                        "申购现金替代保证金率", "赎回现金替代保证金率",
                        "申购现金替代金额", "赎回现金替代金额",
                        "市场", "映射代码", "是否实行约定",
                    ]
                for i, fname in enumerate(field_names):
                    if i < len(fields):
                        row[fname] = fields[i]
                    else:
                        row[fname] = ""
                stock_rows.append(row)

    # 整理基本信息
    info_summary = {}
    key_mapping = {
        "基金名称": ["基金名称"],
        "管理公司": ["管理公司名称"],
        "基金代码": ["基金代码"],
        "目标指数": ["目标指数代码"],
        "基金类型": ["基金类型"],
        "交易日期": ["申赎清单日期"],
        "现金差额": ["现金差额"],
        "申赎单位净值": ["最小申购赎回单位资产净值", "赎回单位资产"],
        "基金份额净值": ["基金份额净值"],
        "预估现金差额": ["预估现金差额"],
        "现金替代比例上限": ["现金替代比例上限"],
        "是否发布IOPV": ["是否需要发布IOPV"],
        "最小申赎单位": ["最小申购赎回单位"],
        "是否开放申购": ["是否开放申购"],
        "是否开放赎回": ["是否开放赎回"],
    }
    for out_key, possible_keys in key_mapping.items():
        for pk in possible_keys:
            for bk, bv in basic_info.items():
                if pk in bk:
                    info_summary[out_key] = bv
                    break
            if out_key in info_summary:
                break

    stock_df = pd.DataFrame(stock_rows) if stock_rows else pd.DataFrame()
    return info_summary, stock_df


# ==================== 工具函数 ====================

def _clean_html(text):
    """清除HTML标签"""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', str(text))
    return clean.strip()


def query_etf_list(market="all", fund_code="", keyword="", etf_class="", date=None):
    """
    统一查询接口：查询ETF申赎清单

    参数:
        market: 市场，"sh"=上交所, "sz"=深交所, "all"=全部
        fund_code: 基金代码(6位数字)
        keyword: 关键字搜索(仅上交所支持)
        etf_class: ETF分类代码(仅上交所支持)，01=股票,02=债券,06=商品,33=跨境
        date: 日期，格式 "YYYY-MM-DD"(仅深交所使用)
    返回:
        DataFrame
    """
    dfs = []

    if market in ("sh", "all"):
        print("正在查询上交所ETF申赎清单...")
        sse_df = query_sse_etf_list(etf_class=etf_class, fund_code=fund_code, keyword=keyword)
        if not sse_df.empty:
            dfs.append(sse_df)
            print(f"  上交所查询到 {len(sse_df)} 条记录")
        else:
            print("  上交所未查询到数据")

    if market in ("sz", "all"):
        print("正在查询深交所ETF申赎清单...")
        szse_df = query_szse_etf_list(fund_code=fund_code, date=date)
        if not szse_df.empty:
            dfs.append(szse_df)
            print(f"  深交所查询到 {len(szse_df)} 条记录")
        else:
            print("  深交所未查询到数据")

    if not dfs:
        return pd.DataFrame()

    result = pd.concat(dfs, ignore_index=True)
    return result


def query_etf_detail(fund_code, market=None, date=None):
    """
    查询单只ETF的申赎清单详细信息（成份股明细）

    参数:
        fund_code: 基金代码(6位数字)
        market: 市场 "sh" 或 "sz"，不指定时根据代码自动判断
        date: 日期，格式 "YYYY-MM-DD"(仅深交所使用)
    返回:
        (基本信息dict, 成份股列表DataFrame)
    """
    if market is None:
        market = _guess_market(fund_code)

    if market == "sh":
        return query_sse_etf_detail(fund_code)
    elif market == "sz":
        return download_szse_pcf(fund_code, date=date)
    else:
        print(f"无法识别基金代码 {fund_code} 所属市场，请指定 market 参数")
        return None, pd.DataFrame()


def _guess_market(fund_code):
    """根据基金代码猜测所属市场"""
    code = fund_code.strip()
    # 上交所ETF: 51xxxx, 56xxxx, 58xxxx, 50xxxx
    if code.startswith(("51", "56", "58", "50")):
        return "sh"
    # 深交所ETF: 15xxxx, 16xxxx
    if code.startswith(("15", "16")):
        return "sz"
    return ""


def display_result(df, max_rows=50):
    """格式化展示查询结果"""
    if df.empty:
        print("\n未查询到数据")
        return

    print(f"\n{'='*80}")
    print(f"ETF申赎清单查询结果 (共 {len(df)} 条)")
    print(f"{'='*80}")

    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 120)
    pd.set_option('display.max_colwidth', 30)

    if len(df) <= max_rows:
        print(df.to_string(index=False))
    else:
        print(df.head(max_rows).to_string(index=False))
        print(f"\n... 仅显示前 {max_rows} 条，共 {len(df)} 条")

    # 按市场统计
    if "市场" in df.columns:
        print(f"\n--- 按市场统计 ---")
        market_counts = df["市场"].value_counts()
        for mkt, cnt in market_counts.items():
            name = "上交所" if mkt == "sh" else "深交所"
            print(f"  {name}: {cnt} 条")


def save_to_csv(df, filename=None):
    """将查询结果保存为CSV文件"""
    if df.empty:
        print("无数据可保存")
        return

    if filename is None:
        filename = f"etf_list_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    df.to_csv(filename, index=False, encoding="utf-8-sig")
    print(f"\n数据已保存到: {filename}")


# ==================== 主程序 ====================

def main():
    parser = argparse.ArgumentParser(
        description="ETF申赎清单查询工具 - 从上交所和深交所获取当日ETF申购赎回清单"
    )
    parser.add_argument(
        "--market", choices=["sh", "sz", "all"], default="all",
        help="市场选择: sh=上交所, sz=深交所, all=全部 (默认: all)"
    )
    parser.add_argument(
        "--fund-code", type=str, default="",
        help="基金代码(6位数字)，如 510300"
    )
    parser.add_argument(
        "--keyword", type=str, default="",
        help="关键字搜索(基金名称，仅上交所支持)"
    )
    parser.add_argument(
        "--etf-class", type=str, default="",
        help="ETF分类(仅上交所): 01=股票ETF, 02=债券ETF, 06=商品ETF, 33=跨境ETF"
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="查询日期，格式 YYYY-MM-DD，默认当天(仅深交所使用)"
    )
    parser.add_argument(
        "--detail", action="store_true",
        help="查询单只ETF的详细申赎清单(成份股明细)"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="将结果保存为CSV文件"
    )
    parser.add_argument(
        "--max-rows", type=int, default=50,
        help="最大显示行数 (默认: 50)"
    )

    args = parser.parse_args()

    if args.detail and args.fund_code:
        # 查询单只ETF详细信息
        market = args.market if args.market != "all" else None
        basic_info, stock_df = query_etf_detail(
            args.fund_code, market=market, date=args.date
        )
        if basic_info:
            print(f"\n{'='*60}")
            print("ETF基本信息:")
            for key, value in basic_info.items():
                print(f"  {key}: {value}")
            print(f"{'='*60}")

        if not stock_df.empty:
            print(f"\n成份股明细 (共 {len(stock_df)} 只):")
            display_result(stock_df, args.max_rows)
            if args.save:
                save_to_csv(stock_df, f"etf_detail_{args.fund_code}_{datetime.now().strftime('%Y%m%d')}.csv")
        else:
            print("未查询到成份股明细")
    else:
        # 查询ETF列表
        df = query_etf_list(
            market=args.market,
            fund_code=args.fund_code,
            keyword=args.keyword,
            etf_class=args.etf_class,
            date=args.date,
        )
        display_result(df, args.max_rows)
        if args.save:
            save_to_csv(df)


if __name__ == "__main__":
    main()
