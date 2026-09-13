# 2026-09-09 DeepSeek thinking 128K 技术预检

用户先要求提高8192上限，又在32768版本真实调用前要求进一步提高。32768版本只生成plan，没有attempts日志或真实响应，保留但不执行。

## 调用前固定范围

- 同一官方 `deepseek-v4-pro`，thinking enabled，reasoning_effort high，JSON object，不发送temperature，不加工具、历史消息或新提示。
- `max_tokens=131072`，单次socket timeout=3600秒；提高超时以免思考尚未完成就被客户端断开，但这不是服务端完成保证或严格总墙钟deadline。
- 相同两道target-free技术canary，最多2次调用、262144 completion tokens（含reasoning，不含input）。顺序、每题只调用一次；无自动retry/resume，不启动48题。旧8192结果和旧primary保持原样。
- 只判断能否得到同模型的非空reasoning、stop finish、有效最终option；不判解题能力或交换性，不打开private key。两题用于见过截断结果后的预算校准，不是新增独立实验样本。
- 原响应解析器的6项合成测试通过；128K runner fake transport验证了131072/3600/high参数、两次上限、成功终态、不启动benchmark、再次执行被拒绝。fake结果仅在临时目录，不混入真实结果。

官方文档：[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)公布最大输出384K；本次选128K，未用满服务端上限。

脚本：`reviews/deepseek-thinking-128k-canary-20260909.py`。
本地产物：`artifacts/deepseek-thinking-128k-canary-20260909/`，文件0600，逐次请求前后fsync。

## 状态

已完成：2026-09-09北京时间10:53:19，2/2 valid、stop、无截断，passed=true。两题output分别20633和12715，其中reasoning分别20622和12706；合计input1486 / output33348（reasoning已含其中）。耗时分别401.8秒、244.5秒。结果已保存，无重试，benchmark calls=0。

此结果只说明128K预算在两道技术题上允许完整作答，不是能力证据。随后用户另行授权48题，见 [独立48题后续对照](deepseek-thinking-128k-live-20260909.md)；不要重跑本canary。
