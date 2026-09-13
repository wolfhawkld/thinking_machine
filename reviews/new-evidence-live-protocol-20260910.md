# 新证据候选规律生成：正式调用协议（2026-09-10）

用户已通过当前goal授权实施、冻结、执行、核验及总结：27次正式请求，最多2次技术重试。采用 `artifacts/new-evidence-draft-v2-20260910/`；科学设计及评分严格沿用 `reviews/new-evidence-protocol-draft-20260910.md`。本文件只将草案中的待授权调用设置落实，不变更观察、目标、筛选、评分或样本。

## 调用与停止

- DeepSeek官方原endpoint，model `deepseek-v4-pro`，thinking enabled、reasoning_effort high、max_tokens131072、JSON object格式、非流式；temperature/top_p不显式设置，沿用已验证配置。
- 9world×3条件27正式请求；每个独立上下文，仅发送公共rendered_prompt，不发送私有记录或测试标签。
- 按公共提示hash排序九world块；baseline/new_evidence/repeat_evidence三种循环顺序各使用3次，各条件在每个位置出现3次。不是六排列等频。每3秒发起一个请求，最多27在途，不等待上一请求结束才发下一条。
- 单请求技术timeout3600秒，防止永不返回；不是搜索时间预算或根据结果提前停止。max_tokens为每请求输出上限，29次最大上限合计3801088 tokens，不是实际费用预测。
- 首轮全部在途请求终止后，仅最低调度序号的最多2个可重试技术失败各重试一次。限TransportError允许的物理重试或HTTP429/5xx；无额外canary，无隐含客户端重试。HTTP200的内容、格式、截断或预测失败不重试。非可重试HTTP（含401/403）停止新派发并保留现场。
- 原始响应、每attempt记录和fsync台账逐题保存0600，拒绝覆盖。不得重启已有台账来重复收费；进程异常先核对现场及存活句柄，不自动重发交付状态未知的请求。
- 全部请求结束后自动原始响应重放核验、评分与报告，随后更新TODO/HANDOFF和论文轴线；无论正负结果都保留。完成后停止，不自动做第三项非thinking预算敏感性或新增模型。

## 评分与解释

模型输出严格JSON expression中的DSL候选规律，不是旧option ID。冻结v2评分保留9world分母，缺失／无效0，主指标每world64私有点准确率等权平均；可见观察一致性另列，不一致不暗中扣主指标。报告三条件配对差、赢输平、有效程序、D0／全部观察一致性、64点全对及125全域精确恢复，三个代码基线全部保留。仅探索性描述，无新p值。

历史transport的generation.kind仍是原transport名称，valid_choice字段仅代表合法二值DSL（不是预测正确）；本次plan、原始content和新评分决定科学终点。不得从历史evaluable推断K4或科学发现成功。截断即使碰巧合法仍按冻结程序评分，并单列截断信息；不追加内容修复或正确性重试。

这是一条明确给定目标证据后的单次规律生成／修正，不是目标独立噪声、内部熵机制、自主闭环或RSI。九world已用于development，不是独立新world泛化。重复旧观察不匹配新增观察的新颖程度及位置；公开生成器先验及普通规则筛选仍可能解释结果。

## 产物与验证

新目录 `artifacts/new-evidence-live-20260910/`：plan.json、private.json、attempts.jsonl、逐attempt/raw、generation.json、response-audit.json、analysis.json、results.md。冻结plan绑定源码、正式协议、v2材料及九个原始world文件。旧实验文件不修改。

入口：`python3 reviews/new_evidence_live_20260910.py freeze`；只读 `validate`；批准网络后一次 `run --execute`。完成后只读 `python3 reviews/new_evidence_response_audit_20260910.py` 和 `python3 reviews/new_evidence_live_analysis_20260910.py --verify`。局部测试用模拟响应，不消耗API预算。
