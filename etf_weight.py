# -*- coding: utf-8 -*-
"""
ETF申赎清单成份股占比计算工具

查询场内ETF的申赎清单，获取每只成份股的上一交易日收盘价，
按照申赎数量计算每只股票占ETF的比例。

用法:
    python etf_weight.py --fund-code 510300
    python etf_weight.py --fund-code 159008 --market sz
"""

import argparse
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from etf_redemption import query_etf_detail

# ──────────────────────────────────────────────────────
#  SQLite 缓存（stock_valuation 表）
# ──────────────────────────────────────────────────────

_DB_PATH = Path(__file__).parent / "financial_data.db"


def _get_conn():
    """获取 SQLite 连接（自动建表）"""
    conn = sqlite3.connect(str(_DB_PATH))
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
    conn.commit()
    return conn


def _read_valuation_cache(stock_codes, date=None):
    """
    从本地缓存读取估值数据（只返回股息率有效的记录）

    返回:
        dict: {code: {"pe": float|None, "pb": float|None, "dy": float|None}}
              只包含股息率 >= 0 的有效记录，确保 dy=None 的条目下次会重新拉取
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    placeholders = ",".join("?" * len(stock_codes))
    sql = f"""
        SELECT stock_code, pe, pb, dividend_yield, price
        FROM stock_valuation
        WHERE stock_code IN ({placeholders}) AND date = ?
              AND dividend_yield IS NOT NULL
    """
    rows = conn.execute(sql, list(stock_codes) + [date]).fetchall()
    conn.close()
    return {
        r[0]: {"pe": r[1], "pb": r[2], "dy": r[3], "price": r[4]}
        for r in rows
    }


def _save_valuation_cache(data_map, date=None):
    """
    批量写入估值数据到本地缓存

    参数:
        data_map: {code: {"price", "pe", "pb", "dy"}}
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    conn.executemany(
        """INSERT OR REPLACE INTO stock_valuation
           (stock_code, date, pe, pb, dividend_yield, price, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (code, date,
             v.get("pe"), v.get("pb"), v.get("dy"), v.get("price"), now)
            for code, v in data_map.items()
        ],
    )
    conn.commit()
    conn.close()
    print(f"[缓存] 已写入 {len(data_map)} 只股票估值数据 (date={date})")


# ──────────────────────────────────────────────────────
#  东方财富 datacenter-web 批量获取股息率（TTM分红计算）
# ──────────────────────────────────────────────────────

_DC_PAGE_SIZE  = 100   # datacenter 每页条数
_DC_INTERVAL   = 1.0   # 每页请求间隔（秒）


def _get_dividend_yield_datacenter(stock_codes, existing_map):
    """
    通过东方财富 datacenter-web 接口获取分红记录，计算TTM股息率。

    计算方式（TTM滚动12个月）：
        1. 查询每只股票最近365天的分红记录（ASSIGN_PROGRESS="实施分配"）
        2. TTM每股分红 = Σ(每10股税前分红) / 10
        3. 股息率(%) = TTM每股分红 / 当前股价 × 100

    参数:
        stock_codes:  证券代码列表
        existing_map: 已有缓存数据 {code: {...}}，用于获取当前股价
    返回:
        dict: {code: {"dy": float|None}}
    """
    target_codes = set(str(c).strip() for c in stock_codes)
    # 已有缓存且股息率非空的，直接跳过
    target_codes -= {
        c for c, v in existing_map.items()
        if v.get("dy") is not None and v.get("dy") >= 0
    }
    if not target_codes:
        print("[datacenter] 股息率缓存完整，跳过网络请求")
        return {}

    print(f"[datacenter] 需获取 {len(target_codes)} 只股票TTM股息率...")

    # 构建批量过滤条件: (SECURITY_CODE in ("xxx","yyy",...))
    code_list = list(target_codes)
    in_vals = ",".join(f'"{c}"' for c in code_list)
    code_filter = f"(SECURITY_CODE in ({in_vals}))"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }
    session = requests.Session()
    session.headers.update(headers)

    today = datetime.now()
    # dividend_map: {code: total_div_per_10_shares}
    dividend_map = defaultdict(float)

    page = 1
    max_pages = 20  # 每只股票平均1-2条分红，100条/页足够

    while page <= max_pages:
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
               f"sortColumns=EX_DIVIDEND_DAYS&sortTypes=1"
               f"&pageSize={_DC_PAGE_SIZE}&pageNumber={page}"
               "&reportName=RPT_SHAREBONUS_DET"
               "&columns=SECURITY_CODE,PRETAX_BONUS_RMB,EX_DIVIDEND_DATE,ASSIGN_PROGRESS"
               f"&filter={code_filter}")

        data = None
        for attempt in range(3):
            try:
                resp = session.get(url, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                print(f"  [datacenter] 获取分红失败(page={page}, 第{attempt+1}次): {e}")
                if attempt < 2:
                    time.sleep(3)

        if data is None:
            break

        items = (data.get("result") or {}).get("data") or []
        if not items:
            break

        stop_paging = False
        for item in items:
            code = str(item.get("SECURITY_CODE", "")).strip()
            if code not in target_codes:
                continue

            ex_date_str = item.get("EX_DIVIDEND_DATE")
            bonus = item.get("PRETAX_BONUS_RMB")
            progress = item.get("ASSIGN_PROGRESS")

            # 跳过无除权日的记录（预披露、董事会预案等）
            if not ex_date_str:
                continue
            # 只统计已实施的分红
            if progress != "实施分配":
                continue
            # 跳过无分红金额的记录
            if bonus is None:
                continue

            try:
                ex_date = datetime.strptime(ex_date_str[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                continue

            # 只统计最近12个月的分红（TTM）
            if (today - ex_date).days <= 365:
                dividend_map[code] += float(bonus)
            else:
                # 数据按EX_DIVIDEND_DAYS升序（最新→最旧），
                # 一旦超过12个月，后续记录更旧，可停止分页
                stop_paging = True

        total_count = (data.get("result") or {}).get("count", 0)
        if stop_paging or len(items) < _DC_PAGE_SIZE or page * _DC_PAGE_SIZE >= total_count:
            break

        page += 1
        time.sleep(_DC_INTERVAL)

    session.close()

    # 计算股息率: dy = (TTM每股分红 / 当前股价) × 100
    dy_map = {}
    for code, total_per_10 in dividend_map.items():
        div_per_share = total_per_10 / 10.0  # 每10股 → 每股
        price = existing_map.get(code, {}).get("price")
        if price and price > 0:
            dy_map[code] = {"dy": round(div_per_share / price * 100, 4)}
        else:
            dy_map[code] = {"dy": None}

    print(f"[datacenter] 成功计算 {len(dy_map)}/{len(target_codes)} 只TTM股息率")
    return dy_map


# ──────────────────────────────────────────────────────
# 东方财富 EM 接口获取港股分红，计算TTM股息率
# ──────────────────────────────────────────────────────

# 汇率近似值（USD->HKD, CNY->HKD），当EM接口提供港币等价值时优先使用
_FX_USD_HKD = 7.80
_FX_CNY_HKD = 1.10


def _parse_hk_dividend_hkd(fhfa):
    """从东方财富港股分红方案文本中提取每股分红金额（港币）。"""
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


def _get_hk_dividend_yield_em(hk_codes, existing_map):
    """通过东方财富 EM CoreReading 接口获取港股分红记录，计算TTM股息率。"""
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
    today = datetime.now()
    dividend_map = {}
    success_count = 0

    for code in sorted(target_codes):
        code_5 = code.zfill(5)
        url = (f"https://emweb.securities.eastmoney.com/"
               f"PC_HKF10/CoreReading/PageAjax?code={code_5}")
        try:
            resp = session.get(url, timeout=15)
            data = resp.json()
            fhpx = data.get("fhpx", [])
            ttm_div = 0.0
            for item in fhpx:
                pxr = item.get("pxr", "")
                if not pxr or pxr == "--":
                    continue
                try:
                    ex_date = datetime.strptime(pxr[:10], "%Y/%m/%d")
                except (ValueError, TypeError):
                    continue
                if (today - ex_date).days > 365:
                    continue
                amt = _parse_hk_dividend_hkd(item.get("fhfa", ""))
                if amt:
                    ttm_div += amt
            dividend_map[code] = ttm_div
            success_count += 1
        except Exception:
            dividend_map[code] = None
        time.sleep(0.3)
    session.close()

    dy_map = {}
    dy_count = 0
    for code, ttm_div in dividend_map.items():
        if ttm_div is None or ttm_div <= 0:
            dy_map[code] = {"dy": None}
            continue
        price = existing_map.get(code, {}).get("price")
        if price and price > 0:
            dy_map[code] = {"dy": round(ttm_div / price * 100, 4)}
            dy_count += 1
        else:
            dy_map[code] = {"dy": None}

    print(f"[港股分红] 成功计算 {dy_count}/{len(target_codes)} 只港股TTM股息率")
    return dy_map


def _code_to_tencent_symbol(code):
    """
    将证券代码转为腾讯行情接口的symbol格式
    6xxxxx / 688xxx -> sh{code}
    0xxxxx / 3xxxxx -> sz{code}
    港股短码(<=5位) -> hk{code零填充到5位}

    A股代码固定6位，<=5位一定是港股，仅按长度判断即可。

    返回:
        tuple: (symbol, is_hk)
    """
    code = str(code).strip()
    if len(code) <= 5:
        return f"hk{code.zfill(5)}", True
    return (f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"), False


def _get_prices_tencent(stock_codes):
    """
    通过腾讯财经接口批量获取股票最新价格、PE、PB（支持A股+港股）

    参数:
        stock_codes: 证券代码列表
    返回:
        dict: {代码: {"price": float, "pe": float|None, "pb": float|None}}
              失败返回空 dict
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }
    data_map = {}  # {code: {price, pe, pb}}
    batch_size = 60  # 每批最多60只
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
                # 解析响应，每行格式: v_sh600519="1~贵州茅台~600519~1182.19~...";
                # 字段: [3]=现价  [39]=动态PE  [46/47]=PB
                text = resp.content.decode("gbk", errors="replace")
                for line in text.strip().splitlines():
                    line = line.strip().rstrip(";")
                    if '="' not in line:
                        continue
                    parts = line.split('"')[1].split("~")
                    if len(parts) >= 47:
                        raw_code = parts[2].strip()
                        is_hk = line.startswith("v_hk")
                        info = {"price": None, "pe": None, "pb": None}
                        try:
                            p = float(parts[3])
                            if p > 0:
                                info["price"] = p
                        except (ValueError, IndexError):
                            pass
                        try:
                            pe = float(parts[39])
                            if pe != 0:
                                info["pe"] = pe  # 允许负PE（亏损股）
                        except (ValueError, IndexError):
                            pass
                        try:
                            # A股PB在[46]；港股[46]为英文ticker、[47]为TTM股息率、[58]才是市净率
                            pb_idx = 58 if is_hk and len(parts) > 58 else 46
                            pb = float(parts[pb_idx])
                            if pb > 0:
                                info["pb"] = pb
                        except (ValueError, IndexError):
                            pass
                        if info["price"] is not None:
                            orig = norm_to_orig.get(raw_code, raw_code)
                            data_map[orig] = info
                break
            except Exception as e:
                print(f"  [腾讯] 批量获取行情失败(第{attempt+1}次, batch {i//batch_size+1}): {e}")
                if attempt < 2:
                    time.sleep(3)

        batch_num = i // batch_size + 1
        total_batches = (len(codes) + batch_size - 1) // batch_size
        print(f"  [腾讯] 批次 {batch_num}/{total_batches} 完成")
        time.sleep(0.5)

    print(f"[腾讯] 成功获取 {len(data_map)}/{len(codes)} 只股票行情(含PE/PB)")
    return data_map


def _get_prices_eastmoney(stock_codes):
    """
    通过东方财富接口分页获取股票最新价格（备用）

    参数:
        stock_codes: 证券代码列表
    返回:
        dict: {代码: 最新价}，失败返回空dict
    """
    # 东方财富 clist 接口不支持按代码查询，拉取全量行情后过滤
    url = "http://82.push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f12,f2",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }
    price_map = {}
    target_codes = set(str(c).strip() for c in stock_codes)
    page = 1
    max_pages = 80

    session = requests.Session()
    session.headers.update(headers)

    while page <= max_pages:
        params["pn"] = str(page)
        data = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    session.close()
                    session = requests.Session()
                    session.headers.update(headers)
                resp = session.get(url, params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                print(f"  [东方财富] 获取行情失败(第{attempt+1}次, page={page}): {e}")
                if attempt < 2:
                    time.sleep(5)
        if data is None:
            break
        items = data.get("data", {}).get("diff", [])
        if not items:
            break
        total = data.get("data", {}).get("total", 0)
        for item in items:
            code = str(item.get("f12", ""))
            price = item.get("f2")
            if code in target_codes and price is not None and price != "-":
                price_map[code] = float(price)
        if len(price_map) >= len(target_codes) or len(items) < int(params["pz"]):
            break
        page += 1
        time.sleep(1.5)

    session.close()
    print(f"[东方财富] 成功获取 {len(price_map)}/{len(target_codes)} 只目标股票价格")
    return price_map


def get_batch_data(stock_codes, market_map=None):
    """
    获取股票批量行情数据（价格、PE、PB、股息率）

    流程：
        1. 腾讯财经批量获取价格/PE/PB（支持A股+港股）
        2. 读取本地缓存股息率（stock_valuation 表，仅A股）
        3. 缓存缺失时调用 datacenter-web 补全TTM股息率（仅A股）
        4. 港股成份股调用东方财富 EM 接口获取TTM股息率
        5. 结果写回缓存（仅A股）

    参数:
        stock_codes: 证券代码列表
        market_map: {code: "hk"|"sh"|"sz"} 市场类型映射
    返回:
        dict: {代码: {"price": float, "pe": float|None, "pb": float|None, "dy": float|None}}
    """
    if market_map is None:
        market_map = {}

    # ① 腾讯财经获取价格/PE/PB
    print("[数据源] 尝试腾讯财经接口...")
    data_map = _get_prices_tencent(stock_codes)

    # 腾讯获取不足 80% 时，用东方财富兑底（仅A股，港股不支持东财接口）
    missing = [c for c in stock_codes if str(c).strip() not in data_map]
    if missing and len(missing) > len(stock_codes) * 0.2:
        print(f"\n[数据源] 腾讯财经缺失 {len(missing)} 只，尝试东方财富接口兑底...")
        em_map = _get_prices_eastmoney(missing)
        for code, price in em_map.items():
            data_map[code] = {"price": price, "pe": None, "pb": None}

    print(f"\n合计获取 {len(data_map)}/{len(stock_codes)} 只股票行情")

    # ② 读取本地缓存股息率（仅A股）
    a_share_data_keys = [
        c for c in data_map.keys()
        if market_map.get(str(c).strip(), "") != "hk"
    ]
    today = datetime.now().strftime("%Y-%m-%d")
    cached = _read_valuation_cache(a_share_data_keys, date=today) if a_share_data_keys else {}
    # 合并缓存股息率到 data_map
    for code, info in data_map.items():
        info["dy"] = cached.get(code, {}).get("dy")

    cached_dy = sum(1 for v in data_map.values() if v.get("dy") is not None)
    print(f"[缓存] 命中 {cached_dy}/{len(data_map)} 只股息率 (date={today})")

    # ③ 仅对A股调用 datacenter-web 补全TTM股息率
    a_share_codes = [
        c for c in data_map.keys()
        if market_map.get(str(c).strip(), "") != "hk"
    ]
    if cached_dy < len(a_share_codes) * 0.8 and a_share_codes:
        dy_map = _get_dividend_yield_datacenter(a_share_codes, data_map)
        # 合并结果
        for code, dy_info in dy_map.items():
            if code in data_map:
                data_map[code]["dy"] = dy_info.get("dy")

    # ④ 港股: 调用东方财富 EM 接口获取TTM股息率
    hk_codes = [
        c for c in data_map.keys()
        if market_map.get(str(c).strip(), "") == "hk"
    ]
    if hk_codes:
        hk_dy_map = _get_hk_dividend_yield_em(hk_codes, data_map)
        for code, dy_info in hk_dy_map.items():
            if code in data_map:
                data_map[code]["dy"] = dy_info.get("dy")

    # ⑤ 仅缓存A股估值数据
    a_share_data = {
        c: v for c, v in data_map.items()
        if market_map.get(str(c).strip(), "") != "hk"
    }
    if a_share_data:
        _save_valuation_cache(a_share_data, date=today)

    return data_map


def calc_etf_weight(fund_code, market=None, date=None):
    """
    查询ETF申赎清单并计算成份股占比及ETF加权PE/PB

    参数:
        fund_code: 基金代码(6位数字)
        market: 市场 "sh" 或 "sz"，不指定时自动判断
        date: 日期(仅深交所使用)
    返回:
        (基本信息dict, 带占比的成份股DataFrame, ETF指标dict)
    """
    basic_info, stock_df = query_etf_detail(fund_code, market=market, date=date)

    if stock_df is None or stock_df.empty:
        print("未查询到成份股明细")
        return basic_info, pd.DataFrame(), {}

    print(f"\n成份股共 {len(stock_df)} 只，正在批量获取行情...")

    # 提取成份股代码列表
    stock_codes = [str(row["证券代码"]).strip() for _, row in stock_df.iterrows()]

    # 检测成份股市场类型（港股/A股），用于行情查询和股息率处理
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
    hk_count = sum(1 for _, row in stock_df.iterrows()
                   if "香港" in str(row.get("市场", "")) or "hk" in str(row.get("市场", "")).lower())
    if hk_count > 0:
        print(f"  检测到 {hk_count} 只港股成份股，将使用腾讯港股行情接口")

    # 批量获取成份股行情（价格 + PE + PB）
    batch_data = get_batch_data(stock_codes, market_map=market_map)
    if not batch_data:
        print("无法获取行情数据")
        return basic_info, pd.DataFrame(), {}

    # 按证券代码匹配数据
    close_prices, pe_list, pb_list, dy_list = [], [], [], []
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
        if info.get("price") is not None:
            matched += 1

    print(f"成功匹配 {matched}/{len(stock_df)} 只成份股行情")

    stock_df["收盘价"] = close_prices
    stock_df["PE"] = pe_list
    stock_df["PB"] = pb_list
    stock_df["股息率(%)"] = dy_list

    # 计算每只股票的市值（数量 × 收盘价）
    stock_df["数量"] = pd.to_numeric(stock_df["数量"].astype(str).str.replace(",", "", regex=False), errors="coerce")
    stock_df["市值"] = stock_df["数量"] * stock_df["收盘价"]

    # 优先使用「最小申赎单位净值」作为分母（与官方口径一致）
    nav_total = _parse_nav_total(basic_info, stock_df)

    # 计算占比
    stock_df["占比(%)"] = (stock_df["市值"] / nav_total * 100).round(4)

    # 计算 ETF 加权 PE / PB
    etf_metrics = _calc_etf_pe_pb(stock_df)

    # 按占比降序排列
    stock_df = stock_df.sort_values("占比(%)", ascending=False).reset_index(drop=True)

    return basic_info, stock_df, etf_metrics


def _calc_etf_pe_pb(stock_df):
    """
    根据成份股市值占比计算 ETF 加权 PE / PB / 股息率

    计算方法（与官方口径一致）：
        整体PE   = Σ(占比) / Σ(占比 / PE)   调和平均（总市值÷总净利润）
        整体PB   = Σ(占比) / Σ(占比 / PB)   调和平均（总市值÷总净资产）
        加权股息率 = Σ(占比 × 股息率) / Σ(占比)  算术加权平均
    """
    result = {}
    # 过滤有市值的行
    valid = stock_df[stock_df["市值"].notna() & (stock_df["市值"] > 0)].copy()
    if valid.empty:
        return result

    # 整体 PE（调和平均：Σ占比 / Σ(占比/PE)）
    # 排除PE为负或0的亏损股，等价于“总市值÷总净利润”
    pe_valid = valid[valid["PE"].notna() & (valid["PE"] > 0)].copy()
    if not pe_valid.empty:
        w_pe = pe_valid["占比(%)"].sum()
        denom = (pe_valid["占比(%)"] / pe_valid["PE"]).sum()
        etf_pe = w_pe / denom if denom > 0 else 0
        result["整体PE"] = round(etf_pe, 2)
        result["PE覆盖率"] = f"{len(pe_valid)}/{len(valid)}"

    # 整体 PB（调和平均：Σ占比 / Σ(占比/PB)）
    # 等价于“总市值÷总净资产”，排除净资产为负的股票
    pb_valid = valid[valid["PB"].notna() & (valid["PB"] > 0)].copy()
    if not pb_valid.empty:
        w_pb = pb_valid["占比(%)"].sum()
        denom_pb = (pb_valid["占比(%)"] / pb_valid["PB"]).sum()
        etf_pb = w_pb / denom_pb if denom_pb > 0 else 0
        result["整体PB"] = round(etf_pb, 2)
        result["PB覆盖率"] = f"{len(pb_valid)}/{len(valid)}"

    # 加权股息率（算术加权：Σ(占比×股息率) / Σ(占比)）
    dy_col = "股息率(%)" if "股息率(%)" in valid.columns else None
    if dy_col:
        dy_valid = valid[valid[dy_col].notna() & (valid[dy_col] > 0)].copy()
        if not dy_valid.empty:
            w_dy = dy_valid["占比(%)"].sum()
            weighted_dy = (dy_valid["占比(%)"] * dy_valid[dy_col]).sum() / w_dy if w_dy > 0 else 0
            result["加权股息率(%)"] = round(weighted_dy, 2)
            result["股息率覆盖率"] = f"{len(dy_valid)}/{len(valid)}"

    return result


def _parse_nav_total(basic_info, stock_df):
    """
    解析占比计算所用的分母（最小申赎单位净值）

    优先从基本信息中取「最小申赎单位净值」，取不到则用成份股市值之和。
    """
    for key in ("最小申赎单位净值", "申赎单位净值"):
        raw = basic_info.get(key, "")
        if not raw:
            continue
        # 去除货币符号、逗号、空格
        cleaned = str(raw).replace("￥", "").replace("元", "").replace(",", "").strip()
        try:
            val = float(cleaned)
            if val > 0:
                stock_sum = stock_df["市值"].sum()
                print(f"使用「{key}」作为占比分母: {val:,.2f}"
                      f"（成份股市值之和: {stock_sum:,.2f}）")
                return val
        except ValueError:
            pass

    stock_sum = stock_df["市值"].sum()
    print(f"未找到最小申赎单位净值，使用成份股市值之和作为分母: {stock_sum:,.2f}")
    return stock_sum


def display_weight_result(basic_info, stock_df, etf_metrics=None):
    """格式化展示占比计算结果"""
    if basic_info:
        print(f"\n{'=' * 70}")
        print("ETF基本信息:")
        for key, value in basic_info.items():
            print(f"  {key}: {value}")

    if stock_df.empty:
        print("无成份股数据")
        return

    # 展示 ETF 加权 PE/PB 指标
    if etf_metrics:
        print(f"\n{'=' * 70}")
        print("ETF 估值指标（按市值占比加权）:")
        for key, value in etf_metrics.items():
            print(f"  {key}: {value}")

    print(f"\n{'=' * 70}")
    print(f"成份股占比明细 (共 {len(stock_df)} 只)")
    print(f"{'=' * 70}")

    # 选择关键列展示（包含 PE/PB/股息率）
    display_cols = ["证券代码", "证券名称", "数量", "收盘价", "PE", "PB", "股息率(%)", "市值", "占比(%)"]
    available_cols = [c for c in display_cols if c in stock_df.columns]
    display_df = stock_df[available_cols].copy()

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 140)
    pd.set_option("display.max_colwidth", 20)
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")

    print(display_df.to_string(index=False))

    total_mv = stock_df["市值"].sum()
    total_pct = stock_df["占比(%)"].sum()
    print(f"\n合计市值: {total_mv:,.2f}    合计占比: {total_pct:.2f}%")

    # 展示前10大权重股
    print(f"\n--- 前10大权重股 ---")
    top10 = stock_df.head(10)
    for _, row in top10.iterrows():
        pe_str = f"PE:{row['PE']:.1f}" if pd.notna(row.get('PE')) and row.get('PE', 0) > 0 else "PE:-"
        pb_str = f"PB:{row['PB']:.2f}" if pd.notna(row.get('PB')) and row.get('PB', 0) > 0 else "PB:-"
        dy_str = f"股息:{row['股息率(%)']:.2f}%" if pd.notna(row.get('股息率(%)')) and row.get('股息率(%)', 0) > 0 else "股息:-"
        print(f"  {row['证券代码']} {row['证券名称']:<8s}  "
              f"占比: {row['占比(%)']:.2f}%  "
              f"数量: {row['数量']:.0f}  收盘价: {row['收盘价']:.2f}  "
              f"{pe_str}  {pb_str}  {dy_str}")


def main():
    parser = argparse.ArgumentParser(
        description="ETF申赎清单成份股占比计算工具"
    )
    parser.add_argument(
        "--fund-code", type=str, required=True,
        help="基金代码(6位数字)，如 510300"
    )
    parser.add_argument(
        "--market", choices=["sh", "sz"], default=None,
        help="市场: sh=上交所, sz=深交所 (默认自动判断)"
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="日期 YYYY-MM-DD (仅深交所使用)"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="将结果保存为CSV文件"
    )

    args = parser.parse_args()

    basic_info, stock_df, etf_metrics = calc_etf_weight(args.fund_code, market=args.market, date=args.date)
    display_weight_result(basic_info, stock_df, etf_metrics)

    if args.save and not stock_df.empty:
        filename = f"etf_weight_{args.fund_code}_{datetime.now().strftime('%Y%m%d')}.csv"
        stock_df.to_csv(filename, index=False, encoding="utf-8-sig")
        print(f"\n结果已保存到: {filename}")


if __name__ == "__main__":
    main()
