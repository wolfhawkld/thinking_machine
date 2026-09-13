# 非thinking输出预算敏感性：结果与结论（2026-09-10）

状态：用户批准的32正式＋最多2技术重试已执行完毕，全部32槽获得有效响应。发生1次timeout，按既定规则重试成功，实际33次物理请求，未使用第2次备用重试。会话网络中断后只读恢复，未重启或追加请求；generation、原始响应审计、analysis和自动results.md均已由运行器保存，恢复后再次核验通过。原调用句柄已丢失，不据此声称观察到原进程exit code；终态台账及全套产物证明本轮已完成。

## 1. 设计和历史检查

按[方案](nonthinking-budget-plan-20260910.md)及[正式协议](nonthinking-budget-live-protocol-20260910.md)，从原24world中按固定stratum与anchor hash选8个（四层各2），保留两臂16任务。选择算法不读取历史成绩；研究人员已知旧结果，故仍是事后设计的探索性对照。每题同期比较max_tokens256与8192，其他请求内容相同：deepseek-v4-pro、thinking disabled、temperature0.2、原提示、JSON输出、120秒技术timeout。

原48条non-thinking响应全部有效且finish=stop，实际输出8–14token，未触及256上限。此检查已经不支持“原答案被输出上限截断”这一直接解释。本轮继续检验放宽请求上限本身是否改变行为，而非强迫模型使用更多token或另加长推理提示。

## 2. 同期对照与同题历史参照

| 配置 | own /16 | cross /16 | 完整双臂 /8 | own−cross | favorable / adverse / tie |
|---|---:|---:|---:|---:|---|
| 本轮non-thinking，256 | 1 | 0 | 0 | +1 | 1/0/7 |
| 本轮non-thinking，8192 | 1 | 1 | 0 | 0 | 0/0/8 |
| 历史non-thinking，同16任务 | 1 | 2 | 0 | −1 | 0/1/7 |
| 历史thinking high，同16任务 | 11 | 1 | 3 | +10 | 7/0/1 |

四行都使用相同8world／16臂，不混入原24world总分。同期两档各16/16有效、0缺失、0截断。

同期8192−256：

- own总差0；逐world赢／输／平为0/0/8。
- 完整双臂总差0；逐world赢／输／平为0/0/8。
- 配对净分总差−1；逐world赢／输／平为0/1/7。

这不是所有选项选择都完全相同，而是own和完整成功指标没有改善；高档额外出现了一次cross。新低档对历史低档的own与完整成功也无变化，cross减少2次、净分增加2，提示跨次生成仍可能波动。没有新增p值，不继承原sign test或power；未改善不等于已证明两档等效。

## 3. 实际输出与失败记录

| 本轮档位 | input tokens | output tokens | 输出最小／中位／最大 | finish=stop | 显式reasoning内容 |
|---|---:|---:|---|---:|---|
| 256 | 10682 | 158 | 8／10／13 | 16/16 | 16条均未出现 |
| 8192 | 10682 | 158 | 8／10／13 | 16/16 | 16条均未出现 |

32条最终成功响应的已知用量为input21364、output316，合计21680tokens。**一次timeout的消耗未知，所以不是包含所有33次物理请求的完整账单**；generation的usage_complete保留false。超时发生在调度index1（8192档），delivery_ambiguous=true，无原始返回，首轮结束后在同槽重试成功。不能断言该timeout一定由用户端会话断网引起，也不据此重复其他已完成请求。

实际输出仍只有十余token，两个档位均未触及上限；没有观察到增加实际可见输出的效果。没有返回reasoning内容不等于测量了模型内部推理消耗为0；本项也没有让两种模式的实际计算量相等。

## 4. 结论及论文影响

在当前固定提示、8个条件化构造world和本次采样下，将non-thinking的输出上限提高32倍，没有改善own或完整双臂成功，且没有追上同题历史thinking配置。

因此，“原non-thinking低表现主要是256输出上限把答案截断”的解释缺乏证据；单纯将上限放宽至8192也未消除已有配置差距。这收窄了一个直接预算解释，**不是证明thinking开关、内部增熵—降熵或其他机制的因果作用**。历史thinking还涉及实际输出量、采样方式及时间等差异；也未检验其他提示、显式长推理、更多采样或所有预算档位。

三项补充任务至此均按已同意的小规模范围交付：针对两种简单策略的九world挑战获得有限正向；冗余参考对照结果混合；另行新增证据候选规律实验的新信息特异收益较弱；本项未发现放宽non-thinking上限的收益。旧阴性与正向记录全部保留，不将不同cohort或评分终点合并成总成功率。下一阶段是整稿，不自动扩大实验。

## 5. 保存与复核证据

目录 `artifacts/nonthinking-budget-live-20260910/`：plan/private、33个attempt、32个raw-response、attempts.jsonl、generation、response-audit、analysis、results.md。9项本轮测试及3项共享链路回归通过；其中完整模拟覆盖成功、无效不重试、34次最坏技术预算、raw重放、分析保存／重建及独立own计数。

恢复核验：

1. `python3 reviews/nonthinking_budget_live_20260910.py validate`：32任务、原草案、源码／历史文件hash与两档映射通过。
2. `python3 reviews/nonthinking_budget_response_audit_20260910.py`：33物理attempt、32raw重放、1技术重试、32槽与台账一致。
3. `python3 reviews/nonthinking_budget_analysis_20260910.py --verify`：评分与自动报告重建一致。
4. 独立从每槽最后一个raw响应提取选项、对照原私有own／cross标签直接计数，未使用本轮score_pair／summarize；两档全部8个world逐行与analysis一致，分别(own,cross,full)=(1,0,0)和(1,1,0)。

| 产物 | SHA256 |
|---|---|
| plan.json | 4b6fa1244faea61c8dfdace485878b4f27cb245063e220a6f2192815e531564b |
| generation.json | 60952f91b5dd50730f98ff97eeddf0a3f8d413c181f8844a0956344994e2f93e |
| attempts.jsonl | 26eb5c6f351e43b001110f4b31b411b8432b7d4fe194d383c07cca5cb7072446 |
| analysis.json | 0c1dd49a085713e1006c55d00d11031797eb2c6b15d8869f9ac6294602a203b7 |
| response-audit.json | 08647852a84719396c7d428a5e4d83a7465c09e04b707c1c97ad91c2216c4e4e |

所有旧冻结文件不改，本轮只读恢复不产生新模型调用。结果与讨论、TODO和HANDOFF更新后停止；本轮没有commit/push授权，只保存本地文件。
