# 2026-09-09 用户授权的错峰并发调度变更

用户要求不等待上一题返回、每隔几秒异步发起请求，并在明确获知中断/重发可能重复计费后确认执行。

## 切换边界

- 原串行session 42174已收到Ctrl-C并以130退出，traceback表明正在读取HTTP响应；本地已停止，不保证服务端取消。
- 原ledger保存indices0–5（第1–6题）的完整响应；index6（第7题）只有attempt_started，没有完整响应。旧ledger和plan保持原样，不把旧串行尝试改称正常完成。
- 新方案复用6条已保存响应，重新调度indices6–47共42题。唯一授权的重复尝试是index6；其旧响应不可用、usage未知且服务端可能继续。原7次物理尝试+新最多42次=最多49次，不能误报为48次收费请求。

## 新调度（调用前固定）

- 每3秒submit一道剩余题，48个worker上限足以容纳全部42个未返回请求。发起无需等待前一道返回；响应顺序可不同，逐题0600文件+加锁fsync ledger即时保存，最终按原task顺序合并。
- 请求参数不变：deepseek-v4-pro / thinking enabled / high / max_tokens131072 / socket timeout3600 / JSON object，原prompt，无temperature/seed/history/tools。
- 不读取private key、不据正确性挑选保留或重发；6条既有响应全部保留。旧中断请求的迟到响应不参与新bundle。
- 无后续自动重试/恢复。新HTTP/transport/envelope失败则停止继续submit，等待已提交请求返回并保留全部结果；不完整bundle不能做完整配对结论。received-invalid照原规则消耗slot、不重试；最终评分仍按旧paired scorer处理。
- 总usage标记不完整：记录已保存响应的已知token用量，但不能把中断请求未知token记为0。

## 证据解释

此为运行中、部分响应已可见后获用户授权的执行方案修订，采用混合串行/错峰调度且含一次中断重发。结果仅作为明确披露修订的描述性follow-up；不继承原无重试串行协议的身份或事前指定检验标签，也不改写2026-09-08 primary。后续可报告相同48题的paired endpoints和全部简单策略，但不能将时间/并发影响假设为必然为零。

## 验证与恢复

2项fake-transport测试通过：保留6条原响应、仅42道pending、最终原序48条、已知49物理尝试、拒绝重启、3秒间隔参数，以及HTTP503停止派发并等待已发请求。测试没有真实API调用。

脚本 `reviews/deepseek-thinking-128k-staggered-20260909.py`，新目录 `artifacts/deepseek-thinking-128k-staggered-20260909/`。先plan，再run --execute。恢复时先读新目录generation/failure/attempts；不要重启新或旧run。新bundle不能直接送入旧串行analyze，后续应按新provenance验证并复用相同评分函数。

终态：北京时间12:05:35至12:15:46，42条新响应全部完成、valid且无截断/新请求失败，连同原6条组成完整48条。已知input35770/output736187，旧中断请求usage未知。独立离线分析已完成：own34/48、cross1/48、完整切换11/24、22/0/2；全部简单策略比较、边界解释及hash见 [最终结果](utilization-thinking-results-20260909.md)。无运行任务，不重跑。
