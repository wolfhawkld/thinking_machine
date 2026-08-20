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
