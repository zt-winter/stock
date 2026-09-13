# 资本配置（Capital Allocation）
**一句话**: 管理层最重要的活儿不是把生意经营好，而是把赚来的钱放到回报率最高的地方；留存只有在能创造等值市值、或再投资的回报率不低于主业时才正当——高回报生意把现金持续投进低回报项目，无论整体多赚钱，这笔配置都该被追责。

**判定标准**: 三条判据，按近 5-10 年窗口计算，① 是主判据，②③ 用于解释 ① 为什么不合格；②③ 同时成立时升级为一票否决：
① 一美元留存测试：近 5-10 年累计留存 = 累计 `PARENTNETPROFIT` − 累计 `ASSIGN_DIVIDEND_PORFIT`（`em_cash_flow` quarter=4），市值变化（`stock_valuation_history` 的 `pe_ttm` × 归母净利推算，`pb` × `BPS` 交叉验证）÷ 累计留存，≥1.0 为 PASS，0.7-1.0 为 QUESTION，<0.7 为 REJECT——留存的每一块钱没创造出一块钱市值，钱被浪费了；期间累计留存为负（分红超过利润）时记 NA，不得默认 PASS。
② 增量资本回报下滑：近 5 年 `ROEJQ` 均值比前 5 年下滑 >5pct，且 capex/折旧摊销 = `CONSTRUCT_LONG_ASSET` ÷ (`FA_IR_DEPR` + `IA_AMORTIZE`) 持续 >2.0——赚来的钱又投回了低回报的地方。
③ 账面回报与现金回报背离：`ROEJQ` − `ROEKCJQ` 持续 >5pct（扣非后质量下降），或 `NETCASH_OPERATE` ÷ `PARENTNETPROFIT` 持续 <1，说明高 ROE 不是真实的增量回报，配置决策更不可信。
现金去向的第三种形式是**回购**：回购只有在价格低于内在价值时才创造价值，价格纪律与留存纪律同源，一并纳入本卡判定。

**巴菲特原话**:
> **原文**（1984）: "Managers of high-return businesses who consistently employ much of the cash thrown off by those businesses in other ventures with low returns should be held to account for those allocation decisions, regardless of how profitable the overall enterprise is."
> **出处**: `letters:1984:0081` · **来源**: letters · **检索关键词**: capital allocation, held to account, high-return businesses

> **原文**（1987）: "The lack of skill that many CEOs have at capital allocation is no small matter: After ten years on the job, a CEO whose company annually retains earnings equal to 10% of net worth will have been responsible for the deployment of more than 60% of all the capital at work in the business."
> **出处**: `letters:1987:0053` · **来源**: letters · **检索关键词**: capital allocation, CEO, deployment of capital

> **原文**（2024）: "Sometimes I’ve made mistakes in assessing the future economics of a business I’ve purchased for Berkshire – each a case of capital allocation gone wrong."
> **出处**: `letters:2024:0002` · **来源**: letters · **检索关键词**: capital allocation gone wrong, mistakes

**适用**: 账上有大额现金或理财、有大额资本开支或并购、留存收益规模大的公司；尤其是"主业很赚钱、却总在买别的生意"的公司——主业的高回报会掩盖别处反复的配置失败，报表整体依然好看。

**不适用**: 把利润全部投回主业、且边际回报并未下滑的高成长期公司——1984 年信明确说，只要留存每 1 元能创造超过 1 元市值，分配反而损害股东利益；也不适用于累计留存为负（分红超过利润）的公司，一美元留存测试此时无意义（脚本记 NA）。

**反面案例**: 1984 年信 Dividend Policy 小节用 "Pro-Am 高尔夫" 打比方：业余球手全是不入流的废物，团队的 best-ball 成绩却依然体面，全靠队里那一位职业选手——"了不起的核心业务"正是这样把别处反复失败的资本配置掩盖掉的；同段还说，这些管理层"定期汇报从最近一次失望中学到的教训"，然后转头去寻找下一次教训（"失败似乎让他们上了头"）。2024 年信 Mistakes 小节，巴菲特把买错生意直接自认为 "capital allocation gone wrong"。

**关联**: retained-earnings-one-dollar, owner-earnings, earnings-quality, management-integrity
