# 一美元留存测试（Retained Earnings One-Dollar Test）
**一句话**: 每留存 1 美元，至少要能创造 1 美元市值，否则就该分掉——留存本身不是美德，只有能增值的留存才是。

**判定标准**: 以 ≥5 年为窗口（脚本 `MIN_YEARS_RETENTION`＝5），ratio＝市值变化÷累计留存；ratio ≥1.0 → PASS；0.7 ≤ ratio <1.0 → QUESTION；ratio <0.7 → REJECT（"留存的每一块钱没有创造出一块钱的市值，钱被浪费了"）。累计留存＝Σ(归母净利 − 股息)，归母净利取 `PARENTNETPROFIT`（年报口径）；市值变化＝期末市值−期初市值，由 `stock_valuation_history.pe_ttm` × 归母净利推算，脚本另用 `pb`×BPS×股本 交叉验证、两法背离 >20% 时明确告警。累计留存 ≤0（分红超过利润）时本测试不适用，记 NA —— 数据不足时绝不默认 PASS。
**口径警告（必须看）**: 股息优先取 `dividend_annual_yield`（每股股息 × 股本，口径最干净）；该股无数据时退到 `em_cash_flow.ASSIGN_DIVIDEND_PORFIT`（分配股利、利润或偿付利息支付的现金）——这个科目把**利息支付**也算进去了，股息被高估 → 留存收益被低估 → **本测试偏保守**。看到偏低的 ratio，先确认是不是这个口径造成的，再下"钱被浪费了"的结论。

**巴菲特原话**:
> **原文**（1984）: "for every dollar retained by the corporation, at least one dollar of market value will be created for owners."
> **出处**: `letters:1984:0073` · **来源**: letters · **检索关键词**: retained, market value, dividend policy

> **原文**（1984）: "If earnings have been unwisely retained, it is likely that managers, too, have been unwisely retained."
> **出处**: `letters:1984:0081` · **来源**: letters · **检索关键词**: unwisely retained, managers, dividend policy

> **原文**（2000）: "但我们没有理由把价值90美分的一美元留在公司里。"
> **出处**: `annual_meeting:2000:0025` · **来源**: annual_meeting · **检索关键词**: 留存收益, 股息, 一美元

**适用**: 留存收益大、分红少的公司；账上趴着大量现金却说不清用途的；管理层用"我们要做大事"来解释为什么不分红的；以及连续多年留存却看不到增量回报的公司。巴菲特 1984 年给的标准是"reasonable prospect——最好有历史证据佐证"，所以本卡既看后视镜里的 ratio，也看管理层能否说清前瞻性的理由。

**不适用**: 高速成长、留存全部投入高回报扩产的早期企业（ratio 高是正常的，不该因此要求它分红）；累计留存为负（分红超过利润）的公司，测试不适用，记 NA；市值受整体市场情绪抬升或打压的年份——单看一个 5 年窗口会失真，巴菲特自己用的是 5 年滚动平均，而他在 2010 年会上亲口承认最初的措辞"没有经过深思熟虑"；金融类公司市值波动大，ratio 噪声高，须结合 `ROEJQ` 与每股净资产一起读。

**反面案例**: 1984 年信里点名的一类管理层——"高回报业务的管理层，持续把业务产生的现金投入其他低回报的冒险"，这种资本配置决策必须被问责，不管整个企业有多赚钱；同段还有一句狠话：收益若被不明智地留存下来，管理层多半也一样被不明智地留任了。2010 年会上巴菲特自我更正：1984 年那套说法在股市五年大跌期间会让伯克希尔"看起来很糟糕"，即便同期资金配置做得不错——所以这个测试要用长期视角跑，任何单个五年窗口都不足以下判决。

**关联**: capital-allocation, owner-earnings, management-integrity
