# 修订提示微型闭环：正式协议（2026-09-11）

用户已明确批准新一轮108正式响应＋最多2次技术重试。单独目录 `artifacts/microloop-revised-live-20260911/`，不覆盖或合并旧轮的原始数据、主结果和事后重评分。

沿用原12world，三条件active/random/repeat，各两轮反馈＋最终回答。此次是看过旧结果后的修订版探索性重测，不是未见world确认实验。只更换已测试的语法提示／表达式接受契约：允许根gt/eq，并统一展开为ite二元整数表达式；深度5、节点31按展开后canonical表达式计算。其他合法性、query调度、反馈与评分分母不变，提示示例不提供目标信息。

DeepSeek `deepseek-v4-pro`，thinking enabled、reasoning_effort high、max_tokens131072、JSON模式、stream false，不额外指定temperature或seed。最多4条轨迹并发，每轨迹阶段依赖串行，单次3600秒技术超时。最多110物理请求，completion理论上限14,417,920tokens，输入另计，不是预计账单。

原技术重试规则沿用，读取响应中断归入网络技术错误；无效内容、错误预测或length截断不重试。全局额度耗尽后按原规则保留缺失，未知中断状态停止核对；原请求可能服务端已处理，失败用量不当零。没有额外canary。逐次保存raw、prompt、结果及终态，完成后自动保存records、修订版analysis和complete用量台账。

主要终点仍为12world等权的最终64点准确率及active−random；报告初始至最终变化、合法率、观察一致性、缺失与全域精确恢复。代码基线和恒0强基线不隐去，不把高raw准确率直接解释成发现规律，不新增事后显著性检验。新旧轮并列而非混合，不将提示变化前后的差异全归为模型能力变化。
