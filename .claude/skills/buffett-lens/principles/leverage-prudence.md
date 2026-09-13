# 杠杆的审慎（Leverage Prudence）
**一句话**: 杠杆既放大收益也放大归零——任何一串正数乘以一个零都会蒸发；信用像氧气，充足时没人注意，一缺就是全部。所以该问的不是"利率多低"，而是"最坏的年景里，它还活不活得下来"。

**判定标准**: 以最近一年年报（`em_financial_indicator`，`REPORT_DATE_NAME LIKE '%年报'`）为准：`ZCFZL` >60% 或 `INTEREST_DEBT_RATIO` >40% → REJECT（一票否决，无论估值多便宜）；`ZCFZL` ≤40% 且 `INTEREST_DEBT_RATIO` ≤15% → PASS；`ZCFZL` ≤55% 且 `INTEREST_DEBT_RATIO` ≤30% → QUESTION；其余为中间地带，须逐项看债务结构。同时算偿还年数＝有息负债÷`PARENTNETPROFIT`（几年的利润才还得清），它没有硬阈值，但利润一旦下滑分母就缩水，年数会非线性拉长——杠杆真正杀人的位置就在这里，不在当期比率上。
**读数位置**: `buffett_analysis.py screen` 的【七】杠杆；柜台只看最近一年，历史序列要自己拉 `em_financial_indicator` 逐年比。

**巴菲特原话**:
> **原文**（2010）: "any series of positive numbers, however impressive the numbers may be, evaporates when multiplied by a single zero."
> **出处**: `letters:2010:0090` · **来源**: letters · **检索关键词**: leverage, borrowed money, single zero

> **原文**（2010）: "Borrowers then learn that credit is like oxygen. When either is abundant, its presence goes unnoticed. When either is missing, that’s all that is noticed."
> **出处**: `letters:2010:0092` · **来源**: letters · **检索关键词**: credit, oxygen, borrowers

> **原文**（1994）: "其中的关键在于：(1)能否控制自己的业务，使杠杆不会造成危险； (2)使用杠杆的时候带来什么样的ROE。"
> **出处**: `annual_meeting:1994:0032` · **来源**: annual_meeting · **检索关键词**: 杠杆, 危险, ROE

**适用**: 有息负债不低的公司；金融、地产类必看；账上有大额债务而利润波动大的生意；以及"利率这么低，为什么不借钱"这种提议出现的任何场合——2010 年信的 Life and Debt 一节就是专门写给这个问题的。

**不适用**: **银行与保险不能直接套 `ZCFZL`**——存款与浮存金是它们的原料而非催命符，90% 的资产负债率在银行业是常态，对这两类生意要看资产质量、久期错配与浮存金成本，而不是这张卡的比率（本卡就近代路由到 `insurance float` 的检索）。同样不适用于无息负债占绝对主导的公司（如预收款模式），以及处于清算状态、账面净资产本身已失真的公司。

**反面案例**: 1990 年信里的坦帕电视台（Tampa station）——"kill-'em-at-birth" 的资本结构：债台高筑到利息超过电视台的全部收入，哪怕把人工、节目、服务全算成免费赠送，也只有收入爆炸才活得下去，否则注定破产，而买这些债的储贷机构后来纷纷倒闭。2010 年信又补了 2008 年：信用在一夜之间消失，很多长期繁荣的公司"忽然发现只有现金才算数"。中文场合同样直白——2011 年会上巴菲特说，麻烦在于所有银行都认为自己能明智地使用杠杆，而一两个不明智的行为会让所有人承担后果。

**关联**: insurance-float, capital-allocation, margin-of-safety
