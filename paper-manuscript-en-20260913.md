# Context-Guided Exploration in Finite Rule Tasks: Entry Selection, Rule Revision, and Predictive Limits

Working manuscript, 13 September 2026. Revised English draft incorporating the two-feedback experiment. The [previous English draft](paper-manuscript-en-20260910.md) and [Chinese historical draft](paper-manuscript-20260910.md) remain unchanged. See the [current supplement](paper-supplement-en-20260913.md). Public release and venue-specific formatting remain pending.

## Abstract

Can language models use context and feedback to explore finite rule problems? We distinguish code-verified opportunities, model entry selection, evidence-consistent revision, and predictive improvement. In 24 outcome-conditioned worlds with paired contexts requiring different correct actions, DeepSeek without thinking, DeepSeek with thinking, and GLM with thinking achieve complete two-arm success in 0/24, 11/24, and 15/24 worlds. The original primary test is negative, and a simple public policy also achieves 11/24. A targeted nine-world challenge retains 2/9 model successes, without excluding other heuristics. Reference, new-observation, and output-cap controls provide mixed or limited additional support. A separate exploratory experiment extends the model's role to two queries and rule revision on 12 fixed worlds. Under a corrected prompt–parser contract, all 108 responses are executable and evidence-consistent. Final unseen-input accuracy is 65.10% with model-selected queries, 73.05% with random queries, and 49.87% with repeated observations. Model-selected queries more often reveal contradictions to current candidates, but their final predictions are lower than random in eight worlds and higher in two. These local behaviors do not establish an average active-query advantage in this setting. Strong code baselines, limited identifiability, and exploratory reuse constrain interpretation. The results motivate separate evaluation of exploration and predictive utility, not claims of an internal entropy mechanism or general scientific discovery.

## 1. Introduction

When an agent has an incomplete explanation, can additional information help it move beyond its current candidate, choose a useful direction, and formulate a better rule? This question motivates our study. We use entropy expansion and contraction as a conceptual description of broadening candidates and subsequently constraining them with evidence, not as a measured property of model computation. We do not directly test recursive self-improvement (RSI) or general scientific-discovery capability.

We distinguish two questions: whether a context offers an opportunity for success within a specified budget, and whether a model utilizes that opportunity. Enumeration and deterministic verification address the first; actual model responses are required for the second. Model failure is difficult to interpret if no successful action exists, while code-verified availability alone says nothing about model utilization.

We construct paired contexts with matched opportunity counts but different correct actions. Public-information baselines, a targeted policy challenge, auxiliary-reference controls, and an output-cap control constrain the interpretation of successful choices. A separate experiment extends the model's role to producing one candidate rule, comparing a new true observation with repetition of an old observation. A subsequent two-feedback experiment lets the model propose queries and revise executable rules on 12 fixed worlds. We separately ask whether local behaviors occur and whether model-selected queries improve prediction over random queries. Neither local success nor a negative mean comparison settles general discovery capability.

### 1.1 Related work and positioning

Explicit hypotheses and executable validation have direct precedents. Hypothesis Search translates natural-language hypotheses into programs tested against observations; Hypothesis Refinement combines proposing, selecting, and revising rules while distinguishing induction from application. Combining LLM proposals with an external interpreter is therefore not our novelty claim. [Wang et al., 2024](https://arxiv.org/abs/2309.05660v2); [Qiu et al., 2024](https://arxiv.org/abs/2310.08559v4).

FunSearch places generated programs in an execution-based evolutionary loop. EvoDiverse studies hypothesis quality and diversity under a fixed validation budget using parallel-tempering-inspired search. We neither implement nor benchmark these search systems, and our experiments do not validate temperature scheduling or a diversity mechanism. [Romera-Paredes et al., 2024](https://www.nature.com/articles/s41586-023-06924-6); [Wang et al., 2026](https://arxiv.org/abs/2606.10587v1).

DiscoveryBench evaluates multi-step, data-driven hypothesis discovery. The recent SCILAWS-BENCH preprint distinguishes rule proposal from fixed real observations from active querying of synthesized hidden laws, and separates predictive fit from scientific validity. Our enumerable local task has a narrower scope. Unseen-input prediction, hidden answers, and executable verification do not by themselves make it a complete scientific-discovery evaluation. [Majumder et al., 2024](https://arxiv.org/abs/2407.01725v1); [Huang et al., 2026](https://arxiv.org/abs/2609.01552v1).

Our design separates available opportunities from model utilization, uses paired entries with distinct correct actions, and tests whether short feedback-driven revision improves prediction. These are design contributions, not claims of priority or superiority over existing discovery systems. Different tasks and budgets are not directly comparable.

## 2. Tasks and Methods

### 2.1 Worlds and model responsibilities

A world is a finite binary rule problem over three variables in {-2, -1, 0, 1, 2}, giving 125 inputs. Its construction fixes a hidden target and a bank of 256 rules consistent with 12 initial observations, D0, on one coordinate face. The remaining inputs comprise 49 ordered evidence points and 64 test points. A parent program is selected from the bank by node count and then canonical hash, without selecting it by the hidden target. The model sees D0 and the parent, but not the target, test labels, or private answer key.

Each entry task additionally supplies an arithmetic context fragment and ten replayable edits to the parent's predicate operands, each using the fragment once. The response is an opaque option ID, not a generated program or an experiment sequence. Replacing the context changes the program produced by an edit; the paired task consequently tests appropriate action selection under changing context rather than preference for unchanged option text.

After selection, code executes the edit and performs verification and rule identification. Subsequent queries, oracle responses, and hypothesis filtering are not model decisions. Sections 2.5–2.6 define separate single-response and two-feedback rule tasks; neither inherits the entry endpoint.

### 2.2 Opportunity construction and sampling

We enumerate context–action combinations and evaluate their opportunity structure under a frozen four-round verification budget. In each arm of the original cohort, exactly one nonconstant raw action satisfies the strong-K4 endpoint, no constant action does, and K2 opportunity counts are matched across arms. The two correct raw actions differ. Uniqueness concerns successful actions, not pairwise behavioral distinctness of every incorrect action.

In each verification round, the oracle scans the 49 evidence points in fixed order and returns the first disagreement's position and true label, or MATCH. Code retains hypotheses producing the same complete response, not merely the same label. The disagreement position implicitly certifies agreement at preceding points: four rounds are not four ordinary independent labels. The first query is the edited child; subsequent queries are selected from the remaining semantic hypotheses by node count and canonical hash. Test labels do not enter this selection or filtering.

K1 requires a valid single-use context edit with the specified lineage: the child is binary over the full domain, consistent with D0, distinct from the parent, and behaviorally different on the evidence domain. K2 additionally requires that the child is not a direct target hit but reaches a singleton semantic hypothesis within four rounds, retains the truth, recovers full-domain behavior, and includes at least one non-MATCH response that reduces the hypothesis set. K3 further requires failure of the parent to identify the target within the same budget.

The entry labels use strong-K4, or `K4_full_pool`. Beyond K3, replacements matched on parent, edit frame, context stratum, and complexity are deduplicated by full-domain child behavior. At least three distinct control behaviors must exist, and all controls in the frozen matched pool must fail exact identification. This is not the earlier two-replacement endpoint, failure of every possible context, or exclusion of every simple model policy.

Selection uses code-evaluated outcomes. These are therefore outcome-conditioned development worlds, not random natural tasks or an independent held-out evaluation. Target-independent context generation does not make subsequent success-conditioned selection target-independent. The original model-response protocol was specified before those responses, but this does not change the world's sampling status.

| Source | Selection and use | Dependence |
|---|---|---|
| Original 1024-world pool | 24 worlds, six per context stratum; 48 arms reused across three configurations | Not 72 independent worlds |
| Another 1024-world pool | Nine worlds where two specified policies fail complete success; one pair each | Policy-conditioned, not a balanced or generalization sample |
| Same nine worlds | 54 reference-control responses and 27 rule-generation responses | No increase in distinct worlds |
| Fixed new 12-world construction | No performance-based selection; original and revised two-feedback runs | Same worlds reused after a contract repair, not independent confirmation |
| Eight of the original 24 | Two per stratum, selected by fixed stratum/anchor-hash rules; 32 output-cap responses | A subset, not an additional cohort |

The context strata are affine commutative, affine directional, affine multiplicative, and pairwise variable—not hidden-target classes. The challenge has counts 3, 4, 0, and 2 in this order. One pair per challenge world is chosen by frozen pair-hash rules without reading subsequent model responses. Follow-up designs remain exploratory.

### 2.3 Entry endpoints, configurations, and policies

For world i, O_i counts selections of each arm's own correct action, and C_i counts selections of the opposite arm's correct action. The paired net score is S_i = O_i − C_i. Cross hits are not all errors. Positive, negative, and zero scores define favorable, adverse, and tied worlds. Complete two-arm success requires both own-context choices to be correct; a favorable world need not be completely successful.

The original primary test is a one-sided exact sign test on non-tied world scores, with alpha = 0.05. Complete success is emphasized descriptively, not substituted retrospectively for that endpoint. Follow-ups introduce no new inferential tests and do not inherit its planning power. All original responses were valid; failure rules appear in Supplement S5.

The original tasks were run with DeepSeek V4 Pro without thinking (output cap 256), DeepSeek V4 Pro thinking high, and GLM-5.3 thinking high (both caps 131072), using the same prompts and independent contexts. Sampling settings are in S5. Mode, budget, sampling, and time differ, so these are neither single-factor thinking interventions nor compute-matched comparisons.

Public-information policies select, for example, a minimum-node candidate among publicly supported actions, or one maximizing full-domain behavioral difference from the parent, with fixed tie-breaking. There were 24 original policies. After observing DeepSeek thinking results but before GLM responses, two diagnostics added nonconstant filtering. We retain these strong diagnostics without relabeling them as original prespecified baselines; all 26 appear in S3.

### 2.4 Reference and output-cap controls

For the nine challenge worlds, we fully expand the ten candidate programs and compare aligned numerical reference tables, unmatched tables, and no added table, yielding 54 new responses. The aligned table evaluates the original context on all 125 inputs. The unmatched table comes from a fixed function library, matches stratum, node count, depth, and output multiset, but does not match the behavior of arithmetic subexpressions in the parent or candidates. Unmatched does not mean statistically independent of the target.

All conditions retain the complete candidates and original scoring and use DeepSeek thinking. Only the reference block changes. No added table does not remove context already embedded in the candidates. This is a redundant-presentation control, not new target evidence or a complete context-removal ablation. Matched distributions or input-token counts do not establish equal information or computation.

The output-cap control uses eight fixed worlds from the original cohort. Each arm is queried contemporaneously at caps of 256 and 8192, retaining non-thinking mode, temperature 0.2, the original prompt, and other request content. Historical thinking responses are same-task descriptive references. A larger cap does not force longer output or equalize actual reasoning compute.

### 2.5 New evidence and single-response rule generation

Each of the same nine targets receives three separate requests sharing D0, the parent, and the public generator grammar: no additional observation, one new true observation, or repetition of one D0 observation. There is no action menu. Code selects the new query among the 49 evidence points by the balance of predicted partitions over the 256-rule bank, with public-hash tie-breaking, before reading that query's target label. The repeated point is chosen from D0 by a fixed hash. The model does not design the query.

New labels leave 61–189 compatible rules. Only 2/9 contradict the parent, but the other seven still exclude some compatible rules; all nine are retained. These candidate counts are properties of a finite hypothesis set, not measurements of model-internal entropy.

The model returns an executable binary DSL expression of depth at most 5 and at most 31 nodes (S1). Retaining the parent is allowed: validity and consistency do not establish novelty. The main descriptive score is accuracy on 64 private test points per world, averaged with equal world weights. Missing, malformed, illegal, or nonbinary outputs score zero with fixed denominators. Executable but observation-inconsistent programs retain their raw test accuracy, with consistency reported separately. We also report perfect test accuracy and exact recovery across all 125 inputs. Neither 576 scored points nor 27 calls constitutes that many independent worlds.

Code baselines retain the parent or select a minimum-node observation-consistent program from the public generator reservoir, either allowing constant approximations or excluding constants. Selection uses fixed hash ties, not the private 256-member list or test labels, but benefits from generator priors and enumeration. The constant-allowing version is an approximation baseline rather than a search confined to the nonconstant target family.

### 2.6 Two-feedback querying and rule revision

Twelve new seed instances were fixed before target construction, without filtering by model performance, parent accuracy, or two-query recoverability. They use the same generator and 12/49/64 observation/query/test split, not new rule families. Each world has three separately sampled trajectories with identical initial prompts. The model returns a candidate and suggested query, receives one true point label, revises and suggests another query, then returns a final candidate. Active executes the suggested unused query; random overrides suggestions with two distinct points ordered by a fixed target-independent hash; repeat supplies two fixed D0 observations. Prompts explicitly allow different or repeated points. Prior raw answers and actual feedback are replayed, not hidden reasoning.

Only individual labels are returned, not a first-mismatch position, matching prefix, private bank or test labels. The revised grammar accepts root gt/eq as shorthand for ite with outputs 1/0; depth 5 and 31-node limits apply after expansion. Invalid active queries consume a stage without feedback; controls keep their schedule. Missing or invalid final rules score zero without removing worlds. Earlier missing stages do not automatically zero later valid rules.

Let A denote test accuracy for world i, condition c and stage s (0: initial; 1–2: after feedback), and G the initial-to-final gain:

$$
A_{i,c}^{(s)}=\frac{1}{64}\sum_{x\in T_i}\mathbf{1}\!\left[\hat f_{i,c}^{(s)}(x)=f_i(x)\right],\qquad G_{i,c}=A_{i,c}^{(2)}-A_{i,c}^{(0)}.
$$

The main descriptive contrast is the equal-world mean paired difference:

$$
\Delta_{\mathrm{active-random}}=\frac{1}{12}\sum_{i=1}^{12}\left(A_{i,\mathrm{active}}^{(2)}-A_{i,\mathrm{random}}^{(2)}\right).
$$

Here T is the disjoint test set, f the target and the hatted f the model candidate; invalid or missing final candidates receive accuracy zero. Differences are reported in percentage points (100 times the accuracy difference). Calls and ceilings, not actual compute, are matched. Two binary labels cannot generally distinguish all 256 D0-compatible hypotheses; exact recovery is secondary. Public-reservoir baselines use random or balanced queries and consistent minimum-node rules, with and without constants; enumeration resources are not model-matched.

### 2.7 Execution and protocol repair

The first two-feedback run exposed a prompt–parser mismatch: root comparisons appeared allowed by the prompt but were rejected as integer expressions. Of 108 stages, 106 responses were saved and two remained missing after the two-retry allowance. Fixed root-predicate expansion was then used for a separately labelled post-hoc rescore, without changing historical feedback. A new prompt and parser contract was frozen and rerun on the same 12 worlds. The revised run reported below has 108/108 valid, observation-consistent, untruncated responses and no retries. It is exploratory, not a fresh confirmation set; old scores and rescoring remain separate in S7.

Raw responses, prompts, per-world scores and attempt accounting are retained; correctness never determines retries. Earlier experiments follow their own failure rules (S5). Revised raw replay and independent pointwise scoring agree with the aggregate. Local auditing is not a released public reproduction package.

## 3. Entry-Selection Results

### 3.1 Original cohort and public policies

| Configuration or policy | Own /48 | Cross /48 | Complete /24 |
|---|---:|---:|---:|
| DeepSeek, non-thinking | 5 | 6 | 0 |
| DeepSeek, thinking high | 34 | 1 | 11 |
| GLM-5.3, thinking high | 38 | 1 | 15 |
| Minimum nodes | 22 | 6 | 2 |
| Maximum parent behavioral difference | 29 | 2 | 8 |
| Nonconstant filter + maximum difference | 33 | 1 | 11 |
| Nonconstant filter + minimum nodes | 31 | 2 | 8 |

The original non-thinking configuration has favorable/adverse/tied counts of 0/1/23, net score −1, and primary p = 1. It does not show a positive direction merely lacking significance. Both thinking configurations have counts of 22/0/2 and some complete successes, without replacing the original negative finding.

The nonconstant maximum-difference policy matches DeepSeek thinking's 11 complete successes. GLM reaches 15, but agrees with that policy on 37/48 choices; ten worlds are complete successes for both, five for GLM alone, and one for the policy alone. Agreement does not identify an internal algorithm, and disagreement does not establish reasoning beyond heuristics. No additional superiority test is performed.

### 3.2 Targeted challenge and code-budget sensitivity

In the nine challenge worlds, DeepSeek thinking achieves own 9/18, cross 3/18, and complete success 2/9. Two worlds have both arms correct, five one correct, and two neither correct. Nonconstant minimum-node and maximum-difference policies have own scores of 7/18 and 5/18, respectively, and no complete successes. All 26 policies have zero complete successes on these selected pairs.

Policy failure is part of selection; the model's two complete successes are observed after freezing. They cannot be fully explained by always executing either specified deterministic policy, but other policies, mixtures, and random choices remain possible. Twenty-six policies are not independent confirmations, and the difference from 11/24 in the original cohort does not estimate a change in model capability.

A code-only sensitivity check changes verification from four to five rounds on the original cohort. Parent identification rises from 0/24 to 19/24, while the correct children succeed in 48/48 arms under both budgets. The opportunity advantage is specific to a procedure and budget, not evidence that discovery without that entry is permanently impossible.

## 4. Controls and Rule-Generation Results

The following panels have different denominators and endpoints; their scores must not be pooled.

| Experiment and condition | Main observation | Additional observation |
|---|---|---|
| Reference: aligned | Own 8/18 | Cross 3/18; complete 2/9 |
| Reference: unmatched | Own 7/18 | Cross 1/18; complete 1/9 |
| Reference: none | Own 9/18 | Cross 2/18; complete 1/9 |
| Rule generation: no addition | Accuracy 52.95% (305/576) | Perfect test and full-domain recovery: both 0/9 |
| Rule generation: new observation | Accuracy 69.27% (399/576) | Both 0/9 |
| Rule generation: repeated observation | Accuracy 65.80% (379/576) | Both 0/9 |
| Non-thinking: cap 256 | Own 1/16 | Cross 0/16; complete 0/8 |
| Non-thinking: cap 8192 | Own 1/16 | Cross 1/16; complete 0/8 |

Aligned references produce one fewer own hit but one more complete success than no reference: there is no consistent benefit. Reporting only two versus one complete successes would obscure this mixed result. It also does not show that context in general is useless, since complete candidates already contain the context.

All 27 generated rules are valid and consistent with visible observations. New versus no additional observation improves mean accuracy by 16.32 percentage points, with five world-level wins, zero losses, and four ties. Against repetition, the difference is only 3.47 points, with two wins, two losses, and five ties. Repetition itself exceeds no addition by 12.85 points, with three wins, one loss, and five ties. One response per condition cannot separate prompt, attention, or generation variability reliably; the evidence for an information-specific benefit is weak.

The unchanged parent scores 66.67% in all conditions. For no addition, new observation, and repetition, minimum-node baselines score 73.44%, 83.16%, and 73.44% when allowing constants, and 64.93%, 75.52%, and 64.93% when excluding them. The model's new-observation condition exceeds the parent but remains below both minimum-node baselines. No model condition perfectly predicts the test set or recovers the full target behavior. Non-identifiability limits possible recovery, but does not turn partial predictive accuracy into discovery of the true rule.

The original non-thinking responses used only 8–14 output tokens and ended normally. In the contemporaneous cap comparison, outputs remain 8–13 tokens under both caps, with zero wins, zero losses, and eight ties on own and complete scores. Historical responses on the same 16 arms have own/cross/complete counts of 1/2/0 without thinking and 11/1/3 with thinking. Simply increasing the cap provides no observed improvement and does not support direct answer truncation as an explanation. This neither establishes statistical equivalence nor identifies the cause of the historical thinking difference.

### 4.1 Two-feedback prediction and revision

| Condition | Initial accuracy | After one label | Final accuracy |
|---|---:|---:|---:|
| Model-selected queries | 71.35% | 63.54% | 65.10% |
| Random queries | 71.35% | 70.57% | 73.05% |
| Repeated observations | 67.58% | 65.36% | 49.87% |

Active minus random is −7.94 percentage points (2 wins, 8 losses, 2 ties). Their initial programs have identical full-domain behavior within all 12 worlds. Active decreases by 6.25 points, whereas random increases by 1.69. Active still improves over its own initial score in 4 worlds, declines in 7, and ties in 1: a negative mean is not absence of every local improvement.

Active and random exceed repeat by 15.23 and 23.18 points; gain differences are 11.46 and 19.40 points. Repeat itself declines by 17.71 points and entails fresh generation plus repeated presentation, not an unchanged-candidate control. These differences do not isolate net knowledge gain. All conditions are valid and visible-observation-consistent at every stage; full-domain recovery is 0/12 throughout.

A supplementary process diagnostic counts labels contradicting the immediately preceding candidate: 16/24 for active and 6/24 for random. Initial-to-final behavior changes in 11/12 active worlds and 7/12 in each control. These observations are not information-gain comparisons under a common candidate distribution.

Constant zero scores 77.47%, but fits final visible evidence in only 7/12 active and random worlds: a predictive reference, not a fully evidence-consistent alternative everywhere. Public balanced-query minimum-node rules score 76.95% without constants and 83.85% allowing constants; random-query counterparts score 71.22% and 77.08%. The model does not establish an advantage over strong public policies. Per-world stages and technical history appear in S7.

## 5. Discussion and Limitations

### 5.1 Local behavior and predictive utility are distinct

Entry selection, obtaining a contradiction, consistent revision, and unseen prediction are separate evaluation targets. Entry experiments support local context utilization; the two-feedback run demonstrates executable querying and evidence-consistent revision, not general discovery. The active mean declines despite individual improvements and is lower than random in this run.

A counterexample can exclude an incorrect candidate without ensuring its replacement predicts better. Conversely, lower accuracy does not imply no information was incorporated. Finding contradictions need not distinguish remaining alternatives efficiently. The active–random contrast covers the interaction policy, not query quality isolated from updating or scheduler-presentation effects. A candidate-selection bottleneck is a hypothesis, not an identified cause.

### 5.2 Alternative explanations and configuration differences

Hidden answers do not exclude grammar priors, semantic or structural matching, short-rule preferences, or ordinary constraint fitting. Strong policies remain important alternatives. The targeted challenge excludes always following two specified policies as a complete account, not all shortcuts.

Thinking comparisons differ in budget, sampling and time; the cap control narrows direct truncation without identifying a mechanism. The two-feedback study matches configured model and call ceilings, not realized compute. Provider aliases do not guarantee unchanged service behavior. Old and revised runs must not be pooled or their difference attributed solely to grammar clarification.

### 5.3 Construction and statistical limits

Original opportunities and challenges are outcome- or policy-conditioned. The 12-world construction avoided performance filtering, but the revised prompt reuses those worlds after examining the first run. All use one finite generator. Additional configurations or stages do not increase independent worlds; test points assess within-world prediction, not cross-task transfer.

With 12 worlds and one trajectory per condition, the negative active–random difference neither supports a hidden positive trend nor establishes stable harm. Non-identifiability limits the force of zero exact recoveries; class imbalance limits raw-accuracy interpretations. Evidence consistency and strong baselines remain essential checks.

### 5.4 Relation to the motivating hypothesis

An exploration–constraint cycle remains a motivation, not a measured internal entropy process. Evidence can add true constraints while a selected rule's prediction worsens; short or definite explanations need not be accurate. No result identifies internal candidate expansion, an entropy mechanism, human-like creativity, or an RSI prerequisite.

Limited automatic query selection and two-step revision have now been tested. Automatic generation of seemingly unrelated useful information, longer-horizon discovery, and independent-task generalization have not. Separating query policies from update procedures and candidate retention could investigate the observed gap; these data do not establish its cause.

## 6. Conclusion

Some tested configurations select effective context-dependent entry actions in finite rule tasks, and a targeted challenge retains successes not explained by two fixed policies. Strong heuristics, the original negative primary test, and mixed information controls constrain this local evidence. The corrected two-feedback experiment extends observed behavior to querying and evidence-consistent revision, while model-selected queries yield lower mean final accuracy than random queries despite more frequent contradictions to current candidates.

These findings support evaluating opportunity utilization, feedback response, and predictive improvement separately. They neither erase local capabilities nor establish reliable active-query gains, an internal entropy mechanism, general scientific discovery, or RSI. The contribution is a controlled account of local exploratory behavior and its observed predictive limits.

## References

- Wang, R., Zelikman, E., Poesia, G., Pu, Y., Haber, N., & Goodman, N. D. (2024). [Hypothesis Search: Inductive Reasoning with Language Models](https://arxiv.org/abs/2309.05660v2). ICLR 2024. arXiv:2309.05660v2; first posted in 2023.
- Qiu, L., Jiang, L., Lu, X., Sclar, M., Pyatkin, V., Bhagavatula, C., Wang, B., Kim, Y., Choi, Y., Dziri, N., & Ren, X. (2024). [Phenomenal Yet Puzzling: Testing Inductive Reasoning Capabilities of Language Models with Hypothesis Refinement](https://arxiv.org/abs/2310.08559v4). ICLR 2024. arXiv:2310.08559v4; first posted in 2023.
- Romera-Paredes, B., et al. (2024). [Mathematical discoveries from program search with large language models](https://www.nature.com/articles/s41586-023-06924-6). Nature, 625, 468–475. doi:10.1038/s41586-023-06924-6. Published online on 14 December 2023; cited by volume year.
- Wang, H., Shojaee, P., Meidani, K., Sun, K., Hernández-Lobato, J. M., Head-Gordon, T., He, J., Reddy, C. K., Zhang, C., & Du, Y. (2026). [Towards Diverse Scientific Hypothesis Search with Large Language Models](https://arxiv.org/abs/2606.10587v1). arXiv:2606.10587v1; the author record identifies ICML 2026.
- Majumder, B. P., Surana, H., Agarwal, D., Mishra, B. D., Meena, A., Prakhar, A., Vora, T., Khot, T., Sabharwal, A., & Clark, P. (2024). [DiscoveryBench: Towards Data-Driven Discovery with Large Language Models](https://arxiv.org/abs/2407.01725v1). arXiv:2407.01725v1.
- Huang, Y., et al. (2026). [Can LLMs Discover Scientific Laws in Real and Parallel Worlds?](https://arxiv.org/abs/2609.01552v1). arXiv:2609.01552v1, posted 1 September 2026. Cited as a preprint without assuming peer review.

## Supplement and Availability

The [English supplement](paper-supplement-en-20260913.md) contains methods (S1), provenance (S2), all policies (S3), per-world rule results (S4), execution records (S5), local artifact references (S6), and two-feedback results and repair history (S7). The [literature-check record](related-work/related-work-20260910.md) documents the bounded review. Historical drafts and frozen experiments remain unchanged. The [current editorial check](reviews/paper-editorial-check-20260913.md) records local verification and pagination. Venue-specific formatting, a distributable artifact inventory, and independent-environment reproduction remain pending.
