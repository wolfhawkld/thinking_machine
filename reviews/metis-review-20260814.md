# Metis Review — thinking_machine 研究项目审查

**审查日期**：2026-08-14
**审查者**：Metis（Hermes / deepseek-v4-pro）
**审查范围**：spark-to-knowledge 研究线的策略、方法、实验设计、代码逻辑（含未提交的 cross-model 新代码）
**审查方式**：只读审查，未修改任何项目文件。代码级验证通过直接阅读 src/、tests/、artifacts/ 完成。
**研究定位（作者确认）**：本文档的审查标准按作者的研究目标校准——目标是**快速产出可引发相关前沿研究者兴趣的内容**（预印本短论文/技术文章），**不是**一份统计上完全 robust 的可发表论文。因此统计稳健性相关的担忧按"已知并接受的边界"记录，不要求大幅增加实验量来防御。

---

## 一、结论总览

这套研究的工程纪律与科学诚实度显著高于同类个人研究项目的平均水平。核心骨架扎实：目标信息隔离在代码层验证干净、prospective 冻结 + SHA 哈希链 + 历史结果 replay 保护使"事后改规则"的空间极小、结论边界声明极为克制（每一层结果都显式声明不能推出什么）。

发现 1 个**严重问题**（K4=3/32 证据链缺少候选基数分母，建议交给 Codex 修复——修复是纯离线零 API 重算，不增加实验量）；3 个中等问题（其中 2 个作者已确认为已知局限，1 个降级为接受的边界）；2 个轻微/本质局限（定位为设计性质说明，非缺陷）。

## 二、严重问题（建议修复，交给 Codex 评估）

### S1. K4=3/32 的解释强度受"候选基数未报告"制约

**问题描述**：
K4 是分层漏斗 E1⊇E2⊇E3⊇E4 的最深一层。E2 的定义是"**至少一个**同一 slot 的 child 非 direct hit、truth retained、四轮后 N_T=1"——这是一个 ∃（存在量词）判定。当每个 slot 的 eligible children 基数较大时，"至少一个 child 能 closure"的基线概率本身就高。设计用 parent-deletion（E3）和 matched-replacements（E4）对照来对抗这个问题，方向正确；但**分析 artifact 未报告每一层判定背后的候选基数**：

1. 每个 slot 的 **eligible children 总数**（enumerate_reachable_children 的输出规模）未进入 analysis.json；
2. 每个 K4 slot 的 **matched replacement 池大小**及其中"也达到 endpoint"的比例未记录——`matched_replacements` 字段只保存被选中按 hash 顺序取前两个的 replacement 轨迹，不记录池中其余成员的结局；
3. E4 的判别力完全取决于"replacement 池中能 closure 的比例"：若池中 20 个 replacements 有 18 个都不达 endpoint，那么"取前两个恰好都不达"是大概率事件，E4 的 specificity 意义被稀释；若池中大部分都能达，则 E4 才是强证据。当前 artifact 无法区分这两种情况。

**证据**：
- `spark-to-knowledge-experiment-plan.md` 第 19.2 节：E2 用"至少一个"表述；第 14.1 节 gate 要求"每 world 至少 16 个 eligible children"（证明基数可大可小）；
- `artifacts/spark-closure-layered-v1-20260814/analysis.json`：per-world `slot_results` 含 `lineage_valid`、`matched_replacements`（只存 2 个被选 replacement 的轨迹）、`strict_event_checks`，但**无** eligible children 总数、无 replacement 池规模、无"池中 closure 比例"字段。

**为什么重要**：同一个"child 256→1、parent 卡 N_T=18"的证据链，发生在"3 个 eligible children 中 1 个成功"还是"20 个中 1 个成功"，对读者的说服力完全不同。前一种情形下 spark 的特异性令人印象深刻；后一种情形下它更像"多试几个总有一个能过"。当前 artifact 让外部读者无法区分。

**修复建议（零新增实验量）**：
1. `enumerate_reachable_children` 是确定性、零 API 的纯离线计算。对 layered-v1 的 32 个 world 离线重跑该枚举，把每个 slot 的 eligible children 总数写入补充分母表；
2. 对 3 个 K4 slot，枚举**全部** matched replacements（不是只取前两个），逐一对每个 replacement 跑四轮确定性 compressor（同样零 API），记录"池中达到 endpoint 的比例"；
3. 在 analysis 报告中补报：每个 K4 slot 的 eligible children 数 / 池规模 / 池中 closure 率，作为描述性分母，不改动已冻结的 K1-K4 主判定；
4. 若第 3 项显示池中 closure 率很高（如 >50%），则 E4 的强 specific 解读需要降级为"弱对照通过"；若很低（如 <20%），则当前 K4=3/32 的解释强度得到实质性加固。

**注意**：以上补算全部基于已封存的 plan/generation，不产生新的模型调用，不修改已冻结的分类规则，与作者的"不增加实验量"原则完全一致。补算结果应作为新的 description-only artifact 追加，不得回头改写 layered-v1 的 analysis_sha256 或 classification。

## 三、中等问题（作者已确认或降级为接受的边界）

### M1. "冻结在结果之前"依赖诚实作者假设，无第三方锚点（作者已确认）

SHA 哈希链 + git 提交时间戳防的是误改和后悔式修改，但作者本人（或拥有仓库写权限者）理论上可以事后改代码并重算哈希。对单人 + 单 Codex 实例的项目，作者已确认研究过程无额外污染，此风险可接受。

**建议（可选、低成本）**：在预印本/技术文章公开时，显式披露 git 仓库完整历史作为冻结证据；若想更强，可在结果公开前把 plan_sha256 发到公开渠道（X 帖/archive）。对"引起讨论"的目标，git 历史披露通常已足够。

### M2. 跨模型"逐字节相同 prompt"无法延伸到 tokenizer 层（作者已确认）

两模型共享同一 `build_closure_prompt(plan, slot)` 输出，prompt 字节级相同已代码验证成立。但 DeepSeek 与 MiniMax 的 tokenizer 不同，"同一 prompt"在各自模型内部是不等价的表示。这是所有跨模型研究的固有局限，文档第 20.5 节已诚实声明两 route 的 qualification 证据不对称。**只需在文章 limitations 中声明即可，不影响配对设计的内部有效性**（配对对比的是"两模型各自在自己的表示下面对同一任务的差异"，这正是跨模型复现想回答的问题）。

### M3. calibration 与 confirmatory 共享组件的过拟合风险（降级为接受的边界）

motif 库分层、ρV 阈值（≥20%）、DSL 约束、四轮预算等设计组件全部在 retired seeds 1000-1008 上迭代得出；layered-v1 的 32 个新 seeds 验证了随机性，但没有验证"设计选择的泛化性"，且只跑了一个批次。

**降级理由**：作者明确目标是 screening/机制信号而非发生率估计，且正在做跨模型实验来部分对冲模型特异性。文档对"32 worlds 是 screening 规模"已有诚实声明。按作者"不追求统计 robust、不大幅增加实验量"的原则，此项记录为**已知边界**而非待修复项。写文章时在 limitations 里点明即可。

## 四、轻微 / 本质局限（定位为设计性质说明，非缺陷）

### L1. R⊥F|D0 是构造性保证，不是统计检验

"扰动与目标独立"这一核心前提由 target-blind 随机流构造保证（代码级已验证：`_select_motif` 不读 target、`generate_closure` 不访问 hidden target、prompt 只含 D0/parent/motif），数据本身无法自证这一点。这是程序生成世界范式的固有性质，不是缺陷。文章应明确写"独立性由构造保证，可由代码审计核验"。

### L2. outcome-exposed 的开发历史已如实披露

第 16 节 24-call exploratory closure 曾因 fake-provider 测试暴露 outcome，研究者已如实标记为 outcome-exposed development demonstration，与 prospective 结果严格分离。这是加分项，不是漏洞。

## 五、已代码级核查无误的要点（备忘）

- 目标信息隔离：prompt（仅 D0/parent/motif/grammar）、motif 选择（target-blind SHA）、generation 流程（不访问 target）三层均干净；
- 未提交的 spark_closure.py diff（+545 行）**未触碰 E1-E4/K 计算逻辑**；新增 `_SEALED_HISTORICAL_REPLAY_ANALYSES` 表对已封存 plan/generation 对硬编码返回历史 digest，防止代码演进破坏历史复算——合理设计；
- cross_model 正确复用 `spark_closure._analyze_factual_slot` 与 `classify_layered_outcome`，非重实现；
- joint analysis 每 world 只构造一次共享 world（含 target），两模型 action 分别评估，parent/D0 一致性有运行时校验；
- 统计单位为 world（非 slot），四格表强制校验 32 seeds 完整配对；
- 执行调度交错平衡 route×time，每请求独立无对话历史，无上下文污染。

## 六、给 Codex 的修复清单（仅 S1，其余为记录项）

1. 对 layered-v1 的 32 world 离线重跑 `enumerate_reachable_children`，产出 per-slot eligible children 总数表；
2. 对 3 个 K4 slot 枚举全部 matched replacements，逐一跑四轮确定性 compressor，产出"池中 closure 率"；
3. 将以上作为新 description-only artifact（不重算、不改写 layered-v1 的 analysis_sha256 与 classification）；
4. 根据池中 closure 率评估 E4 解读是否需要降级表述，并同步到后续技术文章/预印本的 limitations 段落。
