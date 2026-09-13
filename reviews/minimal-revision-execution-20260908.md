# 最小修订实施记录（2026-09-08）

用户已确认 [最小修订方案](../minimal-revision-plan-20260908.md)。本记录固定补充报告的解释口径，并记录已执行的离线核对；不覆盖任何冻结 primary 协议、题目或历史结果。

## 历史重放差异的定位

一次完整重放的诊断见 [replay-audit-20260908.json](replay-audit-20260908.json)，复现脚本为 [replay-audit-20260908.py](replay-audit-20260908.py)。脚本仅观察哈希计算前的报告，不替换哈希返回值、不关闭原校验；原 validator 仍按设计报告不一致。

除 analysis digest 本身外，sealed 与当前重放对象只有五处差异：replacement-1/child 的 log2 描述性总和与均值，以及三个 world-rate Clopper–Pearson 下界的浮点末位。最大绝对差为 `3.552713678800501e-15`。所有 world/slot 轨迹、候选、版本空间整数计数、K1–K4 endpoint、分类及其他字段完全一致。

差异定位到 `spark_closure._signed_summary` 的浮点求和及 `_clopper_pearson_interval` 的浮点二分输出；与运行环境/浮点累积差异相符，但本次未跨旧环境进一步归因。当前解释器为 Python 3.12.3。无需为了使用整数 endpoint 结论而修订原 sealed JSON。

依赖核对：新 construction 使用共用 world/lineage/compressor 和 `_is_k2` 等逻辑，但未使用上述旧描述性均值或区间下界计算标签；新 live scorer 使用固定 private 正确选项和整数配对计数。此次差异没有改变现有 scientific endpoint，不能再称为“已发现评分逻辑错误”。旧字节级重放测试仍失败，不能写全仓测试通过。此前将 layered-v1 暂移出主定量证据的原因现已解释；若引用，须同时披露数值末位差异，不删除历史版本，也不把旧 cohort 混入当前分母。

## 模型结果之前固定的补充报告口径

- 使用 [utilization-supplement-20260908.py](utilization-supplement-20260908.py) 生成独立描述性报告；冻结源码、config、prompt、primary、failure policy 不变。工具与测试位于 `reviews/`，作为独立报告材料，不加入或替换冻结分析器。
- 预先保留全部 24 条 target-blind policy。已生成 [utilization-baselines-20260908.json](utilization-baselines-20260908.json)，`model_results_available=false`。未来报告完整切换 `C/24`、四 strata、每条规则的同题四格比较和差值，不新增显著性检验、最佳基线门槛或总体置信区间。
- 明确 `H/48` 沿用原 scorer 的配对有效性处理：任一臂 received-invalid 时，该 world 的两个 own-hit 指标都为零。因此报告名为 `paired_valid_own_hit_count`，不称独立逐调用准确率。完整切换和主分数的 invalid 规则保持不变。
- 补充报告有 `--analysis` 时先重放冻结正式 analyzer，要求与输入 analysis 完全一致且 primary 可评价，才进行模型对照。缺失/transport failure 不转为部分样本描述性“正式结果”。无 `--analysis` 只生成基线表。
- 所有简单策略均为合理比较对象，不能因其强而删除或重选 cohort。规则可同样通过当前 sign test；拒绝该 null 只表示净 context 匹配方向，不识别内部熵机制，也不证明优于规则。

## 原 sign test 的成立条件复核

对固定 pair，交换两臂完整响应会使 signed score 变号；若任一臂 invalid，交换后仍是 tie。条件于非 tie，符号对称性需要 joint response/validity 的交换性；跨 world 检验另需适用的独立性假设。相同 route/配置、stateless、共享选项顺序和不暴露 arm 标签有助于排除实现差异，但不能从 hard balance 推出数学假设成立。

把该检验限定为冻结 pair/schedule 下的响应交换性 null，可以保留原 primary 的有限角色；不能把 null 简写成“模型没有科学探索能力”。持续的时间/服务状态相关、context 导致的 validity 差异等仍是限制。小 canary 只检查明显 dispatch/响应契约异常，不证明科学交换性。正式授权须按既有协议在 canary 后记录人类对该限定解释的审核，不由本记录虚构批准。

## 预算敏感性与执行状态

四轮/五轮诊断只重放所选 24 个已打开 development targets 和两臂的 K1-supported actions；原 cohort、正确选项与 K4 标签不改。检查 target retention、child hash、四轮 K2 机会数与原 private key 一致，并把五轮闭环明确标为诊断性的 K2-like 结果。

第一版误复用了带固定四轮上限的 `_is_k2` 来统计五轮新增闭环；完成产物保存在 `budget-sensitivity-20260908-retired.json`，不作为有效敏感性结论使用。parent/正确 child 的直接 identification 计算不受这一口径错误影响。修订脚本使用显式预算并对四轮口径作一致性断言，追加五轮边界测试；修订重放已完成。该修正没有重抽 target、改变 cohort 或调用模型。

有效结果见 [budget-sensitivity-20260908.json](budget-sensitivity-20260908.json)，复现脚本为 [budget-sensitivity-20260908.py](budget-sensitivity-20260908.py)：

| 所选 cohort 上的确定性指标 | 四轮 | 五轮 |
|---|---:|---:|
| parent 唯一识别 world 数 | 0/24 | 19/24 |
| 原正确 child 唯一识别数 | 48/48 | 48/48 |
| 两臂所有 K1-supported raw actions 中的 K2-like 闭环数 | 48/299 | 244/299 |

五轮新增 196 个闭环机会；四轮列与原 K2 口径逐臂一致，五轮列只是显式延长预算的诊断，并未重新计算 K4 replacement specificity。raw action 不是独立样本，299 也不是 world 分母。

含义：多数 selected worlds 中 parent 多一轮即可恢复，因此当前成功应解释为固定预算内的查询路径效率优势。只有 5/24 个 world 在五轮仍保留 parent-failure 对比；这不证明五轮之后仍无法成功。不能把四轮下的 strict unique K4 几何延伸为任何预算下都只有一种发现途径。此敏感性不产生模型证据，也不改变原 primary 标签。

已通过 5 项补充报告/预算边界合成测试和原 live 模块 12 项 fake-response 测试。冻结 live source manifest 仍为 `23a23195ef1a6bdd00a12a2880959edc2ef1db3f968f7d6c434486faf670ec69`，原 live plan 本地校验通过。上述测试没有 provider 调用。凭据与请求配置本地预检查通过；凭据未写入报告。

## 真实 canary 完成与正式调用恢复点

已按冻结计划运行 8 次 `deepseek-v4-pro` 无目标 canary：8/8 valid、0 invalid、0 transport failure，response contract 与 route 稳定；两个 phase 中两臂各 2 次，均有效，未观察到明显 dispatch/validity 不对称。无重试、换 route 或正式 benchmark 调用。

- artifact：`artifacts/spark-strong-k4-utilization-primary-live-v1-20260908/deepseek-pro-canary.json`
- file SHA-256：`07ccb80a814a34835a929b5ea371109d43e518d157a560f70eea55947b92b3a6`
- canonical SHA-256：`ad4564e630183b282355c677520935936f5e87e2324d3b4b59ade5beaeae3d3f`
- route binding：`d44699c6e1463c8f428c72e04585feac9cdaf20cd64a680109b1e4d1d9255936`
- plan file SHA-256：`f8d99fba3c50510beb6180c2f14fc34d91fed30ee0ef61caff1c923ee07641de`

canary 仍明确标记 `scientific_exchangeability_proven=false`、`human_approval_still_required=true`、`primary_calls_authorized=false`。确认方案本身已授权本轮实现与 canary；但原协议要求的 canary 后人工交换性审核尚未完成，不能把之前对草案的确认回填成已经审核新 canary 的事实。正式 48 次调用、authorization、generation、analysis 均未创建。

需审核的有限解释：检验只针对本批固定题、同一 route 下两臂响应的净匹配方向；成立依赖无利用 null 下响应/validity 交换性与适用的跨 world 独立性。canary 未发现明显实现异常，不证明这些假设；显著也不证明内部熵因果、总体泛化或优于简单规则。用户确认这一限定后，才用既有 `authorize --human-approve-exchangeability` 记录授权并执行 48 次任务。

当前凭据来源为已注入的 `DEEPSEEK_API_KEY`，base URL 使用项目已冻结的官方地址并通过请求契约校验；旧 `../IntentWeight/.env` 路径在本设备不存在。无须用户在聊天中发送 token。

## 正式调用授权与启动（2026-09-08）

用户在收到 canary 8/8 结果、预算敏感性和有限统计解释后明确回复“启动”，完成上述 canary 后人工审核授权。已使用冻结 CLI 创建 `authorization.json`，file SHA-256 为 `a752013da4cb7b07f57bacc17795fe7348a5155b20038ab5c98a8b96461afe7f`。这只记录用户接受受限推断所需的假设，不表示交换性已获实验证明。

正式 48-call `deepseek-pro` 任务现已启动；使用同一 plan/canary/public/authorization 绑定、无重试或补题的失败规则，ledger 按调用保存。待完整 bundle 封存后再运行原正式 analyzer 与独立描述性补充报告，不在生成过程中评分或调整设计。

## 正式调用与分析已完成

上述启动阶段已结束：48/48 响应有效，无 transport failure/retry；完整切换 0/24、own 命中 5/48、配对 0 favorable / 1 adverse / 23 tie，原单侧 p=1。冻结分类为未检出。正式 analysis 与完整的 24-policy 对照均已保存并通过原 analyzer 精确重放。详见 [正式结果记录](utilization-primary-results-20260908.md)，包含 hashes、分层结果、API 用量与解释边界。当前没有运行中的实验，不自动开始额外调用。
