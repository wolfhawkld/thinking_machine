# 结构化数值参考三条件：正式调用协议（2026-09-10）

用户已批准DeepSeek thinking、54正式请求＋最多2技术重试。本文件在调用前冻结；以机器plan绑定的内容为准。不得更改旧实验或本次草案以追求结果。

## 固定材料与配置

全部九world、两臂、三条件：aligned、unmatched、no_reference。任务与私有评分逐字继承已验证54草案，完整候选、原option标签与4轮评分不变。对应和不匹配参考字符数／数值多重集匹配，不声称等token、等信息量或与隐藏目标统计独立。解释与[设计草案](context-structured-reference-plan-20260910.md)一致。

模型deepseek-v4-pro、thinking enabled、reasoning_effort high、max_tokens131072、response_format json_object、stream false，不显式发送temperature/top_p，endpoint `https://api.deepseek.com/chat/completions`。每题单user消息独立上下文；只有prompt进入消息，不发送task元数据、私有映射或标签。credential仅读取DEEPSEEK_API_KEY，不写入记录。

## 调度与停止

按固定公开原task ID哈希排序18个task block；每block三个条件采用6种排列循环，各排列恰好3次，每条件在每个block内位置出现6次。每3秒发起下一个请求，最多54个在途；同world仍是配对观察，不因并发或条件数增加独立样本。条件顺序与模型响应无关。

单请求3600秒是技术timeout，不是科学搜索截止或基于效果早停。第一轮全部收尾后，仅选择按调度index最小的最多2个可重试技术失败，各一次。HTTP429/5xx及标记可重试的传输失败可重试；格式错误、已收到内容无效、截断或答错不据此重试；401/403等不可重试协议失败停止新的派发，已发请求收尾。总物理请求不得超过56，无额外canary。

沿用已测试的旧transport代码，独立adapter只在本进程改task总量54、总预算56及新目录；原文件不修改。旧transport写入generation.kind的历史名字保留作实现来源，实际实验身份由本次plan定义。每attempt即时保存raw body、独立JSON与fsync台账；所有技术失败保留，未知消耗不称精确总账单。排他创建台账防重复run；中断时先检查产物，不能盲目重新执行run或重置预算。

## 评分与解释

调用后自动执行独立分析。每条件分母9world/18task，输出own、cross、完整双臂切换、有效／无效／缺失及逐world结果。三组配对比较aligned−unmatched、aligned−no_reference、unmatched−no_reference，不新增p值，不继承旧power，不复用旧响应。原始响应／台账与评分重放核验另保存审计。

aligned优于unmatched不单独证明帮助；需结合no_reference区分收益与干扰。正向只支持当前冗余辅助数值参考对应性的局部行为；仍可由预计算帮助、注意或简单语义匹配解释。阴性不证明模型完全忽略context；全部结果保留，不扩样或换题。

输出上限总和56×131072=7,340,032 token，不是预计或保证用量。完成后记录实际usage、截断及有效结果；在有失败用量未知时明确不完整。
