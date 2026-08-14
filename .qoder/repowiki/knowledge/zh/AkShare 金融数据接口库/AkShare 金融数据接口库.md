---
kind: external_dependency
name: AkShare 金融数据接口库
slug: akshare
category: external_dependency
category_hints:
    - vendor_identity
    - sdk_real_api
scope:
    - '**'
---

### AkShare 金融数据接口库
- **角色**: 本项目核心数据源，提供A股、港股、ETF等金融数据的API接口
- **集成点**: 
  - `stock_dividend.py`: 使用 `stock_history_dividend_detail` 接口获取单只股票历史分红明细
  - `financial_report.py`: 使用多个接口获取财报数据（新浪/同花顺/东方财富）
  - `etf.py`: 使用指数和基金相关接口
- **稳定用法**: 通过 `ak.stock_*` 系列函数调用不同数据源，返回pandas DataFrame格式数据
- **注意**: 接口文档地址为 https://akshare.akfamily.xyz/data/stock/stock.html，具体方法参数需参考官方文档确认