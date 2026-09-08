# Spark-to-Knowledge 研究交接（更新至2026-09-08）

## 当前状态

- 分支：`main`。
- Opportunity creation / utilization construction feasibility 已完成：strict unique-action tier 在当前 cap 下最高为四 strata 各 `q=6`、共24 worlds，冻结 fallback 为 `q=4/n=16`；degraded disjoint-two-choice tier 可达 `q=8/n=32`。
- 历史三route与新的单primary-route Opportunity utilization prospective power均已完成并分别封存。它们仍是纯离线 operating-characteristic calculations，不是模型实验，也没有观察 utilization。
- 2026-08-27的当前优先策略保留strict unique-action q6/n24，并把`deepseek-pro`事前固定为唯一`preregistered_prospective_primary` response route。新formal result已确认：在同一冻结SESOI下，单primary `alpha=1/20`时q6/n24 exact power为`0.9179412677578405`，通过`0.90` gate；q4/n16为`0.7400839271090688`，不通过。旧protocol的source/config/plan/result均保持immutable，不覆盖、不重生、不改写历史标签。
- 正式construction plan阶段没有读取967MB private feasibility result、private shards或本benchmark model outputs，也没有发起provider/model call。2026-09-08正式construct只按safe manifest逐一读取并验证128个private shards，没有读取967MB monolithic result、模型输出或provider credentials；本benchmark `provider_calls_made=0`。这不是对更早历史实验调用的全局陈述。
- 人类已在任何benchmark mint/live call/model output之前决定复用feasibility-v2 development worlds，并冻结benchmark config v2：`configs/spark-strong-k4-utilization-primary-benchmark-v2.json`（file SHA `a49cc90f8a73ce85a0ad17e7a7a8ca28b4b4172270a5267347de84696a3f3135`）。v2在live前显式supersede v1；v1及既有artifacts保持immutable historical records。world层永久为`outcome_conditioned_development_only`，response层为`preregistered_prospective_primary`，不称independent held-out confirmation。v2 config、离线构造器、正式target-free construction plan及formal public/private/result现均已生成并通过双路只读验证；构造完成仍不授权provider calls。
- 2026-08-28构造器候选源码已完成：target-free plan、reviewed semantic/file双hash屏障、128-shard逐文件hash+schema验证、fresh strict q6 matching、target-free parent/context replay、24-world/48-task masking、public/private/result交叉绑定和关键tamper tests均已实现。source commit `418ed197aead375323c2b5766a21ed207037fefe`曾通过17项新config/builder focused tests、66项相关回归、516项全仓tests、compileall与diff check；这些是下述lineage修正前结果，不能替代新source freeze的复核。
- 第一份基于`418ed197aead375323c2b5766a21ed207037fefe`生成的候选construction plan虽通过内容/provenance只读审计，但在提交前发现旧validator会把plan artifact自身的后续commit误判为Git head漂移。该候选状态为`retired_nonformal_precommit_candidate`：`formal_artifact=false`、`construct_authorized=false`、`hash_reuse_forbidden=true`；它从未提交或push（canonical SHA `a03176153590ce3853254665e831f52ef03f15c0463703fccd28ef9cf8e82dab`，file SHA `0f7de95c02319b9fb93d1baf732dabf45a8d78ec88525c76201b6bb1927c9e3f`），不得恢复到正式默认`plan.json`路径。lineage现已改为“source manifest不变 + frozen commit为HEAD祖先 + 固定protocol pathspec无diff/dirty”；两路只读复审均PASS，真实Git测试覆盖非协议descendant commit通过与tracked协议文件删除被拒绝，focused 21/21、相关回归72/72、全仓518/518、compileall与diff check均PASS。提交新的source freeze后，须生成hash全新的正式plan。
- 新source freeze commit为`06db9dae69e961570181e1de43d26b0ee8305a28`，source manifest为`f101b9e646899c413976b38ac69a84c736642fc68935eaefb4bd390f67bfebfe`。由此生成的新正式plan位于`artifacts/spark-strong-k4-utilization-primary-benchmark-v2-20260827/plan.json`，canonical SHA为`2e0750569083c5dc00615c29678521a58d4975220b2f86535138291112307f31`，file SHA为`53403b4685d6d3b4046b39b4af5f2e5d8c13075e7628f2489b182fb439d772f6`，plan commit为`34f048ad7b5c1f9a1680719f60f28d8a7b35c906`。两路新的provenance/schedule审计均PASS；plan只含24个target-free schedule slots，`private_shards_read=false`、`target_or_pair_identity_read=false`、`model_outputs_read=false`、`provider_calls_made=0`、`final_benchmark_minted=false`。plan提交使HEAD前移后，正式validator仍在相同source manifest与祖先lineage下PASS，证明lifecycle修正按设计工作。
- 先前“当前设备0/128 shards”的记录在2026-09-08复核时已过时；本设备实际存在safe manifest绑定的128/128 exact private shards，总大小967,864,320B且mode均为0600。正式construct已逐一完成path/range/size/raw SHA/inner SHA/schema验证并mint q6 benchmark；shards和private scoring key继续只留本机且被Git忽略。

## 当前正式power结论（strict 单primary route）

冻结参数为：单侧exact sign test、唯一事前指定route `deepseek-pro`、family与primary alpha均为`1/20`、目标power `0.90`；SESOI为`P(favorable)=0.60`、`P(adverse)=0.10`、`P(tie)=0.30`。power artifact中带`confirmatory`的overall classification仅作为历史数值门槛provenance保留，当前response证据标签不继承该措辞。

| design | tier | n | exact power | gate |
|---|---|---:|---:|---|
| strict fallback q4 | unique-action | 16 | 0.7400839271090688 | fail |
| strict maximum q6 | unique-action | 24 | 0.9179412677578405 | pass |

首个任意样本量达标值为`n=23`；要求四strata平衡时为`n=24`。因此冻结分类为：

- tier：`strict_unique_switch_power_adequate_at_q6`
- q4：`fail`
- q6：`pass`
- historical power overall：`q6_confirmatory_primary_power_pass_q4_fail`（只绑定旧数值门槛，当前标签不继承`confirmatory`）

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
- 三条模型route不是三个独立world。为回答“一个事前指定的强模型是否能在受控条件下利用context”这一存在性/机制问题，只把既有最高能力档`deepseek-pro`设为唯一`preregistered_prospective_primary`；选择依据是事前模型档位，不是新cohort或新模型输出。它未来若canary/response contract失败，primary实验停止，不得换`deepseek-flash`或`glm-5.2`补位。
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

## 2026-08-27 benchmark config v2（复用决议与标签冻结）

人类已明确选择复用既有development worlds。该决定发生在任何benchmark mint、live call或benchmark model output之前；没有因模型结果挑题。`configs/spark-strong-k4-utilization-primary-benchmark-v2.json`（file SHA `a49cc90f8a73ce85a0ad17e7a7a8ca28b4b4172270a5267347de84696a3f3135`，详细记录见实验计划第30节）在live前显式supersede v1。v1 config file SHA仍为`7564fd5881608091eb55f78e21913f47204dcce9af6888de31ca3e6550ac0470`，它和既有artifacts只作为immutable historical records保留，不再用于mint/run。

证据标签分层为：world来源=`outcome_conditioned_development_only`，永久不能称natural/independent-heldout sample；尚未发生的模型响应检验=`preregistered_prospective_primary`。历史power classification `q6_confirmatory_primary_power_pass_q4_fail`只作为exact power gate provenance绑定，其`confirmatory`字样不继承。v2保持四strata各6、24 worlds、48次`deepseek-pro` task calls、strict pair、opaque masking、schedule、sign test、alpha 1/20、failure policy、baselines和全部live barriers不变。

如果primary显著，最宽只使用`prospective_primary_positive_on_fixed_development_constructed_finite_DSL_challenge`；不能使用`confirmatory_primary`、`independent_heldout_confirmation`、自然机会率、模型总体能力、内部entropy因果、人类未知发现或真实世界外推等标签。另注意`validate_scan_plan()`会读取旧sealed private result（88.6MB），compact extraction应逐shard单独校验、只保留strata eligibility，不能盲调。

## 2026-08-28 benchmark构造器恢复点

新增`src/spark_strong_k4_utilization_primary_benchmark.py`及合成测试。config用canonical hash整体锁定，allowed/forbidden evidence labels、route、analysis、baseline与live barriers不能在同一protocol id下漂移；plan精确绑定evidence scope、upstreams、q6 cohort、route、source manifest与Git commit，并且正式plan命令只接受clean worktree。construct只有在reviewed plan semantic/file hashes同时匹配后才打开private shards；它不读取967MB单体result，而是逐一验证128 shards并从全部1024 worlds重新执行q6/q6 matcher，不能沿用或扩展旧q4 assignment。

public只允许固定顶层字段和48条`task_id/rendered_prompt/prompt_sha256`记录；private按world seed重新构造target-free D0/parent/old subtrees并与shard parent hash和prompt逐项核对；result的selected indices/stratum counts从private pairs重算。所有构造产物仍为`evidence=false`，world/response标签分别为`outcome_conditioned_development_only`与`preregistered_prospective_primary`，passing construction不授权provider calls。

## 2026-09-08 benchmark v2正式离线构造

在当前`main`与clean protocol source下，正式construct使用已审核plan canonical SHA `2e0750569083c5dc00615c29678521a58d4975220b2f86535138291112307f31`和plan file SHA `53403b4685d6d3b4046b39b4af5f2e5d8c13075e7628f2489b182fb439d772f6`通过双hash屏障，随后顺序验证safe manifest绑定的128个shard。验证覆盖1024 worlds和967,864,320B；全部path/range/size/raw SHA/inner SHA/schema检查通过。fresh strict matcher报告57个strict pair candidates，并按冻结schedule选择24个互异development worlds：`affine_commutative`、`affine_directional`、`affine_multiplicative`、`pairwise_variable`各6个，形成24 pairs / 48 tasks。

正式产物位于`artifacts/spark-strong-k4-utilization-primary-benchmark-v2-20260827/`：

- `public.json`：file SHA `15685ceb9502caec31589fa241ebb6c94daf59f3e1814f7054c1ebdbdb19c07f`；canonical manifest SHA `b154f959e2cf5df19070705dd0645d3bbab163784926a6f364e38428b02170cb`。
- `private.json`：file SHA `bbe76032ba8d120c9eb7866cabb3643e81f619fbf91bfbed4c40237e3588f06a`；canonical key SHA `dde7328e7183de273dfb1e68d066c846da9038cf89efcbba9e062c491995c495`。它含评分与world绑定，只在本机以mode 0600保存，被Git忽略，绝不能force-add。
- `result.json`：file SHA `e424369b5db2a2051315440a2d66fed504a37b32d4ef80bdc8166df4689d7845`；canonical result SHA `7003e1b954f70519f724d8022fc428ae3e106d61bd41fd0a14303715132cdbd3`。
- public/private共同绑定的private design commitment为`b6ee5ff26eb36ecaf19e1d8a17e53ab24b29b423b12ee7943ed888ea92c49adc`。
- safe `public.json`与`result.json`已由commit `86a89af4602eb824827962d0aef27f2567b0d0f3`单独封存；`private.json`不在该commit或Git index中。

内置validator与独立`luna_worker`均复核PASS：public恰有48个唯一task，private恰有24个pair且与public形成exact 48-task bijection，四strata各6，每pair两臂correct action互异且K2 opportunity count均为1，三文件hash/provenance/cross-binding一致。public/result未包含target、world seed、correct action、motif、arm label、private option mapping或scoring key；result按冻结contract公开24个selected candidate indices作为development provenance摘要，但不公开答案。focused unittest 8/8与`git diff --check`通过。

本步骤只完成匿名试卷构造，不是模型实验。`result.json`明确记录`evidence=false`、`independent_heldout_confirmation=false`、`model_outputs_read=false`、`provider_calls_made=0`、`provider_calls_authorized=false`。它支持“冻结benchmark已按协议可复现地构造完成”，不支持Opportunity utilization、模型能力、entropy因果或现实世界发现结论。

## 2026-09-08 strict-q6 live协议与plan冻结

新增独立live层`configs/spark-strong-k4-utilization-primary-live-v1.json`、`src/spark_strong_k4_utilization_primary_live.py`及对应测试。它没有修改或重生既有benchmark config/plan/public/private/result。live config raw SHA为`b21b2d7a193c674920feaf20e15815f668096f6ccfb9c67c8a16bb5cf1e736f2`，canonical SHA为`dc6b5f686fc9bd6840b38ee45442af890bd8168991610e37d69a675594e9bd85`；live source freeze commit为`72c509992053972f08405f8d9392a5130dae1ac4`，source manifest为`23a23195ef1a6bdd00a12a2880959edc2ef1db3f968f7d6c434486faf670ec69`。

协议将执行严格分成`plan -> canary -> authorize -> run -> analyze`五步。只有`canary`和`run`接受显式执行授权；公开执行函数还要求`execute=True`并强制current-source验证。正式调用前会重新验证construction safe inputs及private key的存在/0600元数据，但不读取private bytes；只有完整48-call generation bundle通过public-only验证后，analysis才以绑定路径、`O_NOFOLLOW`、regular-file/inode/mode/raw-SHA检查打开private key。received-invalid消耗slot且不重试；transport/HTTP/payload/route-contract失败使整个primary non-evaluable，不允许retry、resume、fallback或replacement。

正式live plan位于`artifacts/spark-strong-k4-utilization-primary-live-v1-20260908/plan.json`，plan commit为`545eb8bcafe0f895509669180916119747118046`，file SHA为`f8d99fba3c50510beb6180c2f14fc34d91fed30ee0ef61caff1c923ee07641de`，canonical SHA为`4ca4f2371f897ca06190ac9fa820b8e8a2bfac5146aa810f8ade94696fd61193`。它冻结4个retired target-free pairs、8个canary calls；schedule SHA为`8bd723a25caf8ed61d22399f81c9976d09fda037670da74e60dd0aa86c6bed8f`，prompt-set SHA为`9a2efe1326309a0c49ac44f813161601c29a5b62bfc02b9169ab3976462267f7`。phase×arm均为2，与48个formal tasks的ID及prompt hash交集均为空。它同时冻结joint-exchangeability justification、response/failure policy、单侧exact sign test `alpha=1/20`、public/private file hash binding，并在看到任何primary输出前将exploratory routes冻结为空；未来若要运行`deepseek-flash`或`glm-5.2`必须建立新协议。

内置validator与独立`luna_worker`对source lineage、construction bindings、schedule、prompt隔离、information barriers和hash均复核PASS。新live focused tests为12/12，相关旧协议回归为46/46，`py_compile`与diff check通过；全部测试均使用fake responses，没有网络或真实provider调用。一次repository-wide run在耗时较长的历史`layered-v1` sealed replay测试报告既有路径的analysis hash不一致后停止；该测试不经过新live模块，本轮没有改写该历史artifact或把全仓测试误报为PASS。

本步骤仍不是模型实验：plan记录`private_key_bytes_read=false`、`provider_calls_made=0`、`primary_calls_authorized=false`，只说明live协议和匿名canary试卷已冻结，不支持或反驳Opportunity utilization假设。

## 当前恢复点

下一步是唯一尚未执行的live动作：按已封存plan对`deepseek-pro`运行8次target-free paired canary。它只检查route、请求/响应contract、opaque option格式及joint observable状态是否出现明显不对称；它不是模型证据，也不能证明exchangeability。canary通过后必须停下，由人类审阅exchangeability假设并显式生成authorization artifact；在authorization生成前严禁48次primary calls。

执行canary时必须使用plan file SHA `f8d99fba3c50510beb6180c2f14fc34d91fed30ee0ef61caff1c923ee07641de`，且必须显式提供`--execute`。若canary收到transport failure，不得重跑同一协议；若内容格式失败，可封存failed canary，但不能授权primary。当前协议不运行任何exploratory route。

不要修改`source_manifest`范围内文件，否则当前live plan立即失效。换设备继续时，Git可恢复live plan及public/result；analysis仍需要另行安全转移exact `private.json`，恢复后设为mode 0600并核对file SHA `bbe76032ba8d120c9eb7866cabb3643e81f619fbf91bfbed4c40237e3588f06a`。
