# 非thinking预算对照：正式执行（2026-09-10）

用户明确授权“32＋2次调用、256／8192两档预算，继续”。科学方案和固定子集沿用 `nonthinking-budget-plan-20260910.md` 及 `artifacts/nonthinking-budget-draft-20260910/`，其“待确认”保留为历史。仅将授权状态落实，不改观察、提示、选择或评分。

- 原24world中按预存hash规则选四类各2个，共8world16臂；每臂两档，共32正式槽。每档16次，不按历史成绩挑题。
- 模型deepseek-v4-pro，thinking disabled，temperature0.2，JSON object，stream false，120秒技术timeout；无top_p或reasoning_effort新增参数。只在两条件之间改变max_tokens256／8192。原提示逐字保留。
- 原32草案顺序不变，3秒错峰，最多32在途。两档条件ID只用于本地记录，不发送给模型。最高输出预算151552tokens，不含输入，不是实际用量预期。
- 采用已有单次HTTP transport，首轮全部结束后，最多2个最低序号可重试技术失败各一次；无canary或客户端隐含重试。HTTP429/5xx或允许重试的传输失败才可重试；内容错误／无效／截断不重试。非可重试HTTP停止派发，保存现场，不擅自改预算。
- 每次raw和attempt独立0600落盘、台账fsync，禁止重启覆盖；最终generation保留全部32槽。自动raw重放核验后执行分析与results.md输出。
- 每档固定8world16臂，报告own/cross/full/net及逐world配对变化；无效缺失保留分母。不新增p值；历史nonthinking与thinking只取相同16task参照，不直接与旧24world总分配对。
- thinking历史131072、采样、时间等设置不同；同期高低差不等于普遍推理能力或内部机制。未截断不证明内部没有预算相关实现，但排除可见答案被256硬截断这一直接解释。
- 绑定当前runner、audit、analysis、原transport依赖、固定草案和历史generation。旧文件不改。

目录 `artifacts/nonthinking-budget-live-20260910/`；入口 `python3 reviews/nonthinking_budget_live_20260910.py freeze|validate|run --execute`。完成后只读 `nonthinking_budget_response_audit_20260910.py`、`nonthinking_budget_analysis_20260910.py --verify`。用户授权不等于系统网络许可；如需审批单独请求。

最终核验、日期化报告、TODO/HANDOFF与论文讨论更新后停止，不追加模型／样本，也不因高档表现差而追加长推理提示。
