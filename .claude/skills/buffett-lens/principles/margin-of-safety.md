# 安全边际（Margin of Safety）
**一句话**: 安全边际就是格雷厄姆那三个词——只在价值明显高于价格时才买；不给价格留余地，就是不给自己的判断留活路。

**判定标准**: 取 `stock_valuation_history` 全序列（需 ≥20 条记录才谈分位），按 `pe_ttm` / `pb` 的历史分位判定：主信号分位 ≤30 → PASS；≤60 → QUESTION；>60 → REJECT（"估值处于历史高位，没有留出安全边际"）。硬约束：`PE_TTM` >40 时，无论分位多低，最高只能给 QUESTION。**周期股（601600/601919 等）PE 分位语义是反的**——周期顶利润高、PE 低，周期底利润薄、PE 高——此时改以 `pb` 分位为主信号（该反转规则的出处与展开见 `.claude/skills/security-analysis/cyclical-analysis-guide.md` §一，本卡不另立一套；周期股判断前先读那一节）。这张卡每次都要过，且**放在最后**：前面全过了它才有意义。
**读数位置**: `buffett_analysis.py valuation --code X`（只看估值分位）或 `screen` 的【九】安全边际。

**巴菲特原话**:
> **原文**（1992）: "we insist on a margin of safety in our purchase price. If we calculate the value of a common stock to be only slightly higher than its price, we’re not interested in buying."
> **出处**: `letters:1992:0049` · **来源**: letters · **检索关键词**: margin of safety, purchase price

> **原文**（1990）: "In the final chapter of The Intelligent Investor Ben Graham forcefully rejected the dagger thesis: "Confronted with a challenge to distill the secret of sound investment into three words, we venture the motto, Margin of Safety." Forty-two years after reading that, I still think those are the right three words."
> **出处**: `letters:1990:0064` · **来源**: letters · **检索关键词**: Margin of Safety, Ben Graham

> **原文**（1996）: "第20章讲的是安全边际，这就是说，不要试图开着9,800磅重的卡车过承载能力10,000磅的桥。"
> **出处**: `annual_meeting:1996:0044` · **来源**: annual_meeting · **检索关键词**: 安全边际, 桥, 聪明股票投资人

**适用**: 每一次买入决策前的最后一个关口；估值处于历史高分位、或"好公司但价格已经反映了一切"的场合；市场刚经历大涨、手里现金蠢蠢欲动时（配合 `market-mr-market.md` 一起用）；周期股改用 `pb` 分位判断时也走这张卡。

**不适用**: 分位只是**相对历史**——若这家公司历史上的估值本身就长期偏高，低分位也不等于便宜，须结合 `ROEJQ`、`XSMLL` 与生意质地一起看；`stock_valuation_history` 少于 20 条记录时不谈分位，记 NA；亏损企业 `pe_ttm` 为负或无意义，此时以 `pb` 与重置成本为主；周期股的 PE 分位不可直接采信（语义反转已如上修正）。另外安全边际解决不了"看不懂"的问题——能力圈没过，再便宜也不该看。

**反面案例**: 1990 年信里的坦帕电视台：债务利息超过电视台总收入，资本结构"保证失败"，买这些债的储贷机构后来纷纷倒闭——巴菲特说，投资者无视"安全边际"这条简单信息，在 1990 年代初付出了惨重的代价。1992 年信里另一面：如果算出来的价值只比价格高一点点，那就**不买**——"只便宜一点点"不是安全边际，是自我安慰。1996 年会上那句更形象：别开着 9,800 磅的卡车过限重 10,000 磅的桥，多走几步找座 15,000 磅的桥。

**关联**: circle-of-competence, market-mr-market, sell-discipline
