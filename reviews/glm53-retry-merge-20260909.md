# 2026-09-09 GLM第25题人工授权重试与合并

原流式48题在北京时间14:54:32结束：47条完整响应，index24（第25题）在输出部分推理后连续120秒无内容增长被终止，无完整final/usage。原文件为 `artifacts/glm53-stream-live-20260909/failure.json`，已知47响应input31470/output1000305，失败请求消耗未知。

用户明确要求「失败的那题重试下，结果合并到最终结果里」。据此建立独立修订，不改写原failure、47条原响应或原无自动重试协议。

- 只调用原index24、task TASK-TTKR33WLE4KMYN，原prompt、glm-5.3/high/128K/temperature1/top_p0.95/流式不变；继续使用120秒无内容增长截止、3600秒硬总截止。
- 新增最多1次请求，不自动继续重试。选择依据是没有取得最终响应，不读取private答案或按正确性重试。
- 如果取得完整响应（无论最终选项有效/正确与否），将该响应填入index24，其他47条JSON值逐项保持一致，按原48题顺序生成独立generation.json。invalid选项仍按原规则计分；若再次transport/envelope失败，不生成完整合并结果，保留原47条。
- 任务物理尝试数变为49（不含技术canary与诊断），最终计分最多48条；旧失败请求usage未知，已知响应usage不等于全部账单用量。
- 仅探索性跨模型结果，不将补齐的bundle冒称原无重试完整运行。底层执行记录保留一次人工授权重试；不改变三个条件不等于独立样本扩充的解释。

3项本地测试通过：只替换缺失slot、不修改原输入、失败/错误task拒绝合并、恰好1次调用、拒绝重启、凭证不落盘、49次计数及usage不完整标记。

脚本 `reviews/glm53-retry-merge-20260909.py`，目录 `artifacts/glm53-retry-merge-20260909/`。plan→run --execute；成功后自动合并到新generation，原目录不动。恢复时查看retry-response.json/generation.json/attempts.jsonl，不重启任何旧runner。合并后另行离线分析，不把技术完成当作能力结果。

终态：第25题重试成功，HTTP200/curl0/stop/DONE、valid，无截断，677.27秒、input668/output39102（reasoning39089）。北京时间15:24:30已自动按原顺序合并成48条；其他47条原样保留。独立评分及全部比较已完成，GLM38/48、15/24，详见 [最终结果](utilization-glm53-results-20260909.md)。无运行任务，不重跑。
