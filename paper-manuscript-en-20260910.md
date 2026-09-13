# Context-Dependent Entry-Action Selection by LLMs in Finite Rule Tasks: A Small Controlled Study

Working manuscript, 10 September 2026. English adaptation of the [Chinese manuscript](paper-manuscript-20260910.md), with unchanged experiments and endpoints. The [supplement](paper-supplement-en-20260910.md) provides implementation and reporting details. Venue formatting, page-count verification, and a public reproducibility package remain pending.

## Abstract

Can additional context help language models select useful exploratory actions? We separate code-verified opportunity availability from model utilization in finite symbolic rule tasks. In 24 outcome-conditioned development worlds, two contexts provide matched opportunities for success within a fixed verification budget but require different entry actions. The model selects only the entry action; code performs subsequent verification. DeepSeek without thinking, DeepSeek with thinking, and GLM with thinking achieve complete two-arm success in 0/24, 11/24, and 15/24 worlds, respectively. The original non-thinking primary test provides no positive support, and a simple public-information policy also achieves 11/24. In nine worlds selected from another candidate pool where two specified policies fail, DeepSeek with thinking succeeds completely in 2/9. Additional controls show no consistent benefit from aligned redundant references. In single-response rule generation on the same nine worlds, new, repeated, and no additional observations yield mean unseen-input accuracies of 69.27%, 65.80%, and 52.95%. The new-observation condition remains below two minimum-node baselines, and none recovers the target over the full domain. Raising the non-thinking output cap from 256 to 8192 also produces no improvement on an eight-world subset. These observations support the feasibility of local context utilization, but provide weak evidence for an information-specific benefit and do not establish a general scientific-discovery capability or an internal entropy-expansion–contraction mechanism.

## 1. Introduction

When an agent has an incomplete explanation, can additional information help it move beyond its current candidate, choose a useful direction, and formulate a better rule? This question motivates our study. We use entropy expansion and contraction as a conceptual description of broadening candidates and subsequently constraining them with evidence, not as a measured property of model computation. We do not directly test recursive self-improvement (RSI) or general scientific-discovery capability.

We distinguish two questions: whether a context offers an opportunity for success within a specified budget, and whether a model utilizes that opportunity. Enumeration and deterministic verification address the first; actual model responses are required for the second. Model failure is difficult to interpret if no successful action exists, while code-verified availability alone says nothing about model utilization.

We construct paired contexts with matched opportunity counts but different correct actions. Public-information baselines, a targeted policy challenge, auxiliary-reference controls, and an output-cap control constrain the interpretation of successful choices. A separate experiment extends the model's role to producing one candidate rule, comparing a new true observation with repetition of an old observation. Together, these experiments examine a local component of exploration, not an autonomous discovery loop.

### 1.1 Related work and positioning

Explicit hypotheses and executable validation have direct precedents. Hypothesis Search translates natural-language hypotheses into programs tested against observations; Hypothesis Refinement combines proposing, selecting, and revising rules while distinguishing induction from application. Combining LLM proposals with an external interpreter is therefore not our novelty claim. [Wang et al., 2024](https://arxiv.org/abs/2309.05660v2); [Qiu et al., 2024](https://arxiv.org/abs/2310.08559v4).

FunSearch places generated programs in an execution-based evolutionary loop. EvoDiverse studies hypothesis quality and diversity under a fixed validation budget using parallel-tempering-inspired search. We neither implement nor benchmark these search systems, and our experiments do not validate temperature scheduling or a diversity mechanism. [Romera-Paredes et al., 2024](https://www.nature.com/articles/s41586-023-06924-6); [Wang et al., 2026](https://arxiv.org/abs/2606.10587v1).

DiscoveryBench evaluates multi-step, data-driven hypothesis discovery. The recent SCILAWS-BENCH preprint distinguishes rule proposal from fixed real observations from active querying of synthesized hidden laws, and separates predictive fit from scientific validity. Our enumerable local task has a narrower scope. Unseen-input prediction, hidden answers, and executable verification do not by themselves make it a complete scientific-discovery evaluation. [Majumder et al., 2024](https://arxiv.org/abs/2407.01725v1); [Huang et al., 2026](https://arxiv.org/abs/2609.01552v1).

Our contribution is a controlled local test with matched opportunities and distinct correct actions, accompanied by controls that expose its interpretive limits. This describes our design, not a priority claim established by a systematic review or a performance advantage over other discovery systems. Scores across these different tasks and budgets are not directly comparable.

## 2. Tasks and Methods

### 2.1 Worlds and model responsibilities

A world is a finite binary rule problem over three variables in {-2, -1, 0, 1, 2}, giving 125 inputs. Its construction fixes a hidden target and a bank of 256 rules consistent with 12 initial observations, D0, on one coordinate face. The remaining inputs comprise 49 ordered evidence points and 64 test points. A parent program is selected from the bank by node count and then canonical hash, without selecting it by the hidden target. The model sees D0 and the parent, but not the target, test labels, or private answer key.

Each entry task additionally supplies an arithmetic context fragment and ten replayable edits to the parent's predicate operands, each using the fragment once. The response is an opaque option ID, not a generated program or an experiment sequence. Replacing the context changes the program produced by an edit; the paired task consequently tests appropriate action selection under changing context rather than preference for unchanged option text.

After selection, code executes the edit and performs verification and rule identification. Subsequent queries, oracle responses, and hypothesis filtering are not model decisions. Section 2.5 defines the separate rule-generation task, which does not inherit the entry endpoint.

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

### 2.6 Execution and audit

Prompts, frozen scoring, raw responses, and per-world analyses are retained. Each task and condition has one final response; correctness does not determine retries. All final responses are valid and untruncated, and local replay checks have been completed. Attempts, usage, and artifact references are in S5–S6. Local audit is not equivalent to a released public reproduction package.

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

## 5. Discussion and Limitations

### 5.1 Local utilization is not a discovery loop

Entry successes show that some configurations can utilize context under these conditions. Rule generation expands the model's role but remains a single response without an autonomous experiment sequence or full target recovery. New observations are true target information supplied by code, not unrelated inspiration found by the model. The separate results cannot be assembled into evidence that one agent completed an exploration–verification–discovery loop.

### 5.2 Alternative explanations and configuration differences

Hidden targets and answer keys prevent direct answer disclosure, not grammatical priors, semantic or structural matching, short-rule preferences, or ordinary inference. Strong policies provide substantive competing explanations. Targeted selection challenges specified policies without exhausting shortcuts; minimum-node performance also leaves ordinary constraint filtering as an explanation for new-observation results.

Thinking configurations differ substantially in observed performance, but reasoning settings, effective sampling, actual output, and time are not all controlled. The cap comparison narrows a direct truncation explanation without identifying internal mechanisms. A common model alias does not guarantee unchanged weights or service behavior across runs, and equal vendor labels do not imply equal compute.

### 5.3 Construction and sampling limits

Opportunity- and policy-conditioned selection limits generalization; the 64 unseen inputs evaluate prediction within worlds. Reusing worlds or adding configurations does not add independent samples. Small cohorts and single responses limit estimation of stable probabilities but do not justify reinterpreting negative results as latent positive effects. Original directions, full denominators, and mixed controls must be considered together.

### 5.4 Relation to the motivating hypothesis

Broadening candidates and then constraining them through verification is a possible future design principle. Candidate-set contraction does not demonstrate internal entropy reduction, short programs need not be true, and correct entry choices do not reveal an internal expansion–contraction sequence. Our observations are compatible with this motivation but do not identify it as their cause.

Future work would need automatic information and experiment selection, a distinction between new target evidence and seemingly unrelated material, independent worlds after freezing diagnostic policies, and multi-round revision with separate verification. These are open directions, not delivered capabilities. RSI remains a motivation, with neither necessity nor sufficiency established here.

## 6. Conclusion

On outcome-conditioned finite rule tasks, some model configurations select context-specific, correct, and different exploratory entry actions. A targeted challenge retains a small number of successes not fully explained by two fixed policies. However, the original non-thinking primary test provides no positive support, strong public policies remain competitive, aligned references show mixed effects, and new observations offer only a weak advantage over repeated observations without full target recovery. Increasing the non-thinking output cap does not improve the tested subset.

The evidence supports the feasibility of local context utilization, not its attribution to an internal entropy-expansion–contraction mechanism or its extension to general scientific discovery and autonomous RSI. Automatic information selection, active experimentation, iterative rule revision, and independent-task generalization require separate validation.

## References

- Wang, R., Zelikman, E., Poesia, G., Pu, Y., Haber, N., & Goodman, N. D. (2024). [Hypothesis Search: Inductive Reasoning with Language Models](https://arxiv.org/abs/2309.05660v2). ICLR 2024. arXiv:2309.05660v2; first posted in 2023.
- Qiu, L., Jiang, L., Lu, X., Sclar, M., Pyatkin, V., Bhagavatula, C., Wang, B., Kim, Y., Choi, Y., Dziri, N., & Ren, X. (2024). [Phenomenal Yet Puzzling: Testing Inductive Reasoning Capabilities of Language Models with Hypothesis Refinement](https://arxiv.org/abs/2310.08559v4). ICLR 2024. arXiv:2310.08559v4; first posted in 2023.
- Romera-Paredes, B., et al. (2024). [Mathematical discoveries from program search with large language models](https://www.nature.com/articles/s41586-023-06924-6). Nature, 625, 468–475. doi:10.1038/s41586-023-06924-6. Published online on 14 December 2023; cited by volume year.
- Wang, H., Shojaee, P., Meidani, K., Sun, K., Hernández-Lobato, J. M., Head-Gordon, T., He, J., Reddy, C. K., Zhang, C., & Du, Y. (2026). [Towards Diverse Scientific Hypothesis Search with Large Language Models](https://arxiv.org/abs/2606.10587v1). arXiv:2606.10587v1; the author record identifies ICML 2026.
- Majumder, B. P., Surana, H., Agarwal, D., Mishra, B. D., Meena, A., Prakhar, A., Vora, T., Khot, T., Sabharwal, A., & Clark, P. (2024). [DiscoveryBench: Towards Data-Driven Discovery with Large Language Models](https://arxiv.org/abs/2407.01725v1). arXiv:2407.01725v1.
- Huang, Y., et al. (2026). [Can LLMs Discover Scientific Laws in Real and Parallel Worlds?](https://arxiv.org/abs/2609.01552v1). arXiv:2609.01552v1, posted 1 September 2026. Cited as a preprint without assuming peer review.

## Supplement and Availability

The [English supplement](paper-supplement-en-20260910.md) contains methods (S1), provenance (S2), all policies (S3), per-world rule results (S4), execution records (S5), and local artifact references (S6). The [literature-check record](related-work/related-work-20260910.md) documents the bounded review. Historical drafts and frozen experiments remain unchanged. Template selection, actual pagination, a distributable artifact inventory, and independent-environment reproduction remain pending.
