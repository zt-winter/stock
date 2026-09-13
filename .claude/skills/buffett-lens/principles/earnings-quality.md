# 盈利的现金含量（Earnings Quality）
**一句话**: 账面利润不等于现金；长期看，经营现金流至少要跟得上净利润，否则那些利润只是纸面上的数字。

**判定标准**: 用年报累计口径（`em_cash_flow` 与 `em_income_statement` 均取 quarter=4），把 `NETCASH_OPERATE` 与 `PARENT_NETPROFIT` 逐年配对，近 10 年有效样本 ≥7 年：
① 累计 OCF/累计净利 ≥1.0，且逐年 ≥1 的年份 ≥7 年 → PASS；
② 0.8 ≤ 累计比值 ≤1.0 → QUESTION；
③ 累计比值 <0.8 → REJECT。样本不足 7 年判 NA，NA 不是 PASS。
`NETCASH_OPERATE` 已经把折旧摊销加回来了，所以这个比值天然包含了 (b)；真正要盯的是 ( c) ——资本开支与营运资金占用。旁证：应收款与存货的增速是否长期快于 `TOTALOPERATEREVE`；`ROEKCJQ` 与 `ROEJQ` 是否长期大幅背离。

**巴菲特原话**:
> **原文**（1986）: "But "cash flow" is meaningless in such businesses as manufacturing, retailing, extractive companies, and utilities because, for them, ( c) is always significant. To be sure, businesses of this kind may in a given year be able to defer capital spending. But over a five- or ten-year period, they must make the investment - or the business decays."
> **出处**: `letters:1986:0105` · **来源**: letters · **检索关键词**: cash flow, capital spending, business decays

> **原文**（2001）: "Bad terminology is the enemy of good thinking. When companies or investment professionals use terms such as “EBITDA” and “pro forma,” they want you to unthinkingly accept concepts that are dangerously flawed."
> **出处**: `letters:2001:0043` · **来源**: letters · **检索关键词**: EBITDA, pro forma, bad terminology

> **原文**（1998）: "我们认为完全没有意义的一个数字就是所谓的EBITDA。大多数拥有大量固定资产的企业，其产生的现金流大多数需要被重新投资，以保持其竞争力和市场地位。"
> **出处**: `annual_meeting:1998:0070` · **来源**: annual_meeting · **检索关键词**: EBITDA, 现金流, 竞争地位

**适用**: 高增长但现金吃紧的公司——利润表漂亮、经营现金流却常年落后；以及用来识别「先确认收入、后收钱」的商业模式（工程、分销、部分消费电子），这些公司的利润领先现金是常态。

**不适用**: 金融机构与保险公司——它们的经营现金流包含存款、保费与投资组合的巨大变动，与净利润本就没有可比性；也不适用于单年比较，营运资金的一次性变动（备货、预收款）足以让某一年的比值失真，必须看累计。

**反面案例**: 1986 年信里那家被收购的公司，用 (a) + (b) 的「现金流」包装出售，巴菲特直接说这类数字是投资银行家用来 justify the unjustifiable 的；他还用了一个比方——牙医说不管牙齿它们就会自己掉，但 ( c) 不会。同一封信里另有那场测验：两家 GAAP 利润不同的公司，股东盈余其实完全一样，说明**利润表可以被会计手法改写，现金不会**。

**关联**: owner-earnings, roe-consistency, moat-durability
