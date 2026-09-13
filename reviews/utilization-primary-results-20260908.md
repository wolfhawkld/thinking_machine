# Strict-q6 Opportunity utilization 正式结果（2026-09-08）

## 完成状态与冻结结论

用户在获知 canary 与有限统计解释后明确授权“启动”。正式 `deepseek-pro` / `deepseek-v4-pro` 完成 48/48 次调用，48 条响应格式均有效；transport/missing failure 为 0，无 retry、resume、补题或 route 替换。generation 完整封存后才运行原正式 analyzer。独立描述性报告再次重放 analyzer 并确认与封存 analysis 完全一致。

冻结分类：`prospective_primary_not_detected_on_fixed_development_constructed_finite_DSL_challenge`。

| 预定结果 | 观察值 |
|---|---:|
| 完整双臂正确切换 | 0/24 worlds |
| own-context 正确动作 | 5/48 calls |
| cross-context 正确动作 | 6/48 calls |
| favorable / adverse / tie | 0 / 1 / 23 |
| signed total | -1 |
| 原单侧 exact sign-test p | 1.0 |
| received-invalid world | 0/24 |

23 个 tie 不等于 23 个 world 完全没有正确动作：tie 是 own 与 cross 命中抵消后的配对净分数。当前没有完整双臂切换，也没有 favorable world；5 次单臂命中不能代替预定的配对机会利用结果。

## 同题简单策略比较（描述性，不新增检验）

| 方法 | own-context 命中 | 完整切换 | favorable / adverse / tie |
|---|---:|---:|---:|
| DeepSeek Pro，冻结配置 | 5/48 | 0/24 | 0 / 1 / 23 |
| `public-k1-min-node-hash` | 22/48 | 2/24 | 16 / 2 / 6 |
| `public-k1-max-parent-novelty-node-hash` | 29/48 | 8/24 | 19 / 0 / 5 |

与最短策略的完整切换配对四格：仅模型 0、仅策略 2、双方 0、双方失败 22。与最大 parent 行为差异策略：仅模型 0、仅策略 8、双方 0、双方失败 16。全部 24 条既定策略及逐 pair 类别见 [utilization-model-comparison-20260908.json](utilization-model-comparison-20260908.json)。不根据这些差异新增显著性阈值或选择新“最佳基线”。

四 strata 各 6 worlds，完整切换均为 0；own-context 命中分别为 affine commutative 2/12、directional 0/12、multiplicative 1/12、pairwise variable 2/12。唯一 adverse world 属于 commutative stratum。

## 对假设与短文的解释

- 此轮没有观察到预期的条件化 context-concordant switching，也没有提供 LLM 优于简单策略的证据。不能把结果仅表述为“已有正向趋势，只是样本小不显著”；当前预定净方向为 -1，完整切换为 0。
- 响应格式全部有效，且确定性构造和简单策略确认题中存在可利用机会，因此不能用格式失败或不存在成功路径解释当前结果。但本实验未识别模型为何选择这些动作，不能据此断言模型采用了某种内部 shortcut。
- 结论限定于该 route、prompt、thinking disabled、temperature 0.2、256 输出 token 上限、候选集合及所选 outcome-conditioned development worlds；不推断所有 LLM/Agent 都没有该能力，不否定非答案性启发的一般可能性，也不证明或否定 RSI。
- 原 Opportunity creation 的确定性结果与当前 utilization 的未检出结果并列保留。再结合预算敏感性（parent 四轮 0/24、五轮 19/24），短文可以报告机会可被代码确认、所测模型未利用出预期切换、结果高度依赖预算的边界，而不能写成已验证“模型增熵—降熵—科学发现”。
- 按已确认的最小方案，本轮结果汇总后停止扩展实验，进入解释与写作讨论；不自动加样、换 route、改变 prompt 或用另一个结果替代本轮。

## 可复现 artifacts 与资源使用

正式文件目录：`artifacts/spark-strong-k4-utilization-primary-live-v1-20260908/`。authorization/generation/analysis 均为本机 mode `0600`；ledger 保留原调用过程，private scoring key 沿用原文件。

| 文件 | raw file SHA-256 |
|---|---|
| `authorization.json` | `a752013da4cb7b07f57bacc17795fe7348a5155b20038ab5c98a8b96461afe7f` |
| `generation.json` | `bf40ab09660fcf561cdac434017ff2f7c07c9d1582fdd571592ac55ad11b8eba` |
| `analysis.json` | `e74e486b43fd25cc9464608c0a5b02d68b937ca18a529edb8a161c5be600f7dd` |
| `reviews/utilization-model-comparison-20260908.json`（根目录相对路径） | `a5e16795abf98743658c140a5ecf213f9b5f932bc54234d44a1b74fd48248541` |

generation canonical SHA：`5c968842df2d61bc04df51a31d3eda1c64a71d0a94ff355ac6654da4b2251526`。analysis canonical SHA：`064e252a4fbdb3ef89bb241a605e8be8b6fa1689663d1f8dece2dc9aee6bf855`。

正式 48 calls 的 provider 记录合计 input tokens 32,026、output tokens 469，总计 32,495；cache-hit tokens 0。这是实验 API 用量，不是 Codex 对话 quota；不含此前 8 次 canary。`reasoning_tokens` 字段在 48 条响应中均缺省，不把缺省计作实测 0；请求配置为 thinking disabled。报告未估算费用。

历史核对、预算敏感性与 canary 的细节见 [最小修订执行记录](minimal-revision-execution-20260908.md)。本轮只使用原冻结分析及事前完成的补充报告代码，没有修改正式评分逻辑。
