# 2026-09-09 DeepSeek thinking 128K / 48题后续对照

用户在128K技术预检2/2通过后明确授权「那启动吧」。复用已完成canary，不重复调用。

## 调用前固定方案

- 原24 worlds / 48 public tasks，原顺序、原prompt，官方deepseek-v4-pro，thinking enabled / high，max_tokens131072，timeout3600秒，JSON object，无temperature/seed/tools/history。
- 最多48次顺序调用，理论completion上限6291456 tokens（含reasoning、不含input，不是预估实际消耗）。按两道技术题均值粗估约80万输出token、约4.3小时；仅两题估计，不作保证。
- 新目录 `artifacts/deepseek-thinking-128k-live-20260909/`；每请求前后fsync日志，最终generation另存0600。无自动retry/resume、替换或扩样。HTTP/transport/envelope失败立即停止、整轮不完整；received-invalid消耗slot，对应world按旧scorer记tie/miss。截断另报，不择优删样。
- 请求阶段仅发送public rendered_prompt，不读取private评分key。生成完成后独立analyze；比较原disabled配置、own/cross命中、favorable/adverse/tie、完整双臂切换、四strata、全部24种简单策略与old/new完整切换四格表。无新推断性检验。
- 这是原primary阴性结果后、通过技术预算校准选出的描述性配置follow-up，不继承旧primary身份，不增加独立world数。thinking/采样/预算/超时同时不同，不能只归因于thinking开关；不能由此直接声称科学发现、内部entropy机制或通用能力。原primary、8192失败和128K预检均保留。

## 启动验证

相关合成测试9/9通过，其中新runner覆盖恰好48次、原顺序、无重复canary、131072/high/3600参数、HTTP503停止、拒绝重启和显式execute要求；没有测试provider调用混入真实产物。diff check通过。

命令：`python reviews/deepseek-thinking-128k-live-20260909.py plan`，`run --execute`。完整generation产生后才运行同脚本 `analyze`。不要重启已有attempts的run；读日志或等待现有任务。

## 当前状态

历史串行尝试在保存6条响应、第7题在途时按用户授权中断；没有串行完整generation，不运行本脚本analyze。随后迁移至 [错峰并发修订](deepseek-thinking-128k-staggered-20260909.md)，最终48条已完成并用独立修订分析器评分。结果与限制见 [结果记录](utilization-thinking-results-20260909.md)；旧串行plan和ledger保留，不重跑。
