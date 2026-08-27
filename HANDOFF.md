# Spark-to-Knowledge 研究交接（更新至2026-08-27）

## 当前状态

- 分支：`main`。
- Opportunity creation / utilization construction feasibility 已完成：strict unique-action tier 在当前 cap 下最高为四 strata 各 `q=6`、共24 worlds，冻结 fallback 为 `q=4/n=16`；degraded disjoint-two-choice tier 可达 `q=8/n=32`。
- 历史三route与新的单primary-route Opportunity utilization prospective power均已完成并分别封存。它们仍是纯离线 operating-characteristic calculations，不是模型实验，也没有观察 utilization。
- 2026-08-27的当前优先策略保留strict unique-action q6/n24，并把`deepseek-pro`事前固定为唯一confirmatory primary route。新formal result已确认：在同一冻结SESOI下，单primary `alpha=1/20`时q6/n24 exact power为`0.9179412677578405`，通过`0.90` gate；q4/n16为`0.7400839271090688`，不通过。旧protocol的source/config/plan/result均保持immutable，不覆盖、不重生、不改写标签。
- 本阶段没有读取967MB private feasibility result、没有读取model outputs、没有调用provider/model、没有mint新的public/private benchmark。
- 2026-08-27的benchmark construction config v1已冻结：`configs/spark-strong-k4-utilization-primary-benchmark-v1.json`（file SHA `7564fd5881608091eb55f78e21913f47204dcce9af6888de31ca3e6550ac0470`），绑定feasibility-v2 safe manifest与primary-route power v1 config/plan/result；其声明的全部upstream file hashes已逐一与仓库实际文件核对一致。config只封存契约，未生成plan/result、未读取shard payload/private result/model outputs，`provider_calls_made=0`。
- 审计状态：feasibility-v2 shard元数据审计PASS（128 shards覆盖0..1023、总大小967,864,320B、全部存在/大小匹配/0600、manifest hashes一致）；第28节power plan/result统计与provenance两路复核均PASS；primary_power_code_audit因quota中断未出最终结论，其修复项已含于source freeze `a46d359`。

## 当前正式power结论（strict 单primary route）

冻结参数为：单侧exact sign test、唯一confirmatory hypothesis/route `deepseek-pro`、family与primary alpha均为`1/20`、目标power `0.90`；SESOI为`P(favorable)=0.60`、`P(adverse)=0.10`、`P(tie)=0.30`。

| design | tier | n | exact power | gate |
|---|---|---:|---:|---|
| strict fallback q4 | unique-action | 16 | 0.7400839271090688 | fail |
| strict maximum q6 | unique-action | 24 | 0.9179412677578405 | pass |

首个任意样本量达标值为`n=23`；要求四strata平衡时为`n=24`。因此冻结分类为：

- tier：`strict_unique_switch_power_adequate_at_q6`
- q4：`fail`
- q6：`pass`
- overall：`q6_confirmatory_primary_power_pass_q4_fail`

primary rejection将来最多表示`deepseek-pro`在所选finite-DSL strict challenge上的paired net utilization方向，不等于每个world都完成双臂switch；complete context-concordant switch仍是secondary。这个power pass本身不是模型证据。

第27节的历史三route协议结论仍保留：在Holm首步`alpha=1/60`下，strict q4/n16为`0.5195276335337472`、strict q6/n24为`0.7898078702451884`，均fail；degraded q8/n32为`0.9161773022953812`，pass。这是不同claim family的历史敏感性结果，不与当前单primary结果混合。

## 统计成立条件

- 在“不利用context”的null下，两臂joint observable outcomes必须在交换arm labels后保持exchangeable；这包含received/validity status以及valid时的parsed choice。IID arms是充分条件，但stateless calls、期望相等或aggregate hard balance本身都不是证明。
- 当前exact power只对selected tier内independent worlds、共同`p_favorable/p_adverse`的homogeneous planning model精确，不保证四个strata存在异质性时仍有相同power。
- later benchmark必须在live前冻结并检查arm/display schedule、exchangeability canary和逐stratum报告；条件不可辩护时不能使用当前sign-test gate。

## 2026-08-26 解释与策略讨论记录

- `power`是“假定冻结SESOI真实存在时，当前设计得到显著结果的概率”，不是“假设为真的概率”。在检验、效应分布和独立性假设不变时，样本量增加会提高检出概率。对第27节当时的三route `alpha=1/60`设计，`n=16/24`未通过事前`0.90` gate不表示它们没有科学信息，只表示其漏检风险高于该历史确认性标准；第28节的单primary q6/n24已另行通过新gate。
- 本研究不以“AI在每个world都必然发现新知”为假设。发现型过程本来可以稀疏失败、偶尔成功；一个事前冻结、盲测且经shortcut检查的context-concordant正向案例，可以支持“该行为在受控条件下能够发生”的存在性/描述性结论。要声称模型存在稳定的总体utilization倾向，仍需预注册的成组检验与相应不确定性报告。
- 证据强度分层记录如下：确定性代码确认Opportunity creation，只支持实验前提；模型正向但aggregate不显著时属于方向一致或提示性证据；预注册primary在相应识别条件下显著时，仅对所选finite-DSL challenge支持paired net Opportunity utilization；再通过shortcut sensitivity、重复实验或独立模型复现后证据更强。所有失败、tie和adverse结果必须同时报告，不得只选成功案例。
- 受控行为结果可以支持“增熵 -> 降熵 -> 形成task-local新知”所预测的行为链，并与该机制解释一致；仅凭最终action不能直接识别模型内部是否真实经历了这些阶段，也不能升级为训练外发明、自然机会率或现实世界未知发现。
- 这些讨论没有改写第27节三route协议的历史结论。后续以新协议另行冻结单primary claim，才在不放宽action的前提下使strict q6/n24达到新的power gate；degraded disjoint-two-choice不再是当前优先live路线。

## 2026-08-27 strict单主路线策略

- 新策略不放宽action：继续要求两个context各自只有一个nonconstant-K4正确action，且两者不同。使用当前geometry的最大四strata平衡容量q6/n24；其exact pair/world identity尚未冻结。
- 三条模型route不是三个独立world。为回答“一个事前指定的强模型是否能在受控条件下利用context”这一存在性/机制问题，只把既有最高能力档`deepseek-pro`设为唯一confirmatory primary；选择依据是事前模型档位，不是新cohort或新模型输出。它未来若canary/response contract失败，primary实验停止，不得换`deepseek-flash`或`glm-5.2`补位。
- `deepseek-flash`和`glm-5.2`只保留为可选exploratory replication。它们不进入primary family、不与主路线池化成`3n`，也不能在看到结果后用较小p值替换primary结论；是否运行必须在live前冻结。核心实验因此只需24 worlds x 2 context arms = 48次`deepseek-pro`正式task calls，target-free canary和可选复现另算。
- 在冻结SESOI `P(favorable/adverse/tie)=0.60/0.10/0.30`和单侧exact sign test `alpha=1/20`下，formal result确认：n16 power `0.7400839271090688`，n24 power `0.9179412677578405`；最小任意n为23，四strata平衡后为24。因此新协议在保留strict和短小规模时通过`0.90` prospective power gate，且没有回写旧三route artifact。
- 即使未来primary显著，结论也只限于该route在所选outcome-conditioned finite-DSL strict challenge上的paired net context-responsive unique-action utilization；complete two-arm switch仍是secondary，行为结果不直接证明内部entropy因果。若不显著但出现正向案例，则按前节只报告描述性/提示性存在证据。
- 新模块只读取tracked safe artifact manifest并复用旧exact-Fraction算术；没有读取private result/shards、模型输出或provider credentials。目标测试覆盖q4 fail/q6 pass、route/claim/path drift、source/plan/result bindings与0600不覆盖输出；与旧power及config测试合计27项通过，compileall和diff check通过。一次独立代码审计发现的safe-manifest路径元数据问题已修复，非科学所需的hostile-input hardening与伪review capability已移除。

## 当前primary-route artifacts与provenance

- 源码冻结commit：`a46d35929ef75b79f11a9b0a3b29acc6aa6dbf43`
- source manifest：`5cd2fdf3808a85f9a24d0203b34d2e54700a9528550a687d81448f810da0e354`
- config file SHA：`7f6b07777f94a113ea8d5d06a3f32c15f2b4cde361446b98deb6dc64f1ce4fa1`
- plan commit：`896dce7192ef289006b5791c86a1a9380367ceb3`
- plan：`artifacts/spark-strong-k4-utilization-primary-route-power-v1-20260827/plan.json`
  - canonical SHA：`9f95ebd14f4efe9380a30f49c5aa6872970a65e21a9fdd6165dea9a0cc2eec9d`
  - file SHA：`734345d7fe7816c3be2b8d72eecd7db161edcecb234b05f8adc9f862fc497b8e`
- result commit：`b828ec8d3a65a0fad2c4aba876a965ebf832d47c`
- result：`artifacts/spark-strong-k4-utilization-primary-route-power-v1-20260827/result.json`
  - canonical SHA：`091b665907018a16d93816888d7ac4fe5ecd93bad065d21448c3683cda6437e6`
  - file SHA：`db8b6c68390ee624558cd7cb6d317d105e9631dff9bf45decdcd863fe79710c5`

两路`luna_worker`分别对formal plan和formal result完成统计与provenance只读复核，均为PASS。它们独立重算exact power、minimum n、semantic/file hashes及source/config/upstream bindings。新旧power相关27项unittest、compileall与diff check通过；本步骤没有重跑全部502项repository-wide suite。plan/result生成时为mode `0600`，且均记录`provider_calls_made=0`、`model_outputs_read=false`、`final_benchmark_minted=false`。

第27节历史三route power artifacts继续保留在source commit `cd2de1d11aa430f41d2d4446ee62911f6d24176f`、plan commit `3c51ef4ff7099837bdaf41b5d9e5e33f9db6929d`和result commit `0d0e4e760f831113d58f8aed3cb0aab05eecb497`；详细hash见实验计划第27.3节。

## 2026-08-27 benchmark config v1（设计冻结）

benchmark construction的config契约已冻结：`configs/spark-strong-k4-utilization-primary-benchmark-v1.json`，file SHA `7564fd5881608091eb55f78e21913f47204dcce9af6888de31ca3e6550ac0470`（详细记录见实验计划第29节）。它把24-world cohort选择（四strata各6、48次formal task）、strict pair contract、opaque masking、display/context schedule、`deepseek-pro`唯一primary、sign-test analysis binding、target-blind structural baselines与artifact contract全部预先固定。config本身不生成plan/result，不读取shard payload/private result/model outputs，`provider_calls_made=0`、`model_outputs_read=false`、`final_benchmark_minted=false`。

审计提示：feasibility-v2把全部materialized worlds标记为`development_only_never_confirmatory`，而§28.3计划从同一128 shards选q6作confirmatory primary；本config保留`selected_worlds_remain_development_only=true`且route role=`confirmatory_primary`，该标签关系必须在sealed config中由人类明确决议，不能默默混用。另注意`validate_scan_plan()`会读取旧sealed private result（88.6MB），compact extraction应逐shard单独校验、只保留strata eligibility，不能盲调。

## 当前恢复点

当前不要直接调用模型。benchmark config v1已冻结；恢复点是从config推进到具体24-world cohort与blind materials：

1. 先在sealed config中显式解决development-only/confirmatory标签blocker（见上），并把`remaining_live_barriers`逐项落实：target-free route canary、joint-exchangeability canary与justification、response contract/failure policy、exploratory route执行决定、analysis contract对public/private file hashes的绑定。
2. 逐shard验证128个private feasibility-v2 shard，只抽取compact strict eligibility，按config内冻结的`deterministic_tier_matching`选择四strata各6个world；不能从q4 cohort直接追加，也不能按route/输出/人工吸引力选pair。
3. 在config契约下另行封存plan.json、public.json、private.json与result.json（exact 48-task bijection、public/private交叉绑定、0600、exclusive create、全部128 shards校验后才允许mint）。
4. 上述全部通过后才允许48次`deepseek-pro` primary calls。

旧power result和degraded候选继续作为历史敏感性结果保留，但不再是当前优先live路线。模型/API凭据只在新的masked benchmark、route identity和canaries全部事前封存之后才需要。
