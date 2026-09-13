# 新 world 微型闭环：公共代码基线结果（2026-09-10）

## 状态与范围

本次是固定 12 个新 world 的离线、描述性代码基线，不包含任何模型调用。对每个 world 运行三种公开策略：

- `random`：固定 hash 顺序查询两个 evidence 点；
- `balanced`：在公开 DSL reservoir 的当前相容候选中，选择二元划分尽量均衡的查询点；
- `repeat`：固定 hash 顺序重复两个 D0 点，不获得新信息。

每条轨迹均为 D0 候选、第一次查询后候选、第二次查询后候选三个阶段。候选包括非恒定最小节点规则（`nonconstant_minnode`）、允许常数的最小节点规则（`minnode_allow_constants`）、固定 parent，以及常数 0/1 参考。评分使用私有 64 点 test 和 125 点全域核对；策略选择只使用公开 reservoir 和当时已经观察到的标签。

共完成 `12 × 3 = 36` 条轨迹、`12 × 3 × 3 = 108` 个阶段评分。公开 reservoir 有 1,767 个候选。逐 world 结果、等权汇总和 hash 审计保存在 [`artifacts/microloop-baselines-20260910/`](../artifacts/microloop-baselines-20260910/)。`provider_calls=0`，没有按结果筛选或替换 world。

## 最终阶段结果

下表是 12 个 world 等权平均；“perfect/exact”分别是 64 点 test 全对和 125 点全域完全一致的 world 数。

| 策略 | 候选 | 平均 test 正确数 / 64 | 平均准确率 | perfect | exact | 可见观察一致 world |
|---|---|---:|---:|---:|---:|---:|
| random | nonconstant_minnode | 45.583 | 71.224% | 0/12 | 0/12 | 12/12 |
| random | minnode_allow_constants | 49.333 | 77.083% | 0/12 | 0/12 | 12/12 |
| random | parent | 45.667 | 71.354% | 0/12 | 0/12 | 8/12 |
| random | constant_0 | 49.583 | 77.474% | 0/12 | 0/12 | 7/12 |
| random | constant_1 | 14.417 | 22.526% | 0/12 | 0/12 | 0/12 |
| balanced | nonconstant_minnode | 49.250 | 76.953% | 0/12 | 0/12 | 12/12 |
| balanced | minnode_allow_constants | 53.667 | 83.854% | 0/12 | 0/12 | 12/12 |
| balanced | parent | 45.667 | 71.354% | 0/12 | 0/12 | 6/12 |
| balanced | constant_0 | 49.583 | 77.474% | 0/12 | 0/12 | 5/12 |
| balanced | constant_1 | 14.417 | 22.526% | 0/12 | 0/12 | 0/12 |
| repeat | nonconstant_minnode | 43.583 | 68.099% | 0/12 | 0/12 | 12/12 |
| repeat | minnode_allow_constants | 49.583 | 77.474% | 0/12 | 0/12 | 12/12 |
| repeat | parent | 45.667 | 71.354% | 0/12 | 0/12 | 12/12 |
| repeat | constant_0 | 49.583 | 77.474% | 0/12 | 0/12 | 12/12 |
| repeat | constant_1 | 14.417 | 22.526% | 0/12 | 0/12 | 0/12 |

## 配对和阶段变化

对主要非恒定候选，`balanced − random` 的最终 test 差异为 `+3.667` 点（`+5.729` 个百分点），12 个 world 中 7 胜、1 负、4 平；`balanced − repeat` 为 `+5.667` 点（`+8.854` 个百分点），8 胜、1 负、3 平。允许常数的最小节点候选对应差异分别为 `+4.333` 点（`+6.771` 个百分点，5 胜/1 负/6 平）和 `+4.083` 点（`+6.380` 个百分点，4 胜/3 负/5 平）。这些是成对描述统计，不是显著性检验。

从初始到最终阶段，`balanced` 的非恒定最小节点候选平均上升 `+8.854` 个百分点，允许常数候选上升 `+6.380` 个百分点；`random` 分别为 `+3.125` 和 `−0.391` 个百分点；`repeat` 两者均为 0。固定 parent 的阶段分数不变，这是预期的代码参照行为。

## 解释边界

常数 0 已达到 `77.474%`，高于 random 和 balanced 的非恒定候选；这显示 test 标签存在明显类别不平衡，单看较高的 raw accuracy 不能当作发现规律的证据。balanced 的允许常数候选虽然达到 `83.854%`，相对 constant 0 只高 `4.083` 个 test 点，而且没有任何 world 达到 test 全对或 125 点全域精确恢复。balanced 相对 random 的优势因此只能说明该公开的均衡查询启发式在这组有限构造上产生了描述性差异，不能证明模型主动发现、泛化科学规律或增熵—降熵机制。

此外，balanced 是能枚举 1,767 条公开 DSL 候选的代码策略，不能直接视为模型可达到的能力上限；random/repeat 只是预先固定的比较策略。12 个 world 各只有一条轨迹，本文不据此估计稳定成功概率，也没有进行新的推断统计。正式模型轨迹仍需独立冻结配置和预算后执行，且必须沿用同一固定材料和评分边界。

## 可复核信息

- 输入 `public.json` SHA-256：`faef2604aa69fc5c4afac47b310bff12833f43ff01d9ce04ce1f144aa9e1573e`
- 输入 `private.json` SHA-256：`d7446002fc49abb0a53d7e88ba4556dae3538db76b95de8569a03ca3505ed676`
- 输入 `audit.json` SHA-256：`3815e47b80bda13149079033fbf9a1bc3cf0bda60e0b8607d5a46c5379978d7c`
- 基线 `summary.json` SHA-256：`ac5c334d942bb8f85d62af29fb46508b8b99d5f3fbbffc5df7b2b18df46975f8`

基线代码见 [`microloop_baseline_analysis_20260910.py`](microloop_baseline_analysis_20260910.py)，单元测试为 [`test_microloop_baseline_analysis_20260910.py`](test_microloop_baseline_analysis_20260910.py)。
