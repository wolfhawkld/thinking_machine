# 2026-09-09 DeepSeek thinking 小规模后续试验

用户授权：先开启 DeepSeek thinking 试验。此记录在本轮调用前建立。

## 固定设置与范围

- 同一 `deepseek-v4-pro`、官方 `https://api.deepseek.com/chat/completions`，thinking enabled、reasoning_effort high、max_tokens 8192、timeout 300 秒、JSON object。temperature 不发送：官方说明 thinking 模式忽略该参数。无 tools、seed、对话历史，仍只发送原 rendered_prompt。
- 先使用旧 target-free canary 中固定的第一对（2 次），只检查两次均有非空 reasoning、有效 option、stop finish、正确模型名。不是能力检验，也不声称它证明交换性。通过后依原 public 顺序运行相同 24 worlds / 48 tasks。
- 最多 50 次物理调用；每次 completion 上限 8192，总上限 409600（不含输入）。无自动重试、恢复、替换、扩样或调预算。每次调用前后 fsync 记录；已收到的无效选择保留，任一臂无效则该 world 按旧规则记 tie / complete-switch miss。transport/HTTP/envelope 失败停止且整轮不完整，不作完整配对结论。
- 单独 runner 与 artifact 目录，不修改冻结 primary、公共题目或评分 key。正式推理阶段不读取 private key；独立 analyze 命令才评分。
- 全部最终选择和 usage 留存；reasoning 留存存在性、字符数、SHA256及服务端 reasoning-token 字段（若有），不保存原始推理正文。finish=length 单独统计，不选择性删样。

## 解释预先约定

这是看到原 thinking-disabled 阴性结果后选择的描述性配置 follow-up，不继承旧 primary 的事前指定检验身份。比较同一 cohort 的 own/cross 命中、favorable/adverse/tie、完整双臂切换、四 strata 与全部24个冻结简单策略，并给出 old/new 完整切换四格表；不新增推断性检验。

thinking、有效采样方式、token cap 和 timeout 同时改变，所以结果只能比较整套配置，不能归因于单一开关。相同24题的新调用不是新独立world；改善不能覆盖旧结果、排除shortcut或证明通用科学探索/内部entropy机制。即使有 improvement，仍须看简单策略是否已解释该表现。

官方接口说明：[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)、[Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/)。

运行命令：`python reviews/deepseek-thinking-followup-20260909.py plan`，随后 `run --execute`，完成后 `analyze`。产物目录：`artifacts/deepseek-thinking-followup-20260909/`（本地0600，不提交private数据）。

## 执行终态：技术canary未通过，已停止

北京时间2026-09-09 10:32启动，两次各约135秒。请求模型与返回模型均为 `deepseek-v4-pro`；两次都有非空reasoning，均为 `finish_reason=length`、最终content为空，0/2有效选项。每次input743、output8192、reasoning8192，合计input1486、output16384（全部reasoning）；总输入+输出17870 tokens。这里reasoning已包含在output中，不能再相加。

因此thinking的确开启，但high/8192预算在这两道技术题上不足以产出最终选项。它不是“模型选错了”，也不能用作Opportunity utilization、能力改善/退化、假设支持/反驳的证据。48题没有启动，未产生generation或analysis，不运行analyze。没有transport failure、重试或替换；进程已正常退出，无后台任务。

原始记录（本地0600）：

- plan file SHA256：`73886ff2be2fdef0219e7514bbcd27781ca52bb05ced6d2a205457b56c3e116f`。
- canary file SHA256：`24aa78cd55470f0b73d6fddc1a9ec29672c2d06e42927ac94d4fda2236311faa`，`passed=false`。
- `attempts.jsonl`包含启动和两次请求前/响应后的即时记录；reasoning正文不保存，存在性/字符数/hash和精确usage已保存。

验证：新合成测试6/6、原live回归12/12、diff check通过；旧primary绑定文件与原源码未改。下一步应先讨论提高单次token上限或降低reasoning effort的新技术试跑，而不是扩world样本或直接运行48题。需要新设置和新产物目录；不覆盖本轮失败记录，也不保证增加预算一定能出答案。
