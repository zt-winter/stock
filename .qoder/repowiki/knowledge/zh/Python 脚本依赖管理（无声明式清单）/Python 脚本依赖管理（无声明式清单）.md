---
kind: dependency_management
name: Python 脚本依赖管理（无声明式清单）
category: dependency_management
scope:
    - '**'
source_files:
    - financial_report.py
    - stock_dividend.py
    - etf.py
---

本仓库是一个面向 A 股/港股财报与 ETF 申赎清单采集的 Python 脚本集合，**未采用任何声明式的依赖管理机制**。具体表现如下：

1. **不存在依赖清单文件**：仓库根目录及子目录下没有 `requirements.txt`、`setup.py`、`pyproject.toml`、`Pipfile`、`poetry.lock`、`environment.yml` 等任何标准 Python 依赖声明文件。
2. **依赖以注释形式散落各处**：仅在 `financial_report.py` 和 `stock_dividend.py` 的模块 docstring 中以 `pip install akshare pandas` 的形式记录所需包名，属于纯文档约定而非可执行约束。
3. **第三方库通过运行时 import 隐式声明**：所有外部依赖均在脚本中直接 `import`，包括：
   - `akshare`（A 股/港股行情与财务数据接口封装）
   - `pandas`（DataFrame 数据处理）
   - `baostock`（部分 ETF 场景使用）
   - `requests`（HTTP 请求）
   - `sqlite3` / `argparse` / `json` / `re` / `time` / `datetime` / `pathlib` / `collections` 等均为 Python 标准库，无需额外安装。
4. **无版本锁定与私有源配置**：未发现 `go.mod`、`package.json`、`vendor/`、`GOPRIVATE`、私有 PyPI 源或 `pip.conf` 等任何版本锁定、供应商化或私有注册表相关配置。
5. **数据库为 SQLite 文件**：`financial_data.db` 作为本地持久化存储，随脚本同目录分发，不属于依赖管理范畴。

**结论**：该仓库对依赖的管理处于“零配置”状态——仅靠开发者手动 `pip install` 安装运行时所需的包。若需引入规范的依赖管理，建议新增 `requirements.txt` 并配合 `pip-tools`/`Poetry` 进行版本锁定与更新跟踪。