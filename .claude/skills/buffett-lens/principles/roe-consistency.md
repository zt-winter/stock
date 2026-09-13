# 股本回报的一致性（ROE Consistency）
**一句话**: 高回报的价值不在某一年有多高，而在它**能不能持续**；「record earnings」是最容易骗人的标题——股本每年都在长，EPS 涨 5% 而股本涨 10%，其实是退步。

**判定标准**: 只看年报口径（`REPORT_DATE_NAME LIKE '%年报'`），近 10 年有效样本 ≥7 年；先剔除 `ZCFZL` >60% 的年份（高杠杆推高的 ROE 不是本事，且不能静默剔除）：
① `ROEJQ` 最低值 ≥15% 且均值 ≥18% → PASS；
② 最低值 ≥10% 且均值 ≥15% → QUESTION；
③ 任一低于 → REJECT。
另用 `ROEKCJQ` 交叉：扣非 ROE 均值与 `ROEJQ` 均值相差 >5pct，说明利润含较多非经常性损益，PASS 降级为 QUESTION。**判的是最低的那一年，不是最高的那一年**；`EPSJB` 单独看没有意义，必须与 `BPS` 的增速一起看。

**巴菲特原话**:
> **原文**（1977）: "Most companies define “record” earnings as a new high in earnings per share. Since businesses customarily add from year to year to their equity base, we find nothing particularly noteworthy in a management performance combining, say, a 10% increase in equity capital and a 5% increase in earnings per share."
> **出处**: `letters:1977:0003` · **来源**: letters · **检索关键词**: record earnings, earnings per share, equity base

> **原文**（1977）: "Except for special cases (for example, companies with unusual debt-equity ratios or those with important assets carried at unrealistic balance sheet values), we believe a more appropriate measure of managerial economic performance to be return on equity capital."
> **出处**: `letters:1977:0003` · **来源**: letters · **检索关键词**: return on equity capital, managerial economic performance

> **原文**（1979）: "The primary test of managerial economic performance is the achievement of a high earnings rate on equity capital employed (without undue leverage, accounting gimmickry, etc.) and not the achievement of consistent gains in earnings per share."
> **出处**: `letters:1979:0006` · **来源**: letters · **检索关键词**: return on equity, earnings per share, undue leverage

**适用**: 判断一家公司过去的赚钱能力是真的、还是靠股本膨胀与杠杆堆出来的场合；也适用于比较两家 EPS 增速相同但 ROE 走势相反的公司——这时一致性比增速更能说明问题。

**不适用**: 刚上市、股本因增发或并购发生阶跃的公司（基数突变会让 ROE 失去可比性）；强周期行业在景气顶部的读数，那一年的高 ROE 是价格给的，不是护城河给的；以及账上资产严重低估的公司（1977 年信里说的 special cases），此时 ROE 会被虚假地抬高。

**反面案例**: 1977 年信里把「股本每年增加、EPS 每年跟着涨」的纪录比作一个**完全休眠的储蓄账户**——复利会让利息收入年年上升，但这不叫经营能力；1979 年信里又说，只要分红率够低，even a "stopped clock" can look like a growth stock（停摆的钟也能装成成长股）。同年信里承认自己的 EPS 涨了约 20%，但那是因为资本多得多，资金使用效率其实不如上一年——**EPS 上升而 ROE 下降**。

**关联**: moat-durability, owner-earnings, earnings-quality
