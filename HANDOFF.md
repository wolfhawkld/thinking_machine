# Spark-to-Knowledge 研究交接（更新至2026-08-27）

## 当前状态

- 分支：`main`。
- Opportunity creation / utilization construction feasibility 已完成：strict unique-action tier 在当前 cap 下最高为四 strata 各 `q=6`、共24 worlds，冻结 fallback 为 `q=4/n=16`；degraded disjoint-two-choice tier 可达 `q=8/n=32`。
- Opportunity utilization prospective power 已完成并封存。这仍是纯离线 operating-characteristic calculation，不是模型实验，也没有观察 utilization。
- 2026-08-27已选择新的优先策略：保留strict unique-action，拟采用q6/n24；把`deepseek-pro`事前固定为唯一confirmatory primary route，使同一冻结SESOI下的设计alpha从三route首步`1/60`变为单一primary的`1/20`。新power源码/config/tests已实现并通过目标检查，尚待source-freeze commit及正式plan/result封存；旧protocol的source/config/plan/result均保持immutable，不覆盖、不重生、不改写标签。
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

## 2026-08-26 解释与策略讨论记录

- `power`是“假定冻结SESOI真实存在时，当前设计得到显著结果的概率”，不是“假设为真的概率”。在检验、效应分布和独立性假设不变时，样本量增加会提高检出概率；`n=16/24`未通过事前`0.90` gate不表示它们没有科学信息，只表示其漏检风险高于本次确认性标准。
- 本研究不以“AI在每个world都必然发现新知”为假设。发现型过程本来可以稀疏失败、偶尔成功；一个事前冻结、盲测且经shortcut检查的context-concordant正向案例，可以支持“该行为在受控条件下能够发生”的存在性/描述性结论。要声称模型存在稳定的总体utilization倾向，仍需预注册的成组检验与相应不确定性报告。
- 证据强度分层记录如下：确定性代码确认Opportunity creation，只支持实验前提；模型正向但aggregate不显著时属于方向一致或提示性证据；预注册primary在相应识别条件下显著时，仅对所选finite-DSL challenge支持paired net Opportunity utilization；再通过shortcut sensitivity、重复实验或独立模型复现后证据更强。所有失败、tie和adverse结果必须同时报告，不得只选成功案例。
- 受控行为结果可以支持“增熵 -> 降熵 -> 形成task-local新知”所预测的行为链，并与该机制解释一致；仅凭最终action不能直接识别模型内部是否真实经历了这些阶段，也不能升级为训练外发明、自然机会率或现实世界未知发现。
- strict与degraded的正式结论没有因本次讨论改变。strict unique-action仍是更强、更干净但当前低功效的问题；degraded disjoint-two-choice是可达`n=32`且通过冻结power gate的更窄问题，尚未被正式选为live主实验。

## 2026-08-27 strict单主路线策略

- 新策略不放宽action：继续要求两个context各自只有一个nonconstant-K4正确action，且两者不同。使用当前geometry的最大四strata平衡容量q6/n24；其exact pair/world identity尚未冻结。
- 三条模型route不是三个独立world。为回答“一个事前指定的强模型是否能在受控条件下利用context”这一存在性/机制问题，只把既有最高能力档`deepseek-pro`设为唯一confirmatory primary；选择依据是事前模型档位，不是新cohort或新模型输出。它未来若canary/response contract失败，primary实验停止，不得换`deepseek-flash`或`glm-5.2`补位。
- `deepseek-flash`和`glm-5.2`只保留为可选exploratory replication。它们不进入primary family、不与主路线池化成`3n`，也不能在看到结果后用较小p值替换primary结论；是否运行必须在live前冻结。核心实验因此只需24 worlds x 2 context arms = 48次`deepseek-pro`正式task calls，target-free canary和可选复现另算。
- 在冻结SESOI `P(favorable/adverse/tie)=0.60/0.10/0.30`和单侧exact sign test `alpha=1/20`下，离线复核得到：n16 power `0.7400839271090688`，n24 power `0.9179412677578405`；最小任意n为23，四strata平衡后为24。因此新策略有望同时保留strict、短小规模和`0.90`目标。它必须由新的power plan/result正式封存后才能成为gate结论，不能回写旧三route artifact。
- 即使未来primary显著，结论也只限于该route在所选outcome-conditioned finite-DSL strict challenge上的paired net context-responsive unique-action utilization；complete two-arm switch仍是secondary，行为结果不直接证明内部entropy因果。若不显著但出现正向案例，则按前节只报告描述性/提示性存在证据。
- 新模块只读取tracked safe artifact manifest并复用旧exact-Fraction算术；没有读取private result/shards、模型输出或provider credentials。目标测试覆盖q4 fail/q6 pass、route/claim/path drift、source/plan/result bindings与0600不覆盖输出；与旧power及config测试合计27项通过，compileall和diff check通过。一次独立代码审计发现的safe-manifest路径元数据问题已修复，非科学所需的hostile-input hardening与伪review capability已移除。

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

## 当前恢复点

当前不要直接调用模型。claim已选择为strict q6/n24的单主路线机制challenge；先完成新的独立power协议、plan和result封存。之后才构建q6 provider-facing benchmark：

1. 将已复核的新power源码、config、tests和第28节设计commit/push；随后单独生成并复核plan，再生成result，均不得读取private geometry或模型输出。
2. q6/n24目前只有capacity结论，没有pair identity。power通过后，需逐一验证128个既有private shards并只抽取compact strict eligibility，再按冻结matcher选择四strata各6个world；不能从q4 cohort直接追加，也不能按route或人工吸引力选pair。
3. 另行封存新的public/private manifests、opaque option mapping、display/context schedule、`deepseek-pro` route canary、joint-exchangeability canary、failure policy和analysis labels；上述全部通过后才允许48次primary calls。

旧power result和degraded候选继续作为历史敏感性结果保留，但不再是当前优先live路线。模型/API凭据只在新的masked benchmark、route identity和canaries全部事前封存之后才需要。
