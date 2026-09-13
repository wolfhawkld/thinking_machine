# 2026-09-09 GLM-5.3流式48题对照

用户在两道流式预检全部通过后授权「那开始吧」。不重复预检，不重启旧非流式runner。

## 冻结设置

同一24对48题、原prompt/顺序/评分，TokenHub glm-5.3 / thinking enabled / reasoning_effort high / max_tokens131072 / temperature1 / top_p0.95 / JSON object / stream=true，stream_options.include_usage=true。

完全复用已通过预检的流式传输：连接15秒超时、120秒无reasoning/answer字符增长停止、每请求3600秒硬总截止，每30秒保存进度，精确token取最终usage，reasoning正文不保存。HTTP200、DONE、usage、model契约检查保持不变；最终invalid选择占slot、不重试，评分时对应world按冻结规则tie/miss。

每3秒发起一道、最多48worker，最多48次请求、completion上限6291456（含推理，不含输入；不是预估消耗）。无自动重试/恢复/替换/扩样。transport/envelope失败停止新派发，等待已发请求终态，保存所有响应；缺题不构成完整可评分组。

逐题0600结果、fsync日志、最终按原task顺序聚合generation。正式调用不读private key。完整结果后另行离线评分，与DeepSeek两配置、24原基线及2种单列的非恒定过滤诊断比较。跨模型探索性证据，不合并为独立72 worlds、不以三条件为显著性依据、不新增推断性检验。

## 验证与恢复

原流式5项测试＋本次2项48题调度测试共7/7通过，覆盖解析/截断终态、无进度终止、恰好48调用/原序、不重跑canary、拒绝重启、失败保存及凭证不落盘。测试均为本地fake，无provider调用。

脚本 `reviews/glm53-stream-live-20260909.py`，产物 `artifacts/glm53-stream-live-20260909/`。plan→run --execute；恢复时查看attempts、response-XX、generation/failure，不重新run。新bundle含流式终态与元数据，不能直接送入旧DeepSeek专属验证器。

前置预检：两道均valid/stop/DONE，分别926.21秒/31029输出token、656.24秒/13556输出token，共44585输出token；无截断。旧非流式2个中断请求与诊断调用不计入本48题，旧消耗有未知部分。

终态：原48次请求已结束，47条完整、index24一次无内容增长超时，旧目录failure保留。用户明确授权补跑缺失题后，独立目录合并成48条并完成评分，GLM38/48、15/24，见 [最终结果](utilization-glm53-results-20260909.md)。本脚本不重启。凭证继续只读本地tokenhub.key（0600且本地Git忽略），不回显或提交。
