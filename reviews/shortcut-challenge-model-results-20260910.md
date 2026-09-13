# 九world简单策略挑战：模型结果（2026-09-10）

## 结论

本轮已完成，18次正式请求全部得到有效选项，0技术重试、0截断、18次均返回reasoning内容。DeepSeek thinking在9个world中有2个双臂全对；筛选时固定的非恒定最少节点、非恒定最大差异策略均为0/9。

这提供了有限的正向行为证据：在这两个成功world上，模型输出不能由始终执行任一指定确定性策略充分解释。但没有证明模型不使用其他规则、策略混合或随机选择，也没有证明内部增熵—降熵、通用科学发现或RSI。

## 冻结与样本

见 [执行协议](shortcut-challenge-live-plan-20260910.md)及 [完整搜索报告](shortcut-challenge-1024-results-20260910.md)。从完整新1024池选联合失败类的全部9个不同world，每个选pair SHA256最小的一对；选题及评分代码在响应前冻结。18任务按固定摘要顺序发送，显示顺序沿用并冻结原诊断顺序。全部26种策略发送前公开特征重放一致。

实际分层为commutative3、directional4、pairwise2、multiplicative0；这是固定选择规则的结果，不是四层平衡cohort。未因缺失乘法层再换题。本轮不与旧24world池合并为独立的33个确认性样本，也不继承旧检验的power或alpha。

配置与既有thinking随访一致：deepseek-v4-pro、thinking enabled、reasoning_effort high、max_tokens131072、JSON输出、非流式，temperature/top_p不显式指定。18正式请求，预算最多另2技术重试，本轮未使用；没有额外canary请求。

## 主要描述性结果

| 方法 | own正确 / 18臂 | cross正确 / 18臂 | 双臂全对 / 9 worlds |
|---|---:|---:|---:|
| DeepSeek thinking | 9 | 3 | 2 |
| 非恒定＋最少节点 | 7 | 3 | 0 |
| 非恒定＋最大行为差异 | 5 | 3 | 0 |

2个world双臂正确、5个只对一臂、2个两臂均错。九world双臂均为有效答案，因而本轮全分母与有效配对子集结果一致。own/cross是当前context和对侧context的动作标签匹配，不是模型独立产出新规则的数量。

对每个重点策略，双臂成功交叉表均为：模型独有2、策略独有0、共同成功0、均未成功7。策略0/9是挑战筛选条件的一部分，不能将它当作独立发现；模型2/9是在冻结后观测到的结果。

全部26种基线在这九对上双臂全对均为0，完整比较保留在analysis.json。这里不能将26个基线当作26次独立支持，尤其固定动作/固定显示位置不能实现不同正确动作的双臂切换，在该几何下本来就受到结构限制。

| 分层 | worlds | own / 臂数 | cross / 臂数 | 双臂全对 |
|---|---:|---:|---:|---:|
| commutative | 3 | 3/6 | 2/6 | 1/3 |
| directional | 4 | 5/8 | 1/8 | 1/4 |
| pairwise | 2 | 1/4 | 0/4 | 0/2 |

## 如何改变当前解释

- 不能再把所有观察到的成功都直接归结为固定的这两种简单策略：本轮存在模型完整成功而它们不完整成功的world。
- 这只是小规模、条件化挑战上的有限证据；own仅9/18，完整成功2/9，没有得到普遍可靠的能力表现，也没有按结果增加样本来追求显著性。
- 旧thinking随访为11/24双臂成功，本轮为2/9；两批world及筛选难度不同，不能把差值解释为模型能力退化、context因果效应或确定的shortcut依赖程度。
- 不计算新的p值，不声称统计显著或稳定优越性。仍可由别的启发式、混合策略、采样和有限DSL结构解释部分成功。
- 第一项补充实验作为“九world联合策略失败挑战”已完成；不宣称原设想的更强双臂误导/完全排除简单策略目标都已完成。第2项context有效关联消融、第3项非thinking预算敏感性仍未执行。

## 保存与核验

目录 `artifacts/shortcut-challenge-live-20260910/` 保存plan.json、private.json、attempts.jsonl、每次attempt JSON及原始response body、generation.json、analysis.json、response-audit.json。原始返回和评分材料0600，正文不公开私有标签/seed/响应全文。

会话32918正常exit0，随后独立评分自动完成。51项相关测试通过；事后逐条重放18份原始响应解析，核对18个开始/结束事件、无重试、slot映射、全部9个world评分及usage，验证通过。不增加模型调用。

- 计划SHA256：`66b98756b83016fe4c702ea914d1be7ff984eb8f37873e79ad480a0e842496c2`。
- generation SHA256：`ddcba75e07be82c46e40f5c5f5ebeb7b49ff5557ae4cc337fb31358ce2e114ec`。
- analysis SHA256：`1975ea9dd6797db98d95ac74ba99373d52f365ac27ebf86c0bec0ceacc7047e0`。
- 实际input13406、output379301 token；usage完整。output包含生成内容及供应商计入的推理消耗，不能直接等同单独的reasoning token数量。
- 安全只读复核：`python3 reviews/shortcut_challenge_live_audit_20260910.py`。不要重新执行run、材料build或analysis来覆盖既有结果。

## 后续恢复点

本轮预算已经执行完毕，不继续请求模型。下一项为context有效关联消融，先依据 [语义预审](context-ablation-semantics-20260910.md)固定可解释的干预：直接替换fragment会改变动作语义，不能沿用原标签。需选定干预及单独预算后才能调用模型；不把一次正向结果作为自动扩模型或扩样的理由。
