# 英文论文内容修订检查（2026-09-13）

范围：执行用户批准的论文方向调整；不改变实验设计、冻结响应或评分，不增加模型调用。对应[正文](../paper-manuscript-en-20260913.md)、[补充](../paper-supplement-en-20260913.md)、[主张轴线](../paper-claim-evidence-status-20260913.md)、[结构记录](../paper-claims-and-structure-20260913.md)。旧20260910中英文及PDF保留。

## 内容与数值

- 标题、摘要、引言和结论统一为有限任务中入口选择、观察相容修订及预测边界。保留正向局部观察，不抹去原主检验阴性、强简单策略和新版active−random负差。
- 正文新增§2.6–2.7、§4.1；讨论补查询/更新未分离、repeat重新生成、类别不平衡、两标签限制和12world复用。没有将熵机制、完整科学发现或RSI写成已证明。
- 补充新增全部12world×3条件×3阶段结果、15个代码基线单元格、三版本区别、缺失/重试/用量及复现入口。
- [离线检查脚本](check_paper_revision_20260913.py)核对12逐world表行（108阶段分数）、9个阶段均值、15个基线均值、主要差值和4/7/1局部改善计数。旧正文/补充所有表行、六条参考文献内容保持原样；当前四份文档本地链接目标存在。未重新开展文献搜索或声称系统性新颖性验证。
- 重新运行[新版原始响应审计](microloop_revised_audit_20260911.py)：108条raw校验、108个提示/逐点评分、聚合重放一致；无新模型调用。

冻结records SHA256：ff5eab01357fd6de99842f2267ccc25589245b269783d58c01a60bdf90c2e30c。

冻结analysis SHA256：8d2ee5f668ed3068d40b14307042b6f2defeaa5a4b983120d0719ba87fa94ae8。

## PDF

使用单独的[新版渲染入口](render_english_preprint_20260913.py)，复用旧转换函数但不修改旧渲染器、源稿或输出。10pt、19mm边距；无缩字体来维持旧页数。

- 正文PDF8页：主文7页、参考文献及可用性说明1页。
- 补充PDF6页。
- XeLaTeX两遍编译：无Overfull、Missing character、Undefined control。
- [PDF检查脚本](check_english_pdf_20260913.py)：14页均无页外文字或替换字符；[逐页检查记录](../paper-build-20260913/pdf-check.json)已保存。
- 已目视查看两份全页缩略拼图：表格完整，无明显遮挡、裁切或空白页。缩略图检查不等于逐字校对；标题断词、分页、表格列宽仍可按投稿格式精修。

为PDF核验临时安装PyMuPDF/Pillow至/tmp/thinking-machine-pdf-check-20260913，未加入项目依赖。工具依赖不属于实验环境变更。

复核命令：python3 reviews/check_paper_revision_20260913.py；python3 reviews/microloop_revised_audit_20260911.py；python3 reviews/render_english_preprint_20260913.py；PYTHONPATH=/tmp/thinking-machine-pdf-check-20260913 python3 reviews/check_english_pdf_20260913.py。最后一项需要相应临时依赖。

## 仍待完成

当前通过的是本地内容整合与一致性检查，不是投稿就绪认证。目标篇幅/格式、完整论文复现包、独立机器复现、聚焦新颖性论证仍待完成。未commit/push、公开上传或投稿。

## 续轮更新：提示附录与隔离评分

本轮补两个display公式（阶段准确率/改善量、world等权主动−随机差），缩短少量重复解释；原表格和参考文献仍逐行保留。标题禁止断词。重新编译正文8页（7+1）、补充6页；14页检查通过，公式所在第4页目视无裁切/重叠。

新增[提示附录](../paper-prompts-en-20260913.md)及[最小候选包](../paper-reproduction-20260913/README.md)。[构建器](build_paper_bundle_20260913.py)仅读冻结输入，输出到新目录，显式挑选字段，不递归复制原始目录。287条提示SHA256匹配；其中108新版阶段提示由原状态机重建后还与保存request逐字比较。示例按冻结顺序首项及world0选择。

导出白名单6文件（5个payload及manifest）：原DSL、独立评分脚本、提示JSON、候选/实际反馈/初始与测试观察JSON、README、manifest。对导出内容进行常见key/Bearer/private-key格式检查；未读取凭证文件。常见模式检查不是保证排除一切隐私；发布前仍需内容/许可审查。评估test标签有意包含，只用于评分，不能混入模型prompt。

[隔离检查脚本](check_paper_bundle_20260913.py)只复制白名单至临时目录，用清空环境及Python -I运行，未访问项目目录或API。结果：287提示hash、108阶段合法性/可见相容性/测试正确数通过，三个阶段均值与正文一致。[机器结果](../paper-build-20260913/reproduction-check.json)保存。仅同机同解释器的可移植性检查，不标成独立环境科学复现。

当前包不含旧microloop原轮/重评分、所有旧实验的离线评分、完整消息角色/请求设置、全量构造或provider原始响应核验；这些仍由原仓库冻结材料承担，不暗示整篇论文已一键复现。附录为Markdown+JSON，尚未另排完整提示PDF；正文/补充PDF均提供对应说明。

新命令：python3 reviews/build_paper_bundle_20260913.py；python3 reviews/check_paper_bundle_20260913.py。均无模型调用，不用于覆盖冻结实验产物。
