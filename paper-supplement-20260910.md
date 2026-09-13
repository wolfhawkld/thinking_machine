# 补充材料：有限规则任务中的上下文依赖入口选择（2026-09-10）

对应[正文](paper-manuscript-20260910.md)。本文件整理方法细节、全部26策略、补充结果及复现入口；沿用已冻结材料，不新增评分或实验。当前为本地补充稿，原始文件未打包为公开数据发布，不能声称读者已能通过公开下载完整复现。

## S1. 任务、动作与验证协议

输入域为{-2,-1,0,1,2}³，共125点；D0为同一坐标面上的12点，其余分成49个有序evidence点和64个test点。256成员bank与D0相容，验证器按完整域行为归并语义假设。parent按节点数及canonical hash选取，不按隐藏目标选择。

程序语言含x1/x2/x3、常数−3..3、neg/add/sub/mul、gt/eq、ite；深度≤5、节点≤31，分类器须完整域二值。context库含105个motif，交换仿射21、方向性42、乘法21、成对变量21；这是context构造层，不是目标类别。

十个原始动作是在parent谓词左／右操作数两个路径上各用五种编辑frame：replace、add(OLD,CONTEXT)、sub(OLD,CONTEXT)、sub(CONTEXT,OLD)、mul(OLD,CONTEXT)。显示选项ID经冻结映射对应原始动作。context须在parent中不存在、在child中出现一次；合法child须D0一致、完整域二值、区别于parent并改变evidence行为。详见[来源绑定](src/spark_lineage.py)、[提示及策略](src/spark_strong_k4_benchmark.py)。

四轮流程与strong-K4定义保留在正文§2.2，因为其关系到结论解释，不能只藏在附录。这里强调：oracle返回首个不一致位置／标签或MATCH，更新按完整响应而非单标签过滤；得到singleton或不可区分的evidence-equivalence状态可提前停止。K4_full_pool是在同parent、frame、motif层及复杂度的冻结匹配池中按child行为去重，至少三个对照且全部未精确识别，不是全部可能context。实现：[验证器](src/spark_compressor.py)、[strong-K4](src/spark_strong_k4_scan.py)、[v2配对](src/spark_strong_k4_utilization_feasibility.py)。

## S2. 样本构造与版本时间线

原1024候选池中构造四层各6的24world，每world仅取一对，形成48臂；严格唯一性约束成功原始动作，不要求全部错误动作行为互异。K2两臂按机会数量配平，非恒定K4各一个且不同，常数K4为零。[v2配置](configs/spark-strong-k4-utilization-primary-benchmark-v2.json)固定选择、显示与原统计协议。旧v1保留历史，不用于重新构造或改写当前结果。

另一批1024候选完整扫描得到19个strict world、41对；其中两种非恒定策略联合完整失败类为9world、22对。保留该类全部9world，每world按pair hash取一对；实际context层为交换仿射3、方向性4、成对变量2、乘法0。41对和22对都不是相应数量的独立world；此9world不是四层平衡样本。[搜索报告](reviews/shortcut-challenge-1024-results-20260910.md)。

响应与设计顺序：原非thinking主试验 → DeepSeek thinking随访 → 非恒定策略诊断 → GLM随访 → 新池策略挑战 → 同9world参考对照 → 同9world候选规律输出 → 原24中8world的同期上限对照。各轮运行前冻结不等于所有设计均在原结果之前预注册。后续对照不独立增加world数，不继承原检验power。

## S3. 原24world全部策略

固定动作和固定显示位置各10种，公开信息策略4种，共24种既定策略；最后2行为DeepSeek thinking之后、GLM之前提出的诊断。全部使用公开信息。固定策略无法在不同正确动作的两臂上同时成功，是结构限制，不代表20项独立有力基线。

| 策略ID或诊断 | own /48 | cross /48 | 完整 /24 |
|---|---:|---:|---:|
| fixed-semantic-00 | 14 | 14 | 0 |
| fixed-semantic-01 | 0 | 0 | 0 |
| fixed-semantic-02 | 0 | 0 | 0 |
| fixed-semantic-03 | 2 | 2 | 0 |
| fixed-semantic-04 | 12 | 12 | 0 |
| fixed-semantic-05 | 12 | 12 | 0 |
| fixed-semantic-06 | 0 | 0 | 0 |
| fixed-semantic-07 | 0 | 0 | 0 |
| fixed-semantic-08 | 1 | 1 | 0 |
| fixed-semantic-09 | 7 | 7 | 0 |
| fixed-display-position-00 | 4 | 4 | 0 |
| fixed-display-position-01 | 4 | 4 | 0 |
| fixed-display-position-02 | 7 | 7 | 0 |
| fixed-display-position-03 | 9 | 9 | 0 |
| fixed-display-position-04 | 3 | 3 | 0 |
| fixed-display-position-05 | 7 | 7 | 0 |
| fixed-display-position-06 | 2 | 2 | 0 |
| fixed-display-position-07 | 6 | 6 | 0 |
| fixed-display-position-08 | 5 | 5 | 0 |
| fixed-display-position-09 | 1 | 1 | 0 |
| first-public-K1-else-first-displayed | 9 | 2 | 2 |
| public-k1-min-node-hash | 22 | 6 | 2 |
| public-k1-min-positive-node-hash | 4 | 10 | 0 |
| public-k1-max-parent-novelty-node-hash | 29 | 2 | 8 |
| 非恒定过滤＋最大parent行为差异 | 33 | 1 | 11 |
| 非恒定过滤＋最少节点 | 31 | 2 | 8 |

数值来源：[机器比较](reviews/utilization-glm53-comparison-20260909.json)中的all_24_frozen_policies与separately_labelled_nonconstant_diagnostics；前24条的cross由own−signed_total得到。固定动作ID按原始动作编号选择；固定显示ID按位置选择。其余策略的公开支持过滤、正节点变化及并列规则以[实现](src/spark_strong_k4_benchmark.py)为准，诊断定义见[案例记录](reviews/thinking-case-audit-20260909.md)。九world挑战中全部26策略完整均0/9，但其筛选针对两个重点策略，不当作26次独立验证。

## S4. 新观察逐world结果与评分边界

每条件一次独立响应，使用相同目标及64个私有测试点。下表按冻结ordinal，不按效果排序。无附加／新观察／重复旧观察的总正确数305／399／379，分母均576；完整测试与125点全域恢复各组均0/9。

| world ordinal | 无附加正确 /64 | 新观察正确 /64 | 重复观察正确 /64 |
|---|---:|---:|---:|
| 0 | 12 | 32 | 49 |
| 1 | 13 | 45 | 46 |
| 2 | 39 | 39 | 39 |
| 3 | 42 | 51 | 51 |
| 4 | 35 | 57 | 30 |
| 5 | 47 | 47 | 47 |
| 6 | 40 | 51 | 40 |
| 7 | 34 | 34 | 34 |
| 8 | 43 | 43 | 43 |

新观察后剩余候选依次189、187、161、184、86、171、89、85、61；只2/9反驳parent，全部保留。新−重复为2赢2输5平，不能只展示world4和6。重复点已见，因此该对照不匹配新颖程度、输入位置或严格信息量。完整评分及全部代码基线见[新观察结果](reviews/new-evidence-model-results-20260910.md)、[设计](reviews/new-evidence-protocol-draft-20260910.md)。合法但不符合观察的程序仍计算原始测试准确率，一致性另报；本轮27条均合法且一致，并未按一致性删样本。

## S5. 统计、参数与技术记录

原主检验对非tie world的净利用分方向做单侧exact sign test，alpha=0.05；原非thinking为0 favorable、1 adverse、23 tie，p=1。收到无效内容时整个world记主终点tie及完整失败；传输／缺失使原整轮不可评价，不用部分分母。实际原48条均有效。后续实验各遵循独立技术失败规则，均未按正确性重试，不把它们追溯视为原主检验复现。

原DeepSeek非thinking：temperature0.2，上限256；DeepSeek thinking high：上限131072，不显式发送temperature/top_p；GLM thinking high：上限131072，temperature1.0、top_p0.95。后续挑战／参考／规律输出复用DeepSeek thinking配置。同期上限对照为非thinking、temperature0.2，256或8192，其他请求内容固定。各任务独立上下文，不把供应商high或相同上限视为等计算量。

| 模型实验 | 最终响应 | 任务物理尝试 | 已知输出tokens |
|---|---:|---:|---:|
| 原DeepSeek非thinking | 48 | 48 | 469 |
| 原DeepSeek thinking | 48 | 49 | 736187 |
| 原GLM thinking | 48 | 49 | 1039407 |
| 九world挑战 | 18 | 18 | 379301 |
| 三条件参考 | 54 | 54 | 911963 |
| 三条件规律输出 | 27 | 27 | 308880 |
| 两档非thinking上限 | 32 | 33 | 316 |

上表不含技术预检；含失败尝试的部分用量未知，不据此计算完整账单或精确总成本。供应商计入output的reasoning不重复相加；reasoning字段缺失也不代表内部计算为零。全部最终响应有效且未截断。上限对照的已知成功input21364、output316，共21680；timeout消耗未知。参考和规律输出的逐条件用量见各结果报告。

## S6. 复现材料入口与发布边界

| 证据 | 协议／方法 | 已完成结果与核验入口 |
|---|---|---|
| 原24world | [v2配置](configs/spark-strong-k4-utilization-primary-benchmark-v2.json) | [原主结果](reviews/utilization-primary-results-20260908.md)、[DS thinking](reviews/utilization-thinking-results-20260909.md)、[GLM](reviews/utilization-glm53-results-20260909.md) |
| 原4／5轮代码检查 | [预算脚本](reviews/budget-sensitivity-20260908.py) | [结果JSON](reviews/budget-sensitivity-20260908.json) |
| 新池挑战 | [调用计划](reviews/shortcut-challenge-live-plan-20260910.md) | [模型结果](reviews/shortcut-challenge-model-results-20260910.md) |
| 辅助参考 | [设计](reviews/context-structured-reference-plan-20260910.md)、[协议](reviews/context-association-live-protocol-20260910.md) | [结果](reviews/context-structured-reference-results-20260910.md) |
| 新观察规律输出 | [设计](reviews/new-evidence-protocol-draft-20260910.md)、[协议](reviews/new-evidence-live-protocol-20260910.md) | [结果](reviews/new-evidence-model-results-20260910.md) |
| 非thinking上限 | [协议](reviews/nonthinking-budget-live-protocol-20260910.md) | [结果](reviews/nonthinking-budget-results-20260910.md) |

各结果报告定位到本地artifacts目录中的plan、private映射、generation、analysis、results及原始响应／attempt记录，并列出对应hash和已完成的只读核验。原始提示的精确内容、逐world映射与余下配对统计沿用这些冻结文件，不在本稿手工重构。历史报告的“后续未执行”只反映当时状态，当前以HANDOFF顶部为准。

复核不需要重新发模型请求；不要重跑run/freeze/build覆盖旧产物。模型原始响应、私有目标与密钥文件不应混为一份可发布目录。本补充稿不包含密钥，也不意味着已获发布私有产物的授权。投稿前仍需确定可分发文件清单、准备去凭证复现包、精确提示附录及独立环境验证；这与当前本地审计通过不同。
