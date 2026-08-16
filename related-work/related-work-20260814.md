# Related Work 分析 — spark-to-knowledge 研究线

**撰写日期**：2026-08-14
**撰写者**：Metis（Hermes / deepseek-v4-pro）
**目的**：为 spark-to-knowledge 研究线（及 002 技术文章）梳理 2026 年最新相关工作的定位，明确本研究的差异化切面。供后续技术文章/preprint 的 related work 小节直接取用。
**检索方式**：web 检索 2026 年 LLM 科学发现/假设搜索/程序合成方向的最新论文与系统发布。

---

## 一、总体态势

LLM 驱动的科学发现是 2026 年最活跃的方向之一，顶尖团队（DeepMind、Sakana、FutureHouse 等）在 2026 年 5 月密集发布系统级成果。相关工作的共同点是：**多 agent 生成-辩论-演化闭环**，把"假设搜索"工程化。但几乎全部工作在**优化导向**框架内（找最好的假设），很少有工作把"探索的随机机制"作为一等研究对象，更没有工作做"扰动是否因果性地改变证据获取路径"的归因度量。

## 二、逐项分析

### 2.1 EvoDiverse（ICML 2026）— 最直接对撞

- **论文**：Haorui Wang et al., "Towards Diverse Scientific Hypothesis Search with Large Language Models", ICML 2026（PMLR 306）。作者来自 Georgia Tech、Virginia Tech、CMU、Berkeley、Cambridge、Microsoft Research New England
- **做什么**：把 LLM 假设搜索形式化为 sampling 问题，提出 **parallel tempering 启发**的进化框架 EvoDiverse——多温度种群并行搜索 + 跨温度 Metropolis-Hastings 交换。核心观察："标准 LLM 进化搜索会 diversity collapse（多样性坍缩）"；多温度机制能同时改善假设质量和多样性
- **验证**：分子发现、方程发现（LLM-SRBENCH）、算法发现三个 benchmark
- **与本研究的对撞点**：这是本研究温度实验的**正面镜像**——它回答了"温度机制在假设搜索中有没有用"（在多温度种群进化的形式下：有用），而本研究的 V2 negative 结果是"单一采样温度 + verifier 反馈调度"这一特定操作化无效
- **关键区分**（写 related work 必须声明）：
  1. EvoDiverse 是**种群级多温度并行进化**，本研究的 V2 是**单请求采样温度的反馈调度**——操作化完全不同，negative 结果不与之矛盾
  2. EvoDiverse 的指标是 diversity + quality 的优化收益；本研究 spark-to-knowledge 线的指标是**扰动的因果归因**（lineage 可删除重放、parent-deletion/matched-replacement 反事实、版本空间收缩的可认证度量）
  3. EvoDiverse 不做"扰动与答案无关"的构造性保证（R⊥F|D0），也不度量"证据获取路径是否被改变"——这正是本研究的核心问题
- **citation 必要性**：高。002 技术文章若扩展为 preprint，必须 cite 此篇并明确操作化差异，否则会被视为未做文献调研

### 2.2 DeepMind Co-Scientist（Nature, 2026-05-19）

- **论文**：Gottweis et al., "Accelerating scientific breakthroughs with an AI co-scientist" 系（Nature s41586-026-10644-y, 2026）
- **做什么**：基于 Gemini 的多 agent 联盟——Generation / Proximity / Reflection / Ranking / Evolution / Meta-review + supervisor，以 "tournament of ideas"（类比 AlphaGo 自我对弈）生成、辩论、排名、演化假设。大部分算力用于 **verification**（对照文献与数据库核查假设）
- **验证**：真实生命科学场景——肝纤维化药物重定位（实验室验证 91% 抑制）、ALS、细胞衰老、病原蛋白靶点等
- **与本研究的对撞点**：低。Co-Scientist 是"假设生成的生产力工具"导向，不研究探索的随机机制；但其 **verification-heavy 设计哲学**（多数算力用于验证而非生成）与本研究"证据驱动压缩"的取向一致，可作为理念呼应引用
- **引用价值**：作为"LLM 科学发现系统"的背景引用；并可用于论证"验证/证据约束是当前系统共识"——本研究的版本空间压缩度量是对这一共识的量化版本

### 2.3 FutureHouse Robin（2026-05-19）

- **做什么**：端到端科学发现多 agent 系统（此前发布过 Crow/Falcon/Owl 文献搜索、Phoenix 化学合成、Finch 数据分析）
- **与本研究的对撞点**：低。是工程系统展示，无机制研究
- **引用价值**：背景引用（科学发现 agent 生态）

### 2.4 Sakana AI Scientist-v2

- **做什么**：workshop 级自动科学发现，agentic tree search
- **与本研究的对撞点**：低。自动写论文流水线导向
- **引用价值**：背景引用（AI Scientist 谱系）

### 2.5 进化搜索/程序合成线（FunSearch / CoEvo / LLM-SRBENCH）

- **做什么**：LLM 作为进化算子迭代改进程序/公式，FunSearch 发现新数学构造、CoEvo 做符号回归动态知识库
- **与本研究的对撞点**：中低。这一线与本研究的任务形式（程序合成 + 验证器）同源，但全部是**优化导向**（fitness 提升），无因果归因
- **引用价值**：方法论背景引用。特别地，这一线的"验证器引导搜索"与本研究共享组件，但本研究把验证过程本身（版本空间收缩）作为研究对象而非工具

## 三、本研究的差异化定位（供 related work 小节直接使用）

与上述工作相比，本研究独特之处在于三点：

1. **研究对象**：不是"如何找到更好的假设"（优化问题），而是"一个与答案无关的扰动（R⊥F|D0 构造性保证），能否因果性地改变证据获取路径，从而打开常规搜索轨迹不会产生的发现分支"——一个更底层的机制问题
2. **度量工具**：把"验证/压缩"从工程手段升级为研究对象，提供**版本空间收缩的可认证度量**（初始 log-volume、逐轮 N_T、contraction bits、certified facts、terminal singleton），使"spark 是否真的改变了证据路径"可被严格判定
3. **反事实纪律**：lineage 可删除重放、parent-deletion、matched-replacement（same-frame/same-stratum 对照）三层反事实，全部 prospective 冻结——这是对"探索机制归因"的因果要求，现有工作均未触及

一句话定位：**现有工作把假设搜索做成了优化工程；本研究把"探索的偶然性是否因果有效"做成了可证伪的度量实验。**

## 四、建议动作

1. 精读 EvoDiverse 全文（含 appendix 的实验细节），确认其温度机制实现与本研究 V2 操作化的确切差异表述
2. 精读 Co-Scientist Nature 论文的 verification 部分
3. 技术文章/preprint 的 related work 按本文档结构撰写：先承认"温度/多样性"已有 EvoDiverse 正面结果，再声明本研究的问题层级不同（因果归因 vs 多样性优化）
4. 002 的 citation_audit.md 补充这两篇的条目
