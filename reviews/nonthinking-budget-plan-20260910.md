# 第3项：non-thinking输出预算敏感性（2026-09-10）

状态：用户已要求设置goal并执行本项；goal已创建，历史输出诊断和固定小组／材料草案完成。**新API精确预算待确认，尚无新调用**。本文件不覆盖旧primary或任何冻结模型结果。

## 1. 历史诊断结果先于新的调用

核对原primary的plan、generation及原24world公共／私有材料文件hash，并逐条核对响应prompt绑定。原48条non-thinking响应结果：

| 检查 | 结果 |
|---|---|
| 请求／返回模型名 | deepseek-v4-pro |
| thinking／temperature／输出上限 | disabled／0.2／256 |
| 收到且格式有效 | 48/48 |
| finish_reason | 48条均stop，0条length |
| 实际输出token最少／中位／最多 | 8／10／14 |
| 实际输出token合计 | 469 |
| 实际输入token合计 | 32026 |
| 达到256上限的响应 | 0/48 |
| reasoning_tokens字段缺省 | 48/48；不能把缺省当成实测内部推理消耗为0 |

输出分布：8token×7、9×13、10×15、11×12、14×1。原结果依然是own5/48、cross6/48、完整双臂0/24。

**当前没有“原答案因为256上限被截断或格式失败而低分”的证据。** 因此提高上限的新增信息价值比发现大量截断时低。本项若继续，检验的是放宽请求上限是否改变行为，而不是预设原请求缺少足够输出空间。也不把更大上限等同实际增加推理算力。

## 2. 官方接口核对

2026-09-10读取的[DeepSeek模型说明](https://api-docs.deepseek.com/quick_start/pricing/)列出Pro同时支持thinking和non-thinking、最大输出384K；[thinking指南](https://api-docs.deepseek.com/guides/thinking_mode/)说明可以显式设置 `thinking.type=disabled`。拟用8192作为32倍放宽档，在官方列出的输出范围内，不要求使用最大额度；实际请求接受情况仍须调用时验证，文档检查不是已调通的新canary。

[官方接入说明](https://api-docs.deepseek.com/)当前将Pro模型名映射到V4-Pro-0813；同名模型不是不可变snapshot证据，不能仅凭返回model字符串保证跨日权重／路由完全一致。没有证据表明9月8日至今实际发生了更新；这里只记录比较限制。

## 3. 固定小组和唯一预算变量

从原24world中选8个（不是刚做过候选规律生成的9个world）：四个原construction stratum各选2个。按 `SHA256('nonthinking-budget-subset-v1-20260910:' + pair_anchor_sha256)` 排序取前2，保留两臂。算法只用既有stratum和anchor，不用历史模型成功／失败、目标或正确动作挑题。研究人员已知旧总体结果，故仍是事后设计的描述性follow-up，不声称前瞻独立样本。

共16个原任务，每题同期运行以下两配置：

| 配置 | thinking | temperature | max_tokens | 正式调用数 |
|---|---|---:|---:|---:|
| 同期低上限 | disabled | 0.2 | 256 | 16 |
| 同期高上限 | disabled | 0.2 | 8192 | 16 |

模型名、endpoint、原rendered_prompt、JSON expression输出、无工具／独立上下文、其余采样设置保持相同；不添加“请详细推理”等新提示，不显式设置reasoning_effort，不更改top_p。沿用原120秒技术timeout于两条件；若不足或接口拒绝，保存失败，不擅自增加上限或换模型。

每个原任务两档相邻，先后顺序按固定pair位置与arm交替，16个任务各档先出现8次；32任务公共草案已保存。正式runner将为条件槽生成不同记录ID，发送内容只取原rendered_prompt，不发送pair、条件或私有标签。3秒错峰；并发仅用于完成请求，不改变科学评分。

同期低上限是为了让本次主要比较只改变max_tokens，而非高上限新结果对旧低上限的跨日差异；历史non-thinking及thinking仅报告同8world的描述性参照，不能将整套thinking配置差异解释成单一开关因果效应。

## 4. 需确认的新预算与停止条件

- 32正式请求，最多另2次技术重试，总数最多34，无额外canary。
- 正式输出上限合计 `16×256 + 16×8192 = 135168` tokens；将2次重试均按高档估计，总输出上限151552 tokens。此数不含输入，也不是实际用量预测；原低档实际每题仅8–14tokens。
- 首轮结束后最多对2个按固定序号优先的技术失败各重试一次，限可重试传输异常或429/5xx；错误答案、无效内容、截断不重试。
- 非可重试鉴权／模型／接口契约错误停止新派发并保存现场。不能因表现不佳扩大样本、追加thinking或改提示后混并结果。
- 不复用上一轮27＋2的调用预算。本轮尚未确认32＋2，因此目前仅离线产物，无正式冻结plan／live runner／模型调用。

## 5. 分析和解释范围

沿用原action评分含义，在选中的固定8world／16臂内报告own、cross、双臂正确、配对净方向及每world变化；缺失／无效保留分母。历史记录按相同任务ID对齐，不拿8world新结果直接与24world总成功率当成配对差。

新增检查实际token、finish、格式有效率、reasoning遥测、provider模型／可用fingerprint，不只看max_tokens。主要对照为同期8192−256；旧低档与新低档也报告差异，用于展示跨次生成变化。无新增确认性p值，不继承旧exact power。

- 高上限若改善且同期低上限未同样改善：增加请求预算敏感性的描述性证据；若仍只输出十余token，也不能称为实际长推理带来改善。
- 高低相近：说明本次上限放宽未带来明显改善，与原未截断现象一致；不能证明thinking内部机制。
- 若两个同期条件均较历史变化：不把共同变化归因于高上限，应保留采样／时间等解释。
- 即使高档追上历史thinking，也仅增强配置／预算解释，不证明其一般能力相等；没追上也不能证伪non-thinking在其他提示／配置下的能力。

本项完成后统一整理论文结果与讨论，不自动发起其他模型或新增样本。

## 6. 已保存、测试及恢复

- `reviews/nonthinking_budget_audit_20260910.py`：原48响应诊断和结果无关的分层固定小组。
- `reviews/test_nonthinking_budget_audit_20260910.py`：2测试通过，验证选择不依赖输出／输入顺序、各层2world、真实hash及两条件只改cap。
- `artifacts/nonthinking-budget-draft-20260910/audit.json`、`public-draft.json`：0600、独立排他创建，包含固定8pair／16task身份和32条草案。
- `python3 reviews/nonthinking_budget_audit_20260910.py --verify`已通过，0模型调用。下次只读verify，不重建覆盖。

恢复点：等待用户确认32正式＋最多2技术重试以及8192高档；确认后实现／测试／冻结运行与原action评分的对接，再调用并自动保存汇总。
