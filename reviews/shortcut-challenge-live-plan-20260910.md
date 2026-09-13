# 九world模型挑战：执行协议（2026-09-10）

用户已确认：九个不同world、18次正式DeepSeek thinking请求、最多另2次全局技术重试。构造扫描已完成且audit通过。本实验是新的描述性挑战随访，不继承旧primary显著性检验，不改变原研究结论。

## 材料与发送前验证

使用完整新1024池中全部9个联合策略失败world，每world选pair SHA256最小的一对，不按LLM响应选题。raw动作显示顺序沿用该world原诊断顺序并在此次live冻结，opaque option/task ID使用独立命名空间。18个任务按固定task ID摘要排序调度；不强求四层等额，报告实际构成。

target-free方式重建D0、parent及context，复核parent/prompt身份；重放每个配对的全部26种公开策略，必须与扫描产物一致。只将原公开提示的D0、parent、context、DSL定义及opaque选项发送给模型，绝不发送正确标签、target、world seed、策略失败说明或挑选条件。私有评分映射单独保存0600，发送进程只读取公共plan。

## 配置与预算

复用既有DeepSeek配置：endpoint `https://api.deepseek.com/chat/completions`；model `deepseek-v4-pro`；thinking enabled；reasoning_effort high；max_tokens 131072；response_format json_object；stream false；temperature/top_p均不显式设置。单请求技术timeout 3600秒，不是候选搜索截止。凭证从既有DEEPSEEK_API_KEY读取，不记录或打印。

18个正式请求，3秒错峰、最多18并发、每次独立上下文。第一轮结束后按失败task index升序，最多选择2个可重试的技术失败任务，各重试一次；不因完成先后或正确性选择重试。仅transport失败、HTTP429/5xx允许技术重试。收到200但内容无效、截断、解析失败/模型返回契约不符均不重试；401/403等不可重试HTTP错误停止新派发，已发请求排空并记录缺失分母。全部物理请求最多20次，输出上限总和最多2621440 token；这是上限，不是预计消耗。

每个请求开始前持久化日志，所有返回和失败保留；独占日志拒绝重复启动/自动恢复。技术失败可能存在服务端已计算但usage未知，已知usage与完整性分开报告。不新增canary调用：复用既有传输与解析实现，不宣称已有canary保证当前网络/接口可用。

## 评分与停止

先保存完整generation，再独立加载私有映射评分。固定九world/18任务分母；缺失或无效臂记miss，按臂报告own/cross，双臂全对按9个world报告；另报双臂均有效子集的结果以避免混淆与旧配对有效评分的差异。全部26个基线在所选九对上单列；两重点策略不能双臂全对是筛选条件，不是新统计检验。

报告模型与每个基线的双臂成功交叉表、失效/缺失/截断及reasoning实际返回情况，保留各层构成，不计算新p值。完成预算后停止，不加样、不换模型、不放宽筛选，不把一次结果作为通用科学发现、内部熵因果或RSI证据。

机器plan冻结输入源码hash、公共tasks与私有文件hash。执行器只能在核对机器plan后发起请求；本文件是可读说明，以与其一致的机器plan和请求日志为执行证据。
