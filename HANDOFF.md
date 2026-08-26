# Spark-to-Knowledge 研究交接（2026-08-26）

## 当前状态

- 分支：`main`。
- Opportunity creation / utilization construction feasibility 已完成：strict unique-action tier 在当前 cap 下最高为四 strata 各 `q=6`、共24 worlds，冻结 fallback 为 `q=4/n=16`；degraded disjoint-two-choice tier 可达 `q=8/n=32`。
- Opportunity utilization prospective power 已完成并封存。这仍是纯离线 operating-characteristic calculation，不是模型实验，也没有观察 utilization。
- 本阶段没有读取967MB private feasibility result、没有读取model outputs、没有调用provider/model、没有mint新的public/private benchmark。

## 正式power结论

冻结参数为：单侧exact sign test、三条未来route hypotheses、family alpha `1/20`、保守首步设计阈值 `1/60`、目标power `0.90`；SESOI为`P(favorable)=0.60`、`P(adverse)=0.10`、`P(tie)=0.30`。

| design | tier | n | exact power | gate |
|---|---|---:|---:|---|
| strict fallback q4 | unique-action | 16 | 0.5195276335337472 | fail |
| strict maximum q6 | unique-action | 24 | 0.7898078702451884 | fail |
| degraded target q8 | disjoint-two-choice | 32 | 0.9161773022953812 | pass |

首个任意样本量达标值为`n=31`；要求四strata平衡时为`n=32`。因此冻结分类为：

- strict：`strict_unique_switch_power_inadequate_under_available_geometry`
- degraded：`degraded_two_choice_power_adequate_at_frozen_sesoi`
- overall：`degraded_only_power_gate_passed`

不得把degraded pass升级成unique-action switching证据。primary rejection将来最多表示paired net utilization方向，不等于每个world都完成双臂switch；complete context-concordant switch仍是secondary。

## 统计成立条件

- 在“不利用context”的null下，两臂joint observable outcomes必须在交换arm labels后保持exchangeable；这包含received/validity status以及valid时的parsed choice。IID arms是充分条件，但stateless calls、期望相等或aggregate hard balance本身都不是证明。
- 当前exact power只对selected tier内independent worlds、共同`p_favorable/p_adverse`的homogeneous planning model精确，不保证四个strata存在异质性时仍有相同power。
- later benchmark必须在live前冻结并检查arm/display schedule、exchangeability canary和逐stratum报告；条件不可辩护时不能使用当前sign-test gate。

## Artifacts与provenance

- 源码冻结commit：`cd2de1d11aa430f41d2d4446ee62911f6d24176f`
- source manifest：`c37cecb5cb5e56d1b229a907d67f36309045df23128ec1166569a4b1fefbc0f0`
- plan commit：`3c51ef4ff7099837bdaf41b5d9e5e33f9db6929d`
- plan：`artifacts/spark-strong-k4-utilization-power-v1-20260826/plan.json`
  - canonical SHA：`726aaaffa21c1f95e11a13054bccbe521db855f1a7db52210d2f05e579b21949`
  - file SHA：`58912fc3d6ac7ec577aac24445da83262d8cf7ffe589cce33efba6b8eae051c8`
- result commit：`0d0e4e760f831113d58f8aed3cb0aab05eecb497`
- result：`artifacts/spark-strong-k4-utilization-power-v1-20260826/result.json`
  - canonical SHA：`b6b08bfb3d5de03a241aff36a48ae749b176de1bd0cd13bf1decd8544b46bd32`
  - file SHA：`f2a4be8997f485152fd6781c2fc6aca7493478c086c85f34c881ca4821b97db3`

两路`luna_worker`分别完成统计与provenance只读复核，均为PASS。power相关21项unittest、compileall与diff check通过。本步骤没有把当前502项repository-wide suite完整重跑到底；额外单独运行`test_layered_v1_sealed_artifacts_replay_exactly`时复现了历史closure replay mismatch，本次未修改对应源码或artifact。

## 下一步必须先做的人类选择

当前不要直接调用模型。研究者需要先明确二选一：

1. 接受更窄的degraded disjoint-two-choice研究问题，以新的sealed benchmark config冻结q8/n32 cohort、pair matching、public/private manifests、opaque options、display/context order、route schedule、exchangeability canary、failure policy与inferential labels；或
2. 保留strict unique-action目标，新建更大且独立的construction protocol，取得至少四strata各8个strict worlds后重新做matching与power，不复用当前degraded pass。

无论选择哪条，都不能从本power result直接mint benchmark或开始provider calls。模型/API凭据只在新的masked benchmark、route identities和canaries全部事前封存之后才需要。
