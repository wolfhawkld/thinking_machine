# Spark-to-Knowledge 研究交接（更新至2026-09-13）

## 同事交接入口

同事请先看[复现与独立review指南](COLLEAGUE-HANDOFF-20260913.md)，按其中顺序读当前英文稿、运行最小包，再对照历史评价；[反馈模板](reviews/colleague-review-template-20260913.md)可复制填写。当前Git交付只支持新版两轮评分的便携复现，不包含被忽略的完整artifacts；缺失材料按具体路径和目的申请补充，不自动运行新实验或索取API凭证。

## 最新续做：公式、精确提示与最小离线复现包

用户确认无需补新review后批准继续。本轮已精简少量重复措辞，补准确率/改善量/配对差公式，优化标题断词；当前正文仍7+1页，补充6页，数值与版面检查通过。新增[精确提示附录](paper-prompts-en-20260913.md)、[最小复现候选包](paper-reproduction-20260913/README.md)，补充S8说明覆盖范围。

提示集含287条原始user字符串及hash：48原入口、18挑战、54参考、27单观察、32预算、108新版两轮阶段。新版各阶段提示与冻结request逐字一致，例子按首项/world0选择，不挑正例。包内只含显式白名单字段、原DSL和离线评分脚本；无凭证/provider原始信封/推理轨迹，含评估专用test标签，禁止加入模型输入。

仅复制6个文件到临时目录、清空环境、Python -I运行，复算108个阶段分数与观察相容性全部通过；详见[复现检查](paper-build-20260913/reproduction-check.json)。这是同机隔离评分，不是独立机器验证或整篇论文复现。旧microloop及其他实验的全量可移植重算、完整请求设置和构造复现尚未包入，公开发布/许可检查仍待确认。见[当日TODO](TODO-20260913.md)和[检查报告更新](reviews/paper-editorial-check-20260913.md)。无新模型调用、实验修改、commit/push或上传。

## 最新：已按中立评估v2完成英文正文整合

用户批准后，本轮已执行论文方向调整，以[20260913英文正文](paper-manuscript-en-20260913.md)、[补充](paper-supplement-en-20260913.md)、[当前主张轴线](paper-claim-evidence-status-20260913.md)和[结构记录](paper-claims-and-structure-20260913.md)为现行写作入口。旧日期中英文与PDF完整保留，以下“尚未改正文”的记录均属历史。

当前主线：局部context入口利用、反例响应与证据相容修订有观察支持，但本轮active未形成平均主动预测优势。保留11/24、15/24入口正例、挑战2/9及4/12局部改善；正文同时报告新版65.10%对random 73.05%、−7.94个百分点、2胜8负2平，及repeat/恒0/强代码基线、两标签限制、样本复用和三版本修复历史。没有将局部正例升级为熵机制、完整科学发现或RSI证明，也没有把负差扩写成普遍能力否定。

生成[正文PDF](paper-build-20260913/paper-manuscript-en-20260913.pdf)8页（主文7+参考/说明1）与[补充PDF](paper-build-20260913/paper-supplement-en-20260913.pdf)6页。数值与链接、108原响应重放、编译/页边界和缩略目视核验通过；详见[检查报告](reviews/paper-editorial-check-20260913.md)及[当日TODO](TODO-20260913.md)。没有修改冻结代码/评分/响应，没有新模型调用、goal、commit/push或外部上传。

下一步仍是写作精简与复现准备：目标格式、精确提示附录、去凭证可分发清单、独立环境检查、聚焦新颖性论证。不是已投稿就绪，也不自动启动新实验。中文稿保留历史，尚未同步新版。

## 当前评估v2：研究仍有探索价值，新增具体写作修改建议

用户确认中立复核后的判断并要求修订总结。[当前评估v2](reviews/research-hypothesis-value-assessment-20260911.md)为现行入口，[v1](reviews/research-hypothesis-value-assessment-20260911-v1.md)原文留档。当前判断：局部入口／反例响应／候选修订有证据，但稳定主动预测收益与内部机制未建立；这不等于整体研究价值应“大幅折价”。存在性、平均效果、机制与发表价值分开判断，最新active−random负差照实保留。

总结已补4/12局部改善、信息约束与预测区别、因果定位未完成、repeat／恒0／两标签限制、样本复用，以及英文现稿的具体章节修改清单。下一步建议先更新主张轴线／结构，再整合正文与补充、标题摘要结论、复现入口和PDF；本轮仅把建议写入总结，没有改正文、实验代码、冻结结果或启动新任务。没有模型请求、goal、commit/push或外部上传。下方v1价值判断为历史，不能覆盖本段。

## 最新讨论记录：原始假设偏差与研究价值评估已保存

用户要求保存[整体评估](reviews/research-hypothesis-value-assessment-20260911.md)。核心判断：原始“探索带来更好规律”的正向实证期待尚未兑现，价值方向转为局部能力及其后续边界；最新主动对随机为负向，不能称“已有正向只是样本少”，也不据此否定全部原始设想。区分原始实证价值、探索论文价值与项目效率损失，不给无依据的折扣百分比。

建议收束为包含正向局部观察、负向后续结果及方法局限的探索研究，不为挽回原始结论自动追加实验。此为已保存的助手分析／建议，不是用户已批准写作或投稿。本轮只改记录与入口，不改正文／冻结结果，不新增模型调用、goal或commit/push；没有运行中的模型请求。

## 最新终态2026-09-11：新版微型闭环完成并核验

36轨迹108响应全部合法／当时可见一致，0失败／缺失／重试／截断，usage总1,081,946tokens。108raw及阶段提示绑定核对、独立逐点评分与aggregate重放一致；无新API调用。详见[新版模型结果](reviews/microloop-revised-model-results-20260911.md)。目录 `artifacts/microloop-revised-live-20260911/`，审计 `artifacts/microloop-revised-audit-20260911.json`。没有未完成请求，不重启。

active/random/repeat最终65.10/73.05/49.87%，初始→最终−6.25/+1.69/−17.71pp；active−random−7.94pp，2胜8负2平。主动反馈反驳当前候选16/24（random6/24），但未转化为主动预测优势；全域恢复均0/12。语法bug已解决，不能再用于解释本轮负向差异；也不是普遍能力证伪。下一步建议整合论文与主张轴线，不自动付费扩实验。旧轮独立保留，无commit/push或新goal。下方运行中为历史。

## 当前2026-09-11：新版提示真实重测已获批并启动

用户明确批准新的108正式＋2技术重试预算。独立协议 `reviews/microloop-revised-live-protocol-20260911.md`，正式目录 `artifacts/microloop-revised-live-20260911/`。已冻结verify通过；新增2项集成测试（包含108个模拟响应完整链路）确认真实runner调用新版提示／解析及分析，旧模块未改。4并发、thinking high、131072、3600秒，读取中断修复已接入。

启动命令 `python3 reviews/microloop_revised_live_20260911.py run`，会话30835。不要重复启动／修改冻结代码；完成后自动records／analysis／complete。同12world的修订版探索性重测，不是未见确认集，结果不与旧轮合并。无新goal或commit/push。下方“新预算待确认／未真实重测”均为历史。

## 2026-09-11最新：免费语法契约修复及重放完成

用户批准免费修复与重放。独立 `microloop_revision_20260910.py` 接受根gt/eq并展开为ite，保留原复杂度／schema限制，新增提示明确E/P规则；未改原冻结代码／提示／评分。49项microloop＋7项旧评分测试通过，106响应／108阶段重放核对原历史和反馈一致，原freeze校验通过。

详见[修复记录](reviews/microloop-contract-repair-20260911.md)，产物 `artifacts/microloop-revision-20260910/`。修订评分active/random/repeat最终64.06/62.63/57.68%，与既有v2诊断一致；不构成稳健主动选点优势，主张不升级。新版提示尚未调用模型。没有新API调用、goal或commit/push；若要真实重测，先确认新预算。

## 最新终态：微型闭环完成并核验，发现提示／DSL契约不匹配

会话3261已正常退出，36轨迹108阶段、110请求，106返回stop、2阶段缺失。106raw及108terminal核验、原analysis重放一致。详见[模型结果与诊断](reviews/microloop-model-results-20260910.md)。没有运行中的模型请求，不重启旧runner。

原协议active/random/repeat最终准确率0/6.64/12.76%，仅0/1/2个合法最终候选。主要原因是大量直接返回gt/eq而提示未明确要求ite包装。独立事后包装诊断为64.06/62.63/57.68%，不替换原结果；active−random +1.43pp，但排除缺失world4的敏感性检查变−4.26pp，不支持稳健主动选点优势，也不能据接口失败否定反馈利用能力。诊断采用v2文件；v1诊断工具漏计包装项已记录作废。旧论文结论不升级。

下一步建议免费澄清新版本语法提示并补契约测试；任何修订版模型重跑需新预算确认，不能称原12world为新未见集。没有新goal、付费调用或commit/push。下方运行中均为历史。

## 当前恢复运行：微型闭环读取中断已核对，会话3261

原会话91242因未捕获的HTTP IncompleteRead退出：41次已登记请求、37次已保存返回、4次无raw/result。用户确认继续；新增独立 `reviews/microloop_resume_20260910.py` 将HTTP读取异常归入技术网络错误，原冻结源码／plan及37份结果未改。42项microloop测试通过。

`artifacts/microloop-live-20260910/recovery-plan.json` 保存旧证据hash及恢复记录，4次无返回明确记技术缺失／用量未知，不推断各自故障原因。原全局2次重试额度不变，4次中至多重试2次，未恢复的缺失仍按原协议计分并保留分母。恢复命令已启动，会话3261；自动完成records／analysis／complete。不要重复启动原runner或恢复runner。下方原“运行中”会话已失效，尚未宣称本轮完成。

## 当前运行：微型闭环108＋2预算已确认并启动

用户在明确预算提案后回复“开始”。正式plan已冻结到 `artifacts/microloop-live-20260910/plan.json`，网络适配新增5测试通过，原34测试通过，冻结verify通过。DeepSeek thinking、131072、4并发、单次3600秒、全局最多110物理请求。执行会话91242正在运行；不要重复启动或改冻结源码。完成后自动保存records／analysis／complete（含已知usage）。raw响应另存；每轨迹依赖阶段串行。旧“待预算确认”为历史，额度已批准；尚未宣称实验完成。没有新增goal或commit/push。

## 最新goal交付：微型闭环离线准备完成，下一步确认收费调用预算

用户要求的剩余离线准备已完成：12world公共代码基线、模型结果分析、4并发／阶段串行运行器及模拟测试、正式配置预算草案。基线文件 `artifacts/microloop-baselines-20260910/`，完整模拟记录 `artifacts/microloop-mock-20260910/`，均已复核hash和重新汇总一致。所有响应均为代码模拟，零模型API调用。

基线最终64点准确率：非恒定最小规则均衡选点76.95%、随机71.22%、重复68.10%；均衡对随机7胜1负4平。恒0为77.47%，允许常数的均衡最小规则83.85%。标签不平衡明显，后续不能只凭raw高准确率提高论文主张；固定12world不筛换。

见[过程记录](reviews/microloop-offline-progress-20260910.md)与[待确认正式协议](reviews/microloop-live-protocol-draft-20260910.md)。提议DeepSeek thinking、131072输出上限、108正式＋最多2次技术重试、最多4并发、3600秒单次超时；理论completion上限14,417,920tokens，输入另计。尚未获具体预算确认，网络适配／正式冻结未启用；不继承旧授权、不自动调用。旧论文、旧实验、seed及world材料保持不变，无commit/push。下方“基线待运行”等为历史。

## 当前：新world两轮微型闭环，离线准备首阶段完成

用户同意按提高论文证据上限的建议开展小规模主动查询实验。[方案](reviews/microloop-plan-20260910.md)固定12新world×active／random／repeat×三响应，使用两次普通单点标签，不用旧首错位置oracle。主要评价64点预测及active−random，不要求两标签唯一识别256候选。

12/12新材料已保存于 `artifacts/microloop-draft-20260910/`；seed计划先于目标生成，无历史seed碰撞、无按表现筛选。不要重新build或换seed。离线状态机及基线选择函数已实现，18新测试＋7旧评分测试通过，36条固定parent模拟轨迹通过，**不是108次真实模型调用**。细节及hash见[离线进度](reviews/microloop-offline-progress-20260910.md)。

下一步读取现有12world运行公开代码基线、补齐分析与运行器离线验证，再明确模型配置和收费／技术重试预算请用户确认。当前零API调用，没有运行中的模型任务或新goal；不能继承旧32＋2／27＋2授权。旧论文及冻结结论不变，本阶段不提供新模型能力证据。未commit/push。下方旧“下一步排版”等为历史状态。

## 最新交付：英文预印本PDF已生成并核验

[正文PDF](paper-build-20260910/paper-manuscript-en-20260910.pdf)共7页（正文含摘要／结论6页，参考及可用性说明1页），[补充PDF](paper-build-20260910/paper-supplement-en-20260910.pdf)4页。采用中性A4单栏10pt、19mm边距，不是指定会议模板。Markdown源内容未改；通过标题空白调整和表格保持整块排版完成，正文达到当前中性格式的6页目标。

编译、11页文本边界及视觉检查通过，重建脚本／临时检查依赖及产物说明见[排版检查](reviews/paper-layout-check-20260910.md)。下一步作者信息／目标渠道及完整可分发复现包仍待处理；下方“未生成PDF／未测页数”均为历史。本轮无新实验或API调用、无commit/push／外部上传。

## 当前方向已确定：英文短论文

用户明确选择英文。当前正文入口为[paper-manuscript-en-20260910.md](paper-manuscript-en-20260910.md)，中文稿保留核对。英文主体约3453词、摘要212词；入口表与中文逐行一致，六个参考URL一致，不改变冻结实验和结论强度。[检查记录](reviews/paper-english-check-20260910.md)说明统计口径及关键术语。

配套[英文补充稿](paper-supplement-en-20260910.md)已完成，42行数值表与中文逐行一致；正文8行对照结果亦一致，主补充术语与本地链接核对通过。下一步为英文排版与实际页数检查；语言已确定，不再询问中英文。投稿模板、公开复现包及独立环境复现仍未完成。本轮没有新API调用、实验运行或commit/push。

## 最新交付：正文＋本地补充稿，待模板排版与公开材料整理

[正文](paper-manuscript-20260910.md)完成重复表述压缩，同口径字符10244→9392（−8.3%）；[补充稿](paper-supplement-20260910.md)整理方法、全部26策略、九world规律结果、技术记录与冻结材料入口。阴性检验、强基线、重复观察、strong-K4及结构化oracle等解释条件仍保留在正文。引用与数值／链接检查见[编辑记录](reviews/paper-editorial-check-20260910.md)。

下一步先确定语言版本与投稿模板／页数限制，再实际排版；本轮没有PDF或实测页数，不能声称5–6页已达标。补充稿目前为本地整合及索引，精确提示／其余逐world公开导出、无凭证复现包和独立环境复现尚待做。没有新模型调用、实验运行、commit/push或新goal；不按下方旧进度重跑。

## 最新写作交付：标题／摘要／结论与相关工作已统一

[正文](paper-manuscript-20260910.md)已补六篇有限相关工作、正式参考文献条目、中英文标题建议、摘要与结论；标题为“有限规则任务中LLM的上下文依赖探索入口选择：一项小规模受控研究”。核对来源与历史定位处理见[20260910文献记录](related-work/related-work-20260910.md)。仅一手来源，固定引用版本，近期SCILAWS条目标记预印本，不宣称首次或系统综述。

下一步整理完整补充材料、全文去重、引用格式和实际页数。当前不是可直接投稿终稿；下方标题／摘要待写均为历史。旧文献笔记不删但其“现有工作均未触及”等定位不采用。没有新增实验、模型API调用、goal或commit/push。

## 最新写作交付：方法与正文初稿已整合

当前正文入口为[paper-manuscript-20260910.md](paper-manuscript-20260910.md)。已按日期化方案整合引言、任务与方法、入口模型／强策略结果、参考／新观察／预算对照、讨论与局限。明确context构造层不是隐藏目标类别，代码机会验证不是模型发现，27规律任务不能与入口终点合并，原阴性主检验和混合对照均保留。

方法源码复核补充：当前入口使用K4_full_pool，而非早期两个替换K4；全匹配池按child行为去重，至少三个不同对照且均未精确识别。四轮oracle返回首个不一致位置／标签或MATCH，过滤使用完整响应（含匹配前缀），不能写作四个普通标签。正文已明确这两项，不改变任何实验定义或产物。

下一步有限核对相关工作、补正式引用，之后统一标题／摘要／最终结论与补充材料，检查实际篇幅。当前稿含工作标题和结论位置说明，不是可投稿终稿。旧结果讨论稿、旧方案、实验冻结材料保留不变。本轮没有新API调用、实验运行、commit/push，不恢复已结束goal；下方“仅完成方案”为历史。

## 当前阶段：写作方案已交付，下一步整理方法与正文

用户确认后已保存[论文主张与结构定稿方案](paper-claims-and-structure-20260910.md)：定位为小规模受控探索性实证短文，主线是局部context利用及其边界，而非熵机制或完整科学发现验证。入口正向观察与简单策略解释并列；参考对照混合、新观察相对重复对照收益较弱、放宽非thinking上限无改善均须保留在正文。

下一步按方案整理方法与正文初稿，随后有限核对相关工作，最后写标题／摘要／结论。当前只完成方案及进度文档更新，未启动新实验／API调用、未改冻结材料、未commit/push。原goal已结束；不要依据下方历史运行段落重启旧任务。旧提纲定位不再沿用，历史v1与后续各版本原样保留。

## 最新终态：第3项预算对照完成，网络中断后只读恢复

见[预算结果报告](reviews/nonthinking-budget-results-20260910.md)。32/32最终有效，33次物理请求（1次timeout按规则重试），32raw／33attempt和终态台账已保存。原cell175失效，但generation、自动response-audit、analysis及results.md均已存在并只读重放通过；未重复启动。9本轮测试＋3共享回归通过，独立raw直接计数全部world一致。没有未完成的模型请求。

同期256与8192两档均own1/16、完整0/8；cross分别0、1。高−低own／full均0赢0输8平，net为0赢1输7平。两档实际输出均8–13tokens、中位10、合计158，全部stop、无显式reasoning。历史同16题nonthinking为own1/cross2/full0，thinking为11/1/3。结论：本次单纯放宽上限无改善，不支持原答案被256上限截断的直接解释，但不能证明thinking内部机制或普遍能力差异。

已知成功响应input21364/output316，总21680tokens；timeout用量未知，usage_complete=false，不当作完整账单。目录 `artifacts/nonthinking-budget-live-20260910/`；plan `4b6fa1244faea61c8dfdace485878b4f27cb245063e220a6f2192815e531564b`，analysis `0c1dd49a085713e1006c55d00d11031797eb2c6b15d8869f9ac6294602a203b7`。只读verify，不再run/freeze。三项补充任务已交付，论文结果讨论已同步更新，之后整稿另行推进；本goal完成后停止，不自动加实验。下方运行／预算blocked均为历史。

## 当前：第3项32＋2预算已批准，准备正式冻结

用户明确确认32＋2调用、256／8192两档，之前预算blocked已解除，不再重复索要预算。运行器 `reviews/nonthinking_budget_live_20260910.py` 和逐attempt核验已实现；固定草案--verify通过，模拟测试验证cap唯一变量、32＋2上限、无效不重试及防重复启动。分析模块正在实现，尚无真实请求。正式协议 `reviews/nonthinking-budget-live-protocol-20260910.md`，后续冻结到独立目录 `artifacts/nonthinking-budget-live-20260910/`，测试及必要网络审批通过后仅启动一次。

## 当前goal：第3项non-thinking预算检查，已完成离线准备

自动续接状态：连续三轮停在同一新预算确认点，goal标记blocked，避免重复自动询问；不是实验完成或失败。尚无本轮模型请求。用户明确批准32正式＋最多2技术重试、256／8192两档后再恢复，无需重做已通过的离线材料。

用户要求设置goal并执行，goal已active。见[方案与历史诊断](reviews/nonthinking-budget-plan-20260910.md)。原non-thinking48/48有效、全部stop，实际输出8–14tokens、中位10、总469，0触及256上限；因此没有截断／格式失败导致原低表现的证据，不预设放宽必有效。

固定原24world中8个、四层各2个，按stratum及anchor hash选，不按历史成绩挑；每world两臂16task，各做同期256／8192两档，共32正式请求、最多2技术重试，最大输出总上限151552tokens（不含输入）。其余设置保持thinking disabled、temperature0.2、原prompt、JSON、120秒技术timeout。加入同期低档避免新高档对历史低档的时间混杂。历史thinking仅同题描述性参照。

本轮32＋2精确预算尚待确认，**没有新API调用**；非继承已结束27＋2预算。2测试与草案--verify通过，目录 `artifacts/nonthinking-budget-draft-20260910/`；尚无本轮live runner或正式plan，后续获批再实现测试冻结。第3项尚未完成，不把离线诊断当作整个goal完成。下方新证据27任务是已结束历史。

## 最新终态：新证据27任务完成，已核验保存并停止

会话43680正常exit0；27/27合法DSL响应、0技术重试／缺失／截断，全部finish=stop且含reasoning。自动raw重放审计、analysis及results.md保存；只读analysis --verify和27raw再次重放通过，独立直接执行raw候选的逐world计数一致。没有运行中的模型请求。下方“运行中”均是历史。

见[中文结果与论文边界](reviews/new-evidence-model-results-20260910.md)。三条件平均测试准确率：baseline52.95%（305/576），new_evidence69.27%（399/576），repeat65.80%（379/576）。new−baseline +16.32个百分点、5赢0输4平；new−repeat仅+3.47个百分点、2赢2输5平；repeat−baseline +12.85个百分点、3赢1输5平。三组全部可见观察一致9/9，但测试全对和125全域精确恢复均0/9。新观察组低于两个公开最少节点代码基线83.16%／75.52%。

结论：新增观察相对无附加有描述性改善，但重复对照也改善，新增信息特异收益证据较弱；不能归因为内部熵机制、科学发现或RSI，也未排除提示／采样波动及普通规则筛选。9个已用development world，不新增p值、不继承旧K4。旧实验结论保留，第3项非thinking预算敏感性未执行，不自动追加。

实际tokens input18081/output308880，合计326961，usage完整。目录 `artifacts/new-evidence-live-20260910/`；plan `cfc5ce311d8ce2c5d27c3b1f3a64af2dae8158b8614ca4b5976697d4ee965711`，analysis `ebcc159f7d70b27048b32bc4ee103245f3eee84a3499e092756bc8564ec7d732`。25新实验测试＋8回归通过。后续仅只读verify，不重复run/freeze，不修改冻结文件。本轮仅本地保存，未commit/push。

## 当前运行：新证据27任务已启动

会话43680执行 `python3 reviews/new_evidence_live_20260910.py run --execute`，已获网络许可。目录 `artifacts/new-evidence-live-20260910/`，冻结plan SHA256 `cfc5ce311d8ce2c5d27c3b1f3a64af2dae8158b8614ca4b5976697d4ee965711`。25项新实验测试＋8项旧链路回归通过，v2完整重建通过；正式27请求＋最多2技术重试，3秒错峰，max_tokens131072。

单次启动，逐attempt落盘；完成自动执行原始响应核验--save，然后analysis与results.md。恢复先核验真实会话及台账，不重复run/freeze或修改冻结源码。主agent随后只读重放并写日期化中文结果、更新TODO／论文轴线，本goal完成后停止，不追加其他实验。下方未启动／待预算为历史。

## 当前goal：新证据27正式＋2技术重试已授权，运行器准备中

用户通过active goal批准 `reviews/new-evidence-live-protocol-20260910.md` 所述调用预算及自动执行／核验／总结。采用v2固定材料，入口 `reviews/new_evidence_live_20260910.py`，新输出目录 `artifacts/new-evidence-live-20260910/`。运行器新DSL解析、27任务平衡错峰调度及模拟预算／失败测试已实现；分析和原始响应核验正在实现，尚未冻结、尚无真实请求。下方“预算待确认”是历史记录，不再重复索要同一预算。网络权限如需审批仍按系统执行。

严格27正式＋最多2技术重试，max_tokens131072，thinking high，3秒错峰；首次波次结束后才技术重试，不因预测／格式差重试。结果保存后自动评分报告并停止，不扩world或追加模型。

## 最新：27任务新证据／候选规律生成草案已完成，模型预算待确认

最终核验：v2的--verify完整重建通过，10项相关测试通过；初版／v2提示、私有目标、代码基线一致。当前没有模型任务运行。

用户已确认继续职责及评分变更。见 [草案及代码基线](reviews/new-evidence-protocol-draft-20260910.md)，当前目录 `artifacts/new-evidence-draft-v2-20260910/`，初版保留为历史。v2修正空／无效响应跳过评分键校验的问题并补齐九world唯一性及源码绑定，不改变实验材料。从原hash绑定9world文件恢复既有target_index，task_identity核对一致；9条新增真实观察已入新草案，不重抽target、不扩world、无oracle/API。新观察仍保留61–189候选规则，只有2/9反驳parent，全部9world保留。

三条件同D0／parent／生成器语法，分别无附加、新真实观察、重复D0；27任务，输出DSL候选规律不是option ID。原64test私有，主指标是9world等权test准确率（无效／缺失0），一致性另列，不能沿用旧K4。7项新增测试通过（加前序共10项），--verify可重建草案与代码基线；新live runner未实现，27正式＋最多2技术重试预算待确认。

代码基线（非模型）：parent66.67%不变；允许常数的相容最短规则73.44→83.16%；非恒定相容最短64.93→75.52%；重复观察与baseline完全相同。说明新增证据能帮助简单筛选，不证明LLM科学发现。旧结果不变，第3项非thinking预算敏感性仍待后续。当前无模型请求。

## 最新恢复点：新证据增量的离线检查完成，待确认新职责／评分方向

用户同意重新设计“额外信息增加什么”。见 [新信息设计](reviews/new-information-design-20260910.md)，产物 `artifacts/new-observation-feasibility-20260910/audit.json`。仅重建原九world的无目标256规则集合并核对原D0，检查441 evidence点；9/9都能选出两标签分支均非唯一的观察点，分支余量61–195。没有读取／释放新增目标标签，没有生成新world、运行oracle或调用模型；3测试通过。

125点已经12D0＋49evidence＋64test，无reserve；9/9没有未见且完全冗余evidence点。新观察涉及原evidence，不能把它给模型后仍继承旧K4标签／验证预算。不碰private test标签。建议独立“新增真实证据利用／候选规律修正”方向：D0／D0＋新观察／D0＋重复观察，让模型输出候选程序并在未进入提示的测试点评分。涉及职责和终点变化，待用户确认后才实现；潜在27请求不是预算授权，也不是无关噪声启发的替代证明。

旧三条件结果及论文轴线保持，第3项非thinking预算敏感性仍待后续。当前无运行任务；以下模型终态/运行记录均为历史。

## 最新终态：结构化参考三条件试验完成，无运行请求

会话47361正常exit0，54/54有效、0重试／截断、54均有reasoning。`artifacts/context-association-live-20260910/` 的generation、analysis、response-audit及全部54原始返回／attempt已保存；自动raw/台账重放、analysis --verify及独立标签直接计数通过。

见 [结果与论文边界](reviews/context-structured-reference-results-20260910.md)：aligned own8/18、cross3、完整2/9；unmatched 7/18、1、1/9；no_reference 9/18、2、1/9。结果混合，对应参考没有一致收益证据；不把2比1单独包装成明确正向，也不证明context无用，因为完整候选仍含原context结构。原入口和shortcut挑战结果保留。

实际input90342/output911963，合计1002305 tokens，usage完整；18/18任务对应／不匹配表实际输入token相同，仍非等信息量。安全只读复核 `python3 reviews/context_association_live_analysis_20260910.py --verify`、`python3 reviews/context_association_response_audit_20260910.py`。不重复run/freeze/无参数analysis，不修改冻结源码。第2项仅按上述局部操作化完成，下一第3项非thinking预算敏感性尚未执行／授权新预算；下方running均是历史。

## 当前运行入口：三条件54正式＋2技术重试已启动

真实会话47361，命令 `python3 reviews/context_association_live_20260910.py run --execute`，已获沙箱外网络许可，2026-09-10 11:25 UTC+8确认台账3个request_started。目录 `artifacts/context-association-live-20260910/`；plan SHA256 `e6374210450b52f63bfb9117049bfb01f2689aab641084e800df1ff8676e4d74`。20相关测试通过，全部54提示和独立评分源码冻结。

54正式＋最多2技术重试、54最大在途、3秒错峰、DeepSeek thinking high/131072。每attempt即时保存，runner完成自动执行 `context_association_live_analysis_20260910.py` 与 `context_association_response_audit_20260910.py --save`。暂未有结果，不中途查看正确率改调度，不重复run/freeze或修改绑定源码。恢复先核验台账和终态文件；旧“尚无请求”均为历史状态。

## 最新：用户已批准三条件54正式＋2技术重试，冻结准备中

用户明确“好的，开始”。新入口 `reviews/context_association_live_20260910.py`，目录 `artifacts/context-association-live-20260910/`，正式协议 `reviews/context-association-live-protocol-20260910.md`。目前准备机器plan，尚无真实模型请求；模拟测试的56次不是费用。分析脚本完成并测试后冻结，再run --execute；该命令结束会自动分析与响应重放核验。不重跑旧目录，当前54草案材料保持。

## 最新恢复点：结构化参考三条件草案完成，尚未获API预算

见 [三条件计划草案](reviews/context-structured-reference-plan-20260910.md)。在原固定105 motif库中按公开prompt哈希确定替代参考：同stratum／节点／深度／125点输出多重集，且不匹配parent或十候选的算术子树行为。全部18任务有2–8项合格参考，保留九world；不是随机打乱、不扩world、不按模型输出选择。

三条件均显示相同完整候选，分别提供对应数值表、结构化不匹配数值表、无辅助表。54草案已存 `artifacts/context-association-draft-20260910/`，公共与私有评分分离。180候选全域可求值且二值，121符合D0但保留全部；正确槽行为唯一且符合D0，原option映射和标签保持。12测试及 `python3 reviews/context_association_materials_20260910.py --verify` 通过。

仅设计／代码证据，无模型请求、无新world／oracle。该实验检验冗余辅助数值表与候选对应的作用，不能声称新目标信息、所有有效关联被移除或内部熵机制。建议54正式＋最多2技术重试，尚待用户批准；无live runner。获批后另冻结调度顺序与错误策略，三条件同期重跑，不复用旧18响应，不执行旧36次可见性方案。下方旧阻碍均保留作历史。

## 最新：有效关联对照的解耦与关系检查完成，尚不适合调用

见 [设计与实际检查](reviews/context-effective-association-design-20260910.md)。九world18任务180完整候选可按原动作展开；3项测试通过。对侧参考在4/18任务仍行为匹配候选子表达式，不能一概当作无关；原参考18/18均匹配全部十候选，不独自指向正确答案。仅7/9world两臂无对侧子树匹配，不据此自动换cohort；无匹配也不表示完全无关。

产物 `artifacts/context-association-audit-20260910/audit.json`。无API、无模型响应读取、无新world或oracle。本轮解决了候选呈现与辅助参考可以解耦的局部问题，未完成有效关联模型实验或公共任务／评分冻结。下一步需明确关联判据与解释范围，可能的匹配／替代／无辅助三条件只是设计讨论，不沿用36次旧预算，也未授权54次新调用。论文证据轴线保持不变。

## 最新用户确认：有效关联对照，先保存论文证据轴线

用户已明确同意下一步做有效关联对照，先设计与代码验证，不以可见性控制替代；同时要求开始前保存此前已完成／验证与待验证项总结。已保存 [论文主张—证据—待验证轴线](paper-claim-evidence-status-20260910.md)，作为最终论文范围的基准快照。本轮仅文档记录，无新实验或API调用。

下一次从有效关联对照设计与代码可行性验证开始，方向选择已解除，不再重复询问是否接受该方向。新调用预算、扩搜或放宽科学条件尚未批准；旧36+2可见性草案不执行。原持续goal此前blocked是历史工具状态，不等于用户尚未确认方向。未验证清单不是本短文必须全部完成的任务承诺。

## 历史决策点：已由上方明确确认更新

用户已询问并讨论实验边界、完成与未验证主张；尚未选择接受可见性控制或重新设计有效关联对照，也未批准第2项模型预算。助手最新建议优先考虑更贴近原问题的关联对照，但这不是用户决定。原第2项有效关联消融仍未完成，可见性草案不能替代它。等待这一方向选择，不自行更换实验目标、调用API、扩搜或跳到第3项模型运行。

持续目标中的340/1024与会话60938是历史快照。最近一次只读复核：固定1024全量audit通过、summary重放一致；九world模型18份响应与9组评分重放通过，两命令正常exit0，无新增调用。无需再次监控或重启已完成扫描。已有完成证据和报告保持不变。

## 最新恢复点：第2项消融设计及语义验证完成，未发模型请求

见 [context可见性控制设计](reviews/context-ablation-design-20260910.md)。九world中9/9直接交换context等于对侧任务且正确标签不同，9/9隐藏后两臂prompt相同。已保存 `artifacts/context-ablation-feasibility-20260910/audit.json` 和36条私有草案，4项合成测试通过；无API调用、无新world/oracle、构造代码未读模型响应。原执行fragment及标签绑定保留，只变观察界面；draft含私有标签，不得整条发送。

建议仅作为“context信息可见性补充控制”，不冒充等信息量有效关联消融。hidden移除必要材料、两臂不可区分，其own/cross期望相同有结构原因；不把下降解释为内部机制。待用户确认此收窄目的与36正式+最多2技术重试（最多38次）后，另冻结公共任务/评分/调度；visible也同期重跑，不复用上一轮响应。当前无运行任务，第2项模型实验及第3项仍未执行。以下第一项结果保持有效。

## 最新终态：九world策略挑战完成，下一步为context消融设计

会话32918正常exit0；`artifacts/shortcut-challenge-live-20260910/` 已保存generation、analysis及response-audit。18/18有效，0技术重试/截断，18均返回reasoning。模型own9/18、cross3/18、双臂全对2/9；非恒定最少节点7/18、3/18、0/9，非恒定最大差异5/18、3/18、0/9。51项相关测试通过；18原始响应解析、日志/slot及9world评分事后重放核验通过。input13406/output379301，usage完整。见 [结果与解释](reviews/shortcut-challenge-model-results-20260910.md)。

第一项按用户批准的九world联合失败挑战已完成，但不等于所有更强shortcut排除目标都完成。结果为有限正向行为证据，不能证明内部熵循环或通用科学发现；无新p值，不将旧24与新9合并作确认性样本。当前无运行任务，不重复run/build/analysis；只读复核 `python3 reviews/shortcut_challenge_live_audit_20260910.py`。下一项先设计context有效关联消融，替换fragment会改变动作语义/标签，须解决后另定模型预算；第3项预算敏感性仍待执行。下方运行状态为历史。

## 最新运行入口：九world DeepSeek thinking挑战已启动

用户已批准9 worlds / 18正式请求，另最多2次全局技术重试。51项相关测试通过；全部26基线重放一致，机器plan与private评分映射已冻结至 `artifacts/shortcut-challenge-live-20260910/`。plan SHA256为 `66b98756b83016fe4c702ea914d1be7ff984eb8f37873e79ad480a0e842496c2`。每world最小pair摘要确定选一对，实际分层3 commutative/4 directional/2 pairwise/0 multiplicative，非平衡，不换题。见 [执行协议](reviews/shortcut-challenge-live-plan-20260910.md)。

运行会话32918，命令 `python3 reviews/shortcut_challenge_live_20260910.py run --execute && python3 reviews/shortcut_challenge_analysis_20260910.py`，已获沙箱外网络许可并开始写attempts.jsonl。请求保持旧DeepSeek thinking high/131072、非流式、3600秒单请求timeout，3秒错峰并发。发送器不加载private；每次请求即时保存raw与attempt JSON，generation含全部18个slot及技术失败，完成后独立评分。恢复必须先核对该目录和会话，不能重启run或重复analysis输出；原goal工具仍blocked不代表本轮无请求。当前未有模型结果结论。以下扫描完成/预算待确认均为历史。

## 最新终态：新增1024全完成，等待小型模型挑战方案确认

会话41634正常exit0；最终目录 `artifacts/shortcut-challenge-search-resume982-20260910/` 的state/summary/audit.json完整，1024结果全量核验与summary重放通过，982旧结果保留，重复排除0。见 [全量结果与下一步建议](reviews/shortcut-challenge-1024-results-20260910.md)：19 strict worlds/41对，其中9 worlds/22对使两种非恒定简单策略均不能双臂全对；该类四层等额最多4 worlds。后896贡献17 strict worlds/36对，联合失败9 worlds全部来自后896。科学条件未改，无新模型调用；这是构造结果而非模型假设检验。

建议下一步用全部9个联合失败world、每world一对、18正式请求进行DeepSeek thinking小型挑战，另至多2次全局技术重试，须用户确认调用预算后再冻结并运行；不扩搜、不放宽strict、不将22对视为独立样本。第2项消融已记录context与动作语义耦合风险，第3项预算敏感性未运行。当前无扫描任务；原goal工具仍blocked，不宣称后续模型实验完成。下方运行中信息均为历史。

## 最新恢复入口：已获准补跑42个，新4并发会话运行中

用户回复“好的，继续”后，已再次核验982个旧结果并在新目录 `artifacts/shortcut-challenge-search-resume982-20260910/` 引用。入口 `reviews/shortcut_challenge_resume982_20260910.py`，会话41634，已确认active982..985。45项相关测试通过，见 [恢复修订](reviews/shortcut-challenge-resume982-20260910.md)。只计算index982..1023，原seed/target/评分/4并发不变，无模型调用；旧数据不覆盖。固定范围结束自动summary，完整1024时自动全量audit.json；复核命令为 `python3 reviews/shortcut_challenge_resume982_20260910.py --audit`。原goal工具仍显示blocked，本轮实际计算已恢复，不混淆两者；后续恢复先检查新目录和会话，不重复启动。以下等待确认和旧running为历史。

## 最新恢复入口：982个已核验，原会话丢失，尚未续跑

2026-09-10 08:46（UTC+8）发现原会话60938和监控句柄不存在，四并发state超过3小时未更新：982个完整结果（index0..981），最后active982..985，无summary，也无index982及之后的完整world文件。982个结果逐文件/内部hash、seed、完成数、索引、去重、原160复用及冻结依赖已核验通过，未丢失已完成结果。中断原因和退出码未知；不得将旧running当作仍在运行，不得宣称全量完成。见 [现场与核验记录](reviews/shortcut-challenge-interruption-982-20260910.md)。等待确认按原seed在新恢复目录续跑剩余42个，固定4并发，不覆盖旧数据、不重新抽target。尚未启动恢复、未新增模型调用；全量汇总和后续实验未完成。下方正常运行描述均为历史快照。

## 最新恢复入口：160个完成后已切4并发，运行正常

按用户要求，在累计160个完整结果时停止2并发，已全部核验复用。原会话exit1且未写Python终态，旧state的running过期；另存原目录 `threshold-stop-160.json` 绑定hash确认终止。index160无完整结果，同seed重做，其余未扫候选按集合差接续。新入口 `reviews/shortcut_challenge_parallel4_20260910.py`，目录 `artifacts/shortcut-challenge-search-parallel4-20260910/`，见 [修订](reviews/shortcut-challenge-parallel4-20260910.md)。40项相关测试通过，已确认同时运行index160..163四个worker，心跳正常。固定4并发、1024范围、无时间截止、科学引擎不变，无模型调用。终态自动summary，旧160个文件和历史尝试不覆盖；本次外部中断最后派发数不完全可知，物理尝试数按下界记录。恢复只看新state/summary，不重启旧两进程或阈值监视器，先前等待器已取消。

## 当前迁移请求：累计160个完成后切4并发

用户要求达到160个完整结果再从2切4。阈值监视已安排，当前仍需先看两进程state是否达到160及是否终止；不能提前启动4并发。新入口 `reviews/shortcut_challenge_parallel4_20260910.py`、新目录 `artifacts/shortcut-challenge-search-parallel4-20260910/`、[修订](reviews/shortcut-challenge-parallel4-20260910.md)已准备，38项相关测试通过。新入口只在旧进程终止且completed>=160后允许启动，保留全部完整结果，按集合差接续乱序缺口，未完整候选同seed重做。尚未在本段声称已切换；以新目录是否存在及state为准。

## 最新恢复入口：已切换固定2进程并发，运行正常

用户授权后停止原串行会话（exit130），复用index0..128的129个完整结果；index129无完整输出，同seed重做。新入口 `reviews/shortcut_challenge_parallel2_20260910.py`，新目录 `artifacts/shortcut-challenge-search-parallel2-20260910/`，见 [并发修订](reviews/shortcut-challenge-parallel2-20260910.md)。33项相关测试通过；新state已确认同时运行index129、130，心跳正常。固定2进程、无时间截止、仍0..1023，不调用模型。旧科学引擎与seed不变，完成即保存并按index排序，重复任务保留按最小index而非完成时间；实际错误停止派发，保留在途完成结果后暂停。终态自动summary。当前读取新state的active_indices/active_workers，不再读串行active_index；旧state running已过期，禁止重启旧串行。原129文件、旧manifest/中断历史全部保留。

## 最新恢复入口：新增池扩至1024，后896个已启动

用户在结构诊断后同意继续，已保存 [1024扩展方案](reviews/shortcut-challenge-1024-plan-20260910.md)。前128完整结果逐文件hash引用不重算；同一新增namespace顺序扩至index1023，与最早另一批1024区分。新目录 `artifacts/shortcut-challenge-search-1024-20260910/`，入口 `reviews/shortcut_challenge_expand_20260910.py`。28项相关测试通过，真实扫描已启动，从index128开始，无时间截止、单world进程；遇实际错误暂停，不自动扩搜/重试，无模型调用。预计需十几个小时但不承诺完成时间。种子向量、范围扩展以外科学引擎逐字等价均核对；两强策略均无法完整切换的新诊断单列，允许错在不同臂，不误称同臂或双臂都误导。最终自动summary分开报告原128与新增896；恢复先查新state/summary，不重复run。旧128结果及旧策略身份保留。以下“无运行任务”为此前诊断终态。

## 最新恢复入口：2026-09-10 最少节点结构诊断完成，无运行任务

新增128候选已全部完成，2个strict worlds/5对；目标策略挑战集仍未构建成功。按用户要求完成最少节点结构诊断，见 [报告](reviews/minnode-structure-audit-20260910.md)与[当日TODO](TODO-20260910.md)。原48context中5个正确动作严格大于最少非恒定节点数，非恒定最少节点仅31/48、8/24，排除“strict必然最少节点正确”的说法。新128的113个有nonconstant K4机会context中该策略65次命中；最终strict涉及的7个不同context全命中且均唯一最小。代码有复杂度偏好，但没有直接按最少节点赋K4标签；当前瓶颈为配对容量，不是已证明的逻辑不可达。113个公开特征重放、128个pair数复现、3项合成测试通过。没有生成新target、重跑oracle、扩搜或模型调用。下一步可另立新增池128→1024固定范围方案（再896），同时检查两强策略；当前未启动。下方running为历史快照。

## 最新恢复入口：取消时间截止，固定128候选续跑已启动

用户明确授权扫描完固定128候选。前阶段因时间上限结束，完整51个world、strict新world1个但简单策略两臂都正确，无策略失效挑战。51个完整结果已逐文件核验并复用，不重算；同seed补做index51（第52个），再到index127。见 [取消时间截止修订](reviews/shortcut-challenge-full-range-20260909.md)。新入口 `reviews/shortcut_challenge_full_range_20260909.py`，新目录 `artifacts/shortcut-challenge-search-full-range-20260909/`；24项相关测试通过，真实续跑已启动。无任何按耗时截止，数量上限仍128；实际错误暂停，不自动重试或扩搜，不调用模型。终态自动summary。恢复先检查新state/summary，不重复run；旧两阶段产物保留，旧耗时字段含保守600秒计账，不当作精确总时长。

## 最新恢复入口：已授权同seed补做第4个，修订续跑已启动

校准更新：第4个补做39.19秒完成，四个完整结果均已保存/绑定，修订校准通过（累计计账639.20秒，包含旧预算预扣600秒，并非实际总耗时）。已自动进入index4及后续扫描。当前仍在运行，固定128候选或累计计账3600秒即停并自动summary；不要重复启动。

用户同意继续，见 [续跑修订](reviews/shortcut-challenge-resume-20260909.md)。新入口 `reviews/shortcut_challenge_resume_20260909.py`、新目录 `artifacts/shortcut-challenge-search-resume-20260909/`。前三个原文件按hash复用；仅index3同seed重做一次，之后顺序继续原128范围。旧耗时保守计账600秒，第4个新增最多300秒（修订校准计账上限900），总上限仍3600秒，即此次最多3000秒。21项相关测试通过，续跑已启动；最终自动保存summary。恢复先检查新state/summary，不重新启动旧或新入口；原state的running继续是过期快照。本步骤无模型调用、不覆盖旧文件，真实状态以新目录为准。

## 最新终态：新搜索3个完成，技术修复后暂停

见 [执行记录](reviews/shortcut-challenge-search-execution-20260909.md)。前三个新world完整保存（约46/75/88秒，strict均0），第4个主动中断；未完成4个校准，未调用模型。发现旧action-order函数只接受0..23，已修复为等价10位置循环，19项新测试通过；旧8项回归此前通过。会话exit130，原state的running是过期快照，以 `artifacts/shortcut-challenge-search-v1-20260909/termination.json` 和 `interrupted-summary.json` 为准。最后heartbeat232.05秒，精确终止耗时未知；原数据/manifest不覆盖。当前无运行任务，未自动重试。下一步需续跑修订并获准同seed重做未完成第4个、结转预算；不能直接默认重启或reset预算。以下“真实校准已启动”为历史状态。

## 当前实施：有界搜索runner与启动前测试

用户已同意继续实现并启动成本校准。引擎、supervisor及18项新测试、原pair/matching的8项回归通过，真实校准已启动。执行记录见 [新world搜索](reviews/shortcut-challenge-search-execution-20260909.md)。恢复必须先检查 `artifacts/shortcut-challenge-search-v1-20260909/state.json` 和进程，禁止重复启动或重置预算。状态以执行记录和state终态为准，不根据下方历史“尚未实现”重做任务。

## 最新恢复点：新world有界搜索方案已保存

用户同意新增搜索方向。已保存 [有界搜索方案](shortcut-challenge-search-plan-20260909.md)：新namespace固定128 seeds，先4个/600秒成本校准，总累计扫描3600秒、单world进程，保持原strict与四轮K4，不自动扩搜或调用模型。无目标seed核对128个唯一、与已检查历史4200个并集无碰撞；新world尚未生成。下一步实现独立runner和合成测试，再执行成本校准及预算允许的固定范围扫描。本轮只制定方案及seed检查，未运行oracle，无运行任务。旧冻结文件不修改，以下决策等待为此前快照。

## 最新恢复点：第一项现有池可行性检查完成

见 [策略挑战可行性报告](reviews/shortcut-challenge-feasibility-20260909.md)。128/128 shards、1024 worlds核验完成：strict仅24 worlds/57 pairs，24 worlds已全部用于旧实验，排除后新候选为0。因此未进入新候选的策略特征筛选，不是“策略挑战全局不可达”或模型阴性证据。7项合成测试通过，机器汇总已保存。无模型调用、新world或运行进程。下一步需决定有上限的新world代码搜索，或接受旧world内诊断；本轮未自动扩搜、放宽规则或启动任务2/3。下方“尚未执行”是此前快照。

## 最新恢复入口：三项补充实验任务已保存，尚未执行

用户确认后续做三项小体量实验：①简单策略失效挑战；②context有效关联消融；③thinking与生成预算敏感性。当前要求仅保存任务，未启动构造、实验或模型请求。见 [详细任务清单](supplementary-experiment-todo-20260909.md) 和 [当日TODO](TODO-20260909.md)。下一次继续从①代码可行性检查开始；模型、精确样本和预算需在调用前落实冻结。原实验、阴性检验、描述性结论与中文初稿均保留。以下“先统一整稿”等下一步建议为此前快照，以本段为准；无运行任务，未commit/push。

## 最新写作入口：2026-09-09 结果与讨论初稿

用户确认后已完成 [结果与讨论中文工作稿](paper-results-discussion-20260909.md)：三条件结果、既定及诊断策略、预算敏感性和结论边界。仅写作，无新模型调用；旧提纲与概念稿保留，不将其温度调度主线误称当前实证结果。当前无运行实验。下一步建议先审阅此稿，再统一标题、摘要与方法；尚未形成完整投稿稿件，未 commit/push。下方实验记录继续保留。

## 最新恢复入口：2026-09-09 thinking 后续试验

**当前最终恢复点：GLM补跑、48条合并、评分及跨模型/策略对比全部完成，无运行任务。** GLM own38/48、cross1/48、完整切换15/24、22 favorable / 0 adverse / 2 tie；48/48 valid、有reasoning、无截断。DeepSeek thinking为34/48、11/24，disabled为5/48、0/24。非恒定过滤＋最大novelty诊断33/48、11/24；GLM相对其完整成功为独有5/对方独有1/共同10/均无8。相对DeepSeek thinking为6/2/9/7。可报告受控任务中的探索性跨模型行为复现及观测优势，不宣称统计显著优于模型/策略、通用科学发现或排除shortcut；三个条件不是72个独立world。

详见 [GLM最终结果](reviews/utilization-glm53-results-20260909.md) 与 [可共享汇总](reviews/utilization-glm53-comparison-20260909.json)。完整analysis在 `artifacts/glm53-retry-merge-20260909/analysis.json`，SHA `38ad1a3edcaeb121b8eb7e1d1d10164820a8be513f04291083026bec587a79ac`，5项独立验证/算术/重放测试通过。只补跑原缺失第25题，其余47条原样保留；49次任务请求，已知最终响应input32138/output1039407，旧失败及此前技术请求有未计入/未知用量。原失败、预检、诊断与DeepSeek记录均保留，不重复run，不新增p值。下一步可整理短文三条件结果和启发式限制，当前未自动扩样、调用模型、commit或push。以下内容为历史执行快照，以本段为准。

**最新GLM修订：原48题已结束，47完整、index24因120秒无增长失败；用户已授权只重试第25题并合并。** 新脚本 `reviews/glm53-retry-merge-20260909.py`，新目录 `artifacts/glm53-retry-merge-20260909/`，最多1次新调用，同流式high/128K/120秒idle/3600秒总截止。取得完整响应后自动按原题序补入index24，其余47条原样保留，单独生成generation；旧failure不改。再次失败不自动重试。3项本地测试通过。详见 [单题重试记录](reviews/glm53-retry-merge-20260909.md)。恢复先读新retry-response/generation/attempts，不重新run。合并计49次任务物理尝试、48条最终响应，旧失败usage未知；还未评分，不得声称原无重试协议完整完成。

**最新GLM进度覆盖：两道流式预检通过，用户已授权启动原48题流式对照。** 脚本 `reviews/glm53-stream-live-20260909.py`，新目录 `artifacts/glm53-stream-live-20260909/`。沿用glm-5.3/high/128K/temperature1/top_p0.95、每3秒派发、最多48并发；每请求120秒无内容增长停止、3600秒硬截止，每30秒记录进度，完成即保存。7项流式与调度测试通过；无重试/恢复/扩样、不重复canary。恢复先查新attempts/response/generation/failure，不能重新启动旧或新run。完整generation后独立离线评分，对比DeepSeek两条件、原24策略和单列非恒定诊断；当前尚无GLM能力结论。详见 [GLM流式48题](reviews/glm53-stream-live-20260909.md)。前置canary两题共44585输出token、均valid无截断，已完成；下段为历史步骤。

**最新GLM状态覆盖：用户已授权停掉非流式，另开流式预检。** 旧进程PID12885已SIGTERM且session97529确认exit143，两道旧canary没有完整响应、usage未知、48题从未启动。新脚本 `reviews/glm53-stream-canary-20260909.py`，同两题/high/128K，3秒错峰，每30秒记录reasoning/answer字符增长；120秒无内容增长结束，3600秒硬总截止。仅2道预检，结束后停下，不自动开48题。5项本地测试通过。新目录 `artifacts/glm53-stream-canary-20260909/`，详见 [流式预检记录](reviews/glm53-stream-canary-20260909.md)。恢复先读新attempts/response/canary，不重启新或旧runner。旧非流式和独立诊断均仅作技术记录，不计入模型能力结果。

**GLM诊断补充：** 原两道非流式128K/high canary最后检查约等待27分钟，未返回完整响应，48题未启动。独立短题high流式/非流式均HTTP200约10秒完成；另一条退役符号题流式5.11秒收到事件、47.9秒用尽2048诊断预算（reasoning2046），故已确认基础接入和high推理可用。原进程两线程等待网络、连接ESTABLISHED，仍不能确定原请求是在长推理/排队还是上游停滞。131K按诊断约43 tokens/s粗估可达51分钟，不能误报为当前进度；socket timeout也不是总墙钟deadline。诊断过程见 [GLM排查记录](reviews/glm53-connectivity-diagnostic-20260909.md)。本轮额外3次诊断、2959已知tokens，未计入实验，未中断或重发旧canary。若改为流式需明确另开执行方案，不得热改旧记录。

**最新新增实验：用户授权增加GLM-5.3跨模型对照并提供本地tokenhub.key。** 独立脚本 `reviews/glm53-followup-20260909.py`，TokenHub官方端点、glm-5.3、thinking high、128K、temperature1/top_p0.95、3秒错峰；先同两道技术canary，通过自动进入原48题，无自动重试。4项接入fake测试通过。结果目录 `artifacts/glm53-followup-20260909/`，恢复先读canary/generation/attempts，不重复启动run。完整配置与证据范围见 [GLM记录](reviews/glm53-followup-20260909.md)。仅作跨模型探索性复现，不把三个条件合并为72独立world；原24基线及两种单列的非恒定过滤诊断均比较，不新增显著性检验。tokenhub.key已0600并本地Git忽略，永不提交/回显密钥。下方DeepSeek和案例审查结果不变。

**最新解释修订：9组不一致案例审查已完成，无新模型调用。** 对原最大novelty策略增加事后「先排除恒定输出」过滤，全48题得到33/48、11/24，接近thinking的34/48、11/24；模型独有完整成功3、策略独有3、共同8。这削弱了「超出简单启发式」解释，但不改变原模型结果或context利用行为证据。六组模型胜例中四组涉及恒0短表达式陷阱（可完整修复三组），两组涉及hash并列，另一组还包含低novelty正确选择；三组反例均为模型在一臂选了低novelty的非恒定错误候选。完整分析见 [案例审查](reviews/thinking-case-audit-20260909.md)，逐臂0600数据在同artifact目录 `case-audit.json`，3项测试通过。新增两种规则明确为post-hoc，不回填原24基线、不宣称模型实际使用它们。下一步宜按此边界整理论文，若新增实验再讨论匹配非恒定/novelty等简单特征的针对性设计；尚未启动。

**当前终态：48题thinking后续对照、评分、全部24种简单策略比较和文档收尾均已完成，无运行任务。** 结果为own34/48、cross1/48、完整双臂切换11/24、22 favorable / 0 adverse / 2 tie；48/48 valid且无截断。原disabled为5/48、0/24、0/1/23，原primary阴性/p=1保持不变。最大parent差异简单策略为29/48、8/24；与thinking配对完整成功为模型独有6、策略独有3、共同5、均无10。可报告当前受控设置下的描述性正向行为证据，但不能声称已排除shortcut、统计优于简单策略、通用发现能力或内部entropy机制。

完整解读见 [thinking结果](reviews/utilization-thinking-results-20260909.md)，全部24-policy可共享汇总见 [comparison JSON](reviews/utilization-thinking-comparison-20260909.json)。原始generation/analysis位于 `artifacts/deepseek-thinking-128k-staggered-20260909/`，analysis SHA `9cdadefbd7df1df2c9ba5871937b4a222cac2817af4bddcdf0a5e05925a7eab2`。独立分析器为 `reviews/analyze-thinking-staggered-20260909.py`，6项分析测试通过并直接算术复核计分；不能直接用旧串行analyze处理新bundle，不重跑模型。本轮包含6条串行保留响应+42条错峰响应、1次明确授权的中断重发；49次物理尝试，已知input35770/output736187，旧中断请求usage未知。只作披露调度/预算修订的描述性follow-up，不新增显著性检验、不覆盖primary。下一步可做论文结果整理与已有案例的shortcut解释审查；没有启动新模型/扩样，亦未自动commit或push。以下均为过程快照，以本段为准。

**最新调度覆盖：用户已授权切换每3秒发起一次的错峰并发。** 原串行进程已Ctrl-C退出（130）；indices0–5共6条响应原样保留，index6在途被中断、旧usage未知。新脚本 `reviews/deepseek-thinking-128k-staggered-20260909.py` 在独立目录 `artifacts/deepseek-thinking-128k-staggered-20260909/` 调度42道未完成题（包括明确授权重发的第7题）。最多49次物理尝试，不能称无重试的旧串行完整运行；仅作披露修订的描述性follow-up。请求参数不变、3秒间隔、最多48worker，无后续自动重试；遇失败停止新派发并保存已发响应。2项迁移测试通过。详情见 [调度修订记录](reviews/deepseek-thinking-128k-staggered-20260909.md)。恢复先读新目录generation/failure/attempts，**不要重启任一旧或新run**，也不要将新bundle直接交旧analyze。

**覆盖下段的最新进度：128K技术预检2/2通过，用户已授权启动48题thinking后续对照。** 本轮脚本 `reviews/deepseek-thinking-128k-live-20260909.py`，high / 131072-token / 3600秒，原48题顺序执行，最多48次，无重试/恢复/扩样；不重复canary。恢复时先读 `artifacts/deepseek-thinking-128k-live-20260909/` 中generation/failure/attempts，**不要重启run**。generation完整后独立analyze，描述性比较旧配置和全部24种简单策略；旧primary不变。详见 [48题记录](reviews/deepseek-thinking-128k-live-20260909.md)。相关合成测试9/9通过。32768版本没有真实请求；128Kcanary两题output合计33348且无截断，其结果不是能力证据。下段为8192尝试的历史终态。

**最新状态：已停止，无运行任务。** 用户授权的 DeepSeek thinking 尝试停在技术canary：2/2均确实返回reasoning，但每次8192个输出token全部用于reasoning，finish=length、最终content为空，0/2有效选项。按预定门槛未启动48题（benchmark calls=0），因此本轮没有模型能力或配对效果结论。2次合计input1486、output/reasoning16384；用时约4.5分钟。原primary阴性结果不变。

独立配置与终态见 [thinking follow-up](reviews/deepseek-thinking-followup-20260909.md)、[TODO](TODO-20260909.md)。新合成测试6/6、原live回归12/12通过。plan、canary和逐请求日志已保存至 `artifacts/deepseek-thinking-followup-20260909/`。**不要重新执行本轮 run，也不要执行 analyze（没有generation）。** 后续需要用户选择新预算/推理强度，再建立新的技术尝试；不自动重试、扩样或换模型。旧源码、plan、generation和analysis保持原样。

## 最新恢复入口：2026-09-08 审核与用户反馈后的 TODO

**最新状态：正式调用与分析已完成，当前无运行任务。** 48/48 响应格式有效、无 transport failure/retry。完整切换 **0/24**，own-context 命中 **5/48**，配对 **0 favorable / 1 adverse / 23 tie**，单侧 **p=1**，冻结分类为未检出。最短规则完整切换 2/24，最大 parent 行为差异规则 8/24。原始生成、正式 analysis 和全部 24-policy 对照已保存；详见 [正式结果记录](reviews/utilization-primary-results-20260908.md)。后续进入结果解释与短文讨论，不重跑本次尝试或自动扩大实验。下文待授权/未调用的描述属于此前阶段快照，以本段为准。

[最小修订方案](minimal-revision-plan-20260908.md) 已获用户确认，离线实施见 [执行记录](reviews/minimal-revision-execution-20260908.md)。历史重放只有五个描述性浮点数末位不同，轨迹、整数 endpoints 与分类相同；独立基线/模型配对补充报告已实现；预算敏感性显示 parent 四轮 0/24、五轮 19/24 成功，原正确 child 两个预算均 48/48。冻结 cohort/route/primary 与源码 manifest 保持不变，补充合成测试 5 项及原 live 测试 12 项通过。真实 canary 已完成 8/8 valid、0 transport failure；48 次正式响应尚未运行，等待既有协议要求的 canary 后人工交换性审核。canary hash 与恢复信息见执行记录末节，不重跑已完成的 canary。

先读 [TODO-20260908.md](TODO-20260908.md) 及其最新执行记录。用户已确认定位为短小的方向性论文，接受收窄结论；主张/比较解释、历史依赖核对、预算敏感性及 canary 现已完成。下文保留早期技术状态与历史恢复步骤；以顶部最新记录为准，不重做已完成工作、不改写冻结 artifacts、不删除旧版。

## 当前状态

2026-09-08 补充讨论已追加至 [当日 TODO 第 7—10 节](TODO-20260908.md)：研究聚焦非答案性结构启发能否改变证据获取路径，以及局部观察的适用边界；类人熵循环与 RSI 保留为动机，不作已证实前提。Astra 最新公开进展不直接回答这一机制问题，也不自动使研究失去价值；外部来源和解释限制已保存。继续时先形成最小设计修订，不自动切换实验模型或恢复 live calls。

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
