# Spark-to-Knowledge 最小因果实验方案

**状态**：二值 world 与 motif-reachable 零 API 校准已完成；reachable gate 因 headroom 差 1.03 个百分点未通过；24-call exploratory closure 已执行并观察到 1 个可重放 strict event，但正式调用前的 fake-provider 端到端测试曾访问同一批 target outcomes，因此结果只作 outcome-exposed development mechanism demonstration
**记录日期**：2026-08-12；首次离线校准更新于 2026-08-13
**目的**：保存温度调度实验之后形成的研究转向，并定义一个最小、可追踪、可删除重放、可精确计算版本空间压缩的实验闭环。

本文档不修改 V2 的冻结主要结果，也不把未执行的 V3 主网格视为任何性能证据。正式执行前仍需冻结世界生成器、假设库、motif 库、模型路由、预算、统计规则和新 seed registry。

---

## 1. 研究命题的修正

原温度实验隐含了过强的代理链：

```text
更高 sampling temperature
        ↓
更高的有效假设空间熵
        ↓
更高的 productive spark 概率
        ↓
验证反馈将其压缩成更好的规律
```

现有结果不支持把这些概念视为等价。新的研究对象不是 temperature scheduling，而是下面这条可观察、可干预的因果链：

```text
答案无关的结构扰动 R
        ↓
可机器追踪的新候选 s
        ↓
由真实隐藏规律决定的新证据 e
        ↓
确定性的版本空间更新
        ↓
可认证事实增加与隐藏规律唯一识别
```

若隐藏规律为 \(F\)、初始观测为 \(D_0\)、扰动为 \(R\)，设计必须保证：

\[
R\perp F\mid D_0,
\qquad I(F;R\mid D_0)=0.
\]

因此，扰动本身不携带关于目标的新信息，也不可能被算法直接“提炼成答案”。它能够发挥的作用是改变系统提出的假设或实验，从而取得一组不同的、依赖真实目标的证据：

\[
R\rightarrow h_t\rightarrow e_t\rightarrow V_{t+1}\rightarrow \hat F.
\]

本研究若成立，支持的是“偶然启发改变证据获取路径”，而不是“从纯噪声中凭空产生信息”。

## 2. 最小封闭世界

### 2.1 输入域与 DSL

复用当前项目的有限 DSL 与输入域：

- 输入域 \(X=\{-2,-1,0,1,2\}^3\)，共 125 点；
- 候选是受深度、节点数和输出范围约束的 S-expression AST；
- 所有程序都可以确定性执行、canonicalize 并计算完整行为向量；
- 语义相同但语法不同的程序只计为同一个假设。

### 2.2 冻结有限假设库

每个世界建立一个私有假设库 \(B_w\)：

- 当前零 API calibration 后的目标规模为 256 个二值分类假设；
- 256 个假设在完整 125 点行为向量上互不相同；
- 目标规律 \(f^*\) 在模型训练结束后从库中均匀抽取；
- 初始公开观测 \(D_0\) 被构造成无法区分这 256 个假设，因此：

\[
V_0=B_w,\qquad |V_0|=256.
\]

这使初始版本空间 log-volume 恰为：

\[
H_0=\log_2 256=8.
\]

当前构造不在 AST 中硬编码公开点。生成器先从 1767 个完整域语义唯一的小型二值分类器中冻结 12 个 train 点，再按相同的 12-bit train response signature 条件化，并以 target-blind seeded 规则选出 256 个成员。正式执行前还需通过 motif-reachable headroom gate，之后才冻结该 family。

### 2.3 数据边界

125 个点划分为：

- 12 个初始公开观测点；
- 49 个可由验证器逐步释放的 evidence pool 点；
- 64 个始终私有、只在所有轨迹封存后评估的 test 点。

假设库必须满足：evidence pool 足以区分所有 256 个语义假设。模型永远看不到假设库、隐藏目标或私有测试标签。

## 3. Spark context 的构造

### 3.1 Motif 库

预先建立一个合法 DSL motif 库。示例：

```text
(mul (var x2) (const -1))
(add (var x1) (const 2))
(sub (var x3) (var x1))
```

Motif 按深度、节点数、操作符类别分层。每个世界的 motif 使用与目标生成不同的随机流，在目标、模型输出和最终结果不可见时抽取并登记。

不能根据某个 motif 是否成功而重新抽取；无效或无用 motif 仍进入分母。

### 3.2 固定模型设置

- 使用未经微调的通用模型；
- 第一阶段只使用一个冻结 route；
- sampling temperature 固定为低值，例如 `0.2`；
- temperature 不再是实验变量；
- 输出 token、调用次数和提示结构固定；
- 第二模型只在第一模型的开发规则冻结后用于独立复现。

### 3.3 结构化编辑，而非自由改写

每个世界先由确定性规则从 \(V_0\) 选择一个 parent，例如最短 AST、canonical hash 打破平局。模型看到 \(D_0\)、parent 和一个 motif，只能输出一个受限编辑动作。

建议记录：

```json
{
  "parent_hash": "...",
  "spark_id": "...",
  "spark_hash": "...",
  "op": "replace|wrap_binary",
  "path": [1, 1],
  "expected_old_subtree_hash": "...",
  "binary_operator": "add|sub|mul",
  "side": "left|right",
  "child_hash": "..."
}
```

程序根据 action 实际构造 child，而不是相信模型的自我描述。一个 lineage-valid child 必须：

1. 由 parent、motif 和 action 唯一重放；
2. 使用指定 motif 且只使用一次；
3. 通过 DSL 语法、深度、节点数和运行约束；
4. 与全部初始观测 \(D_0\) 一致；
5. 完整行为 hash 不同于 parent。

不能唯一重放或不满足约束的输出计作 spark-generation failure，不做内容重试或结果导向的修复。

## 4. 确定性证据与压缩闭环

### 4.1 Oracle 响应

第一个查询候选是 parent、spark child 或相应控制候选。确定性 oracle 在预先冻结顺序的 evidence pool 上返回：

- `MATCH_ON_EVIDENCE_POOL`；或
- 第一个不一致点及其真实标签。

记完整响应函数为：

\[
O(g,h).
\]

“第一个反例出现在哪里”隐含此前各点均一致，因此版本空间不能只按返回的 \((x,y)\) 过滤。精确更新必须是：

\[
V_{t+1}
=
\{g\in V_t:O(g,h_t)=O(f^*,h_t)\}.
\]

这保证计算出的压缩量与 oracle 实际透露的信息完全一致。

### 4.2 后续候选

第一条证据产生后，下游不再调用 LLM：

1. 精确过滤 \(V_t\)；
2. 从新的 \(V_t\) 中选择最短 AST，canonical hash 打破平局；
3. 把它作为下一条 equivalence query；
4. 固定执行 4 个 oracle rounds；
5. 所有分支完成后，才开放 private test。

正式 round 数应先用不调用模型的离线仿真检查 floor/ceiling。若 4 轮不合适，只允许在任何 live 模型结果之前调整世界构造或 round 数。

## 5. 因果条件与反事实

### 5.1 基本 2×2

| 条件 | Structured spark | 真实 oracle 证据与压缩 |
|---|---:|---:|
| \(Y_{00}\) | 无 | 无 |
| \(Y_{10}\) | 有 | 无 |
| \(Y_{01}\) | 无 | 有 |
| \(Y_{11}\) | 有 | 有 |

主要协同量为：

\[
\delta
=(Y_{11}-Y_{10})-(Y_{01}-Y_{00}).
\]

同时分别报告：

\[
Y_{11}-Y_{01}
\]

即在有真实验证器时，spark 是否增加发现；以及：

\[
Y_{10}-Y_{00}
\]

即没有新证据时，spark 是否只是增加偶然猜中的机会。

### 5.2 附加控制

每个世界还应包含：

- **neutral-edit control**：相同模型调用与编辑机会，但不提供指定 spark motif；
- **context deletion**：把 spark action 变为 no-op，以 parent 为起点重放相同压缩器；
- **candidate-lineage deletion**：禁止 spark child 及其后代证据进入轨迹；
- **matched structural replacement**：保持路径、操作和预算不变，只把 motif 换成同复杂度的独立 motif；
- **prompt replacement**：给模型一个独立同分布 motif，估计一般的 context treatment effect；
- **evidence-yoked replay**：把 factual spark 分支取得的证据 transcript 交给无 spark 的确定性压缩器，区分 spark 的证据获取作用与后续证据利用作用。

远程 API 无法保证在两个 prompt 下复用完全相同的模型随机带，因此不能把两次独立模型生成称为严格的个体反事实。最强的删除结论来自：先冻结真实 child，再对确定性下游做删除和替换重放。跨 prompt 的差异只能通过多世界随机分配估计平均效应。

## 6. 精确压缩指标

所有计数都在按 125 点完整行为去重后的语义假设空间中进行，而不是按 AST 字符串数进行。

令：

\[
N_t=|V_t|.
\]

逐步 version-space contraction：

\[
g_t
=
\log_2\frac{N_{t-1}}{N_t}.
\]

累计 contraction：

\[
G_T
=
\log_2\frac{N_0}{N_T}.
\]

同时报告实际排除的语义假设数：

\[
e_t=N_{t-1}-N_t.
\]

例：

```text
256 → 32 → 4 → 1
逐步 contraction：3 bits、3 bits、2 bits
累计 contraction：8 bits
```

只有在冻结假设库上采用均匀先验时，

\[
\log_2|V_t|
\]

才可以解释为该有限库上的 Hartley entropy。不能把它称为整个 DSL、模型参数或一般科学知识的熵。

若 \(N_t=0\)，这不是“无限压缩”，而是目标遗漏、错误过滤、证据矛盾或实现故障。

## 7. 从不确定性压缩到可认证知识

版本空间变小还不等于形成知识。定义在输入 \(x\) 上版本空间是否已达成一致：

\[
C_t
=
\left\{
x\in X:
\left|\{h(x):h\in V_t\}\right|=1
\right\}.
\]

如果真规律始终保留，则 \(C_t\) 中的共识预测必然正确。相对初始状态新增的可认证事实数为：

\[
K_t=|C_t\setminus C_0|.
\]

必须同时报告：

- 每一步 \(N_t\)；
- 每一步 \(g_t\) 和累计 \(G_T\)；
- 新增可认证事实 \(K_t\)；
- 真规律是否始终属于 \(V_t\)；
- 最终候选在未暴露点和 64 点 private test 上的准确率；
- 是否在完整 125 点上与真规律等价；
- 是否达到 \(N_T=1\)。

解释边界：

- \(N_t\) 下降：有限库内的不确定性缩小；
- \(C_t\) 扩大且真规律保留：新增可认证事实；
- 最终候选全域正确但 \(N_T>1\)：正确恢复，但证据未唯一识别；
- \(N_T=1\)、真规律保留且全域正确：冻结有限假设库内的 operationally verified knowledge identification。

AST 变短属于表示压缩；版本空间变小属于认识不确定性压缩。二者不能混为同一个指标。

## 8. 严格的 Spark-to-Knowledge event

一个单次轨迹只有同时满足以下条件，才计作严格事件：

1. spark 在目标和结果不可见时抽取，且与目标独立；
2. action 和 child 可由 parent 与 motif 唯一机器重放；
3. child 合法、与 \(D_0\) 一致并在行为上相对 parent 新颖；
4. child 在取得新证据前并未直接全域命中目标；
5. child 或其证据后继触发至少一次 \(g_t>0\) 的真实 oracle 响应；
6. 真规律在全部版本空间更新中被保留；
7. \(Y_{11}\) 在固定预算内达到 \(N_T=1\) 且完整域正确；
8. \(Y_{10}\)、\(Y_{01}\) 和 \(Y_{00}\) 没有达到同一终点；
9. context/candidate deletion 后没有达到同一终点；
10. 至少一个 matched replacement 没有达到同一终点。

如果 child 在任何验证之前直接等价于目标，记录为 `direct_hit`，不能算作“由验证转化的 spark”。

一个完整事件可以支持该机制在本系统中的 existence claim；只有在多世界随机对照中稳定提高事件率，才能支持一般效用。

## 9. 最小执行规模

### 9.1 离线 calibration

不调用模型，完成：

- 256-member semantic bank 的可构造性；
- \(V_0\) 大小；
- 49 点 evidence pool 的可分辨性；
- 4-round compressor 的 floor/ceiling；
- motif 编辑的语法与资源约束；
- 全部版本空间与 oracle-response 更新的单元测试。

### 9.2 闭环调试

- 6 个 development worlds；
- 每世界 1 个 neutral edit + 3 个预登记 spark motifs；
- 24 次 LLM 调用；
- 全部验证、压缩、删除和替换分支离线重放；
- 目的仅是确认是否能产生可审计的 lineage 与严格 event。

### 9.3 阶段性开发实验

- 12 个新的、预先登记且难度平衡的 worlds；
- 每世界同样 4 次模型调用；
- 共 48 次 LLM 调用；
- 以 world 为统计单位，不能把候选调用视为 IID；
- 全部预登记 motifs 进入分母，不只展示成功案例。

建议的阶段判断：若至少两个独立 world 出现严格 event，且 aggregate 的 `spark + verify` 相对 `no-spark + verify` 方向为正，则进入更大规模与第二模型复现。这个门槛只表示值得继续，不是显著性或一般性证明。

若 12 worlds 中没有严格 event，则当前 structured-motif 操作化没有 feasibility signal；应先讨论机制或任务设计，而不是继续扩大调用规模。

## 10. 代码实现边界

可直接复用：

- `src/dsl.py`：AST、解析、canonicalization、执行、完整行为向量与 hashes；
- `src/world_generator.py`：有限域与合法规律生成的基础组件；
- `src/verifier.py`：候选合法性、确定性执行与反例；
- 现有 OpenAI-compatible provider adapter。

不建议改造当前与温度、archive、E/E2 controller 紧密绑定的 `run_episode`。下一轮只需增加小型 research-only 组件：

- semantic hypothesis-bank/world builder；
- structured motif edit 与 lineage validator；
- exact oracle-response/version-space compressor；
- factorial/counterfactual runner 与分析脚本。

代码以正确完成实验、保存 lineage 和精确计算指标为目标，不引入与论文实验无关的生产级安全或分布式工程复杂度。

## 11. 可支持和不可支持的结论

### 若出现严格事件，可以支持

> 与答案独立的结构扰动改变了候选与证据获取路径；由该路径取得的真实反例随后被确定性版本空间更新转化为新的可认证事实，并在冻结有限假设库中唯一识别了此前未知的隐藏规律。

### 仍然不能支持

- 随机 context 本身携带或创造了目标信息；
- 裸 LLM 独立完成了全部发现；
- 系统重新发明了人类历史成果；
- 该过程等同于人脑或神经系统的发现机制；
- 一个案例证明该方法普遍提高科学发现率；
- 有限假设库中的 8 bits 等于现实科学知识的绝对信息量。

最准确的研究定位是：固定通用 LLM 与确定性数学验证器组成的系统中，一个可干预、可删除重放的“偶然启发—证据获取—知识识别”机制研究。

## 12. 正式冻结前仍需决定

1. 第一模型 route 与固定 sampling temperature；
2. 256-member binary world family 与 motif-reachable 集合的最终冻结；
3. 12/49/64 点划分和 evidence-pool 顺序；
4. motif 库规模、分层与允许的 edit operators；
5. neutral-edit prompt 的精确定义；
6. oracle round 数是否固定为 4；
7. full-domain direct hit、invalid edit 和 evidence-pool match 的记分规则；
8. 12-world 阶段判断及更大规模统计计划；
9. fresh seed registry；
10. 第二模型 replication 的启动条件。

这些选择必须在 live 模型输出或 private-test 结果出现之前完成。

## 13. 2026-08-13 首次零 API 校准记录

这一节是开发记录，不是预注册结果，也不是对 spark 假设的正负检验。

第一版测量装置已经实现：

- 64 个完整域行为唯一的 DSL 假设；
- 严格的 12/49/64 train/evidence/private-test 分割；
- 完整首反例响应等价类过滤；
- 真规律保留、精确版本空间计数、telescoping contraction 与 certified facts；
- retired seeds `1000`--`1008`，每个世界穷举全部 64 个目标；
- shortest-parent baseline 与不读取 realized target 的 response-entropy benchmark；
- 全过程零模型调用、零 private-test 驱动的查询选择。

第一版 linear bank 的校准结果为：

| Oracle queries K | Baseline singleton | Response-entropy singleton |
|---:|---:|---:|
| 0 | 0.00% | 0.00% |
| 1 | 7.99% | 11.46% |
| 2 | 77.43% | 83.33% |
| 3 | 100.00% | 100.00% |
| 4--6 | 100.00% | 100.00% |

两种策略的无证据 direct-hit rate 都是 `1/64`。在 K=2 时，理论 benchmark 相对 baseline 也只增加 5.90 个百分点；K=3 后完全没有余量。

因此第一版 world family 被离线拒绝：多值 linear family 上的“首个不一致位置 + 精确目标值”单次携带的信息过强，固定压缩器三轮即可对所有目标唯一识别。正式使用四轮会形成 ceiling，无法辨认 spark 是否改变了证据获取路径；直接把预算缩成两轮同样不合格，因为 baseline 已超过 70%，且首查询余量不足 20 个百分点。

在调用任何 live 模型之前，下一步只做 world/oracle 的零 API redesign。优先目标是保留完整、可解释的真实反馈与四轮预算，同时构造更高维、低单次泄露的 semantic bank。候选方案必须在 retired worlds 上同时满足：

- K=4 baseline singleton rate 在 10%--70%；
- target-independent reachable benchmark singleton rate至少 80%；
- benchmark 相对 baseline 至少增加 20 个百分点；
- direct-hit rate 不超过 5%；
- motif-edit reachable 集合中确实存在这种查询余量，而不只是整个 bank 中理论上存在。

任何 redesign 只有在这些 target-independent calibration gates 通过后，才可进入 motif/action lineage 实现和 24-call closure。

## 14. 2026-08-13 二值 256-bank redesign 校准

第二版 world family 保留四轮完整 first-counterexample oracle，但把多值低维 linear family 改为 target-blind 条件化的二值分类器族：

- 全局 reservoir 含 1767 个完整域语义唯一的 classifier；
- 每个 classifier 的 depth 不超过 4、nodes 不超过 10，完整域输出严格属于 `{0,1}`；
- 每个 `world_seed` 先冻结一个 face 上的 12 个 train 点；
- 按 12-bit train response signature 分组，再以 target-blind seeded 规则取得 256-member bank；
- 49 点 evidence 与 64 点 private test 各自都能区分全部 256 个成员；
- target 仍只由独立 `target_seed` 选择；
- fresh world 的生成只使用结构门，不读取 baseline、headroom、motif 成绩或 private-test outcome。

对严格二值候选，首个 mismatch 的 true label 必为候选预测的反值，因此“位置 + label”与 index-only response 在信息量上等价。不过保留带标签反例更接近标准 binary equivalence-query 的实验语义。

在 retired seeds `1000`--`1008` 上，每个世界穷举全部 256 个目标，共 2304 条 target trajectory。四轮结果为：

| 指标 | Shortest-parent baseline | Bank-wide response-entropy benchmark | Headroom |
|---|---:|---:|---:|
| Singleton rate | 64.323% | 97.352% | +33.030 pp |
| Mean terminal \(N_T\) | 4.488 | 1.036 | -3.451 |
| Mean contraction | 7.0985 bits | 7.9680 bits | +0.8695 bits |
| Mean certified facts | 98.784 | 112.490 | +13.707 |
| Direct-hit rate | 0.3906% | 0.3906% | 0 |

K=3 时 singleton rate 仍为 baseline 34.94%、benchmark 80.86%，说明第二版没有在第三轮提前完全饱和。K=4 的 baseline、benchmark、singleton headroom 与 direct-hit 均通过当前零 API 难度门槛。

本次本地结果 artifact 为 `artifacts/spark-calibration-v2-binary256-20260813.json`，SHA-256 为 `211e4b60096a4884fbf3c3bc8b2c8daf8e03e7fc26190d4f5112ff5e2b3cfba5`。artifact 位于项目的 ignored experiment-output 目录；上述数值也保存在本节，避免把未追踪 artifact 当作唯一记录。

这个结果仍只是 bank-wide 理论 headroom，不等于真实 spark manipulation 已经可用。下一步必须实现并冻结 motif/action lineage，然后仅在真实可达、D0-consistent、完整域 binary 的 eligible children 上重算同一门槛。不能因为某个 child 的 entropy 或最终成功率更好而把它纳入；纳入规则只能基于可重放性、合法性、行为新颖性和 response-profile 确实改变等 target-independent manipulation 条件。只有 motif-reachable gate 通过，才进入 6-world / 24-call closure。

在查看 reachable calibration 结果前，暂定该 manipulation gate 为：每个 retired world 至少 16 个完整域语义不同的 eligible children、覆盖 4 个预定义 motif strata且每个 stratum 至少 4 个，并至少形成 16 个不同的 operational response partitions；每个进入这 16 个名额或 benchmark pool 的具体 action frame 必须存在至少 2 个保持同 parent、path、operator、side、stratum 与复杂度的合法 matched replacements。单纯比较 response 值可能把只改了 response 名称、却没有改变版本空间划分的情况误算成 manipulation，因此 primary change rate 定义在每个潜在目标所落入的 induced version-space cell 上：

\[
P_h(i)
=
\left\{j:O(g_j,h)=O(g_i,h)\right\},
\qquad
\rho_V(c,p)
=
\frac{1}{256}
\sum_{i=1}^{256}
\mathbf 1\!\left[P_c(i)\ne P_p(i)\right].
\]

进入 benchmark 候选集的 child 必须满足 \(\rho_V\ge20\%\)。原始 response-value change rate \(\rho_O=\Pr[O(g,c)\ne O(g,p)]\) 仍可作为描述性指标，但不负责通过 manipulation gate。在通过结构门的 reachable children 中，只能按首轮 response-partition entropy 与冻结结构 tie-break 选择 target-independent benchmark，不能按四轮 singleton outcome、realized target 或 private-test 表现选 child。最终仍使用 K=4 baseline 10%--70%、reachable benchmark 至少 80%、singleton headroom 至少 20 个百分点、direct hit 不超过 5% 的 aggregate gate。

### 14.1 Reachable 装置的开发修订记录

本节的 reachable gate 是退役世界上的**开发校准**，不是未观察数据上的预注册检验。实现结构枚举时，在完整九世界四轮结果尚未运行前，初始 motif 分层暴露出确定性的 control-support 问题：裸变量或 signed-variable 层只有 3 个 motif，在某些 parent/action frame 下不可能同时提供两个同复杂度 replacement；随后一次按 pairwise operator 细分的版本在 seed `1002` 上也只有每层 3 个结构可达 child。保留这种分层会把 motif 库的组合学缺口误当成 spark 机制失败。

因此当前开发版冻结为四个都有充分同类 replacement 的算术结构层：

- `affine_commutative`：变量与常数的加法；
- `affine_directional`：变量与常数的有向减法；
- `affine_multiplicative`：变量与常数的乘法；
- `pairwise_variable`：两个变量节点的加、减或乘；允许同一变量重复，因而这是语法结构层而不是“不同变量交互”层。

另一个实现修订发生在 seed `1000` 的单世界 sanity check 后：lineage 层最初先按 hash 截断为两个结构 replacement，可能因为这两个 replacement 未通过 \(\rho_V\) 而产生假阴性，即使同一 action frame 还有其他合法 replacement。当前实现先保留全部按固定 hash 顺序排列的结构 replacement，再应用预先写明的 \(\rho_V\) eligibility，最后取最前两个；仍不读取四轮 singleton、realized target 或 private test 来挑 control。该 sanity check 已看到 seed `1000` 的局部四轮数值，因此后续九世界结果只能称开发校准结果，不能包装成事前未观察的 confirmatory evidence。所有阈值、九个退役 seeds、entropy-only benchmark 选择和“不通过世界不得删除”的规则保持不变；此处记录完成后才运行完整九世界 calibration。

## 15. 2026-08-13 Motif-reachable 九世界校准

在 retired seeds `1000`--`1008` 上完成了真实 lineage 空间的零 API 穷举。每个世界先枚举符合 DSL、二值、\(D_0\) 一致、完整域新颖、motif 唯一插入且可精确重放的 action frame；随后要求 focal child 和两个固定顺序的 same-frame replacements 都满足 operational profile 条件。性能 benchmark 只按首轮 response-partition entropy 选择一个 child，之后才对全部 256 个潜在目标执行该 child 的第一查询与 3 轮 shortest-member 查询。

结构 manipulation gate 在 9/9 世界全部通过：

- 每世界有 23--86 个跨层语义唯一的 eligible child，均超过 16；
- 每个世界四个 motif strata 均至少有 4 个 eligible child；
- 每世界形成 21--60 个不同的 operational partitions，均超过 16；
- 每个纳入分析的 action frame 均有两个通过 \(\rho_V\) 且 partition 与 focal 不同的 matched replacements。

四轮 aggregate 结果为 9 worlds × 256 targets = 2304 条轨迹：

| 指标 | Shortest-parent baseline | Motif-reachable entropy benchmark | 差值 |
|---|---:|---:|---:|
| Singleton | 1482/2304 = 64.323% | 1919/2304 = 83.290% | +18.967 pp |
| 非 direct-hit singleton | 1473/2304 = 63.932% | 1918/2304 = 83.247% | +19.314 pp |
| Mean terminal \(N_T\) | 4.488 | 1.513 | -2.975 |
| Mean contraction | 7.0985 bits | 7.7017 bits | +0.6032 bits |
| Mean certified facts | 98.784 | 108.647 | +9.863 |
| Direct hit | 9/2304 = 0.3906% | 1/2304 = 0.0434% | -0.3472 pp |

逐世界 benchmark 的 singleton 差值为 `+47.66, +16.80, -6.64, +17.97, +19.53, +8.98, +3.13, +32.42, +30.86` 个百分点，说明可达 headroom 有明显世界异质性。全体 810 个 eligible、语义去重后的 child 中，454 个优于 parent、5 个相同、351 个更差；因此“结构 motif”本身不是普遍有利，首轮 partition 的质量才是关键。按预定 structural order 配对的 1620 个 focal-minus-replacement 对比也同时包含正、零、负差值，不能把某个 motif 类别直接解释成内在 spark。

正式 gate 判断为：

- baseline 10%--70%：通过；
- reachable singleton ≥80%：通过；
- direct hit ≤5%：通过；
- reachable 相对 baseline ≥20pp：**未通过**，实际为 +18.967pp，差 1.033pp。

因此当前开发规则下的结论是 `do_not_start_model_calls_revise_operationalization`。这不是 spark 假设的 negative 证据，也不是“真实可达查询无效”：结构可达性和绝对 80% 门槛均通过，而且压缩、认证事实与 terminal \(N_T\) 都有实质改善；但测量装置没有达到事先设定的保守余量，所以不能无声地把 20pp 改成 18pp 后宣称通过。下一步需要在讨论后明确二选一：保留 20pp 并对 world/lineage 操作化做一次新的离线 redesign；或者把 24-call closure 明确降级为低成本、结果不作为证据的 exploratory engineering check，并以带时间戳的 post-calibration amendment 解释为何接受近阈值结果。无论选择哪条，本轮 artifact 与失败判断保持不变。

最终结果 artifact 为 `artifacts/spark-reachable-calibration-v1-r2-20260813.json`，SHA-256 为 `4d618eccf3e31204d9239c841f7c553d712350e6fd7d0464588c5a1ca2cc6ff6`。它用整数分数 `437/2304` 判断 20pp 门槛，严格限制只有完整、同序的九个 retired seeds 能发出正式 decision，并明确区分“未读取 target 的 private labels/outcomes”与“用完整域 candidate semantics 做结构校验”。较早的 full-detail artifact `spark-reachable-calibration-v1-20260813.json`（SHA-256 `636e08049174f030b0f76e9bad66780decd5c398704a39ed2370cff88aadff84`）保留全部 child-level rows，核心数值一致；r2 只保留其充分分布摘要以降低九世界运行的内存占用。二者均位于 ignored experiment-output 目录；核心数值和决策也保存在本节。

## 16. 2026-08-13 Post-calibration exploratory amendment

在看到第 15 节完整 reachable 结果后，研究者决定**不修改**原 `20pp` gate，也不把 `18.967pp` 追溯改判为通过。原结论继续保持：`strict_gate_pass=false`。

同时，由于这只是一个没有统计或理论特殊性的开发裕量阈值，而当前装置已经满足 9/9 结构门、绝对 singleton 超过 80%、direct hit 极低，并且 1919 个 singleton 中有 1918 个来自非 direct-hit 证据路径，允许执行原计划的 6-world / 24-call closure，但其身份降级为：

```text
post-calibration exploratory mechanism/engineering check
```

这 24 次调用只回答：

1. 未经微调的通用模型能否按指定 motif 产出机器可重放的合法 edit lineage；
2. 是否至少观察到一个严格、非 direct-hit、由 oracle 证据介导的 spark-to-knowledge event；
3. context deletion 和 same-frame matched replacements 是否能破坏该事件，从而提供可追踪的机制实例。

它不用于估计一般 treatment effect，不恢复第 15 节的 gate，不产生 confirmatory positive/negative，也不允许据此调节 20pp 门槛。若出现 event，最多支持当前有限系统中的 existence/feasibility signal；若没有 event，只说明该模型、prompt、motif 样本和 24-call 规模下未观察到机制实例。

### 16.1 冻结执行范围

- 6 个尚未用于 spark calibration 的 development world seeds：`3000`--`3005`；它们在本节登记后永久视为 development-only；
- 第一条 exploratory route 冻结为 DeepSeek 官方 OpenAI-compatible endpoint 的 `deepseek-v4-flash` request/response identity；复用 2026-08-12 的 8-call passed route canary `artifacts/v3-canaries-20260812-r2/deepseek-official.json`（SHA-256 `940f07c4e78e2ebca8581e73f35c57eb91fbb4352312fcc5fe4afde2b61a9228`，route binding `02bd12bf58025f50146c47ac7ecb891fd68e37efe0d4dda8692b5947961e5964`）；若 live contract 已漂移则在任何实验结果分析前中止，不换模型补齐；
- 每个 world 的 `target_seed` 由独立 SHA-256 namespace `spark-closure-v1:target:<world_seed>` 确定，在 motif 与模型输出之外冻结；
- 每世界 4 个一次性模型调用：1 个 neutral/no-motif prompt，随后 3 个 target-independent、预登记 motif prompts；
- 三个 motif 的 strata 按 world index 轮转覆盖四个冻结 strata；stratum 内对 frozen syntax-unique motif records（不是语义去重后的行为）按 `SHA256("spark-closure-v1:<world_seed>:<slot>:<stratum>") mod |library|` 选取，不能因 motif 对该 parent 无合法 action、模型 invalid/no-op 或结果不好而重抽；
- 模型 temperature `0.2`、max output tokens `256`、thinking disabled、每 slot 一次物理请求；任意 transport failure 中止而不补抽，格式/语法/action/lineage failure 作为该 slot 的 observed invalid outcome；
- 模型只能输出受限 action。wire 仍使用已验证的外层 `{"expression":"..."}`，其中字符串只能是 `(edit replace 1 1)` 或 `(edit wrap_binary 1 2 add left)` 这类闭集 action；程序注入冻结 motif 与对应 old-subtree hash 后重放 child，不能接受自由表达式替代 lineage；
- action paths 只允许 predicate operands `[1,1]`、`[1,2]`；operation 为 `replace` 或 `wrap_binary`。`replace` 把 old subtree 替换为 motif；`wrap_binary` 中 `side=right` 表示 `(OP old motif)`，`side=left` 表示 `(sub motif old)`。为与 reachable calibration 的 canonical action frames 一致，`add|mul` 只允许 `right`，`sub` 允许 `left|right`；`expected_old_subtree_hash` 由 prompt 展示，模型以 path 选择它，程序从 plan 注入并核对，而不要求模型复述长 hash；
- neutral call 使用同一 parent、D0 和输出预算，但 `spark=NULL/NO_MOTIF`，只允许返回显式 `no_op`；它测量格式/指令服从并为无 spark 分支保留同等的一次模型调用机会，不允许模型自由发明一个未登记 subtree，否则“neutral”会暗含不可追踪 spark。`Y01` 由 parent + 真实 oracle 的确定性离线轨迹定义；neutral prompt 差异仍不是严格个体反事实；
- 所有 24 个生成结果先保存并封存，之后统一离线执行 target-dependent oracle/compression 与 private-test scoring；模型生成阶段不使用 evidence/private labels/outcomes。

执行前冻结记录（尚未观察任何 closure 模型响应）：生成计划位于 `artifacts/spark-closure-20260813/plan.json`，其 canonical `plan_sha256` 为 `6e5ca96db22c71921d45c455d75c755c428369a204f5af73d117c1711dc71480`，文件 SHA-256 为 `585dbecb161bd0008eb1e0b5d029404ff22dce674b902725c84ee64b99370bd5`；计划绑定的完整协议源码 manifest SHA-256 为 `3ccf0fffe2ae3a6cfad14c1caee0517cdec1fffe57e92c8bee4faa07f421f0c3`。

### 16.2 离线重放与 exploratory 判断

每个合法 factual child 执行固定 `child first query + 3 shortest-member queries`。同时确定性重放：

- `context/action deletion`：用 parent 作为 first query；
- `candidate-lineage deletion`：在四轮下游中不允许 factual child 进入，等价于同一 frozen parent baseline；
- 两个按结构 hash 顺序预定的 same-frame matched motif replacements；它们必须独立通过 lineage eligibility，不能按结果挑 control；
- no-evidence score：在 oracle 前只判断 child 是否已完整域 direct hit；不把 private-test 高准确率等同于知识识别。

一个 factual motif slot 只有在以下条件全部成立时计 `strict_event=true`：lineage valid；child 非 direct hit；四轮真实 oracle 轨迹 truth-retained、最终 `N_T=1` 且完整域恢复；parent deletion 没达到同一终点；两个 matched replacements 中至少一个没达到同一终点；至少一次非 MATCH oracle response 产生正 contraction。neutral slot只作为 control outcome，不计 factual strict-event 分母。

阶段结果分类冻结为：

- `exploratory_mechanism_instance_observed`：至少一个 factual strict event；
- `lineage_feasible_but_no_strict_event`：至少一个 factual lineage valid，但无 strict event；
- `model_lineage_interface_not_feasible`：18 个 factual motif slots 全部 lineage invalid。

所有分类均带 `evidence_scope=post_calibration_exploratory_only`，不得升级为核心假设的正式 positive 或 negative。

### 16.3 2026-08-13 实际执行结果与证据等级修正

正式 route 完成了恰好 24 个物理请求：24/24 为 `deepseek-v4-flash`、`finish_reason=stop`、`candidate_format=json_expression`、每 slot `provider_request_count=1`，无重抽；6/6 neutral 均返回合法 `no_op`，18/18 factual outputs 均通过 action grammar。generation 完成后才运行 target-dependent 离线重放。

结果为：

- 18 个 factual actions 中 7 个匹配冻结 reachable lineage，11 个为 `not_in_frozen_reachable_lineage_set`；
- factorial success counts 为 `Y00=0, Y10=0, Y01=9, Y11=5`；其中 `Y01` 是按三个 factual slots 重复记录的同 world parent outcome，不能当作 18 个独立 baseline trials；
- 恰有 1 个 strict event，位于 world `3005`、slot `motif-2`，assigned motif 为 `affine_commutative:b3d97b3f5e914d00`；
- 该 child 不是 direct hit，版本空间轨迹为 `256→4→3→1`；删除 child、改用 parent 后为 `256→143→45→25→3`；两个按冻结结构序选定的 same-frame replacements 均为 `256→182→143→45→25`；
- 因而该实例满足：spark/action 单独不含答案；真实 oracle 反馈提供目标相关信息；确定性更新把版本空间压缩到唯一目标；删除 spark 或替换 motif 后在相同预算内不再达到终点。

冻结 artifacts 与哈希：

- plan：内部 `6e5ca96db22c71921d45c455d75c755c428369a204f5af73d117c1711dc71480`；文件 `585dbecb161bd0008eb1e0b5d029404ff22dce674b902725c84ee64b99370bd5`；
- generation：内部 `10a2e17386567b2638567768944e6c48bde806a84c570782e68f653309ab0f46`；文件 `a7174ba2514e1d4e79d8f8bce210eb00f3142256834e433d31ea01f386ac1587`；
- analysis：内部 `c3f458c5a3bb8ba44411e7fae6e9edb98868f4d9df27234a88f9a7777ffc52af`；文件 `ab3d404ec1ee1449acb15625e0145ff81ce2387cc66cddcd830b773503b83616`。

证据等级必须进一步降级。实现验证阶段的 fake-provider 端到端测试已经在同一 `3000`--`3005` worlds 和同一 target namespace 上调用 `analyze_closure`，因此研究者侧曾接触这些 target outcomes；这些 worlds 不能再称为 fresh、untouched 或严格 prospective。该暴露不进入 live prompt，模型仍未看到 target、evidence 或 private outcomes，24 个真实响应也在分析前完整封存，所以它不抹掉上述事件的数学事实；但它引入 researcher-side outcome exposure 与潜在操作化选择偏差。正式标签修正为：

```text
post-calibration, outcome-exposed development mechanism demonstration
```

因此本轮支持的是“在这个有限、可重放系统中存在至少一个符合定义的 spark→evidence→compression 实例”，不支持总体发生率、平均处理效应、温度/熵因果作用、跨模型泛化、人类未知发现，亦不恢复第 15 节失败的 strict gate。若要取得 prospective evidence，必须在当前代码、阈值、route 和统计规则不再变化后，使用从未被 test/analyze 访问的新 world/target namespace 做独立复制；单测也必须迁移到 toy/noncanonical targets。

## 17. 2026-08-13 Same-protocol prospective mechanism replication

本节在读取任何 replication target outcome 前记录。它复制第 16 节有限机制实例，不是增熵假设、平均处理效应或真实科学发现能力的 confirmatory test。第 15 节的 `strict_gate_pass=false` 永久保持。

### 17.1 未打开的新样本

- world seeds 冻结为连续六个此前未使用的值 `10000`--`10005`；它们已在 registry 中登记为 `reserved-prospective-replication-unopened`；
- 审计覆盖当前仓库、ignored artifacts 与 Git 历史，上述 seed 和 target namespace 均无既往记录；
- seed 冻结前只做了 target-independent 健全性检查：每个 world 均能构造 256-member conditioned bank；没有派生 hidden target、枚举 target outcome、运行 `SparkCompressor` 或计算 `Y/strict_event`；
- target seed 使用全新 namespace `spark-closure-prospective-v1:target:<world_seed>`；prospective plan 只冻结 namespace，不提前写出每个 world 的派生 seed 或 digest；只有24条 generation 全部封存并验证后，analyzer 才首次派生 seed 并构造 hidden target；
- motif selection 保持第 16 节原算法和 namespace `spark-closure-v1:<world_seed>:<slot>:<stratum>`，因此没有根据第 16 节结果改变 motif distribution；新 world 自然产生新的固定 motif draws；
- tests 与 fake-provider E2E 继续只使用已 outcome-exposed 的 `3000`--`3005` development protocol；任何测试不得对 `10000`--`10005` 构造 namespace-derived target、运行 `analyze_closure`、`SparkCompressor` 或 strict-event 计算。

### 17.2 完全不变的操作化

以下项目相对第 16 节全部保持不变：6 worlds ×（1 neutral + 3 factual）=24 calls；slot 顺序；四 strata 轮转；syntax-unique motif library 与 hash selection；禁止重抽；parent/D0；prompt 与 action grammar；official DeepSeek `deepseek-v4-flash` route 和既有 passed canary；temperature `0.2`；max output tokens `256`；thinking disabled；每 slot 一次物理请求；invalid/no-op 留在分母；24 generation 全部封存后才允许首次构造 hidden targets；`child first + 3 shortest-member`；四轮 oracle；parent deletion；结构序前两个 matched replacements；以及 strict event 的八项条件。

若 transport/route failure 使 generation 少于24条，本次为 `non_evaluable_incomplete_attempt`，不补抽、不换模型、不通过另一条 route 续跑。执行期间不根据 partial responses 修改任何代码、prompt、seed、motif、阈值或规则。

### 17.3 预先冻结的复制判断

完整24-call generation 封存后只允许一次统一 analysis：

- `strict_event_count >= 1` → `prospective_mechanism_instance_replicated`；
- `strict_event_count = 0` 且至少一个 factual lineage valid → `prospective_replication_not_observed`；
- 18 个 factual lineages 全部 invalid → `prospective_lineage_interface_failure`。

primary reporting unit 是 world；`strict slots / 18` 只作描述，因为同一 world 的三个 factual slots 共用 target。Positive 只表示：在未打开的新 worlds/targets 上，按完全相同的有限机制定义再次观察到至少一个实例。Negative 只表示该 6-world/18-slot 批次未复制，不能否定机制存在。无论结果如何，都不推出 temperature/entropy 因果作用、总体发生率、平均优势、跨模型泛化或人类未知知识发现。

### 17.4 预执行顺序

1. 保持 default development tests 指向已暴露的 `3000`--`3005`，只增加 prospective plan 的 target-independent metadata/barrier 测试；
2. 完成代码与测试后冻结完整 source manifest、prospective plan、route binding 和文件哈希；
3. 在任何网络前核对 plan 的 source manifest 等于当前实现；
4. 串行完成并封存全部24个 generation responses；
5. generation SHA 固定后，首次解锁 `10000`--`10005` 的 target-dependent analysis，并停在本节三分类节点讨论。

预执行冻结记录（此时尚未派生任何 replication target seed，亦未观察模型响应）：plan 位于 `artifacts/spark-closure-prospective-20260813/plan.json`；canonical `plan_sha256` 为 `60003e4ea397456faf981bc5a954396760257e66b851ef942a819f132cb00f28`，文件 SHA-256 为 `2563a1cef2fcf4f9fa7210112c8bf561375f61ee9f9d54d564e6e6e07820ede6`，绑定 source manifest `661f62b17b8c854ee33dacb414ac70ed1c70c8490d788b52e5cd291ea2702857`、official DeepSeek route binding `02bd12bf58025f50146c47ac7ecb891fd68e37efe0d4dda8692b5947961e5964`，并显式引用第16节 plan/generation/analysis 三个内部哈希。

### 17.5 Prospective-v1 启动结果

`prospective-v1` 在第一个物理请求返回后、任何 action record 发布前，因 live `system_fingerprint` 不再满足 2026-08-12 canary 的 `provider_fingerprint_contract` 而中止。结果冻结为 `non_evaluable_incomplete_attempt`：protocol-accepted generation records 为0，没有 generation artifact，没有派生 target seed，也没有解锁任何 target-dependent analysis；不得续发该 slot 或继续 `10000`--`10005` 批次。该事件只表明 provider backend contract 漂移，不是机制 replication 的 positive/negative 数据。失败记录位于 `artifacts/spark-closure-prospective-20260813/attempt-failure.json`。

## 18. 2026-08-13 Prospective-v2 after route recanary

本节在任何 v2 hidden target 派生前记录。它不是 v1 的续跑：v1 的 `10000`--`10005` 永久 retired；v2 是新 canary、新 plan、新 seeds 与新 target namespace 的独立 prospective attempt，但复制判据与机制操作化不变。

### 18.1 新 route contract 与未打开样本

- v1 中止后，在退役 calibration world seed `1000` 上重新执行8-call generation-only canary；private test 未运行；
- 新 canary `artifacts/v3-canaries-20260813-r3/deepseek-official.json` 8/8 outer schema/search valid，contract passed；文件 SHA-256 `ce63aeb61ad73335d02459a71a5e432906a539b8db226b3fd0f77b44f249bd6d`；
- request/response model 仍为 official `deepseek-v4-flash`，endpoint/static request contract 不变；新 provider fingerprint SHA-256 为 `c4414aeeb35e200f6ba45110ee8d3ef7e846d1228830d9bf3306ed1ddb3f3859`，新 route binding 为 `0f9971ca63a7ff619b163bb31baf763da652eab5642d8d3d9208646fb20c03fa`；
- v2 worlds 冻结为 `10010`--`10015`。冻结前按完整数字 token 检索当前仓库、ignored artifacts 和 Git history，均无既往 seed 使用；没有构造其 target、lineage outcomes 或 compressor trajectory；
- v2 target namespace 为 `spark-closure-prospective-v2:target:<world_seed>`，prospective plan 仍只存 namespace，不存派生 seed/digest；motif namespace 继续是 `spark-closure-v1:<world_seed>:<slot>:<stratum>`。

### 18.2 不变项与判定

相对第17节，除 route canary、world seeds、target namespace 与 attempt id 外，所有科学参数完全不变：24-call grid、prompt、motif strata/library/hash selection、action grammar、temperature `0.2`、256-token cap、thinking disabled、每 slot 单请求、invalid/no-op 留在分母、generation-first barrier、四轮 oracle、parent deletion、两个 frozen-order replacements、strict-event 八项条件，以及禁止补抽。

完整24-call generation 封存后：至少1个 strict event → `prospective_mechanism_instance_replicated`；0 event但存在 valid lineage → `prospective_replication_not_observed`；18/18 invalid → `prospective_lineage_interface_failure`。任何不足24条的 route/transport failure仍为 `non_evaluable_incomplete_attempt`。结果边界仍只针对有限机制存在性复制，不涉及熵因果、发生率、平均效应、跨模型或人类未知发现。

预执行冻结记录（此时尚未派生任何 v2 hidden target，亦未观察24个模型响应）：plan 位于 `artifacts/spark-closure-prospective-v2-20260813/plan.json`；canonical `plan_sha256` 为 `dd7b70faab2960873bdd727f6424bea3e219d6cc341035d0632e9493fa5f8612`，文件 SHA-256 为 `2f1f7dbce8de55080bcf7ba7e105cbbe405e48c83025abf0918fe54f7ec55b33`，绑定 source manifest `94d724fc304f59b530a246e556a7c67dd8ad49dbc3cc955c6f97cc70d3b54b0b`、new canary SHA-256 `ce63aeb61ad73335d02459a71a5e432906a539b8db226b3fd0f77b44f249bd6d`、route binding `0f9971ca63a7ff619b163bb31baf763da652eab5642d8d3d9208646fb20c03fa`，并显式绑定第16节三个 development artifact 哈希与第17节 v1 `non_evaluable_incomplete_attempt`。

Generation barrier 记录：24/24 个预定 slot 全部完成并封存；24/24 action 通过闭合语法解析，每 slot 恰1次物理请求，finish reason 全为 `stop`，response model 全为 `deepseek-v4-flash`；canonical `generation_sha256` 为 `908c445e749920b8ba2333508f4c2f1cdb41aee197563458069e77ee1111c15e`，文件 SHA-256 为 `93ecdfd026ed085b600c873e27c480801d04721761c2e28f9289d3bc97ce5a9c`。该哈希冻结后才首次解锁 hidden-target analysis。

### 18.3 Prospective-v2 结果（2026-08-14）

唯一一次统一 hidden-target analysis 在 generation barrier 之后完成。按预先冻结的判定，结果为：

```text
prospective_replication_not_observed
```

核心计数：

- 24/24 action 通过闭合语法解析；18 个 factual slots 中5个匹配预先冻结的 reachable lineage，13个为 `not_in_frozen_reachable_lineage_set`；
- 5个 valid lineages 中2个 child 在四轮 oracle evidence 后达到唯一目标，但 strict event 为0；
- world `10012` 的成功 child 轨迹为 `256→172→27→1`，但 parent 与两个 matched replacements 也都达到1，因此删除和替换反事实均不成立；
- world `10015` 的成功 child 轨迹为 `256→185→9→5→1`，parent 停在5，但两个 matched replacements 也都达到1，因此只通过删除反事实，未通过 motif-specific replacement control；
- 其余3个 valid lineages 均未在四轮内达到 singleton/full-domain endpoint；
- factorial 描述计数为 `Y00=0, Y10=0, Y01=3, Y11=2`。`Y01=3` 是 world `10012` 的同一 parent outcome 在三个 factual slots 中重复记录，不是三个独立 world 成功。

因此，这批新样本证明了通用 LLM 可以生成部分合法、可追踪的 lineage，且其中两个 child 能在真实 oracle evidence 的介导下压缩到唯一目标；但没有一个实例同时证明这一成功对 assigned spark 具有 parent-deletion 和 matched-replacement 意义上的特异必要性。第16节的 development mechanism instance 未在此 prospective batch 中复制。这不是对机制存在性的全局否定，也不支持事后放宽 strict 标准后把本批改称 positive。

冻结 analysis 的 canonical `analysis_sha256` 为 `1d5eab64cd1540ab872b5bbecef0d53d0eb3219e77ddc5069e33cf71ac728390`，文件 SHA-256 为 `f3af12202952281a2510db22a2a4e759fcb3693b2ce4ff51977570406e06565e`。plan、generation、analysis 三个 canonical digest 均已独立重算通过。

## 19. 2026-08-14 Prospective layered-mechanism follow-up

本节在任何新 hidden target 派生前冻结。它是第18节之后的新分层机制研究，不重解释第18节的 `prospective_replication_not_observed`，也不将事后放宽的标准套回旧数据。

### 19.1 设计与新样本

- 独立统计单位为 world。冻结32个 worlds，每 world 恰3个 factual motif actions，共96次科学调用；
- 删除 neutral/no-op 请求。parent baseline 本就由同一确定性 compressor 离线计算，neutral 既不进入删除反事实，也不进入 replacement 反事实；在同样96-call预算下，删除它将独立 worlds 从24增至32；
- 96个 factual slots 依然按四个 motif strata 冻结轮转，每 stratum 恰24次。同一 world 的三个 slots 是固定 package，不当作三个 IID trials；
- world seeds 不按难度、lineage 或 outcome 挑选。对 index `0..31` 计算 `SHA256("spark-closure-layered-v1:world-seed:" + index)`，取前8 bytes 大端整数并 mask 至63 bits；唯一允许的拒绝条件是与既有 seed registry 碰撞，实际无碰撞；
- 冻结 seeds 按顺序为：`149164194557103187, 197785174046540536, 8689498207041883831, 7372109617068943611, 1788933733710549810, 5850954761208054067, 8468748721542519872, 508095208076430127, 7255759396679503842, 3010699749877793097, 2473712061732812970, 856738614459882241, 5200387050906735940, 6971971984972950855, 8004701421764506100, 329962133897780649, 3073125064765817691, 487714150649552500, 5527731908070175319, 6466267340987574428, 7352683128229967339, 8557001049290273476, 3944888237210388916, 2480113330417097531, 3084195352423810677, 9194213173342005834, 6760555811078959657, 4235194283738692887, 3092150612108050083, 1143034887637611591, 3472459390724036822, 2782549438481220964`；
- seed 冻结后只做了 `target_seed=0` 的公开bank/D0构造检查，32/32 成功。没有派生 `spark-closure-layered-v1:target:<world_seed>`，没有枚举lineage、运行 compressor 或计算任何 endpoint；
- target 与 motif selection 均使用新 namespace `spark-closure-layered-v1`。旧 development 和 prospective-v2 worlds 只用于设计本轮，不进入新轮分子、分母或区间。

### 19.2 四层嵌套 world endpoints

对每个 world `w` 的三个预定 factual slots，定义同一slot上逐层嵌套的指标：

1. `E1_w` / lineage feasibility：至少一个 action 可从冻结 parent + assigned motif 唯一重放，语义合法、D0一致、行为新颖，并属于target-blind frozen reachable set；
2. `E2_w` / oracle-mediated closure：至少一个同一 `E1` slot 的 child 非 direct hit、truth retained、存在正的 non-MATCH contraction，且四轮后 `N_T=1` 并 full-domain recovered；
3. `E3_w` / paired parent-deletion advantage：至少一个同一 `E2` slot 的冻结 parent 在同样四轮预算下不达 endpoint；
4. `E4_w` / strong matched-replacement specificity：至少一个同一 `E3` slot 的两个预先按冻结结构顺序选定、same-frame/same-stratum 的 matched replacements 均不达 endpoint。

`E1 ⊇ E2 ⊇ E3 ⊇ E4`。主报告对象为 world-level counts `(K1,K2,K3,K4)=Σ_w(E1_w,E2_w,E3_w,E4_w)` 及各自 `/32`；另报 `K2/K1, K3/K2, K4/K3`，分母为0时记 `not estimable`。为与旧 strict rule 对照，“至少一个 replacement 失败”仅作 weak secondary，不进入新 primary。

对每个 valid lineage 描述性报告 child、parent、两个 replacements 的 `N_T`，`control_N_T-child_N_T`、`log2(control_N_T/child_N_T)`，以及正/平/负计数。这些slot-level量不作 IID 显著性样本。

### 19.3 冻结判定与区间

主决策终点为 `K4`：

- `K4 >= 2` → `prospective_cross_world_replication_observed`；
- `K4 = 1` → `single_prospective_mechanism_instance_observed`，只支持单实例，不称跨world复制；
- `K4 = 0` → `not_observed_under_frozen_protocol`，不外推为机制或总体假设 negative。

同时以最深非零层定位瓶颈：`K1=0` 为interface failure；`K1>0,K2=0` 为lineage可行但无closure；`K2>0,K3=0` 为closure存在但无parent advantage；`K3>0,K4=0` 为child-start advantage存在但assigned motif不特异。不为四层各自设显著性门槛。

各world-level marginal rate报双侧95% Clopper–Pearson interval，条件转化率为描述性。因四种固定motif package虽各有8个world并完全均衡，却不是完全相同的处理包，pooled Clopper–Pearson 只称为common-rate binomial model-based interval，不称为无条件design-exact interval；另报四种package的分层计数。该区间的解释限于此冻结 world generator、route、prompt、bank 和四轮预算。32 worlds 是机制screening/瓶颈定位规模，不是精确发生率估计或一般假设确证。

### 19.4 执行屏障与解释边界

代码、tests、registry、route canary、plan、source manifest 与上述规则全部冻结后，串行完成并封存96 generation records。在generation barrier前不派生hidden targets、不运行compressor、不查看中间endpoint，不因invalid、no-op或结果替换world，不从32扩样。完整封存后只运行一次统一analysis。route/contract在完成前漂移则整批为`non_evaluable_incomplete_attempt`，不将partial视为科学分母。

可支持的最强结论只是：在有限DSL、固定bank、oracle、4-round预算和当前prompt-route下，是否出现可追踪lineage、evidence-mediated closure、paired starting-candidate advantage与same-frame motif specificity。它不检验temperature/熵因果，不证明人类未知发现，不支持跨模型泛化、一般发生率或平均处理效应。

预执行冻结记录（此时尚未派生任何 layered hidden target，亦未观察96个模型响应）：plan 位于 `artifacts/spark-closure-layered-v1-20260814/plan.json`；canonical `plan_sha256` 为 `45b22d0e1b1b7657bfa7ae016e315e1af980e5b8a8771b9768ed1bef9c13777d`，文件 SHA-256 为 `da752186bbed8208f1be7e441730ae9082059d65c8967aa7c7bab458743412c1`，绑定 source manifest `85085548562b8f26d62c4dcccfa0a8161a0a0868354dd9857c73e3e076a28a4a`、r4 canary SHA-256 `d5a4df862aa4084c34af2e76da3ae98985c7f3c63fbc8cc3bdfe1edfb4edc497`和 route binding `0f9971ca63a7ff619b163bb31baf763da652eab5642d8d3d9208646fb20c03fa`；plan 为32 worlds、96 factual slots、0 neutral，四种strata各24，且不含target seed/digest。

Generation barrier 记录：96/96 个 factual slots 全部完成并封存；96/96 action 通过闭合语法解析，每 slot 恰1次物理请求，finish reason 全为 `stop`，response model 全为 `deepseek-v4-flash`；canonical `generation_sha256` 为 `570e84a005b87925358e13c559ac890d437844fc5fb5f85922c4661d655e827d`，文件 SHA-256 为 `31dcdf9b9ffce8ae6fe75fef2ff15ccbad4bf97d7f64cd964e94d9bde7619ae4`。该哈希冻结后才首次解锁32个 hidden targets。

### 19.5 Layered-v1 结果（2026-08-14）

唯一一次统一 hidden-target analysis 在96-record generation barrier 之后完成。按预先冻结的 world-level primary 判定，结果为：

```text
prospective_cross_world_replication_observed
```

世界级嵌套漏斗为：

- `K1=22/32` worlds 至少有一个lineage-valid action，比率68.75%，common-rate model-based 95% Clopper–Pearson interval 49.99%–83.88%；
- `K2=16/32` 在同一slot上完成非direct、truth-retained、positive-non-MATCH、四轮`N_T=1`且full-domain的oracle-mediated closure，50.00% [31.89%, 68.11%]；
- `K3=9/32` 进一步在同一slot上通过parent deletion，28.125% [13.75%, 46.75%]；
- `K4=3/32` 进一步在同一slot上使两个冻结matched replacements都不达endpoint，9.375% [1.98%, 25.02%]。

描述性条件转化为 `K2/K1=16/22=72.73%`、`K3/K2=9/16=56.25%`、`K4/K3=3/9=33.33%`。在96个slots中，32个lineage-valid，19个达到M，10个达到D，3个达到strong R/S。其余64个虽语法合法，但不在预先冻结的reachable lineage set中。

三个 strong K4 worlds 的同slot证据链为：

1. world `1788933733710549810`，`affine_directional:4558bb5e30a21a2e`：child `256→22→8→2→1`，parent 与两个matched replacements均终止于`N_T=2`；
2. world `5200387050906735940`，`affine_directional:ace591bb0bcc57fd`：child `256→1`，parent 与两个matched replacements均终止于`N_T=17`；
3. world `5527731908070175319`，`pairwise_variable:beb7bdb456c2dc2c`：child `256→1`，parent、replacement-1、replacement-2分别终止于`N_T=18,18,37`。

三个child均非direct hit、truth retained、包含正non-MATCH contraction并full-domain recovered；所有parent与六个matched replacements均未唯一闭合。三个world/target/child均不同，条件没有跨slot拼接。K4分布于两种motif package，其中两个为directional、一个为pairwise；分stratum只作描述，不做显著性比较。

旧weak endpoint（两个replacements至少一个失败）共有7个slots/worlds；冻结artifact顶层的 `strict_event_count=7` 仅是这个secondary legacy weak endpoint，不是新primary。新primary必须读为`layered_endpoints.world_counts_K.K4=3`。

本轮的最强科学解释是：在冻结的有限DSL/bank、target-independent motif prompt、真oracle evidence和确定性四轮压缩器中，已prospective地在三个独立world观察到“可追踪spark/action 改变首个查询路径 → 真实反例证据进入 → 版本空间压缩到唯一目标”，且删除spark或替换为两个同frame controls后在相同预算内都不成功。这是有限系统中的cross-world mechanism signal，不是temperature/熵因果证据，不是一般发生率或ATE，不支持跨模型泛化，更不证明LLM发现了人类未知知识。

冻结analysis的canonical `analysis_sha256` 为 `b9f672c0d7bc117fdee71c701bc5e8fbc37741ec49ddd0139378bb5c76b6d691`，文件 SHA-256 为 `14dc6b9406a7265703f787b008fa62268aa3fbc6d76a0e077b5e8b733489c749`。plan、generation、analysis三个canonical digest及哈希链均已独立重算通过。

## 20. Cross-model matched-triad replication final protocol

**冻结时点声明**：本节定义唯一active的`cross-model-matched-triad-v1`正式协议。本文本封存时，32-world task grid仍保持unopened，尚未生成正式science plan，未发生本协议的288次science调用，也未派生或分析任何hidden target。其后状态只以相应不可变artifact为准；不得通过回写本段改变这一freeze-time事实。

`cross-model-paired-v2`曾完成两路由的pre-plan代码、文本与canary准备，但在正式plan、science call、target seed派生和hidden-target分析之前，因预先决定加入DeepSeek Pro而被本三臂协议取代。它是`pre-plan_superseded_design`，不是实验attempt，也没有positive/negative结果。

### 20.1 研究问题与正式模型路线

本轮只检验一个问题：第19节的有限系统机制信号，能否在三条预选的通用模型alias route上独立复制，以及一致性只出现在DeepSeek家族内，还是能跨越DeepSeek与GLM家族。它不是prompt-level Spark/no-Spark因果试验，也不重新检验temperature或熵。

三个active arms冻结为：

- `deepseek-flash`：DeepSeek官方API的`deepseek-v4-flash`；
- `deepseek-pro`：DeepSeek官方API的`deepseek-v4-pro`；
- `glm-5.2`：Tencent TokenHub的`glm-5.2`。

MiniMax与Kimi路线仍作为pre-plan abandoned development；它们未触碰本批fresh targets，不进入正式模型、science分母、结果或解释。三个active arms也不是从模型总体中随机抽样；Flash与Pro共享模型家族及provider，GLM同时改变模型家族与provider。因此差异不能分解为纯底层模型效应，三路一致也不能推广为所有LLM的普遍规律。

### 20.2 Matched-triad设计

- 一次性冻结32个全新worlds；每world仍为3个factual motif slots、0 neutral，共96个公共slots；
- 三个arms面对逐字节相同的D0、parent、assigned motif、action grammar与slot顺序；每arm 96次，总计288次scientific calls；
- 同一world的bank、hidden target、motif与oracle预算在三个arms间完全相同，唯一任务层变化是各模型生成的action；
- 每个`arm × slot`恰一次物理请求。invalid/no-op保留在分母，不补抽、不重试内容、不按结果替换world；
- 三个96-record generation artifacts全部完成、验证并封存后，才首次派生共同hidden targets并运行唯一一次joint analysis；任一arm不完整则整个triad为`non_evaluable_incomplete_attempt`；
- 独立统计单位仍是32个worlds，不能把三个arms池化成96个worlds，也不能把96个slots或288次calls当IID。

第三个arm增加的是预选路由间一致性与家族边界证据，不会把统计样本从32个worlds自动变为96，也不会自动产生统计显著性。

### 20.3 冻结endpoint与world-level报告

对每arm、每world分别原样计算第19节的同slot嵌套`E1..E4`与`K1..K4`：lineage feasibility、oracle-mediated closure、paired parent-deletion advantage、以及两个matched replacements都失败的strong specificity。四轮oracle、`child first + 3 shortest-bank members`、non-direct/truth-retained/full-domain规则均不变。

每arm仍按原判定：`K4>=2`为该arm的prospective cross-world replication；`K4=1`为单一实例；`K4=0`为frozen protocol下未观察到。`K1=0`另报`model_dsl_interface_failure`，不得把该arm当作机制negative。

对每一层`K1..K4`，报告32个worlds落入的八个互斥通过组合：三路均不通过、仅Flash、仅Pro、仅GLM、仅Flash+Pro、仅Flash+GLM、仅Pro+GLM、三路均通过；每格同时给出count与world seed列表，八格之和必须为32。另对Flash–Pro、Flash–GLM、Pro–GLM三个pairs分别报告配对四格表作描述诊断。同一world跨arm通过是更强的描述性证据，不设为单一必要endpoint。

### 20.4 冻结联合分类与解释边界

令某arm在`K4>=2`时称为“达到cross-world replication阈值”。五档联合分类按下列顺序冻结：

- 三个arms都达到：`all_routes_replication_observed`；这是本轮最强的预选路由一致性，也包含跨家族复制；
- GLM与恰一个DeepSeek arm达到：`cross_family_replication_observed`；支持跨家族复制，但第三路未复制表明鲁棒性仍有边界；
- 只有Flash与Pro达到：`deepseek_family_only_replication_observed`；只支持DeepSeek家族内一致，不称跨家族复制；
- 恰一个arm达到：`single_route_replication_observed`；支持路由特异的cross-world复制，不支持cross-model robustness；
- 没有arm达到：`replication_not_observed`；只表示本冻结triad下未复制，不撤销layered-v1的三个封存实例，也不外推为机制总体negative。

每arm的`K4=1`单实例状态独立报告，不改变以`K4>=2`为准的五档联合分类。任一arm `K1=0`的interface failure也独立报告；联合label仍只编码哪些arm观察到`K4>=2`，解释时不得把interface-failed arm的`K4=0`当作反证。本轮不增加p值、事后阈值或基于结果的model表决规则。

Parent deletion与matched replacements检验的是已生成child之后的查询路径特异性，不等价于“不给模型看Spark时会生成什么”。本轮为保持对layered-v1的精确跨模型复制，不加入prompt-level no-Spark条件。后者应成为单独研究：在相同world中随机分配真实motif与matched sham motif，让两种条件都可自由生成合法action，再比较有效查询概率。

### 20.5 三route target-free action canary与冻结（2026-08-21）

三条active route均完成了12-call target-free action canary。canary只使用退役world的公开D0、target-independent parent、assigned motif与action grammar；没有派生hidden target，没有运行oracle、lineage outcome或compressor，因此不进入science分母。三份canary的prompt-set SHA-256完全相同，均为`41d3d8878f6da9c1c7543ee3e82f01910a1558448a1364f94ab7e278a67e5094`；Flash与GLM共享早先的canary plan，Pro因独立时点生成plan而具有不同plan digest，不影响prompt-set同一性。

- Flash/GLM使用内容逐字节相同的canary plan，分别存于`artifacts/spark-cross-model-canaries-20260820/deepseek-v4-flash-action-plan.json`和`artifacts/spark-cross-model-canaries-20260820-tencent/glm-5.2-action-plan.json`；其canonical SHA-256为`5f96e79b61c6edd8b87fac2837d3ee1b71bd4ad90655eb6983b6c12dcc3531bc`，文件SHA-256为`8b1cd6277df4b347c4e98ee909d0452d6a258277a0df72e377dca5c7a490b3ca`；
- Flash canary artifact `artifacts/spark-cross-model-canaries-20260820/deepseek-v4-flash-action-canary.json`的文件SHA-256为`0516ffd54692097ec06f2278951aae20c0835a86bacdef9af80699508a7e4f6a`；provider为`deepseek-official-openai-compatible`，request/response alias均为`deepseek-v4-flash`，endpoint SHA-256为`948f1ecb6b48f91adc4e110d0351cd172b16450e9936d358992e0dfad7b863f3`，route binding为`0f9971ca63a7ff619b163bb31baf763da652eab5642d8d3d9208646fb20c03fa`，exact provider fingerprint为`c4414aeeb35e200f6ba45110ee8d3ef7e846d1228830d9bf3306ed1ddb3f3859`；
- Pro canary plan `artifacts/spark-cross-model-canaries-20260821-pro/deepseek-v4-pro-action-plan.json`的canonical SHA-256为`8ede1082eb9f3a70a46020c19af1d3dac01529e69e161066f16ed9764df09bc7`，plan文件SHA-256为`15eca6ddced1b19fc22d10633a2dab9dbb840dbf291bd58e05ea7a9b168eb88c`；canary artifact `artifacts/spark-cross-model-canaries-20260821-pro/deepseek-v4-pro-action-canary.json`的文件SHA-256为`1f7b92dabcc8c4c562d89328dd6cb9c4119b8cbec6e287926728c96ce8c0fce6`，provider为`deepseek-official-openai-compatible`，request/response alias均为`deepseek-v4-pro`，endpoint SHA-256为`948f1ecb6b48f91adc4e110d0351cd172b16450e9936d358992e0dfad7b863f3`，route binding为`d44699c6e1463c8f428c72e04585feac9cdaf20cd64a680109b1e4d1d9255936`，exact provider fingerprint为`a2cd55bf7e17b1daa413c2d3ce931256a1d0d5e65084859059777e2bbb546787`；
- GLM canary artifact `artifacts/spark-cross-model-canaries-20260820-tencent/glm-5.2-action-canary.json`的文件SHA-256为`9523e9422ca67bea9ece8a0358a4a06bf6080d38656fbfedce9b84c899178210`；provider为`tencent-tokenhub-openai-compatible`，request/response alias均为`glm-5.2`，endpoint SHA-256为`2095d8a5425aaf2ce7b1c8a4b63baecdc0ffc4851ac92810b191ee3b9194840c`，route binding为`02243ec1c415c25c9938f4d4a209b8e3864212ce3952ae3715d8140e4c13a6e9`；GLM不提供可绑定的backend fingerprint，因此对它的结论只指向本次执行时的Tencent TokenHub alias route；
- 三条route均为12/12 outer JSON有效、12/12 factual action grammar有效且非`no_op`，四个motif strata各3次，每slot恰一个physical request；temperature `0.2`、max output tokens `256`、thinking disabled、zero reasoning tokens与JSON-object response contract保持不变。

### 20.6 Unopened task grid与288-call执行顺序冻结

正式协议ID为`cross-model-matched-triad-v1`。为避免因pre-plan route选择改变任何任务，base、target与motif-selection namespace继续使用已登记但从未打开的`spark-closure-cross-model-paired-v1`，world seed draw provenance也继续是`spark-closure-cross-model-paired-v1:world-seed`；namespace中的`paired-v1`是原始task-grid登记标识，不是active protocol版本。对index `0..31`计算SHA-256(namespace + `:` + decimal index)，取前8 bytes大端整数并mask至63 bits；唯一允许的跳过理由是与冻结前registry碰撞，本轮32个候选均不碰撞且内部唯一。

冻结32个world seeds依次为：`5609854509399487714, 8058848814949332127, 7432589210973578845, 3920682316420328816, 1418744941558891841, 7604204542873609924, 1387282349159788876, 8242426922921378803, 1160497852689591359, 6872575636001638699, 7396720935553072228, 5279887130524777443, 5123783953932712497, 3034756861122824323, 2262333810103905472, 518707974867583009, 7993937249025442561, 3850349365944176259, 7211834526608777947, 6627891344710956940, 4402357155133626695, 4960748528416202938, 5566094773751083457, 3680507740242696405, 6866785901476227762, 5033621553926766983, 5357853615180860507, 3120120567224792408, 1045602656972176335, 2858014253687291177, 1789187785946847608, 6476484620047087171`。

在本三臂协议冻结前，32/32只完成target-free public bank/D0/parent构造检查；没有派生target namespace seed，没有enumerate lineage、运行compressor或读取outcome。新增的Pro canary也只使用retired development worlds，未打开这32个worlds。若后续任一world在正式冻结代码下构造失败，整个协议版本停止，不按难度换seed。

288次调用按public slot `0..95`顺序执行，并在每个slot内调用三个arms。设`F=deepseek-flash`、`P=deepseek-pro`、`G=glm-5.2`，对每个motif stratum单独按其第`k`次出现的`occurrence_index mod 6`依次使用`FPG, FGP, PFG, PGF, GFP, GPF`；在当前四strata轮转布局中等价于按`(serial_index // 4) mod 6`取排列，不得改为`serial_index mod 6`。每个`stratum × permutation`组合各出现4次，总体六个排列各16次；因此每arm各32次位于slot内第一、第二、第三位，任意两route的先后次序也各为48/48。这个顺序同时平衡motif stratum与route×time次序，不改变每arm内部的96-slot固定序列。无内容重试、无补抽、无partial resume；任一中断或contract drift使整批non-evaluable，禁止解锁hidden targets。

### 20.7 固定joint分析与停止规则

任意runtime callback不属于正式分析接口。三份96-record artifacts通过joint barrier后，只能调用source-manifest中冻结的joint layered analyzer。每个world只构造一次共享hidden target、`SparkWorld`、compressor、parent baseline和reachable lineage集合，再分别评估三个arms的action。

最终plan必须绑定layered-v1的canonical plan/generation/analysis SHA、四轮预算、上述endpoint与五档classification、三份active canary文件SHA与route contract、冻结后的source manifest以及288-entry执行顺序。代码、tests、registry与本文本完成后才能生成plan；从plan生成起不得再修改`src/`、`tests/`、`configs/`或本文本。plan、generation与analysis的生成时间和digest只记录在各自immutable artifact及运行报告中，不回写本节。

协议要求在正式science plan生成并独立复核后、288次调用之前停一次；三份generation完整封存后才可运行唯一一次joint analysis。analysis完成后，无论是`all_routes_replication_observed`、其他四档联合结果、single instance或interface failure，都立即停止；不自动加world、改prompt、补调用、换route或追加第四模型。

## 21. Post-hoc action-opportunity map（2026-08-21）

本节在第20节正式结果已经解锁之后记录，因此只定义一次纯离线、事后描述性诊断。它不增加样本、不改变正式`replication_not_observed`分类，也不能把第20节重新判为positive。目的仅是区分：K4稀少主要来自冻结任务中缺少可行action，还是存在可行action而三条route没有选中。整个诊断不得调用模型或provider。

输入只允许第20节已经封存的exact artifact triple：canonical plan SHA-256 `a52c70b7cc8595ce1615dba1c5146576d23ff8330d0d44b2dd1de66ef9798064`、generation bundle SHA-256 `4f0ef3ff627f3e8c0df3667ff4668de57e3bbc2d16314b20f10bb9bd17c4e928`、analysis SHA-256 `a4bbe0eb862d2af20690a759eb47ab7b1975f7197a20ecc34357251d556a6bb0`。三者的self-digest、hash chain、3×96 identities和正式K1--K4必须在诊断前重新验证；任何不一致立即停止。新诊断另绑定执行时的current source manifest，不放宽live generation或正式joint-analysis的source guard。

### 21.1 冻结机会宇宙与端点

对全部32 worlds × 3 assigned-motif slots，不筛world、不筛slot，穷举prompt当时真实允许的10个action frames：两个path `(1,1)`、`(1,2)`分别配`replace`、`add-right`、`sub-right`、`sub-left`、`mul-right`。因此主计数固定为96 slots、960 raw syntactic actions；matched replacements是反事实controls，不加入模型动作菜单，亦不加入其他motif、no-op、多步编辑或prompt外动作。

对每个slot定义严格嵌套的action集合：

- `C1`：action精确匹配`enumerate_reachable_children(world)`中assigned motif的control-ready lineage record；
- `C2`：`C1`且child非direct hit、truth retained、存在positive non-MATCH contraction，并在冻结四轮内达到`N_T=1`及full-domain recovery；
- `C3`：`C2`且同预算parent不能闭合；
- `C4`：`C3`且该lineage按原target-blind结构顺序冻结的前两个matched replacements均不能闭合。

不得引入development reachability calibration的`rho_V`筛选，不得从replacement pool中事后挑两个失败controls，也不得以“任意一对失败”替代正式K4。每行必须落入`invalid / C1-only / C2-only / C3-only / C4`之一，invalid仍留在固定960分母。

`slot opportunity Cj`表示该slot的`Cj`非空；`world opportunity Cj`表示该world三个slots中至少一个`Cj`非空。存在性不受去重影响。密度同时报告：raw action count；K1--K3按`child_behavior_hash`的semantic count；K4按`(child_behavior_hash, frozen replacement-1 behavior hash, frozen replacement-2 behavior hash)`组成的counterfactual-bundle count。相同child经不同frame得到不同controls时必须保留frame dependence，不得择优合并。

现有replacement pool只排除与focal相同行为的control，并不保证两个controls彼此语义不同。为忠实保留原K4，正式判断仍使用冻结前两个；同时另报raw pool大小、unique-behavior pool大小、前两个是否behavior-distinct，以及全pool按behavior去重后的closure比例。这些只作robustness描述，不重分类。

### 21.2 模型overlay与解释边界

机会landscape先仅由plan、hidden target和确定性代码构造，不读取三臂generation选择。随后才将288个已封存动作精确overlay：某arm在某slot的`hit Cj`仅当其规范化实际action恰好属于该slot的`Cj`；有机会但未命中记为`opportunity_miss`，无可行动作记为`no_opportunity`。world层只有在该arm三个已选动作中至少一个命中时才算world hit。机会地图只构造一次，不能把共享96 slots乘成三份独立机会；正式推断单位仍为32 worlds，所有slot/action比例均是有限网格census，不报p值或置信区间。

overlay必须逐arm、逐world、逐qualifying slot精确复现封存正式结果：Flash `26/23/6/0`、Pro `24/21/6/0`、GLM `27/23/7/1`。任何偏差均使机会地图不可发布。重点描述共同六个K3 worlds究竟没有K4机会、只有其他slot有机会，还是同slot另有K4 action；另描述GLM唯一K4所在slot的K4 raw/bundle数量、语义别名、control distinctness及path分布。

“有机会但未命中”只表示：在真实target事后已知后，代码发现当时菜单中存在一个会达到K4的action，而模型没有输出它。模型生成时看不到target，因此不能据此说模型“犯错”、本应知道答案、理解了或未理解spark，也不能识别prompt-level因果、充分/必要条件、重复采样概率、真实世界外推或人类未知发现。若机会world不足2，只能说明该冻结网格对原复制门槛存在availability ceiling；若机会world至少2而模型不足2，才可把正式未复制进一步定位为“可行action存在但实际选择未命中”的事后机制诊断，仍不得改写正式classification。

## 22. Full-pool strong-K4 feasibility scan（2026-08-22）

第21节显示，原正式K4的17个raw actions中只有5个在完整matched-replacement pool下仍稳健；这5个动作又只覆盖2个slots、2个worlds，且四个动作是同一world/slot/child behavior的语法frame变体。两个world分别属于`affine_commutative`和`affine_multiplicative`，`affine_directional`与`pairwise_variable`没有完整pool机会。因此，不能直接在旧32-world网格上通过增加模型调用解决强消融稀疏问题。

实现阶段曾建立`spark-strong-k4-feasibility-v1`候选namespace，但在要求的target-free plan barrier封存之前，开发烟测与随后只读代码审计各自materialize了同一个candidate index 0。两次都没有模型/provider调用，没有生成正式plan、shard或科学结果；但该顺序已使v1不能再声称完全unopened。不得只删除或替换candidate 0来修补，因此v1整套1024-seed namespace在registry中标为`retired-pre-plan-implementation-smoke`并永久排除，不能进入后续benchmark或confirmation。

唯一active扫描改为全新`spark-strong-k4-feasibility-v2`，配置为`configs/spark-strong-k4-feasibility-v2.json`。它只回答：当前有限DSL、world generator、motif library、10-action菜单和四轮压缩器能否构造24或32个跨motif平衡、预先已知存在强消融机会的world。它不是模型实验，不读取任何generation，不调用provider，也不改变第20节的`replication_not_observed`。扫描中打开hidden target，因此其入选集是outcome-conditioned challenge set，不是自然world分布的prospective样本。

### 22.1 新端点`K4_full_pool`

`K1--K3`逐项保持第19--21节定义与四轮预算不变。不得修改旧`K4`或用新结果回写旧artifact。对一个已达K3的focal action，构造其完整可用matched-replacement pool：control必须来自冻结motif library，能在同一parent上独立replay，保持相同path、operation、binary operator、motif side、old-subtree、motif stratum和complexity bucket，通过原target-blind D0/evidence-novelty lineage约束，并且完整域行为不同于focal child。

controls按完整域`child_behavior_hash`去重；语法别名保留作审计，但不增加消融证据数。若同一behavior出现不同endpoint结果，扫描立即失败。新的主端点定义为：

```text
K4_full_pool = K3
               and unique_control_behavior_count >= 3
               and every unique control fails exact identification
```

至少三个controls必须彼此行为不同且都不同于focal child。旧K4的冻结前两个controls只作历史对照，不参与新主端点。full-pool bundle绑定action frame、focal child behavior及排序后的全部unique control behavior hashes；相同child若来自不同frame/control pool仍是不同bundle。未来模型命中必须以exact action或相同full-pool bundle判定，单纯child semantic相同不能算正式命中。

### 22.2 固定候选流与扫描分母

候选协议ID为`spark-strong-k4-feasibility-v2`。候选index固定为`0..1023`，必须完整扫描1024 worlds，不因提前找到足够机会而截停，也不得在结果不足时临时扩到1025以后。world seed按`SHA256("spark-strong-k4-feasibility-v2:world-seed:" + decimal_index)`前8 bytes大端整数并mask至63 bits；只允许排除预先登记的seed collision，本冻结流没有collision。target namespace固定为`spark-strong-k4-feasibility-v2`；对每个world精确复用`_target_seed_for_namespace`，即把`SHA256(namespace + ":target:" + decimal_world_seed)`完整32-byte digest解释为大端整数，并只调用一次现有`generate_spark_world(world_seed, target_seed)`，由其已有`random.Random(target_seed).randrange(256)`选择唯一target。禁止在256个bank members中寻找有利target。

每world仍有三个target-blind assigned motif slots。stratum按`MOTIF_STRATA[(candidate_index * 3 + factual_index) mod 4]`轮转；motif按现有SHA-256选择器一次确定，不因hidden outcome换motif。每slot穷举两个path乘五个prompt内action frames，固定10 actions。invariant、replay、hash或构造错误使扫描停止，不得把异常当作无机会world或补抽seed。

在首次派生target或运行compressor之前，必须生成并独立复核一个target-free scan plan。该plan绑定配置文件bytes SHA、current source manifest、完整1024-seed向量，以及按原candidate index冻结的3072个slot motif/stratum/selection identities；不得包含target seed/index/hash、`SparkWorld.world_hash`或任何endpoint。plan封存后，扫描期间不得修改`src/`、`tests/`、`configs/`或本文本；shard和最终merge都必须绑定同一plan canonical digest。

实现测试可以在`generate_spark_world`被完全mock、没有target RNG draw且不保存candidate-specific输出时验证通用target-seed SHA公式；单独计算该不可见哈希整数不构成target materialization。打开world的边界精确定义为：真实调用`generate_spark_world`并执行`random.Random(target_seed).randrange(256)`，或以其他方式得到target index、labels、trajectory或endpoint。active v2在plan barrier前这些事件计数必须全部为0。

1024-world固定census可分成16个连续64-world shards并行计算；每个shard绑定配置文件SHA、current source manifest、候选range及self-digest，merge必须拒绝gap、overlap、重复range或manifest/config漂移。并行顺序不得改变按candidate index排序的最终结果。所有被扫描的seeds无论是否入选，都属于已打开的development/benchmark-construction worlds，永久排除于未来随机确认样本。

### 22.3 平衡可行性分类

对每个world记录至少一个`K4_full_pool` action所覆盖的motif strata集合。构造四strata联合的bipartite b-matching：world节点容量严格为1，每个stratum节点容量严格为共同配额`q`，因此同一多stratum world不能重复占两个独立名额；只检查四个边际eligible counts不充分。对任一可行assignment，依冻结`MOTIF_STRATA`顺序分别列出该stratum升序candidate indices，再拼成长度`4q`的assignment vector；在所有可行exact-q assignments中唯一选择字典序最小vector。禁止按strong action数量、child `N_T`、压缩幅度、control pool大小、模型偏好或人工观感排序。入选后必须保留原candidate index、slot index和motif assignment，不能因重排world而重新抽取motif。

扫描完整1024 worlds后按以下闭集分类：

- 每个stratum可分配8个互不重复world：`full_32_balanced_feasible`，固定构造32-world cohort；
- 上述不成立，但每个stratum可分配至少6个互不重复world：`reduced_24_balanced_feasible`，固定取每stratum 6个、共24 worlds，不事后选择25--31；
- 任一stratum无法达到6：`balanced_strong_K4_benchmark_not_feasible_under_cap`。

必须同时报告raw actions、unique focal behaviors、full-pool bundles、eligible slots/worlds、每stratum capacity与deficit、每个K3 control pool的raw/unique size及全部unique-control `N_T`、每world的机会数量，以及入选cohort的target/child/motif/frame重复情况。K3未成立的action只报告结构pool size，不为无关controls额外运行compressor。若全局qualifying child behaviors少于4种或任一stratum只有一种，标记`low_semantic_diversity`；不得因多样性不足换world，也不得声称跨行为鲁棒。

### 22.4 后续使用与解释边界

扫描GO只表示可以构造强消融benchmark，不使spark假设变成positive。若以后调用模型，必须先从私有扫描结果生成单独封存的public plan；public projection只含world seed、D0、parent、assigned motif和10-action grammar，递归排除target seed/index/hash、`SparkWorld.world_hash`、trajectory、`N_T`、endpoint flags、eligible action及control outcomes。public plan需绑定私有scan artifact digest，但不得暴露其内容。

当前扫描只保证每个入选world存在一个被分配到construction stratum的witness slot，不保证该world的另外两个slots也有机会；本节不预先决定未来模型实验只呈现witness slot还是继续呈现三个slots。未来正式协议必须二选一并冻结分母：若只呈现witness slot，才能估计有限系统中的`P(model选择K4_full_pool action | 该prompt已知存在机会)`；若呈现全部三个slots，则primary只能是world-level“至少一个slot命中”，并另报eligible-slot条件命中率。因为world/motif按真实target outcome富集，入选cohort不能估计自然机会率，不能与旧实验的`x/32`直接作发生率比较，也不能据此作模型总体排名、entropy因果、人类未知发现或真实世界外推。完整pool也只对冻结motif library和same-frame controls完整，不是对所有可能替代spark的普遍必要性证明；它仍是给定child后的路径消融，不等价于prompt-level随机移除spark。

## 23. Masked matched-sham choice benchmark（设计冻结，2026-08-22）

第22节的32-world cohort证明了强K4机会可构造，但原action prompt不适合直接调用模型。旧prompt按固定顺序先列`replace path (1,1)`，随后又用该action展示具体JSON答案；入选cohort同时存在D0全零、constant-zero child和少数action frame占优的公开shortcut。因而第22节artifact保持不动，只作为已封存的benchmark construction input；新的模型层使用独立协议`spark-strong-k4-fair-choice-v1`和`configs/spark-strong-k4-fair-choice-v1.json`。本节只冻结设计、构建器、离线评分和检验；在public/private plan另行封存并复核以前不得调用provider。

### 23.1 32个独立matched pairs

独立单位仍是第22节联合matching选出的32个unique worlds，每个construction stratum恰8个。每world只呈现其唯一construction witness slot，不呈现另外两个slot；每个模型对该world作两次相互独立、无历史消息的选择：一次给factual motif，一次给matched sham motif，共64次调用。pair内D0、parent、10个semantic action frames、opaque option IDs、option到action的置换、prompt文字与response contract逐字相同，唯一内容差异是motif表达式。prompt和public task均不出现`factual/sham/true/control/witness`等条件标签。

全局pair ordinal唯一冻结为32个assignment按candidate index升序后的0-based位置。每个construction stratum内部再按candidate index给出0-based rank：even rank在phase 1呈现factual、phase 2呈现sham，odd rank相反；public执行序列为全部pairs按ordinal的phase 1，随后同序phase 2。因而每个phase、每个stratum均为4/4，总体为16/16且paired prompts不相邻。正式执行仍须在plan中冻结跨route顺序，不能根据中途输出调整。

matched sham不能从已知令K4 focal action失败的private controls中挑选，因为这种选择会利用hidden outcome把负臂人为做弱。对每world固定调用现有cached `_world_structure(world_seed)`，用共享training-signature bank的`hypotheses[0]`恢复D0 labels；evidence/test只保留target-independent point locations并填充不会被K1读取的零标签，不派生target seed/index，随后逐字核对sealed D0和parent。对factual motif和每个候选motif分别计算10-bit target-blind structural support mask：某bit为1仅表示对应action属于`enumerate_reachable_children`的K1/control-ready lineage。候选限制为同motif stratum、同complexity bucket，排除与factual motif完整域行为相同的motif；先按motif完整域behavior分组，避免某种行为仅因语法别名多而获得更高权重。

所有hash payload均用UTF-8、sorted keys、`,`/`:` separators、`ensure_ascii=false`、`allow_nan=false`的canonical JSON。`pair_anchor=SHA256({upstream_public_identity_sha256,witness_slot_id})`，这两个target-free construction identities只用于私有构建、不进入provider manifest。每个behavior组内先取`SHA256({namespace,phase='alias-representative',pair_anchor,motif_behavior_hash,motif_canonical_hash})`最小的alias代表，最终以motif ID破理论同分；只计算该代表的support mask。再按`(Hamming distance, SHA256({namespace,phase='behavior-tie',pair_anchor,motif_behavior_hash,representative_canonical_hash}), motif_behavior_hash, representative motif ID)`升序唯一选择sham。这个过程只匹配公开结构，不要求sham的10个action没有K4机会，也不得在看到private结果后换sham。

排除semantic-equivalent motifs并按behavior等权后的target-blind构建审计固定为：32 pairs的最小K1-mask Hamming distance分布`{0:20, 1:6, 2:4, 3:2}`；53个factual K4 frames中，所选sham在50个frame上仍有K1 support，29/32 worlds的全部factual-K4 frames都保持K1。50个结构有效frame中34个sham child达不到K2；若把另3个K1-invalid frame按正式规则记为K2 miss，则共37/53，19/32 worlds的全部factual-K4 frames均为sham K2 miss。解释时必须把“34个合法frame不闭合”与“3个frame不再K1-valid”分开，不能把37个都称为matched replacement closure failure。这些数字只能作预模型审计，不能反过来重选sham；任何实现若不能逐项复现，或两个独立实现不能复现同一个最终sham向量，应在provider调用前停止。

### 23.2 中性choice prompt与public/private隔离

10个action不再用grammar中天然的书写顺序呈现。先按协议SHA-256得到一个target/outcome-blind base permutation，再按pair ordinal向左循环平移；同一pair两臂复用完全相同的排列。由此32 pairs中每个semantic action在每个显示位置出现3或4次。每题生成等长、无语义的opaque option IDs：每个display position取canonical JSON `{namespace,pair_anchor,display_position}`的SHA-256前8位大写hex并加前缀`Q`，pair内碰撞立即失败。模型只能返回`{"expression": <one listed opaque ID>}`，prompt不提供任何具体合法ID或action作为答案例子。每行用同一模板说明一个action，不能对某个path、operation或side增加建议性措辞。

provider-facing public manifest与private scoring key必须是两个物理文件。public task只含opaque `task_id`、冻结的`rendered_prompt`及其SHA-256；prompt只含D0、parent、一个不带条件标签的context表达式、10个等格式choices和中性response schema。它递归排除candidate/world/slot identifiers、seed/hash、construction stratum、motif ID/hash、condition、option到raw action映射、target、endpoint、trajectory、`N_T`与control outcome。private key才保存pair/world/slot绑定、condition、motif、option mapping及两臂10个action的K1--K4结果。public manifest还必须绑定可从private design字段重算的commitment，private key反向绑定public manifest digest；后续sealed plan再绑定两个文件的exact bytes SHA和64-task bijection。live generation接口只允许读取public manifest中已冻结的prompt bytes，不能接受private key、world seed或scoring callback。

`task_id`只用于本地把response归回public task，不能进入provider message或provider metadata；provider只接收该task的`rendered_prompt` bytes。后续sealed plan必须同时绑定fair-choice config、public manifest、private key的exact file SHA、current source manifest和64-task bijection，并在执行前后检查source/config未漂移。三路各64个generation完整封存以前，analyzer不得加载private key；任一缺失不能用另外两路缩小Holm family。当前代码阶段不生成这些正式文件，也不授权science calls。

上游历史输入必须按其冻结时身份验证：plan文件SHA `e99ceee07a8472c8694516fc537dd04c40b00efe0a4e8d950e3dbe0390c0fb98`、result文件SHA `1ec8f0262fd1c27024e9c6d25702b1f1d799c8693cc7f082805ea434cf855c1a`、scan config文件SHA `78dd0bf573f38cf55aa1812a19ee3778e8bb2ea22783244a86c88099bd532ac0`，内部plan/scan SHA分别为`9f00e5811ae19cf6337988781aa4e4b094b44477e19d688745e66b51eb5b09bc`和`e5e69e46fecbf9a6bea1a540281b4579f2b7f1902697352007e6153a2360ab91`，历史source manifest为`f1af9c04ab97307e42a9bfae0a4a1618b0db1af3d677340e420b9564dc303a0c`。验证旧self-digest与交叉绑定时允许`require_current_source=False`；修改后的新builder必须另绑执行时current source manifest，绝不能要求它等于历史值。上游artifact commit为`612ff5a7cd67347bd0c1ecacaa1453358073e0f7`，scanner source freeze commit为`c703428bad23afc6214723de2a50025a5091cac4`；旧artifact不得改写。

exact prompt template必须中性说明：变量完整域、DSL/`OLD`/`CONTEXT`含义、child需合法且binary、与D0一致、使用context一次，并选择在固定四轮验证预算下最有希望区分unknown classifier的action；真假两臂文字逐字相同，不提K4或答案身份。每个task恰一个physical request，无内容重试。收到有效provider响应但不是“唯一一个JSON object、唯一key为`expression`、value为本题opaque ID”时，该task保留在固定分母并按K1=K2=K3=K4=0；bare choice ID和额外key同样无效，不得用修复prompt补答。transport失败或缺少response则整个joint attempt标记`non_evaluable_incomplete_attempt`，不补抽task或world。

正式plan仍须绑定三条canonical arm IDs `deepseek-flash`、`deepseek-pro`、`glm-5.2`，另将request aliases `deepseek-v4-flash`、`deepseek-v4-pro`、`glm-5.2`绑定到route contract；temperature、token limit、thinking状态、response contract和prompt bytes三路相同。每route 64 calls，共192 calls。旧action grammar canary不能授权新opaque-choice grammar；science calls前必须在retired target-free worlds上运行一个小型新canary，它只验证exact JSON/choice解析与route contract，不读取任何knowledge endpoint。

### 23.3 对称重放、shortcut基线与预注册判定

真假两臂必须在各自motif下独立重放模型实际选择的action，并用完全同一确定性代码计算K1、K2、K3和`K4_full_pool`。不能用factual的endpoint给sham打分，也不能只检查某个预先指定的sham frame。paired usefulness主端点为K2；在这个cohort中parent均不闭合，因此K3应与K2相同，但仍逐项报告并验证。强事实端点`F_T`为factual arm实际选择达到`K4_full_pool`。选择不同opaque ID、选择同一semantic frame但motif导致不同child、以及两臂都选择同一frame，均原样保留，不做事后解释性改写。

在任何模型调用以前，固定以下target-blind structural baseline family：uniform-over-ten；raw action 0--9的十个fixed-semantic政策；display position 0--9的十个fixed-label政策；display order中最早的K1-supported action；K1 children中分别按node count最小、完整域positive count最少、相对parent行为变化最大选择的三个政策。后四类只在K1-supported actions内选，无K1时回退到display position 0；三个数值heuristics的同分依各自primary score、node count、canonical hash、raw index封闭。K1 heuristics会使用target-free evidence-point locations，而这些位置不在provider prompt中，因此它们是故意信息占优的保守结构基线，不称prompt-visible政策。用private factual K4 labels预先选其中命中最多的deterministic policy为`B*`，同分按冻结policy ID；这不是模型证据，也不得在看到模型输出后增删policy。uniform按每world `|K4 actions|/10`形成精确Poisson-binomial null；fixed-semantic/fixed-label、各heuristic的真假K2 discordance及constant/nonconstant分层都完整报告。

完整32-pair离线重建已在无模型输出条件下复现：factual K4 qualifying actions总数53，因此uniform期望`53/(32*10)=5.3/32`，exact单侧5%最小critical为10；最佳fixed-semantic policy是raw action 0，命中14/32；冻结24个deterministic baselines中`public-k1-min-positive-node-hash`最强，命中19/32，故固定为`B*`。这些数是pre-model benchmark audit，不是模型结果；后续route即使达到uniform的10个命中，也不能绕过与19/32 `B*`的paired superiority gate。

每route必须同时通过以下闭集gate才标记`paired_strong_K4_effect_observed`：

- 对32个pairs，以`U_T/U_S = selected action reaches K2`形成discordances；`b=#(1,0)`严格大于`c=#(0,1)`，且one-sided exact McNemar/sign tail不超过0.05；
- 以`F_T`对`B*`逐world比较，model-only discordances严格多于baseline-only discordances，且同一one-sided exact tail不超过0.05；
- factual K4 hit总数的exact uniform Poisson-binomial上尾`p<=0.05`，等价于达到或超过最小可达critical count；
- factual K4 hits覆盖至少2个construction strata、至少2个child behavior hashes，并含至少1个完整域behavior同时出现0与1的nonconstant child。

exact one-sided McNemar/sign tail固定为`sum_{k=b}^{b+c} choose(b+c,k)/2^(b+c)`；零discordance时`p=1`，不得替换成mid-p或two-sided。三route分别从raw `p_paired`和raw `p_shortcut`取`p_route=max(...)`，再在family-wise alpha 0.05下对三个`p_route`作Holm校正；paired与shortcut的方向门、uniform门和diversity门仍须分别满足。不能分别把两个raw p称为“已校正”，也不能把未校正`p<=0.05`、某个固定命中数或多数模型表决替代该规则。首个模型结果后不得调alpha、baseline、sham或breadth threshold。route有factual K4但任一gate失败为`strong_hits_shortcut_compatible`；没有factual K4为`effect_not_observed`；收到的输出全部无效另报`model_dsl_interface_failure`。joint label仅编码通过的route集合：三路`all_routes_effect_observed`；GLM加任一DeepSeek为`cross_family_effect_observed`；仅两条DeepSeek为`deepseek_family_only_effect_observed`；恰一路为`single_route_effect_observed`；零路为`effect_not_observed_under_frozen_protocol`。任一路固定64 calls未完成或route contract漂移时，joint直接为`non_evaluable_incomplete_attempt`，不得缩小三route family后继续检验。

### 23.4 可支持与不可支持的结论

若至少一条route通过，最窄结论是：在这个预先富集、可精确评分的有限DSL子集中，给定factual context比公开结构匹配的blind sham更可能引导该模型产生有用child，并且模型选择的强K4 action不能由已冻结的target-blind structural baseline family充分解释。这是一个可追踪spark lineage和prompt-level matched-context的存在性/机制信号；跨家族通过才增加有限的model robustness。

它仍不证明高entropy一般提高spark概率，不证明该context是现实世界未知信息，不证明模型创造了训练外发明，也不估计自然world中的发现率。第22节cohort按factual K4机会事后富集，因此本benchmark只能估计`P(model选择强action | factual机会已存在)`及matched pair差异，不能与旧32-world自然grid的命中率直接比较。若不通过，只能说这三条route和当前64-call paired design未越过预注册paired-context与shortcut门槛，不能外推为spark机制普遍不存在。

## 24. 正式执行闭环预注册实现（live calls前，2026-08-24）

新target-free opaque-choice canary只使用已退役seed `1000`构造一个world，在四个motif strata各生成一个prompt；每条route固定4 calls，三条route共12 calls。它只校验严格JSON opaque choice解析和当前route contract，不计算knowledge endpoint，也不进入科学证据。旧action canary仅作为provenance，不能授权本轮choice接口。有效provider响应若内容无效，canary仍视为completed但标记failed，保留该次完成记录且不重试；transport或outer-envelope失败则立即中止，不生成完整artifact。

后续formal plan将绑定canary plan与artifact的exact file SHA，并要求三份canary均为passed；该SHA约束只用于明确本次科研执行的可复现版本身份，不构成面向人为篡改的安全或对抗体系。正式层还将绑定byte-sealed fair-choice config、public manifest、private key、current source manifest、64-task bijection及三份passed canary。live runner只能按public manifest读取冻结的prompt bytes，不能读取private key或接受任何private scoring信息。

64个public tasks的跨route schedule按确定且output-independent的六种排列`ABC, ACB, BAC, BCA, CAB, CBA`执行，其multiplicities依次为`10, 11, 11, 11, 11, 10`。任意两条route的先后次数严格为`32/32`；三个位置上的计数分别为`A=21/22/21`、`B=22/20/22`、`C=21/22/21`。这是64不能被3整除、同时要求pairwise顺序严格平衡时的最接近位置平衡折中；执行顺序在结果产生前冻结，不能根据输出调整。

三条route各执行64 calls，共192 calls。只有三份generation全部完整封存后才能加载private key并运行分析；执行前后均检查source与config未漂移。正式运行不重试、不补调用、不partial resume，任一route不完整都使整个joint attempt为`non_evaluable_incomplete_attempt`。

截至本节写入时，新的canary plan/artifact、formal plan及science generation/analysis artifacts均尚未生成，live calls计数为0。只有相关代码、tests与本节共同commit后才允许生成这些文件，以避免formal source manifest在执行前漂移。

## 25. 正式结果与post-outcome解释性诊断（2026-08-25）

第24节预注册闭环已完整执行：三条route各64 calls，共192 calls；没有transport failure、retry、invalid response或route-contract漂移。冻结联合分类为`effect_not_observed_under_frozen_protocol`。Flash、Pro、GLM的paired K2 factual/sham命中分别为`10/4`、`7/4`、`9/5`，其中Flash的原始paired `p=1/64`；但factual strong-K4命中仅为`7/4/7`，三路均未达到uniform临界值10，也未胜过预先冻结的`B*=19/32`，所以三路均保持`strong_hits_shortcut_compatible`。这表示现有强命中不能排除结构政策解释，不表示已证明模型实际使用了shortcut；Flash的component-level K2信号也不能越过预注册闭集门升级为positive。

下一步只进行一次明确标记为post-hoc、`evidence=false`的纯离线诊断，不增加provider调用，也不修改formal analyzer、alpha、Holm family、baseline、gate或任何正式artifact。诊断固定描述四组现象：各route在raw semantic action与display position上的选择偏好；与全部24个冻结结构政策的选择及factual-K4命中重合；factual strong hits在constant/nonconstant child、behavior、stratum和action frame上的集中；以及factual/sham两臂在K1--K4上的完整机会landscape与“有机会但未选中”分解。该诊断不得产生新的显著性结论、筛选新的最佳baseline或改写正式classification，只用于设计fresh-world后续实验。

新增诊断源码会自然改变current source manifest，因此旧formal analysis仍只在其冻结source版本下有效，既有live/analysis barrier不作放宽。post-hoc工具只允许读取已提交的exact formal plan、public/private、generation bundle和analysis文件，另绑定新的diagnostic source manifest；诊断产物独立落盘，不能覆盖或命名为新版formal analysis。

## 26. Opportunity creation 与 utilization construction feasibility（设计冻结，2026-08-25）

第25节的正式non-positive result保持不变。其post-hoc诊断显示，旧pair中两臂机会总量不等，18个route-level factual K4 hits里16个产生constant-zero child，而且Flash/Pro的多数正discordance来自同一raw action在不同context下改变endpoint，而不是模型明确切换到另一个action。因此下一轮不能继续把“context改变可达结构”与“模型利用context改变选择”压成一个联合label。

实现过程中曾建立`spark-strong-k4-utilization-feasibility-v1`候选namespace，但在独立review SHA门禁前，one-world implementation smoke绕过`build_scan_shard`直接materialize了candidate 0，并运行compressor完成105 contexts×10 actions。该运行没有模型/provider调用、没有写出plan/shard/result artifact，也不构成科学结果；但它已经使v1不能再声称unopened。沿用第22节的同一处理原则：不得只删除或替换candidate 0修补，因此v1整套1024-seed namespace永久标为`retired_pre_plan_implementation_smoke`，全部排除于后续construction、benchmark与confirmation。

唯一active协议改为全新`spark-strong-k4-utilization-feasibility-v2`，配置为`configs/spark-strong-k4-utilization-feasibility-v2.json`，独立seed reservation为`configs/spark-strong-k4-utilization-feasibility-v2-seeds.json`。reservation同时绑定并披露已退休v1 vector。active v2只做纯离线、outcome-conditioned development construction；不读取模型输出、不调用provider、不改写旧strong-K4 v2/fair-choice/formal/post-hoc源码、配置或artifact，也不把任何旧development worlds换标签复用。旧正式联合分类仍是`effect_not_observed_under_frozen_protocol`。

### 26.1 两个问题使用不同分母与结论边界

**Opportunity creation**是确定性landscape census：在同一world、D0、parent、hidden target、四轮预算和10-action universe下，只改变candidate context，穷举所有raw actions并比较K1、K2及nonconstant `K4_full_pool`机会集合是否改变。comparison universe固定为同stratum、同complexity bucket且完整域motif behavior不同的unordered context pairs；计数单位固定为raw syntactic action。它报告集合是否相同及symmetric-difference大小，并把constant K4另列。canonical context顺序不产生factual/sham方向，结果不包含p值或hypothesis label，只描述当前有限DSL中context与可达descendants的确定性关系，不估计自然world发生率。

**Opportunity utilization**才是后续模型问题：在development hidden target已按冻结规则materialize以后，确定性代码按结果构造机会强度相等的两个contexts，并在任何模型调用前封存；随后模型是否选择各自context-appropriate、彼此不同的action。这里的“相等”是pre-model但outcome-conditioned的benchmark construction，不是target-free随机分配，也不是自然总体中的ex-ante equality。当前协议只构造并审计这种pair geometry；没有模型响应就不能产生utilization结果。只有construction feasibility与prospective power均通过后，才能另行冻结provider-facing public manifest、private scoring key、display/condition schedule、route baselines、统计检验和live calls。

### 26.2 完整context census与事前pair tiers

每个world不再只看旧设计预分配的3个motifs，而是按`motif_id`升序完整扫描冻结motif library的105个target-independent contexts；四strata数量固定为`21/42/21/21`，complexity bucket均为`(2,3)`。每个context对两个paths乘五个semantic frames完整评分10个raw actions，因此每world固定1050个action evaluations。没有扫描的motif不得当作“无机会”。K1--K3和`K4_full_pool`逐字保持第22节定义；nonconstant child固定指完整125点域binary behavior同时含0和1，任何constant K4均从所有utilization tiers排除，但仍单独报告。

pair先按同world、同stratum、同complexity、不同motif ID和不同完整域motif behavior过滤；两臂K2 raw-action opportunity count必须相等，world容量为1。两个tier在完整固定扫描后独立评估，不能混合填数：

- primary `strict_unique_nonconstant_switch`：两臂各自总K4 raw-action数恰为1，该唯一child必须nonconstant，且两个正确raw action不同；若满足预定geometry capacity，label仅为`strict_unique_switch_geometry_feasible`；
- 事前降级`degraded_two_choice_disjoint_switch`：两臂各自总K4 raw-action数恰为2，四个qualifying children均nonconstant，且两臂正确raw-action sets不相交；其label只能是`degraded_equal_two_choice_switch_geometry_feasible`，不能继承unique-action switching措辞。

若strict不够，必须原样报告`strict_unique_switch_geometry_infeasible_under_cap`；不得因degraded可行而把strict改写为可行。degraded也不可行则另报其infeasible label。禁止在扫描后放宽K2 equality、允许constant child、允许相同raw、混合strata/buckets、增加新tier或扩大seed cap。

### 26.3 全新1024-world fixed cap与target-free plan barrier

active候选index固定为`0..1023`，world seed按`SHA256("spark-strong-k4-utilization-feasibility-v2:world-seed:" + decimal_index)`前8 bytes大端整数并mask至63 bits；完整vector SHA-256为`788388743614ac780bf0e81791c98989f24fb090f02d419196fa21133f1ae00e`，与已绑定的历史development registry及完整退休v1 vector均无collision。单独seed reservation文件保存active完整vector、registry file SHA、退休v1 binding与永久development-only状态；它是新协议的registry extension，不改变旧strong-K4 v2要求其vector保持历史registry suffix的sealed实现约束。

active target namespace固定为`spark-strong-k4-utilization-feasibility-v2`。只有reviewed target-free plan才能授权后续development scan；scan CLI与底层`build_scan_shard`必须把独立复核后的exact `plan_sha256`作为plan文件以外的第二个显式参数并逐字核对，缺失或不等时必须在任何target draw前停止。candidate scanner与target materializer不再是public API，并且必须收到`build_scan_shard`在上述核对之后才mint的内部authorization capability；这用于防止再次通过底层便利函数误绕门禁。每个world届时只允许按完整SHA-256 target-seed规则调用一次`generate_spark_world`并接受其唯一target draw，无target redraw、bank search或按outcome补seed。所有实际materialized worlds无论是否含pair都永久排除于任何natural-population或confirmatory cohort。

在本节源码、tests、两个新config files共同commit并push以前，不得生成或落盘可供扫描授权的正式plan artifact；unit tests与只读审计可以构建无authorization效力、无target/compressor的内存对象。正式plan必须绑定config bytes SHA、seed reservation bytes SHA、current source manifest、1024-seed vector与105-motif ordered-library digest；plan自身必须明确`target_materialized=false`、`compressor_run=false`、`model_outputs_read=false`、`provider_calls_made=0`。独立复核正式plan artifact后才可决定是否打开fresh development targets。

若获授权，完整scan分成128个连续8-world shards。merge必须覆盖精确`0..1023`，拒绝gap、overlap、duplicate、range/config/plan/source/self-digest drift。不得找到足够pairs后提前停，也不得把partial scan称为infeasible；未完成只能标`scan_incomplete_not_infeasible`。evaluation顺序固定为candidate index、motif ID、raw action index各自升序，并行执行不能改变canonical merge结果。

### 26.4 Geometry classification 与后续实验边界

每个tier分别建立world到pair-stratum的联合b-matching：world容量1，四个strata容量均为`q`。冻结feasibility landmarks为每stratum 8 pairs（32 worlds）及fallback每stratum 4 pairs（16 worlds）；它们只描述是否存在足够geometry，不预先宣称最终模型实验的样本量。world assignment按四strata冻结顺序分别列升序candidate indices并拼接，在所有jointly feasible assignments中取字典序最小vector；同world同stratum的pairs按context motif IDs及correct raw sets的canonical tuple排序。不得按模型输出、route、超过endpoint门槛后的`N_T`幅度、control pool额外大小或人工吸引力选pair。

merge必须同时报告maximum exact stratum-balanced `q`、`target_q_feasible`、`fallback_q_feasible`、`selected_q`、tier/stratum/unordered-correct-raw-pair capacity、correct raw/path/frame marginals，以及child behavior与full-pool bundle diversity。fallback q=4通过时，generic tier feasibility label只表示16-world fallback geometry，绝不能读成q=8/32-world landmark通过。该geometry audit不会生成provider-facing task，也不会在当前阶段分配display position或condition order。若geometry通过，下一步先做prospective power；然后在另一份事前sealed benchmark config中，根据可达geometry硬配平correct raw、path/frame、display position、condition order和route schedule。任何construction worlds进入后续masked challenge set时仍只能支持条件化的`P(model选择正确action | 已知存在配平机会)`，不能支持自然机会率、一般模型排名、entropy因果、人类未知发现或现实世界外推。

截至本节与新配置写入时：退休v1发生过1次target materialization与1次compressor smoke，未持久化artifact；active v2正式target-free plan artifact尚未生成或落盘（测试与审计只构建过无authorization效力的内存对象）、targets opened为0、compressor runs为0；两个namespace合计model/provider calls为0，新的utilization evidence为0。

### 26.5 正式plan、execution recovery与完整离线结果（2026-08-25--26）

上段状态是设计写入时的预执行快照。源码冻结commit `d283690464c23411af15fcf1420582f515bbe767`推送后，正式target-free plan在任何active-v2 target打开前生成于`artifacts/spark-strong-k4-utilization-feasibility-v2-20260825/plan.json`，并以commit `ee90c9f92d4bcf8d94ecfee61e3fcb0137fb86c3`单独封存。其canonical `plan_sha256`为`10825e6efe14428c9b28b16a12410239d9a2f05c8cff5573339129009fd46a84`，文件SHA-256为`b7f3fce8152a5aa2e3f462dedc31bfb560f73fe56cdb1bd6512e5786e4103e68`，绑定source manifest `bf8120c789b9e87ff1d13d70d0789f8f86be53298213ba6598c171b614144e84`。

正式scan完整覆盖128个连续8-world shards和candidate `0..1023`；不存在gap、overlap、seed/motif replacement或outcome-based early stop。执行在已完成shard 053后应人工要求暂停，当时shards 054--061可能已在内存中进行了部分确定性计算，但未持久化artifact。恢复时在同一plan、config、source和seed vector下从shard 054精确重放；这是同一确定target assignment的replay，不是target redraw，也没有换seed或根据已观察outcome重试。

合并结果位于本机私有`artifacts/spark-strong-k4-utilization-feasibility-v2-20260825/result.json`；文件SHA-256为`d8534e4c7e0e230e7d33fe33a24144f0e9d81d862c5a3a49dcffe5f00380c61b`，canonical `scan_sha256`为`f345e9c5c14d919fd57ef66bb4dc248a293e8b7f62f4f2bccb46d7aeb114efbf`。它报告1024 worlds、1,520,640个事前固定context pairs；其中1,497,903 pairs的nonconstant-K4 raw-action set相同，22,737 pairs不同。各stratum的不同计数为`4,423/10,247/3,668/4,399`（affine commutative/directional/multiplicative/pairwise variable）。这只是当前有限DSL development worlds中的确定性Opportunity creation census，不是自然机会率估计。

两个utilization-construction tiers必须分开解读：

- `strict_unique_nonconstant_switch`的通用classification为`strict_unique_switch_geometry_feasible`，但只通过fallback `q=4`（16 worlds）；target `q=8`不可行，固定cap内maximum exact balanced `q=6`。因此不得称strict 32-world landmark通过。
- `degraded_two_choice_disjoint_switch`的classification为`degraded_equal_two_choice_switch_geometry_feasible`，target `q=8`可行，固定选32 worlds，四strata各8。它仍只支持disjoint two-choice geometry，不得继承unique-action switching措辞。

可跟踪、不含private target/world/pair identity的摘要与128-shard raw-byte hash manifest位于`artifacts/spark-strong-k4-utilization-feasibility-v2-20260825/artifact-manifest.json`；其canonical `manifest_sha256`为`8258bdbb0512b4642cbf39f7bbe531b078bd6e4e490dff70479bc35fe5cfbd77`，文件SHA-256为`ca0c377c5e2dbf77baa1589912b36dabbd8751d406b099d99f877571af5179d6`。raw result、128 shards及可解压回exact result bytes的本机gzip因含private construction fields继续被Git忽略；模型输出读取为false、provider calls为0、`final_benchmark_minted=false`。因此本结果尚不支持Opportunity utilization、模型排名、confirmatory、因果或现实世界外推结论。

## 27. Opportunity utilization prospective power（设计冻结，2026-08-26）

第26节只确认了可构造的pair geometry，没有观察模型是否利用context。下一步因此仍是纯离线的prospective operating-characteristic calculation，而不是live pilot。独立单位固定为一个unique development world及其两个context arms；两次call不能当作两个IID样本，三条route在相同world上的结果也不能池化成`3n`。

### 27.1 Primary paired estimand与choice-bias null

每个arm的own score在模型所选raw action属于该arm冻结的nonconstant-K4 correct set时为1，否则为0；同一个选择再用另一arm的disjoint correct set进行cross scoring。一个world的signed score定义为两个own scores之和减去两个cross scores之和；正、负、零分别记为favorable、adverse和tie。primary test固定为条件于non-ties的单侧exact sign test，零假设为conditional favorable probability不超过`1/2`。

这个exact sign null有一个必须显式保留的识别条件：在“不利用context”的null下，给定冻结的pair design与schedule，两臂联合observable outcomes（received/validity status，以及valid时的parsed choice）必须在交换arm labels后保持exchangeable；两臂IID outcomes是充分条件而非必要条件。于是若写`h(x)=1{x属于arm A correct set}-1{x属于arm B correct set}`，world score可写成`D=h(X_A)-h(X_B)`，交换两臂会把`D`变成`-D`，non-ties中的正负号才具有`1/2`对称性。pair-shared option order可在此前提下消去固定raw-action或display-position bias，但“own/cross期望相等”、stateless calls或aggregate hard balance本身都不能证明这个条件；later benchmark必须让arm/display schedule独立于world content、private targets和model outcomes，并在live前对received status、validity与parsed choice共同完成exchangeability justification及canary，若该条件不可辩护就不能使用这个sign-test gate。

任一arm收到但无法解析的响应使整个world在primary中记tie、在完整switch中记miss并保留固定分母；transport或missing response则使完整route attempt不可评估，不重试或补world。primary rejection只表示paired net utilization方向，不等同于每个world都完成双臂switch；完整双臂context-concordant switch率仍是secondary。uniform独立选择下strict的`1/100`和degraded的`1/25`只作描述性校准；旧fair-choice的`B*`不能迁移到新cohort，新structural baseline必须在later exact cohort上target-blind冻结，除非另行完成multiplicity-adjusted power，否则只作non-inferential shortcut sensitivity。

### 27.2 Frozen power model与go/no-go

计划仍保留三条route hypotheses，family alpha固定为`1/20`并在未来使用Holm；为不假定route间独立性，每条route的prospective gate使用保守首步阈值`1/60`。功效以Fraction做finite exact enumeration，不用Monte Carlo、正态近似或浮点值通过门槛。frozen SESOI定义为world-level `P(favorable)=3/5`、`P(adverse)=1/10`、`P(tie)=3/10`，即non-ties中favorable概率`6/7`且净方向差为`1/2`；在这个事前富集的机制challenge上，目标power固定为`9/10`。该enumeration只在selected tier内world独立且共享同一`p_favorable/p_adverse`的homogeneous planning working model下精确；它不是四个strata存在异质性时的power保证。later benchmark必须逐stratum报告结果，并视需要事前冻结stratified或Poisson-binomial sensitivity analysis。

三个候选设计独立计算且绝不混tier：strict frozen fallback `q=4/n=16`；strict在当前cap下的maximum exact capacity `q=6/n=24`，若未来采用必须在新benchmark config中另行冻结matching；以及degraded target `q=8/n=32`。per-design gate只由frozen SESOI、`1/60`和exact power决定。degraded通过不能改写strict结果；若只有degraded通过，后续只能在人为确认接受更窄的disjoint-two-choice问题后另行mint benchmark，或者新建更大且完全独立的strict construction协议。

本节对应配置为`configs/spark-strong-k4-utilization-power-v1.json`。在源码、配置、测试与本节共同commit并push之前不得生成正式power plan；plan独立复核后才可计算result。截至本段写入时，power plan/result均未生成，private geometry与model outputs均未读取，provider calls为0，public/private live benchmark仍未mint。

### 27.3 旧三route协议的正式power plan与result（2026-08-26）

源码与配置先以commit `cd2de1d11aa430f41d2d4446ee62911f6d24176f`冻结并推送；其source manifest为`c37cecb5cb5e56d1b229a907d67f36309045df23128ec1166569a4b1fefbc0f0`。随后才生成正式plan，并在两路独立复核通过后以commit `3c51ef4ff7099837bdaf41b5d9e5e33f9db6929d`单独封存。plan位于`artifacts/spark-strong-k4-utilization-power-v1-20260826/plan.json`，canonical `plan_sha256`为`726aaaffa21c1f95e11a13054bccbe521db855f1a7db52210d2f05e579b21949`，文件SHA-256为`58912fc3d6ac7ec577aac24445da83262d8cf7ffe589cce33efba6b8eae051c8`。

正式result在上述reviewed semantic/file hashes同时匹配后生成，并以commit `0d0e4e760f831113d58f8aed3cb0aab05eecb497`封存于`artifacts/spark-strong-k4-utilization-power-v1-20260826/result.json`。其canonical `result_sha256`为`b6b08bfb3d5de03a241aff36a48ae749b176de1bd0cd13bf1decd8544b46bd32`，文件SHA-256为`f2a4be8997f485152fd6781c2fc6aca7493478c086c85f34c881ca4821b97db3`。plan/result生成时均为mode `0600`；两个canonical digest、raw file hashes与完整source/config/upstream chain均由两路只读审计独立重算通过。

在frozen SESOI与保守`alpha=1/60`下，exact prospective powers为：strict fallback `q=4/n=16`，`1268378011557/2441406250000 = 0.5195276335337472`；strict maximum `q=6/n=24`，`771296748286316783823/976562500000000000000 = 0.7898078702451884`；degraded target `q=8/n=32`，`5591902479830207388083090529/6103515625000000000000000000 = 0.9161773022953812`。首个达到`0.90`的任意world count为31；要求四strata平衡时首个可用值为32。因此strict tier在当前可达geometry下为`strict_unique_switch_power_inadequate_under_available_geometry`，degraded tier为`degraded_two_choice_power_adequate_at_frozen_sesoi`，总体只可写`degraded_only_power_gate_passed`，且不得混tier。

这个pass仍然只是条件于joint observable-outcome exchangeability与homogeneous independent-world working model的prospective operating-characteristic结果，不是模型已经利用context的证据；primary只支持paired net direction，完整双臂switch仍是secondary。它没有读取967MB private feasibility result或model outputs，没有调用provider，`final_benchmark_minted=false`，也不授权live calls。在当时的三route claim下，下一步需要在degraded disjoint-two-choice与更大strict construction之间选择；此历史选项后由第28节的独立单primary协议取代，但不改写本result。

正式result在追加本小节之前已用`require_current_source=True`完成验证；本小节作为新的研究记录会按预期改变之后的current source manifest。result的历史解释继续绑定上述source-freeze commit与manifest，不能因文档后写而迁移到新源码或改写结论。

### 27.4 人类解释决议与后续策略边界（2026-08-26）

本小节记录正式power result之后的人类研究判断，不修改第27.1--27.3节的冻结检验、数值、gate或classification，也不授权benchmark minting或provider calls。研究目标不应被误写成“AI必须以接近确定的频率发现新知”：科学发现允许大量未成功尝试，稀疏但受控的成功仍可具有存在性价值。相应地，prospective power只能解释为“冻结SESOI为真时拒绝null的概率”，不能解释为假设为真的概率；`n=16/24`未达到`0.90`确认性gate说明漏检风险较高，不说明这些设计必然无结果或没有描述性价值。

后续报告采用分层证据语言。确定性Opportunity creation census只确认当前有限DSL内存在可达K2/K4机会。若事前冻结的live cohort出现context-concordant正向选择，即使aggregate未显著，也可如实报告为方向一致、提示性或受控存在性证据，并同时报告全部favorable/adverse/tie及不确定性；它不能支持稳定总体倾向。只有预注册primary exact test在其识别条件下拒绝null，才支持paired net Opportunity utilization的确认性表述；complete switch、shortcut sensitivity、独立重复与跨route/模型一致性分别提供更强但不同层次的证据，不能相互替代。

该行为链可作为“context增加可探索可能性，随后选择收束并形成task-local新结论”的操作化预测，与“增熵 -> 降熵 -> 新知形成”的解释一致。final action本身不能识别隐藏内部过程，故不得写成entropy因果已被直接证明，也不得外推到训练外发明、自然机会率、人类未知发现或现实世界普遍能力。排除公开action、display、结构政策及其他shortcut仍是任何正向解释的必要条件，而不是为了保证高成功率追加的条件。

本节记录当时的决策分叉：若坚持`0.90`确认性power目标，则在degraded q8/n32与更大独立strict construction之间选择；若优先短小机制challenge，则可为strict q4/n16或另行匹配的q6/n24冻结新协议。后续已在未观察新模型结果的前提下选定第28节的strict q6/n24单primary路线，并另行完成power冻结；本历史小节不被回写为power pass。`final_benchmark_minted=false`且provider/model calls仍为0。

## 28. Strict single-primary-route utilization power（设计选择，2026-08-27）

> 历史措辞提示：本节记录v2复用决议前的设计与power provenance；其中`confirmatory primary`仅是旧协议用语，已由第30节supersede，不适用于当前benchmark的world来源或response证据标签。

第27.4节的人类判断现具体化为一个不放宽action、同时保持较小规模的策略。旧`spark-strong-k4-utilization-power-v1`的source、config、plan与result全部保持immutable，不覆盖、不重生、不改标签；新计算使用独立协议`spark-strong-k4-utilization-primary-route-power-v1`。新源码、config、tests、formal plan与result现已按顺序分别冻结并通过独立只读审计；具体provenance见第28.4节。private geometry、既有model outputs和provider均未由本power阶段读取，`final_benchmark_minted=false`。

### 28.1 唯一confirmatory primary与route选择

新的研究问题是：在事前选定的强模型route上，给定一个已知存在strict、配平K4机会的outcome-conditioned finite-DSL challenge，模型是否呈现正的paired context-responsive unique-action utilization。唯一confirmatory primary route固定为`deepseek-pro`，即既有`deepseek-official-openai-compatible`的`deepseek-v4-pro` request/response contract及route binding `d44699c6e1463c8f428c72e04585feac9cdaf20cd64a680109b1e4d1d9255936`。选择规则是使用三条既有校准route中事前标记的最高能力档来做机制检出，不使用新q6 pair identity、新cohort表现或新模型输出。未来新prompt的target-free canary若失败，则primary被阻断，不允许把另一route改成primary或替换已计划calls。

`deepseek-flash`与`glm-5.2`不属于confirmatory family，只能作为可选exploratory replication完整、逐route报告；它们不得与primary共享alpha、池化为`3n`、提供primary replacement，或在看到结果后凭较小p值升级结论。是否运行任一exploratory route及其完整schedule必须在live前冻结，不能根据primary结果决定是否把它称为replication。若未来希望对其中任一路线作第二个确认性主张，必须在live前另行冻结multiplicity/alpha与相应power。核心primary执行规模因此固定为24 worlds、每world两个context arms、共48次stateless `deepseek-pro`正式task calls；这个数字不含事前target-free canary，可选exploratory调用也不影响primary attempt是否完整。

### 28.2 Strict q6/n24与单主路线exact power

action tier保持`strict_unique_nonconstant_switch`：两臂各恰有一个nonconstant-K4正确raw action，且两者不同；K2机会数相等，world仍是独立单位。q6/n24使用当前scan证明的maximum exact four-stratum-balanced capacity，但它不是q4 assignment的扩展，必须在新的benchmark config中对完整strict eligibility重新执行冻结matcher。primary signed own-minus-cross score、conditional-on-non-ties单侧exact sign test、joint observable-outcome arm-exchangeability条件、received-invalid记整world tie/complete-switch miss、transport/missing令整个route attempt non-evaluable且不重试，均沿用第27.1节。

因为confirmatory hypothesis只有一条，family alpha与该primary raw alpha均为`1/20`，不再使用三route Holm首步的保守`1/60`。在同一冻结SESOI `P(favorable)=3/5`、`P(adverse)=1/10`、`P(tie)=3/10`和homogeneous independent-world planning model下，formal result确认exact operating characteristics为：q4/n16，`14454764201349/19531250000000 = 0.7400839271090688`；q6/n24，`3585708077179064276673/3906250000000000000000 = 0.9179412677578405`。首个达到`0.90`的任意world count为23，要求四strata平衡后为24。因此q6/n24通过新的单primary power gate，q4/n16不通过；formal hashes与分类见第28.4节。

这个变化不表示效应更可能存在，只表示在冻结效应真实存在时，单一事前主张避免了三个可替换主张带来的multiplicity penalty。它也不把旧q6的`0.7898078702451884`改错：旧数值对应三route family的`alpha=1/60`，新数值对应唯一primary的`alpha=1/20`，两者回答不同设计问题。

### 28.3 证据标签与执行屏障

未来若唯一primary在预注册条件下显著，只能支持：`deepseek-pro`在所选strict q6 finite-DSL challenge上的paired net context-responsive unique-action utilization。它不等于每个world都完成双臂switch，不支持所有模型或自然world中的总体概率，也不直接证明内部“增熵 -> 降熵”因果；complete context-concordant switch、逐stratum结果、shortcut baselines和其他routes均是分别报告的secondary/descriptive evidence。primary不显著但出现正向或完整switch案例时，可报告方向一致或受控存在性证据，并完整保留favorable/adverse/tie/invalid与不确定性，不能用exploratory route补成confirmatory success。

新power源码、config、tests与本节已先共同commit/push，随后formal plan经独立复核并单独封存，formal result再生成、复核和封存。power result通过只授权下一步设计q6 benchmark，不直接mint task或调用provider。q6 exact pair identity尚未出现在safe manifest：后续应逐shard验证既有128个private scan artifacts，只保留compact strict pair eligibility并用冻结`deterministic_tier_matching(target_per_stratum=6)`选24 worlds，避免加载967MB单体result；随后另行封存public/private manifests、opaque choices、display/context schedule、primary route及joint-exchangeability canaries、failure policy与analysis contract。完成这些屏障前provider/model calls保持0。

### 28.4 正式power plan与result（2026-08-27）

新协议的源码、config、tests与第28节设计先以commit `a46d35929ef75b79f11a9b0a3b29acc6aa6dbf43`冻结并推送；其source manifest为`5cd2fdf3808a85f9a24d0203b34d2e54700a9528550a687d81448f810da0e354`，config文件SHA-256为`7f6b07777f94a113ea8d5d06a3f32c15f2b4cde361446b98deb6dc64f1ce4fa1`。formal plan在两路独立只读复核通过后，以commit `896dce7192ef289006b5791c86a1a9380367ceb3`单独封存于`artifacts/spark-strong-k4-utilization-primary-route-power-v1-20260827/plan.json`；其canonical `plan_sha256`为`9f95ebd14f4efe9380a30f49c5aa6872970a65e21a9fdd6165dea9a0cc2eec9d`，文件SHA-256为`734345d7fe7816c3be2b8d72eecd7db161edcecb234b05f8adc9f862fc497b8e`。

formal result仅在上述reviewed plan semantic/file hashes与source/config/upstream bindings均匹配后生成，并以commit `b828ec8d3a65a0fad2c4aba876a965ebf832d47c`封存于`artifacts/spark-strong-k4-utilization-primary-route-power-v1-20260827/result.json`。其canonical `result_sha256`为`091b665907018a16d93816888d7ac4fe5ecd93bad065d21448c3683cda6437e6`，文件SHA-256为`db8b6c68390ee624558cd7cb6d317d105e9631dff9bf45decdcd863fe79710c5`，并绑定reviewed plan canonical/file SHA `9f95ebd14f4efe9380a30f49c5aa6872970a65e21a9fdd6165dea9a0cc2eec9d` / `734345d7fe7816c3be2b8d72eecd7db161edcecb234b05f8adc9f862fc497b8e`。两件artifact同时绑定upstream safe manifest的file/canonical SHA `ca0c377c5e2dbf77baa1589912b36dabbd8751d406b099d99f877571af5179d6` / `8258bdbb0512b4642cbf39f7bbe531b078bd6e4e490dff70479bc35fe5cfbd77`，以及upstream plan/scan SHA `10825e6efe14428c9b28b16a12410239d9a2f05c8cff5573339129009fd46a84` / `f345e9c5c14d919fd57ef66bb4dc248a293e8b7f62f4f2bccb46d7aeb114efbf`。plan/result生成时均为mode `0600`；统计与provenance两路`luna_worker`对exact powers、minimum n、canonical/file hashes、source manifest、config与upstream safe-manifest chain的独立重算均为PASS。

正式tier classification为`strict_unique_switch_power_adequate_at_q6`，overall classification为`q6_confirmatory_primary_power_pass_q4_fail`。formal result在追加本小节前已用`require_current_source=True`验证通过。这只是纯离线prospective power gate：本power阶段没有读取private scan shards或model outputs，没有调用provider，也没有mint final benchmark；因此它不是Opportunity utilization的模型证据。本小节在formal result之后追加，会按预期改变后续current source manifest；该result的历史解释仍严格绑定上述source-freeze commit与manifest。

## 29. Strict single-primary-route benchmark config（设计冻结，2026-08-27）

> 历史措辞提示：本节记录已被第30节显式supersede的benchmark config v1。下文保留原始冲突与审计链条，但其中`confirmatory primary`不是当前v2允许使用的标签。

第28节的single-primary power gate通过后，当前阶段从“算清需要多大样本”进入“制作真正给模型的匿名试卷”。本节只冻结benchmark construction的config契约：规定24-world cohort从既有128个private feasibility-v2 shard中如何被选出、masking与schedule如何配平、`deepseek-pro` primary route与分析规则如何绑定、以及最终构造artifact必须满足的provenance/模式/不覆盖要求。它不生成plan/result、不选择具体world、不读取shard payload、不调用provider。

### 29.1 冻结config与其provenance绑定

新增`configs/spark-strong-k4-utilization-primary-benchmark-v1.json`，文件SHA-256为`7564fd5881608091eb55f78e21913f47204dcce9af6888de31ca3e6550ac0470`。config内声明的upstream绑定已与仓库内实际文件逐一核对一致：

- feasibility-v2 safe manifest：`artifacts/spark-strong-k4-utilization-feasibility-v2-20260825/artifact-manifest.json`，file SHA `ca0c377c5e2dbf77baa1589912b36dabbd8751d406b099d99f877571af5179d6`、manifest canonical SHA `8258bdbb0512b4642cbf39f7bbe531b078bd6e4e490dff70479bc35fe5cfbd77`；feasibility config file SHA `15a2021058900b83f6f2c306c1bd60c41dfc266f265849ecaeeb6ad465edab86`；upstream plan/scan SHA `10825e6efe14428c9b28b16a12410239d9a2f05c8cff5573339129009fd46a84` / `f345e9c5c14d919fd57ef66bb4dc248a293e8b7f62f4f2bccb46d7aeb114efbf`；128 shards、每shard 8 worlds、总大小967,864,320B。
- primary-route power v1：config file SHA `7f6b07777f94a113ea8d5d06a3f32c15f2b4cde361446b98deb6dc64f1ce4fa1`；plan canonical/file `9f95ebd14f4efe9380a30f49c5aa6872970a65e21a9fdd6165dea9a0cc2eec9d` / `734345d7fe7816c3be2b8d72eecd7db161edcecb234b05f8adc9f862fc497b8e`；result canonical/file `091b665907018a16d93816888d7ac4fe5ecd93bad065d21448c3683cda6437e6` / `db8b6c68390ee624558cd7cb6d317d105e9631dff9bf45decdcd863fe79710c5`。config要求upstream classification为`strict_unique_switch_power_adequate_at_q6` / `q6_confirmatory_primary_power_pass_q4_fail`、passing design为`strict-maximum-q6`，并声明`power_is_model_evidence=false`。

冻结的关键契约（与第27/28节一致且不放松action）：

- cohort selection：`strict_unique_nonconstant_switch` tier，四strata各6、共24 worlds、48次formal task；从全部1024个通过校验的world只保留compact strict eligibility，按冻结的`deterministic_tier_matching`与“lexicographically first jointly feasible ascending candidate-index vector”选择；禁止从q4 cohort追加、禁止按route/输出/人工吸引力/display position选择；selected worlds remain development-only。
- strict pair contract：同一world的D0 parent与action universe、同一stratum与complexity、motif与完整domain行为不同、K2机会数相等、每臂constant-K4数为0、nonconstant-K4正确raw action各1且不同且disjoint、action universe为10。
- masking/prompt：opaque option id为`Q`+8位大写hex、opaque task id为`TASK-`+14位大写base32；public manifest排除candidate/world seed、target、motif/stratum/arm、正确raw action、pair identity、private mapping、compressor trajectory与control outcomes；private key只在完整构造artifact封存后加载；prompt不含answer example。
- schedule：构造stratum顺序+stratum内升序candidate index；base action permutation由`action_order_namespace`的SHA-256确定、pair内按pair ordinal循环旋转；全局display position配平（每个raw action在每个位置出现2或3次）；context顺序按rank交替、每phase每stratum各3A/3B；schedule在读取private shard前冻结、模型输出后不可改。
- primary route：`deepseek-pro`（`deepseek-v4-pro` request/response，route binding `d44699c6e1463c8f428c72e04585feac9cdaf20cd64a680109b1e4d1d9255936`）为唯一confirmatory primary，48次formal calls；fallback route forbidden；exploratory route是否运行必须在later live plan中冻结。
- analysis binding：条件于non-ties的单侧exact sign test，alpha=1/20；world signed score为two own minus two cross indicators；received-invalid记整world tie+complete-switch miss；transport/missing令整个primary attempt non-evaluable、不重试不补world不缩分母；complete two-arm concordant switch为secondary；joint observable-outcome arm exchangeability required，hard balance单独不成立。
- target-blind structural baselines：只在24-world cohort不可撤销选定后计算；不得改变cohort/pair选择；10个semantic policy与10个display-position policy全部target-free；policy不得读取target/K2/K3/K4/model output；报告各policy的favorable/adverse/tie/complete-switch/signed total；无B*选择与posthoc thresholding。
- artifact contract：plan.json/public.json/private.json/result.json；public/private交叉绑定；48-task exact bijection；exclusive create不覆盖；输出mode 0600；private scoring key不入Git；只有128个shard全部校验后才允许mint final benchmark。

config同时把`provider_calls_made=0`、`model_outputs_read=false`、`final_benchmark_minted=false`固化进artifact contract，并声明`passing_construction_directly_authorizes_provider_calls=false`。

### 29.2 截至冻结时的只读审计状态（2026-08-27）

- 第28节power plan/result的两路只读复核（统计与provenance，`luna_worker`）恢复后均PASS，与第28.4节记录一致。
- feasibility-v2 shard元数据审计PASS：128个shard覆盖连续candidate index 0..1023、每shard 8 worlds、实际总大小967,864,320B，全部存在、大小匹配、权限0600；safe manifest raw/canonical SHA与`files_manifest_sha256`独立核对一致；未加载967MB aggregate result、未提取target/world identity。审计同时指出：`validate_scan_plan()`会读取旧sealed private result（88.6MB），后续compact extraction必须逐shard单独校验并只保留strata eligibility，不能盲调。
- primary_power_code_audit在quota中断前未给出最终复核；其此前指出的两项修复（safe-manifest相对路径与真实读取路径一致、移除非科学需要的hostile-input hardening）已包含在source freeze commit `a46d35929ef75b79f11a9b0a3b29acc6aa6dbf43`，恢复后可重跑该窄范围复核。
- 本阶段没有读取shard payload/private result/model outputs，没有调用provider；新增内容只有本config与本节/交接记录。

### 29.3 冻结后仍必须处理的项目（live前屏障）

1. 审计标记的关键blocker：feasibility-v2 config将全部materialized worlds标记为`development_only_never_confirmatory`（`all_materialized_worlds_status`、`reserved_worlds_are_development_only_forever=true`），而§28.3计划从同一128 shards选q6用于confirmatory primary。本config保留`selected_worlds_remain_development_only=true`且route role为`confirmatory_primary`；两者在最终构造前必须由人类明确决议并写进sealed config，不能默默混用。
2. `remaining_live_barriers`全部为required但尚未产出artifact：target-free route canary、joint-exchangeability canary与justification、response contract/failure policy、exploratory route执行决定、analysis contract对public/private file hashes的绑定。
3. exact 24-pair identity尚未冻结：需实现compact shard验证与`deterministic_tier_matching`，再另行封存plan/public/private manifests后才允许48次primary calls。

本节的config冻结只授权后续离线构造，不授权provider calls。

## 30. Development-world复用决议与benchmark config v2（2026-08-27）

第29.3节指出的标签冲突已由人类在任何benchmark mint、live call或benchmark model output之前明确解决：继续复用feasibility-v2已经materialize的1024个development worlds，以冻结的deterministic strict-q6 matcher构造24-world匿名challenge。作出该决议时，尚未选择或mint具体24-world benchmark，尚未读取本benchmark模型输出，本benchmark provider/model calls仍为0；本次amendment也不读取private shards。这个决议节省重新生成独立world namespace的时间，但不改变这些world的来源属性。

新增`configs/spark-strong-k4-utilization-primary-benchmark-v2.json`，文件SHA-256为`a49cc90f8a73ce85a0ad17e7a7a8ca28b4b4172270a5267347de84696a3f3135`。v2在任何benchmark mint或live call之前显式supersede v1；v1 config及既有artifacts保持immutable historical records，不覆盖、不改写，也不得再被用于mint或执行本benchmark。v2完整绑定v1文件SHA `7564fd5881608091eb55f78e21913f47204dcce9af6888de31ca3e6550ac0470`，使这次标签修订可追踪。

### 30.1 两层证据标签

- world/sampling层永久标记为`outcome_conditioned_development_only`。feasibility-v2的`development_only_never_confirmatory`与`reserved_worlds_are_development_only_forever=true`继续生效；入选world不能被称为natural sample、independent held-out sample或独立确认性复现样本，也不能估计自然机会率。
- 尚未发生的model-response层标记为`preregistered_prospective_primary`。这是因为route、strict endpoint、q6/n24、masking、schedule、failure policy和statistics在任何benchmark response之前冻结；它不把development-constructed worlds升级为held-out cohort。
- upstream power result中的历史classification `q6_confirmatory_primary_power_pass_q4_fail`只绑定其exact numeric power gate及provenance。v2明确声明不继承该字符串中的`confirmatory`措辞；当前route role只能是`preregistered_prospective_primary`，不能再写为`confirmatory_primary`。

若冻结primary显著，唯一允许的primary正向标签为`prospective_primary_positive_on_fixed_development_constructed_finite_DSL_challenge`，其含义仅是：`deepseek-pro`在这一固定development-constructed strict finite-DSL challenge上呈现paired net context-responsive unique-action utilization。若不显著，标签为`prospective_primary_not_detected_on_fixed_development_constructed_finite_DSL_challenge`；若按冻结failure policy不可评估，标签为`prospective_primary_non_evaluable_under_frozen_failure_policy`。完整双臂switch、方向一致个案和shortcut sensitivity只使用v2列出的descriptive secondary标签。

以下结论标签被冻结为禁止使用：`confirmatory_primary`、`independent_heldout_confirmation`、`independent_confirmatory_replication`、`natural_world_opportunity_rate_estimate`、`model_general_capability_established`、`internal_entropy_causality_established`、`human_unknown_discovery_established`、`training_external_invention_established`与`real_world_scientific_discovery_generalization`。

### 30.2 未改变的科学与执行契约

这次amendment只修正复用决议和证据措辞，不放宽实验：仍为`strict_unique_nonconstant_switch`、四strata各6、24 independent worlds、48次`deepseek-pro` task calls；两臂各唯一一个nonconstant-K4正确action、两者不同且disjoint、K2机会数相等、constant-K4为0。v1冻结的opaque masking、pair-shared option mapping、display/context schedule、单侧exact sign test、alpha `1/20`、received-invalid/transport failure处理、target-blind baselines和全部live barriers原样保留。action-order namespace也沿用v1，以保持已冻结的具体schedule算法不漂移。

prospective power算术原样绑定：冻结SESOI为`P(favorable/adverse/tie)=0.60/0.10/0.30`；q4/n16 exact power `0.7400839271090688`、gate fail；q6/n24 exact power `0.9179412677578405`、gate pass；首个任意n为23，四strata平衡后为24。power pass仍不是模型证据。

因此标签blocker已经解除，但v2本身仍只授权下一步离线构造。后续必须先基于v2生成并复核construction plan，再逐个校验128个private shards、确定性选择24 worlds、封存public/private/result及其交叉绑定，并完成target-free route canary、joint exchangeability justification、response/failure contract、exploratory-route决议和analysis hash bindings；这些屏障全部通过前不得调用provider。

## 31. Benchmark v2离线构造器实现（2026-08-28）

新增`src/spark_strong_k4_utilization_primary_benchmark.py`及对应合成测试。实现继续绑定第30节的config v2；config raw file SHA保持`a49cc90f8a73ce85a0ad17e7a7a8ca28b4b4172270a5267347de84696a3f3135`，代码另以canonical SHA `b57877f71aa716692e6e9623cc0e6e07877d6f93c1a7c68d7ab33690fa4182df`整体锁定其全部字段。这样旧power classification只能作为历史数值provenance，allowed/forbidden evidence labels、route、analysis、masking、schedule、baselines与live barriers都不能在同一protocol id下静默漂移。

构造被分为两个阶段。`plan`阶段只读取tracked v2 config、feasibility target-free plan、safe artifact manifest与既有power config/plan/result；不打开private shard payload，不读取target或pair identity。它冻结24个pair slots、两phase context顺序、10-action display permutation，并精确绑定current source manifest与Git commit。正式CLI只允许在clean worktree上生成plan；后续验证要求current source hash与Git head同时匹配。`construct`阶段只有在reviewed plan的semantic SHA与raw file SHA同时匹配后才允许打开任何private shard。

正式construct不会加载967MB monolithic result。它按safe manifest顺序逐一检查128个shard的path/range/size/raw SHA/inner SHA，再调用既有完整`_validate_shard`重算105个profiles及pair geometry；只保留compact strict eligibility，随后对全部1024 worlds重新调用`deterministic_tier_matching(target_per_stratum=6,fallback_per_stratum=6)`。因此q6是fresh deterministic matching，不是旧q4 cohort的追加或复用；任何shard缺失或不一致都会使构造停止。

入选world先按plan seed重建target-free D0、parent与old subtrees，并与已校验shard的`parent_canonical_hash`核对。每pair的两臂共享raw action order和opaque option ids；public只允许固定顶层字段及48条`task_id/rendered_prompt/prompt_sha256`，不含candidate/world seed、target、motif id、stratum、arm label、正确action或private mapping。private key保存评分与provenance，且在最终validator/writer中再次按world seed重放完整public context；result的selected indices与stratum counts从private pairs重算。public/private/result均作semantic与file cross-binding、mode 0600、exclusive create、不覆盖。

两路独立只读审计已复测：修改证据措辞、route/model、alpha/test、exchangeability、config任意字段、plan upstream/cohort/evidence、Git head/dirty状态、public额外private字段、private world seed/analysis或result selection摘要，均会被拒绝。所有构造产物仍明确写`evidence=false`、world层=`outcome_conditioned_development_only`、response层=`preregistered_prospective_primary`、`independent_heldout_confirmation=false`且`provider_calls_authorized=false`。

本节写入时尚未生成正式construction plan/public/private/result，未读取本benchmark model outputs，未调用provider。当前设备也没有safe manifest所绑定的128个gitignored private shards；它们须从原生成设备精确转移后才能正式construct。下一顺序固定为：源码/config/tests/docs先commit并push形成clean source freeze；之后生成并独立复核target-free plan；再恢复并逐shard校验private payload。缺少shards不妨碍前两步，但阻止benchmark mint。

冻结前验证结果：新config/builder focused tests 17项、相关旧benchmark/feasibility/power回归66项、repository-wide tests 516项全部PASS；`compileall`与tracked/untracked diff whitespace checks均PASS。第一次全仓运行曾因测试期间继续编辑source tree而由既有source-stability自检按设计报错；停止编辑后完整重跑为516/516 PASS，未把该中间运行计作代码失败。
