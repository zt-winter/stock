# 护城河持久性（Durable Moat）
**一句话**: 伟大的生意有一道护城河，但它护住的必须是**投入资本的回报率**，而且这道河得能自己站住——靠明星 CEO 撑着的、需要不断重建的，都不算。

**判定标准**: 三条同时满足才算 PASS，任一不满足即降级：
① 毛利率 10 年标准差 ≤3pct 且极差 ≤10pct（`XSMLL`）；
② 10 年 ROE 最低值 ≥15% 且均值 ≥18%（`ROEJQ`，`ROEKCJQ` 交叉）；
③ capex/折旧摊销 ≤2.0（`CONSTRUCT_LONG_ASSET` ÷ `FA_IR_DEPR`+`IA_AMORTIZE`）——比值持续 >2 说明护城河要靠不停砸钱维持。
① 或 ② 任一跌破 → REJECT。**经营依赖单个明星人物、或行业变化快到河要反复重挖的，直接判无护城河**，不看数字。

**巴菲特原话**:
> **原文**（2007）: "A truly great business must have an enduring “moat” that protects excellent returns on invested capital."
> **出处**: `letters:2007:0020` · **来源**: letters · **检索关键词**: moat, enduring, competitive advantage

> **原文**（2007）: "A moat that must be continuously rebuilt will eventually be no moat at all."
> **出处**: `letters:2007:0020` · **来源**: letters · **检索关键词**: moat, continuously rebuilt

**适用**: 产品与品牌多年不变、客户转换成本高、有网络或成本优势的生意；判断"这优势三年后还在不在"比判断"今年赚多少"更要紧的场合。

**不适用**: 变化快或受管制扰动的行业——巴菲特明说这类公司被"持久"这一条直接排除在外；也不适用于强周期行业在景气顶部的读数，那时的 ROE 与毛利率本身就是虚高的。

**反面案例**: 2007 年信中把护城河被证伪的公司叫 "Roman Candles"（罗马烟火）——看着亮，转眼就被跨过去了；同段还举了"靠一位顶尖脑外科医生撑着的医疗合伙制"，医生一走，河就没了。

**关联**: pricing-power, roe-consistency, circle-of-competence
