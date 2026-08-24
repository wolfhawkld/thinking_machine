# Spark-to-Knowledge 研究交接（2026-08-25）

## 当前状态

- 分支：`main`。
- 正式 fair-choice 实验已经完成，192/192 calls 有效；联合分类为
  `effect_not_observed_under_frozen_protocol`。
- 结果不是“总体假设被证伪”：三个模型都出现过 factual K4 命中，但没有越过
  shortcut、uniform 和 breadth gates。
- 事后诊断已经封存。最重要的发现是：18 个 factual K4 命中里 16 个是
  constant-zero behavior；Flash/Pro 的大部分正 discordance 还是两臂选择同一个
  raw action 后由 context 改变 endpoint，尚不足以证明模型主动切换了探索方向。
- 正式结果及事后诊断位于
  `artifacts/spark-strong-k4-fair-choice-formal-20260824/`；研究说明主要在
  `spark-to-knowledge-experiment-plan.md` 第 23--25 节。

## 刚完成的只读可行性审计

这次审计只复用了已经打开的 `spark-strong-k4-feasibility-v2` 1024 个
development worlds；没有调用模型/provider，没有打开新 target namespace，也没有把
结果当作 confirmatory evidence。

- 原 3072 个 factual slots 中共有 190 个 K4 raw actions。
- 其中 nonconstant K4 为 48 个，分布在 32 个 slots、28 个 worlds。
- 要求一个 factual slot“恰好一个 K4，且该 child nonconstant”后，只剩 16 个
  worlds；四个 strata 的容量为 `7 / 3 / 2 / 4`。
- 按旧的 target-blind sham selector 对这 16 个 worlds 对称 replay 后，只有
  candidate 950 同时满足：两臂各恰好一个 nonconstant K4、K2 数量相等、两臂正确
  raw action 不同。
- 因而旧 development grid 连四 strata 各取 1 个的 cohort 都构不出来，更不能直接
  改标签后复用为下一轮实验。
- 对 candidate 376 做了一次完整 105-motif 抽查，找到了一个同 stratum、K2 数量
  相等且正确 raw 不同的严格 pair。这说明目标结构并非逻辑上不存在，但需要专门的
  construction scan；当前朴素 full-library evaluator 在该 world 约耗时 80 秒，不应
  未经设计就直接扫完 1024 worlds。

这些数字是 post-hoc development calibration，只用于设计下一轮协议。

## 下一轮研究问题

将原问题拆成两个互不共用结论标签的部分：

1. **Opportunity creation**：完全由确定性代码统计 context 是否创造新的可达
   nonconstant K4 机会。
2. **Opportunity utilization**：在两臂机会强度相等时，盲测模型能否根据不同
   context 选择各自正确、且彼此不同的 action。

首选 utilization pair 的严格条件是：

- 两臂各恰好一个 `K4_full_pool` raw action，且 child 必须 nonconstant；
- 两个正确 raw actions 必须不同，否则不能检验 context-responsive switching；
- 两臂 K2 opportunity count 相等，并保持同 stratum、同 complexity bucket；
- cohort 层面配平 stratum、正确 raw、path/frame、display position、condition order，
  并审计 child behavior 与 control-bundle 多样性；
- constant K4 不进入 primary；
- 严格 cohort 不可行就明确报告 infeasible。任何降级条件必须事前另行冻结，并使用
  更窄的结论标签，不能扫描后静默放宽。

不要采用“两臂逐 raw 的 K4 opportunity vector 完全相同”作为这个 switching
问题的主条件：那会让两臂正确 action 相同，无法证明模型使用 context 改变了方向。

## 明天从这里继续

1. 在打开任何 fresh target 前，冻结独立的新 protocol/config：namespace、固定 scan
   cap、每 world 的 target-free motif 候选规则、严格/降级层级、完整扫描停止规则及
   deterministic matching/tie-break。
2. 新建独立的 offline-only feasibility 模块和测试；不得修改旧 v2、fair-choice-v1
   或其 artifacts。
3. 先提交并 push 源码冻结，再生成并复核 target-free plan barrier。
4. 只有 plan 通过独立检查后，才决定是否打开 fresh development targets。该阶段仍然
   不调用任何模型。
5. construction feasibility 和 prospective power 都通过以后，才设计新的 masked
   public/private benchmark 与 provider calls。

建议先查看：

- `artifacts/spark-strong-k4-fair-choice-formal-20260824/posthoc-design-implications-v1.md`
- `src/spark_strong_k4_scan.py`
- `src/spark_strong_k4_benchmark.py`
- `src/spark_strong_k4_posthoc.py`
- `configs/spark-strong-k4-feasibility-v2.json`

## 工作原则

- 这是论文实验代码：优先科学公平、masking、确定性评分、可复现和结论边界，不做不必要
  的生产安全加固或恶意篡改防御。
- 新 scan 是 outcome-conditioned benchmark construction，不估计自然 world 的发现率。
- development worlds 永久不能进入未来 confirmatory cohort。
- 下一阶段不需要 provider 凭据；仓库中也不要写入任何 API key。
