# 2026-09-09 GLM长请求等待诊断

用户要求排查，不修改既有实验请求，不把诊断加入模型结果，不重发在途的两道canary。

## 已获得的证据

1. 先前low短请求：curl HTTP200，4.48秒返回OK，input17/output3，无reasoning。证明该端点和凭证能够用于glm-5.3请求，但不足以证明high长题正常。
2. 本轮相同简单平方和问题，保留实验的thinking enabled/high、JSON object、temperature1/top_p0.95，将max_tokens设2048，分别stream true/false：
   - 流式：HTTP200，首事件9.97秒，10.83秒正常stop；input87/output36，其中reasoning22，reasoning正文37字符、答案17字符。
   - 非流式：HTTP200，8.78秒正常stop；input87/output37，其中reasoning23，reasoning49字符、答案14字符。
   - 参数组合能够返回，不能将问题归因为high/JSON/非流式普遍不兼容。脚本及机器记录分别为 `reviews/glm53-connectivity-diagnostic-20260909.py` 和 `artifacts/glm53-followup-20260909/connectivity-diagnostic.json`。
3. 另一条未用于本轮的target-free退役技术题（旧canary pairs[1].context_a），独立流式诊断，high/JSON/2048/90秒硬截止：首事件5.11秒，47.90秒返回2047个SSE事件，finish=length，input664/output2048，其中reasoning2046；reasoning7896字符，最终答案为空。curl exit0。该题在真正产生推理，不是单纯无数据等待；达到诊断限额未出答案不作能力判断。
4. 原实验进程PID12885只读检查：主线程futex等待，两个请求线程poll等待；两个socket TCP ESTABLISHED且检查瞬间发送/接收队列均0。表明该瞬间等待网络，没有已排队未消费的数据；不是本地计分计算占用。它不能证明服务端仍在推理，也无法区分排队、长生成或上游停滞。

## 当前判断

最后检查原两道canary约等待27分钟，没有完整响应，没有错误事件，48题尚未开始。接入已被独立请求证实可用，high在符号题上也能够产生推理流；**原两道为何迟迟未结束尚未被直接观测**，不能谎称已定位为正常长推理或确定服务故障。

独立符号题2048输出约48秒，对应粗略43 tokens/秒；如果按类似速度用满131072预算，大约51分钟。此仅是解释量级的线性估算，不是原两道的进度或token计数。高上限是允许值，不是要求模型用满，但不能因已经等二十多分钟就断言不可能仍在生成。

当前runner设置stream=false，且transport在response.read()完成后才向调用层返回HTTP状态/usage。3600秒是socket timeout，不是独立总墙钟截止；若连接持续有keepalive/chunk数据，实际总等待可能超过一小时。因此现有监控没有提供足够的首字节/推理进度信息，不应把「无报错」等同于正常完成。

本轮新增3个诊断请求，已知input838/output2121，总2959 tokens；不含先前low短请求20 tokens，不含仍在途canary未知消耗。所有诊断均不计入实验，密钥未回显或写入记录。

建议（未实施）：若用户要进一步区分长推理和服务端停滞，可明确授权终止现有本地预检，另开有首字节、推理计数、硬总截止与无数据超时记录的流式预检；保留旧attempts，披露未取得完整响应及未知计费。仅修改磁盘源码无法热切换现有进程。当前未中断、重发或修改原协议。
