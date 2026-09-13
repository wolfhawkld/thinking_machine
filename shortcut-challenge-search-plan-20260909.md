# 新 world 有界搜索方案（2026-09-09）

## 状态与目的

用户同意沿新增world搜索方向继续。本轮完成代码路径评估、无目标seed去重检查与方案记录；没有生成新world、运行oracle或调用模型。本文是新的独立development方案，不扩写或冒用旧v2的1024候选冻结协议。下一步实现独立runner及合成测试，再开始下述成本校准。

目的：在保留strict双臂条件的前提下，检查能否找到未用于旧实验、且使已检查简单策略失效的world。当前只授权范围内的离线工作，不包括后续模型试验。

## 计算上限

| 项目 | 本方案边界 |
|---|---|
| 候选范围 | 新命名空间下index 0..127，共128个，不补位、不扩展 |
| 成本校准 | 前4个候选，包含在128个内；该阶段最多600秒 |
| 总扫描预算 | 成本校准和后续扫描累计最多3600秒墙钟运行时间 |
| 计算并发 | 一个world工作进程，不自动增加并发 |
| 每world | 1次target生成；105个motif×10个动作；冻结四轮oracle与K4定义 |
| 模型请求 | 0 |
| 自动扩搜/重抽target | 禁止 |

总时限覆盖world生成、动作计算和候选评分；到期停止工作进程，不仅在world边界检查。保留已完整完成的world，在途world标为未完成，不当作阴性。暂停/恢复累计已有运行时间，不重置一小时预算。收尾写小型状态记录不启动新计算。

前4个若在600秒内无法完整完成，则在保存部分结果后停止本批，先报告成本，不自动进入后124个。若校准全部完成且无契约错误，可继续固定剩余范围；继续与否不依据是否找到正向挑战。后续到128个全部完成或总时限到达即停，不因凑够成功数提前结束，也不因成功不足追加候选。

历史24/1024仅描述旧池的strict容量，不能直接作为新池或策略失效挑战的成功概率。128个是一轮成本受限的可行性探测，不是保证获得12个world的采样计划。当前没有可靠的全新world扫描耗时测量，不从旧文件核验速度推算生成速度。

## 新seed范围与身份

- world namespace：`spark-shortcut-challenge-search-v1-20260909:world-seed`。
- world seed：SHA256(namespace + ':' + decimal_index)的前8字节按big-endian转整数，再与`2**63-1`按位与。
- target namespace：`spark-shortcut-challenge-search-v1-20260909`；沿用原target seed公式 SHA256(namespace + ':target:' + decimal_world_seed)，完整digest按big-endian转整数。每world只生成一个target，不挑选或重抽。
- 128个world seeds按顺序序列化为无空格JSON整数数组，其SHA256为 `e57785d0b2580a2918e965bc13f35010a32603dba1c8690d61111b76bb9f3655`。
- 已只读核对历史registry、v2全部1024 seeds及retired-v1全部1024 seeds的并集（4200个）：新128个内部唯一，与该并集碰撞数0。此检查未生成target。
- 实现时将完整seed向量及源文件hash另存于新目录，不修改冻结registry/config。核对项目其他seed记录是否还有未涵盖来源；若发现碰撞或范围漂移，停止，不静默替换seed。
- seed不同不等于行为独立：生成后检查完整任务身份重复；重复保留在扫描分母但不作新增独立world。相同parent本身不必等于完整world相同，需区分parent、D0与target等绑定。

## 不变的科学条件

保留现有有限DSL、规则库、105个motif、10动作、四轮验证、K1至K4定义及完整counterfactual检查。strict pair继续要求同world/parent/D0、两context同stratum和复杂度但行为不同、K2机会数相等、两臂无constant-K4、各有唯一nonconstant-K4动作且彼此不同。

不把“只找到一臂”“放宽到多个正确动作”“换成五轮成功”计入strict成功。105×10未扫描完整时不报告该world已无可用配对。即使parent已成功，首版也不引入未经等价性验证的捷径裁剪。

## 挑战类别与报告

在新strict pairs中沿用已记录的非恒定过滤＋最大parent行为差异策略（novelty降序、节点数升序、canonical hash升序、raw index升序）。分别统计：

1. 至少一臂策略失败：较弱诊断。
2. 两臂策略均失败：主要挑战候选。
3. 两臂均有novelty严格高于正确动作的非恒定错误候选：强误导类别，不靠哈希并列制造失败。
4. 两臂正确动作均有novelty和节点数相同的非恒定错误候选：并列类别，不宣称所有公开信息都相等。

类别可重叠，不相加当总量；每类报告pair数、唯一world数、各stratum容量和不重复world的平衡匹配容量。同步计算既定简单策略及非恒定最少节点诊断的表现，不因某策略变强就将其删除。所有筛选只用代码几何和公开特征，不读取模型输出。

本次不mint最终benchmark、不生成模型调用任务。如果候选充足，后续再冻结模型、确切cohort、提示、显示顺序及调用预算；不把12个world当硬性凑数目标。

## 实现与验证要求

- 独立代码置于 `reviews/`；新私有产物目录使用 `artifacts/shortcut-challenge-search-v1-20260909/`。首次写前确认Git忽略，私有world/target数据0600；共享报告只含汇总。
- 复用 `generate_spark_world`、缓存compressor、`_context_profile`、`pair_candidates_for_world`、公开特征与匹配组件；不修改旧源码/config，不伪造旧review授权对象或将新seed塞入旧plan。独立runner明确绑定自己的方案。
- 新world逐个完整保存，中断与异常单列；恢复只接续已冻结范围、保留原target seed及累计预算，不能覆盖旧响应或偷偷替换失败候选。
- 合成测试覆盖seed去重、target只生成一次、计时截止、校准阶段失败停止、恢复不重置预算、未完成不计阴性、strict不放宽、world不重复计数。
- 用已有小型测试夹具检查复用组件的语义一致性；不为“测试”偷偷生成额外候选。真实前4个既是成本校准也是正式扫描分母的一部分。
- 输出终态明确区分 `complete_fixed_range`、`calibration_budget_exhausted`、`scan_budget_exhausted`、`validation_error`。只有完整扫描才能陈述固定128个内的完整容量；部分扫描只能描述已完成前缀，可能有运行时选择偏差。

## 交付与停止点

启动前实施补充（未生成target时记录）：遍历所有configs中的seed字段后，历史排除集由4200扩至4204（包含旧live技术题seeds），新128个仍零碰撞。为避免把旧模型见过的提示当作新任务，凡parent+D0与原24相同者保守排除出挑战容量，即使target不同；新候选之间另按完整任务身份（parent、D0、target行为、规则bank及evidence/test点）去重。重复仍保留扫描分母。这是比只比较seed更严格的排除，不重新抽样补位。固定显示位置策略使用独立的确定性diagnostic顺序，不宣称已冻结未来模型benchmark的显示安排。

下一阶段交付：独立runner、合成测试、绑定方案与seed的manifest、前4个成本记录，以及预算允许时固定128个扫描的结果。报告全部完成/未完成/失败分母及运行时间，不追显著性。

本阶段不改变原论文结论。即使找到挑战，也只是实验材料可行；模型能否利用仍待另行试验。若未找到，结论限于搜索范围及完成状态，不推出全局不可构造或模型能力不存在。
