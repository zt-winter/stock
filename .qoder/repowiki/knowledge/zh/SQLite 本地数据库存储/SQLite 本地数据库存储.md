---
kind: external_dependency
name: SQLite 本地数据库存储
slug: sqlite
category: external_dependency
category_hints:
    - framework_behavior
scope:
    - '**'
---

### SQLite 本地数据库存储
- **角色**: 项目统一的数据持久化方案，所有采集数据均存入 `financial_data.db`
- **稳定用法**: 
  - 表名约定：按数据源+数据类型命名（如 `sina_financial_indicator`, `em_balance_sheet`）
  - 幂等写入：先删除旧数据再追加，支持重复执行
  - 统一字段：`stock_code`（股票代码）、`market`（市场标识sh/sz/hk）
- **注意**: 数据库文件位于脚本同级目录，保证可移植性