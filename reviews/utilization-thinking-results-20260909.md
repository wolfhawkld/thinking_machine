# 2026-09-09 thinking 后续对照结果

## 结论

本轮在固定development challenge上出现了明确的**描述性正向结果**：thinking配置正确匹配本context的action为34/48，误匹配对侧context正确action为1/48；11/24个world在两种context下分别选出了各自正确且彼此不同的action。相比原disabled配置，观测表现大幅改善。

这为「LLM在当前受控设置下能够利用不同context选择不同的有效探索入口」提供了有限、直接的行为证据。它不是对通用科学探索、模型内部增熵/降熵机制或RSI的证明，也不是对原primary阴性结果的覆盖。选择入口之后的四轮识别由代码oracle完成，不能改写为模型自主完成完整发现闭环。

## 同题结果对比

所有own命中均沿用旧scorer口径：任一臂invalid则整个world的own/cross都置0。本次及原disabled均48/48 valid，因此这里也等于逐题正确数。

| 配置/策略 | 本context命中 /48 | 对侧context命中 /48 | 完整双臂切换 /24 | favorable / adverse / tie |
|---|---:|---:|---:|---|
| 原DeepSeek Pro，thinking disabled | 5 | 6 | 0 | 0 / 1 / 23 |
| 本轮DeepSeek Pro，thinking high、128K | 34 | 1 | 11 | 22 / 0 / 2 |
| public K1中最大parent行为差异策略 | 29 | 2 | 8 | 19 / 0 / 5 |
| public K1中最少节点策略 | 22 | 6 | 2 | 16 / 2 / 6 |

thinking本context命中率70.8%，完整双臂切换率45.8%；原配置分别10.4%和0%。本轮22个favorable表示own命中数多于cross命中数，**不是22个world全部完成双臂发现**；真正两臂都正确的是11个。signed total=34−1=33。

相同world的old/new完整切换四格表：thinking-only11、disabled-only0、both0、neither13。这里没有新增独立world，也没有新增推断性检验。

## 简单策略与shortcut边界

**后续案例审查补充：** 给最大parent差异策略增加事后「排除恒定输出」条件后，得到33/48、11/24，接近模型34/48、11/24。下文「高于各既定策略」仅对原24种成立，不能继续据此暗示对简单启发式的广泛优势。新增诊断未回填原基线，详见 [案例审查](thinking-case-audit-20260909.md)。

全部24种既定策略均已重算和比较，完整机器可读汇总见同目录 `utilization-thinking-comparison-20260909.json`。本轮在own命中和完整切换这两个观测指标上均高于每一种单独的既定策略，但**相对最强简单策略只是34对29、11对8，不是数量级优势或已证统计优势**。

与最大parent行为差异策略按同一world比较：模型独有完整成功6、策略独有3、双方都成功5、双方都失败10。48次action选择中有31次与该策略一致。与最少节点策略比较：模型独有10、策略独有1、双方都成功1、双方都失败12，action一致26/48。

因此，「模型完全照搬这一条最大差异策略」不能精确描述全部输出；但这些差异也不能排除随机化、其他简单策略或策略组合，不能直接解释为更深层的科学推理。31/48一致只是输出重合率，不是模型使用该策略的概率。最大差异策略8/24的表现仍说明这个benchmark有很强的简单启发式可利用性。

## 分层结果

| stratum | own /12 | cross /12 | 完整切换 /6 | favorable / adverse / tie |
|---|---:|---:|---:|---|
| affine_commutative | 9 | 0 | 3 | 6 / 0 / 0 |
| affine_directional | 10 | 0 | 4 | 6 / 0 / 0 |
| affine_multiplicative | 6 | 1 | 1 | 4 / 0 / 2 |
| pairwise_variable | 9 | 0 | 3 | 6 / 0 / 0 |

改善不只出现在单一stratum；乘法类较弱。每层仅6个world，不据此推断模型总体的类型能力排序。

## 执行与统计解释

- 原24个development worlds，原48题、原选项和评分。当前cohort由outcome-conditioned构造获得，不是自然分布或独立heldout样本。
- thinking high、131072-token上限、3600秒socket timeout。48/48有reasoning、有效最终选项、stop，无截断；最长实际输出29706 tokens，上限未用满。
- 本轮先串行保存6题，用户要求改为每3秒错峰派发；中断第7题本地请求后重发，另发其余41题。42个错峰请求全部返回，无新HTTP/transport失败。新旧总计49次物理尝试、48条最终响应；旧被中断请求usage未知。
- 已保存48条响应的已知input35770 / output736187，其中reasoning735682（已含于output，不重复相加），输入+输出771957。此数不含技术canary，也不含中断请求未知用量，不能称精确账单总量。
- 原primary仍为5/48、0/24、p=1和未检出。本轮在看到原结果之后选thinking、按技术题提高预算且中途变更调度，不能继承原primary标签，也不回填原协议。按事先记录的follow-up约定，仅作描述性分析，不新增p值或显著性宣称。
- 改变了thinking、有效采样方式、token上限、timeout以及调度。观察到的是整套配置的表现差异，不能把改善全部孤立归因于thinking开关或并发本身。推理文本未保存，仅保存最终选择、reasoning存在性/长度/hash及usage，所以本轮也不能据完整推理轨迹审核模型到底用了什么策略。

## 复核与保存

独立离线分析器 `analyze-thinking-staggered-20260909.py` 在打开private评分key之前校验：旧/新plan绑定、串行保留前缀、逐题文件与ledger一致、42次新调度完整、48条唯一task原序合并、response option与usage一致。随后调用冻结scorer和24-policy补充分析。独立直接比较selected option与两臂correct option，重算34/1、11、22/0/2，与scorer一致。

6项分析验证测试通过：完整bundle、缺失/调序拒绝、重复响应拒绝、未知usage不得标为完整、离线重放逐字段一致、独立计分算术一致。真实数据与结果位于 `artifacts/deepseek-thinking-128k-staggered-20260909/`（0600、本地保留）：

- generation SHA256：`b474bf96248f2d6fd6c494322c731aaaf1a8b2c014242e59bf76cc5866dfc36c`。
- analysis SHA256：`9cdadefbd7df1df2c9ba5871937b4a222cac2817af4bddcdf0a5e05925a7eab2`。

结果、逐请求过程、全部策略对照都已保存；本轮结束，无运行任务。本次收尾不新增模型调用、不扩样、不自动commit/push。

## 下一步建议（未执行）

优先将这组结果整理为短文的受限正向案例，并围绕简单启发式解释检查已有成功/失败案例，而不是重复抽样追显著。若后续新增实验，再单独决定针对最大parent差异等启发式的反例/消融，或冻结一致运行方式做跨模型复现；当前未授权或启动这些新调用。
