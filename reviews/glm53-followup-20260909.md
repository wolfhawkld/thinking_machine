# 2026-09-09 GLM-5.3跨模型探索性对照

用户要求增加GLM-5.3，并提供腾讯TokenHub端点及本地凭证位置。目的为跨模型复现，不因凑足「三组」而声称显著性增加。三个条件共享同一24个world，不合并成72个独立world。

## 调用前固定设置

- Endpoint：`https://tokenhub.tencentmaas.com/v1/chat/completions`，model=`glm-5.3`。
- thinking enabled / reasoning_effort high / max_tokens131072 / socket timeout3600秒，temperature1.0 / top_p0.95 / response_format json_object / stream false。
- high作为与DeepSeek同名的明确设置，不声称不同厂商的high强度或算力等价。GLM官方默认effort为max，本轮明确选high；GLM采样1.0/0.95，不能与DeepSeek thinking忽略采样参数视为完全匹配。
- 沿用原两道target-free技术canary与原24对48题。只发送原rendered_prompt单条user消息，不照抄示例curl的额外system消息，不加入历史、工具、答案、pair标签或private字段。
- 每3秒发起一道、最多48worker；先两道canary全部结束且均满足有效option、stop、非空reasoning、返回同模型名，才自动开始48题。canary不评分能力，也不证明交换性。
- 最多50次请求（2+48），理论completion上限6553600 tokens，含reasoning、不含input。每题一次，无自动重试、恢复、替换、扩样。收到invalid占slot；transport/envelope失败停止新派发并保存已发响应，未完成组不做完整结果判断。
- 逐题0600响应文件和fsync日志、按原task顺序聚合；final expression和usage保留，reasoning正文不保存，仅存在性、长度、hash和提供的token字段。TokenHub若不提供reasoning token字段则记null，不填0。

## 比较范围与解释

沿用原paired scorer、own/cross命中、完整双臂切换、四strata，与DeepSeek关闭/开启thinking分别比较；全部24种原策略仍保留。另将已在DeepSeek案例审查中提出的两个非恒定过滤诊断独立列出，不伪装成原24基线；本轮已在GLM输出前明确纳入比较。

不新增推断性检验，不因结果不显著追加采样。即使GLM也成功，只支持当前受控设置下行为可跨模型观察；若简单策略接近模型，不据此声称通用科学探索、内部entropy机制或RSI。原DeepSeek结果和协议全部保留。

官方参考：[腾讯GLM调用指南](https://cloud.tencent.com/document/product/1823/132061)、[深度思考](https://cloud.tencent.com/document/product/1823/131208)。文档说明glm-5.3不支持关闭thinking、支持low/high/max、最大输出128K，推荐temperature1/top_p0.95。

## 凭证与验证

仅运行时读取根目录tokenhub.key，不写入源码、命令参数、prompt、hash或实验记录，不回显Authorization。文件权限已收紧至0600，通过本地.git/info/exclude忽略；冻结.gitignore未改。示例中的凭证未用于运行。

脚本 `reviews/glm53-followup-20260909.py`；产物 `artifacts/glm53-followup-20260909/`。4项fake测试通过：2+48上限、原题顺序、凭证不落盘、拒绝重启、canary格式/返回模型/HTTP失败均阻止benchmark。无测试真实调用。

## 状态与恢复

已准备启动plan→run --execute；canary通过自动进入48题。恢复时先读canary.json、generation.json、attempts.jsonl；不要重新运行已有ledger的run。完整generation后才离线分析，不重用验证DeepSeek模型名的旧分析器。当前尚无GLM能力结果。
