# 股东盈余（Owner Earnings）
**一句话**: 账面利润不是股东到手的钱；真正的估值起点是 1986 年附录给出的那个公式——报告利润加上非现金费用，再**减去维持生意所必需的资本开支**。

**判定标准**: 用年报累计口径（`em_cash_flow` 与 `em_income_statement` 均取 quarter=4，流量为年初至今累计，绝不折算）：
股东盈余 = `NETCASH_OPERATE` − `CONSTRUCT_LONG_ASSET`，与 `PARENT_NETPROFIT` 逐年配对，近 10 年有效样本 ≥7 年：
① 累计比值 ≥1.0 → PASS；
② 0.7 ≤ 比值 <1.0 → QUESTION；
③ 比值 <0.7 或累计股东盈余为负 → REJECT。
注意 `CONSTRUCT_LONG_ASSET` 把扩张性资本开支也算进去了，所以这个读数是**保守下限**；用 `FA_IR_DEPR` + `IA_AMORTIZE` + `LPE_AMORTIZE` 交叉判断——若 capex/折旧摊销 ≤1.2，说明几乎全是维持性开支，此时的比值可以直接采信；若该比值 >2.0，则要额外问一句：投出去的产能还能赚回原来的回报率吗。

**巴菲特原话**:
> **原文**（1986）: "These represent (a) reported earnings plus (b) depreciation, depletion, amortization, and certain other non-cash charges such as Company N's items (1) and (4) less ( c) the average annual amount of capitalized expenditures for plant and equipment, etc. that the business requires to fully maintain its long-term competitive position and its unit volume."
> **出处**: `letters:1986:0099` · **来源**: letters · **检索关键词**: owner earnings, reported earnings, capitalized expenditures

> **原文**（1986）: "Our owner-earnings equation does not yield the deceptively precise figures provided by GAAP, since( c) must be a guess - and one sometimes very difficult to make. Despite this problem, we consider the owner earnings figure, not the GAAP figure, to be the relevant item for valuation purposes - both for investors in buying stocks and for managers in buying entire businesses."
> **出处**: `letters:1986:0100` · **来源**: letters · **检索关键词**: owner earnings, GAAP, valuation

> **原文**（1998）: "它们不会因为有现金流而获得我们的信任，他们只有会因为每年剩下的净现金而获得我们的信任。"
> **出处**: `annual_meeting:1998:0070` · **来源**: annual_meeting · **检索关键词**: 现金流, 净现金, 自由现金流

**适用**: 折旧摊销占成本比重高、又有实打实资本开支的生意——制造业、零售、采掘、公用事业；也适用于判断「净利润年年涨但账上没钱」的公司到底把钱花到哪儿去了。

**不适用**: 几乎不需要资本开支的生意（只持有一座桥或一口长寿命气田这类），此时 ( c) 微不足道，现金流指标本身就够用；也不适用于营运资金剧烈波动的年份——单年读数会被存货和应收款的变动带偏，必须看 5–10 年累计。

**反面案例**: 1986 年信里那场测验的两家公司：GAAP 利润差 1,160 万美元，但差额全来自收购价格会计调整这类非现金项目，两家公司的股东盈余其实一模一样——那些新计提的费用**不是真实的经济成本**。反过来说，把 (a) + (b) 当作股东盈余、跳过 ( c) 来给公司定价的推销话术，才是巴菲特真正要拆的东西。

**关联**: earnings-quality, roe-consistency, one-dollar-retention-test
