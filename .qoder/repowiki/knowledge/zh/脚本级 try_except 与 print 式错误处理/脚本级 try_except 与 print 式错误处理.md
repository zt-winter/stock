---
kind: error_handling
name: 脚本级 try/except 与 print 式错误处理
category: error_handling
scope:
    - '**'
source_files:
    - etf_redemption.py
    - stock_dividend.py
    - financial_report.py
---

本仓库为数据采集脚本集合，未建立统一的错误类型体系、日志框架或中间件。各模块采用最基础的 Python 异常捕获模式，整体呈现“脚本级”的错误处理方式。

1. 网络请求层：etf_redemption.py 使用 `requests.get` + `resp.raise_for_status()`，对 `requests.RequestException` 和 `json.JSONDecodeError` 分别捕获，统一通过 `print(f"[上交所] 请求失败: {e}")` 输出后返回空 DataFrame 或 None，调用方据此判断是否继续。
2. 第三方库调用层：financial_report.py、stock_dividend.py 直接调用 akshare 接口，未显式捕获异常；stock_dividend.py 的 `get_dividend_detail` 用 `try/except Exception as e` 包裹并实现指数退避重试（`max_retries=3, retry_delay=1.0`），失败时打印 `[!] ... 获取分红明细失败（重试 N 次）` 并返回 None。
3. 数据解析层：etf_redemption.py 在 JSONP 正则提取、GBK 解码等位置单独 try/except，失败则回退到空结果。
4. 数据库层：save_to_db 依赖 pandas.to_sql 自动建表，无显式 sqlite3 异常处理；连接通过 `PRAGMA journal_mode=WAL` 开启 WAL 模式提升并发读性能。
5. 顶层流程：financial_report.py 主循环用 `try/finally` 确保 conn.close()，但内部 akshare 调用抛出的异常会直接中断整个采集任务。
6. 缺失项：全仓未见自定义 Error/Exception 类、错误码枚举、结构化日志（logging）、panic/recover 等价机制、错误传播约定或可恢复/不可恢复错误的区分策略。