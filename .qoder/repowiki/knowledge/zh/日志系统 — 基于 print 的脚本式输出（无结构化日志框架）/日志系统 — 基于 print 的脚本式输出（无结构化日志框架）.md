---
kind: logging_system
name: 日志系统 — 基于 print 的脚本式输出（无结构化日志框架）
category: logging_system
scope:
    - '**'
source_files:
    - financial_report.py
    - etf.py
    - _test.py
    - _verify.py
    - _cleanup.py
---

本仓库未引入任何 Python 标准库 `logging` 或第三方日志框架，所有运行期信息输出均通过内置 `print()` 完成，属于典型的“脚本式”调试/进度输出风格。具体表现如下：

1. **输出方式**
   - 数据采集进度与结果统计使用 `print(...)` 直接打印到 stdout，如 `financial_report.py` 中多处 `print(f"正在获取 {stock_code} ...")`、`print(f"  -> 已写入 {table} 表...")`。
   - 中间结果展示使用 `df.to_string(index=False)` 配合 `print` 输出表格片段。
   - 测试与验证脚本 `_test.py`、`_verify.py`、`_cleanup.py` 同样以 `print` 作为唯一输出手段。

2. **无日志级别与结构化字段**
   - 全仓未发现 `import logging`、`logger = logging.getLogger(...)`、`loguru`、`structlog` 等任何日志初始化代码。
   - 没有统一的日志格式模板、时间戳、模块名、调用栈等结构化字段；每条输出都是硬编码字符串拼接。

3. **无日志路由与持久化**
   - 所有 `print` 输出仅落至控制台 stdout，未重定向到文件、未接入外部日志服务，也未实现按级别分流。
   - 数据库层仅启用 SQLite WAL 模式 (`PRAGMA journal_mode=WAL`)，该 WAL 是存储引擎级别的写前日志，不属于应用日志范畴。

4. **对开发者的约束与建议**
   - 当前仓库为一次性采集脚本集合，暂不需要引入完整日志框架；若后续需要可统一替换为 `logging` 并定义 `INFO/WARNING/ERROR` 分级。
   - 建议新增 `log/` 目录集中管理日志配置，将关键步骤（数据源切换、写入成功/失败、异常堆栈）升级为结构化日志，便于后续排查与监控。