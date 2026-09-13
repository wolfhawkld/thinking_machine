# 2026-09-09 GLM流式技术预检（用户授权替换）

用户明确要求停止非流式并开启流式。旧session97529的线程池在Ctrl-C后仍等待网络，随后核实PID12885身份并SIGTERM，session确认exit143。旧ledger只有2次canary attempt_started，无完整响应、无48题调用；服务端是否取消及已消耗token未知，不重写旧记录。

## 新预检

- 同两道target-free技术题、相同glm-5.3 / thinking enabled / high / max_tokens131072 / temperature1 / top_p0.95 / JSON object，只改stream=true、stream_options.include_usage=true及客户端可观测性/截止方式。
- 错峰3秒，只有2次请求；无自动重试/恢复。无论预检通过与否，**结束后停下，不自动运行48题**。
- 首字节、HTTP状态、首个SSE，以及每30秒推理字符数/答案字符数/SSE事件数/距上次内容增长时长写入fsync日志。字符数不是token数；精确token仅取服务端最终usage。
- 120秒无reasoning或answer字符增长则结束本地curl；独立总墙钟3600秒截止，curl自身亦设3600秒上限，连接超时15秒。仅HTTP头/空心跳不能重置内容进度计时。结束客户端连接不保证服务端取消，失败时未知usage不记0。
- 两次均需请求模型返回、非空reasoning、有效最终option、stop、完整usage、DONE及curl正常退出才通过；截断/丢失terminal/网络失败保留为技术失败，不计能力结果。
- 密钥只从0600且Git忽略的tokenhub.key读取，经stdin传curl，不出现在进程命令参数或日志；推理正文只做增量hash和字符计数，不保存正文。

## 验证与产物

5项本地测试通过：SSE拼接/usage、缺失DONE拒绝、错误模型拒绝、fake curl正常退出、无内容进展终止。没有测试模型调用。

脚本 `reviews/glm53-stream-canary-20260909.py`；产物 `artifacts/glm53-stream-canary-20260909/`。按plan→run --execute运行。读attempts.jsonl查看进度，response-0/1.json保存每题终态，canary.json保存总结果。不得重启有ledger的run；不要启动旧非流式runner。

终态：北京时间14:07:08两道流式预检完成，passed=true、均valid/stop/DONE、HTTP200/curl0。第一道926.21秒、input669/output31029，其中reasoning31015；第二道656.24秒、input669/output13556，其中reasoning13545。合计input1338/output44585，无截断，无能力评分。用户随后明确授权48题，见 [流式48题记录](glm53-stream-live-20260909.md)。不重跑本预检。
