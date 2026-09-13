# 新world有界搜索执行记录（2026-09-09）

## 最新状态：用户取消时间截止，固定范围续跑已启动

前轮 `scan_budget_exhausted`，完成51个、index51被截止，累计计账3600.00386秒。完整部分发现1个strict world/pair，简单策略两臂均正确，不属于目标挑战；其余77个未完成或未扫描，不判为阴性。

用户授权后，[无时间截止修订](shortcut-challenge-full-range-20260909.md)已保存；新目录 `artifacts/shortcut-challenge-search-full-range-20260909/`。原51个文件hash核验后引用，index51同seed重做，继续至127。24项相关测试通过并已启动。无时间截止但候选数仍固定128，不自动扩搜；实际错误暂停，无模型调用。新state/summary为恢复入口，不重复run。

## 最新状态：修订续跑已启动

校准已通过：index3补做用时39.19秒，四个完整world均已保存/绑定，已自动继续index4及后续。校准累计计账639.20秒包括旧预算保守预扣600秒，不是实测总时长。全批尚未结束，不提前作机会容量结论。

用户同意继续；[独立续跑修订](shortcut-challenge-resume-20260909.md)及21项相关测试完成。新目录 `artifacts/shortcut-challenge-search-resume-20260909/`，index0..2原hash引用，index3同seed补做一次，之后原序继续。旧时间保守计600秒，第4个最多300新秒，总计账上限仍3600秒；此修订不再宣称原600秒校准协议未变。新state为当前状态，终态自动写summary。旧目录全部保留，不重新启动任何run。

## 最新终态：技术修复后暂停，尚未完成校准

已完成候选index0、1、2，各105 contexts/1050动作，耗时约46.04、75.48、88.36秒；结果全部保留，均未出现strict配对，重复排除0。index3（第4个）被主动中断，没有完整结果；其余124个未启动。完成3/128不代表整个搜索范围不可行，也没有模型能力结论。

启动后只读审查发现：调用旧 `action_order_for_pair(index, namespace)` 在index24及以后会触发旧24-slot限制。为避免继续走向已知错误，主动中断当前会话，exit130。最后保存的heartbeat为232.05397秒；原state的`running`是外部中断后的过期状态，不能理解为仍在运行，最终精确耗时未知。另存 `termination.json` 说明，不篡改原state。

修复改为 `action_order_for_pair(index % 10, namespace)`，保留原10位置循环排序。新增测试覆盖全部128编号及前24编号与旧函数完全等价；19项新测试通过，原8项pair/matching回归先前通过。通过从修复版移除精确patch重建旧engine，SHA与运行manifest完全一致，证明修订范围只有此处。前三个结果不需要因修复作废。

产物目录 `artifacts/shortcut-challenge-search-v1-20260909/`：manifest、3个完整world、原state及summary、termination和`interrupted-summary.json`均保留。原summary在源代码修复前生成，其running标签也是快照；应以termination-aware的interrupted-summary为恢复入口。没有provider调用、自动重试、target重抽或新benchmark。

下一步需要单列续跑修订：保留前三个，决定是否以同一固定seed重新执行未完成的第4个，记录额外物理计算但不更换target，并结转已有预算及计时不确定性。原runner禁止在stale running状态下自动resume，源代码修复也使原manifest校验不再匹配；不能直接重启默认命令或覆盖manifest。当前遵守原方案“不自动重跑中断候选”的限制，已停下。

方案：[固定128候选/600秒校准/3600秒总预算](../shortcut-challenge-search-plan-20260909.md)。独立运行，不修改冻结v2协议，不调用模型。

## 启动前

- Supervisor：`shortcut_challenge_runner_20260909.py`，每world一个隔离工作进程，完整文件0600且排他创建。
- 无模型/凭证/网络入口；旧产物不覆盖。新目录已通过Git忽略检查。
- 7项supervisor测试通过，包括模拟600秒到期终止工作进程、不启动下一候选、不把未完成计为阴性、不重置恢复预算、不自动重抽中断target。
- 原配对和匹配组件8项回归测试通过，未执行真实新world作为测试。
- 对不完整world或异常中断采取保守停止，不自动重跑。只有保存于world边界的暂停状态允许在原累计预算内恢复；崩溃留下的running状态不能作为全新预算重启。

## 当前状态

扫描引擎及18项新测试、8项原逻辑回归均通过。真实校准已启动，manifest和state在 `artifacts/shortcut-challenge-search-v1-20260909/`，每个world完整后单独保存，原冻结文件未改。历史seed排除集完整检查为4204（含4个旧live技术题seed），新128个无碰撞。此处仅启动状态，最终以state/summary为准，禁止重新执行默认启动命令。
